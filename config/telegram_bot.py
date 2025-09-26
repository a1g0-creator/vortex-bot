"""
Telegram бот для управления торговой системой с интеграцией риск-менеджмента
Полностью исправленная production-ready версия
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from datetime import datetime
from collections import deque
import traceback

try:
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    from telegram.error import TimedOut, NetworkError, TelegramError, RetryAfter
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

from config.config_loader import config_loader


class TelegramBot:
    """
    Telegram бот для управления торговой системой
    Полностью исправленная версия с надежной доставкой уведомлений
    """
    
    def __init__(self, token: str, chat_id: str, trading_bot_instance):
        self.token = token
        self.chat_id = chat_id
        self.trading_bot = trading_bot_instance
        
        self.logger = logging.getLogger("TelegramBot")
        self.application = None
        self.is_running = False
        
        # Очередь для надёжной доставки уведомлений
        self.notification_queue = asyncio.Queue(maxsize=1000)
        self.consumer_task = None
        self.max_retries = 5
        self.base_retry_delay = 0.5
        
        # Счетчики и метрики
        self.messages_sent = 0
        self.messages_failed = 0
        self.last_error_time = 0
        
        if not TELEGRAM_AVAILABLE:
            self.logger.warning("Telegram библиотека недоступна")
    
    async def initialize(self) -> bool:
        """
        Инициализация Telegram бота с установкой команд и запуском consumer'а
        
        Returns:
            True если успешно инициализирован
        """
        try:
            if not TELEGRAM_AVAILABLE:
                self.logger.warning("Telegram недоступен - пропускаем инициализацию")
                return False
            
            if not self.token or not self.chat_id:
                self.logger.error("Отсутствуют токен или chat_id для Telegram")
                return False
            
            # Создаем приложение
            self.application = Application.builder().token(self.token).build()
            
            # Инициализируем приложение
            await self.application.initialize()
            
            # Проверяем валидность токена
            try:
                bot_info = await self.application.bot.get_me()
                self.logger.info(f"✅ Бот подключен: @{bot_info.username}")
            except Exception as e:
                self.logger.error(f"Ошибка подключения к Telegram API: {e}")
                return False
            
            # Устанавливаем команды в меню
            await self._set_bot_commands()
            
            # Регистрируем обработчики
            self._register_handlers()
            
            # Запускаем фоновый consumer для очереди уведомлений
            await self._start_notification_consumer()
            
            self.is_running = True
            self.logger.info("✅ Telegram бот полностью инициализирован")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации Telegram бота: {e}")
            return False
    
    async def _set_bot_commands(self):
        """Установка команд бота в меню Telegram"""
        try:
            # Сначала очищаем старые команды
            await self.application.bot.delete_my_commands()
            self.logger.info("🗑️ Старые команды удалены")
            
            # Устанавливаем новые команды для Vortex Trading Bot
            commands = [
                BotCommand("start", "🚀 Запуск бота"),
                BotCommand("status", "📊 Статус системы"),
                BotCommand("balance", "💰 Баланс аккаунта"),
                BotCommand("positions", "📈 Открытые позиции"),
                BotCommand("mode", "⚙️ Режим работы"),
                BotCommand("risk", "🛡️ Статус рисков"),
                BotCommand("risk_show", "📋 Детальные лимиты"),
                BotCommand("risk_set", "⚙️ Изменить лимит"),
                BotCommand("risk_enable", "🔄 Вкл/выкл риски"),
                BotCommand("risk_reset", "🔄 Сброс счетчиков"),
                BotCommand("help", "🆘 Помощь")
            ]
            
            # Устанавливаем команды для всех чатов
            await self.application.bot.set_my_commands(commands)
            self.logger.info(f"✅ Установлено {len(commands)} команд в меню бота")
            
            # Дополнительно устанавливаем описание бота
            await self.application.bot.set_my_description(
                "Vortex Trading Bot v2.1 - Профессиональный торговый бот с управлением рисками"
            )
            
            # Устанавливаем короткое описание
            await self.application.bot.set_my_short_description(
                "Торговый бот с Vortex Bands стратегией"
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка установки команд бота: {e}")
    
    def _register_handlers(self):
        """Регистрация обработчиков команд"""
        try:
            # Основные команды
            self.application.add_handler(CommandHandler("start", self._start_command))
            self.application.add_handler(CommandHandler("status", self._status_command))
            self.application.add_handler(CommandHandler("balance", self._balance_command))
            self.application.add_handler(CommandHandler("positions", self._positions_command))
            self.application.add_handler(CommandHandler("mode", self._mode_command))
            self.application.add_handler(CommandHandler("help", self._help_command))
            
            # Команды риск-менеджмента
            self.application.add_handler(CommandHandler("risk", self._risk_command))
            self.application.add_handler(CommandHandler("risk_show", self._risk_show_command))
            self.application.add_handler(CommandHandler("risk_set", self._risk_set_command))
            self.application.add_handler(CommandHandler("risk_enable", self._risk_enable_command))
            self.application.add_handler(CommandHandler("risk_reset", self._risk_reset_command))
            
            # Обработчик текстовых сообщений (кнопки)
            self.application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_buttons)
            )
            
            self.logger.info("✅ Зарегистрировано 11 команд и 1 обработчик кнопок")
            
        except Exception as e:
            self.logger.error(f"Ошибка регистрации обработчиков: {e}")
    
    async def _start_notification_consumer(self):
        """Запуск фонового consumer'а для обработки очереди уведомлений"""
        try:
            if self.consumer_task and not self.consumer_task.done():
                self.logger.warning("Consumer уже запущен")
                return
            
            self.consumer_task = asyncio.create_task(self._notification_consumer())
            self.logger.info("✅ Запущен фоновый consumer для уведомлений")
            
        except Exception as e:
            self.logger.error(f"Ошибка запуска consumer'а: {e}")
    
    async def _notification_consumer(self):
        """Фоновый consumer для надёжной доставки уведомлений из очереди"""
        self.logger.info("🔄 Consumer уведомлений начал работу")
        
        while self.is_running:
            try:
                # Получаем сообщение из очереди (ждём максимум 1 секунду)
                try:
                    notification = await asyncio.wait_for(
                        self.notification_queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Отправляем с retry логикой
                await self._send_with_retry(notification)
                
            except asyncio.CancelledError:
                self.logger.info("Consumer уведомлений остановлен")
                break
            except Exception as e:
                self.logger.error(f"Ошибка в consumer'е уведомлений: {e}")
                await asyncio.sleep(1)
    
    async def _send_with_retry(self, notification: Dict[str, Any]):
        """Отправка сообщения с retry и exponential backoff"""
        message = notification.get('message', '')
        parse_mode = notification.get('parse_mode', 'Markdown')
        chat_id = notification.get('chat_id', self.chat_id)
        
        for attempt in range(self.max_retries):
            try:
                # Пытаемся отправить с Markdown
                result = await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=parse_mode
                )
                
                if result:
                    self.messages_sent += 1
                    self.logger.info(f"✅ Сообщение отправлено (попытка {attempt + 1})")
                    return
                else:
                    self.logger.warning("⚠️ send_message вернул пустой результат")
                    
            except RetryAfter as e:
                # Telegram просит подождать
                wait_time = e.retry_after
                self.logger.warning(f"Rate limit hit, ждём {wait_time}с")
                await asyncio.sleep(wait_time)
                
            except (TimedOut, NetworkError) as e:
                # Сетевые ошибки - делаем retry
                wait_time = self.base_retry_delay * (2 ** attempt)
                self.logger.warning(f"Сетевая ошибка: {e}, retry через {wait_time}с")
                await asyncio.sleep(wait_time)
                
            except TelegramError as e:
                # Проблема с Markdown или другие ошибки Telegram
                if "can't parse entities" in str(e).lower() or "bad request" in str(e).lower():
                    self.logger.warning(f"Ошибка парсинга Markdown: {e}")
                    # Пробуем отправить без форматирования
                    try:
                        result = await self.application.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            parse_mode=None  # Отправляем как plain text
                        )
                        if result:
                            self.messages_sent += 1
                            self.logger.info("✅ Сообщение отправлено как plain text")
                            return
                    except Exception as fallback_error:
                        self.logger.error(f"Не удалось отправить даже plain text: {fallback_error}")
                else:
                    self.logger.error(f"Telegram ошибка: {e}")
                
                self.messages_failed += 1
                return
                
            except Exception as e:
                # Неожиданные ошибки
                self.logger.error(f"Неожиданная ошибка отправки: {e}")
                self.logger.error(f"Сообщение: {message[:100]}...")  # Логируем начало сообщения
                self.messages_failed += 1
                return
        
        # Если все попытки исчерпаны
        self.messages_failed += 1
        self.logger.error(f"❌ Не удалось отправить сообщение после {self.max_retries} попыток")
    
    # ===========================================
    # ПУБЛИЧНЫЕ МЕТОДЫ ДЛЯ ОТПРАВКИ УВЕДОМЛЕНИЙ
    # ===========================================
    
    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Экранирование специальных символов для Markdown"""
        # Символы, требующие экранирования в MarkdownV2
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        
        return text
    
    async def send_message(self, message: str, parse_mode: str = 'Markdown', escape: bool = False):
        """Публичный метод для отправки сообщения (добавляет в очередь)"""
        try:
            if not self.is_running or not self.application:
                self.logger.warning("Telegram бот не запущен, сообщение не отправлено")
                return False
            
            # Опционально экранируем сообщение
            if escape and parse_mode in ['Markdown', 'MarkdownV2']:
                message = self._escape_markdown(message)
            
            notification = {
                'message': message,
                'parse_mode': parse_mode,
                'chat_id': self.chat_id,
                'timestamp': time.time()
            }
            
            # Добавляем в очередь (неблокирующий вызов)
            try:
                self.notification_queue.put_nowait(notification)
                self.logger.debug(f"📨 Сообщение добавлено в очередь (размер: {self.notification_queue.qsize()})")
                return True
            except asyncio.QueueFull:
                self.logger.error("❌ Очередь уведомлений переполнена!")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка добавления сообщения в очередь: {e}")
            return False
    
    async def send_signal(self, symbol: str, side: str, price: float, reason: str = ""):
        """Отправка сигнала (для совместимости с TradingEngine)"""
        # Форматируем цену с разумной точностью
        price_str = f"{price:.6f}".rstrip('0').rstrip('.')
        
        message = (
            f"📢 *СИГНАЛ*\n\n"
            f"📊 {symbol}\n"
            f"🎯 {side}\n"
            f"💰 Цена: {price_str}\n"
        )
        
        if reason:
            # Укорачиваем reason если слишком длинный
            if len(reason) > 100:
                reason = reason[:97] + "..."
            message += f"📝 Причина: {reason}\n"
        
        message += f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        
        return await self.send_message(message)
    
    async def send_trading_engine_signal(self, symbol: str, signal_type: str, price: float, 
                                        config: str = "", reason: str = ""):
        """Специальный метод для уведомлений от TradingEngine"""
        # Форматируем компактное сообщение без лишних символов
        price_str = f"{price:.6f}".rstrip('0').rstrip('.')
        
        message = f"📢 {symbol} - Сигнал {signal_type}: цена {price_str}"
        
        if config:
            message += f", конфиг {config}"
        
        if reason:
            # Очищаем reason от потенциально проблемных символов
            clean_reason = reason.replace('_', ' ').replace('*', '').replace('`', '')
            message += f", причина: {clean_reason}"
        
        # Отправляем как обычный текст, без Markdown
        return await self.send_message(message, parse_mode=None)
    
    async def notify(self, message: str):
        """Универсальный метод уведомления (для совместимости)"""
        return await self.send_message(message)
    
    # ===========================================
    # КОМАНДЫ БОТА
    # ===========================================
    
    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        try:
            user = update.effective_user
            self.logger.info(f"Команда /start от пользователя {user.username} ({user.id})")
            
            # Проверяем конфигурацию
            config_status = "✅ Конфигурация загружена"
            try:
                telegram_config = config_loader.get_config("telegram")
                bot_config = telegram_config.get("bot", {})
                if bot_config.get("token") and bot_config.get("chat_id"):
                    config_status += f"\n📱 Chat ID: {bot_config.get('chat_id')}"
                else:
                    config_status = "⚠️ Конфигурация неполная"
            except:
                config_status = "❌ Ошибка загрузки конфигурации"
            
            message = (
                f"👋 Привет, {user.first_name}!\n\n"
                "🚀 *Vortex Trading Bot v2.1*\n"
                "Профессиональный торговый бот с управлением рисками\n\n"
                f"{config_status}\n\n"
                "Используйте команды из меню или кнопки ниже.\n"
                "Для справки используйте /help"
            )
            
            keyboard = self._get_main_keyboard()
            await update.message.reply_text(
                message, 
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /start: {e}")
            await update.message.reply_text("❌ Произошла ошибка при инициализации")
    
    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /status"""
        try:
            bot = self.trading_bot
            
            # Получаем статус системы
            status = "🟢 Работает" if bot.is_running else "🔴 Остановлен"
            uptime = time.time() - bot.start_time if hasattr(bot, 'start_time') else 0
            uptime_hours = uptime / 3600
            
            # Получаем статистику
            signals = getattr(bot, 'signals_count', 0)
            trades = getattr(bot, 'trades_count', 0)
            positions_count = len(bot.positions) if hasattr(bot, 'positions') else 0
            
            message = (
                "📊 *СТАТУС СИСТЕМЫ*\n\n"
                f"Статус: {status}\n"
                f"Режим: {bot.mode if hasattr(bot, 'mode') else 'N/A'}\n"
                f"Uptime: {uptime_hours:.1f}ч\n\n"
                f"📈 Сигналов: {signals}\n"
                f"💼 Сделок: {trades}\n"
                f"📊 Позиций: {positions_count}/10\n\n"
                f"📨 Сообщений отправлено: {self.messages_sent}\n"
                f"❌ Ошибок отправки: {self.messages_failed}\n"
                f"📬 В очереди: {self.notification_queue.qsize()}"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /status: {e}")
            await update.message.reply_text(f"❌ Ошибка получения статуса: {e}")
    
    async def _balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /balance"""
        try:
            if not hasattr(self.trading_bot, 'exchange') or not self.trading_bot.exchange:
                await update.message.reply_text("⚠️ Биржа не подключена")
                return
            
            balance = await self.trading_bot.exchange.get_balance()
            if balance:
                message = (
                    "💰 *БАЛАНС АККАУНТА*\n\n"
                    f"Wallet: {balance.wallet_balance:.2f} USDT\n"
                    f"Available: {balance.available_balance:.2f} USDT\n"
                    f"Used: {balance.position_margin:.2f} USDT\n"
                    f"Unrealized PnL: {balance.unrealized_pnl:.2f} USDT"
                )
            else:
                message = "❌ Не удалось получить баланс"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /balance: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /positions"""
        try:
            if not hasattr(self.trading_bot, 'positions'):
                await update.message.reply_text("⚠️ Нет информации о позициях")
                return
            
            positions = self.trading_bot.positions
            
            if not positions:
                message = "📊 *Нет открытых позиций*"
            else:
                message = f"📈 *ОТКРЫТЫЕ ПОЗИЦИИ ({len(positions)}/10)*\n\n"
                
                for symbol, pos in positions.items():
                    hold_time = (time.time() - pos.get('open_time', 0)) / 3600
                    side_emoji = "🟢" if pos['side'] == "Buy" else "🔴"
                    
                    message += (
                        f"{side_emoji} *{symbol}*\n"
                        f"  • Направление: {pos['side']}\n"
                        f"  • Размер: {pos['size']:.6f}\n"
                        f"  • Вход: {pos['entry']:.6f}\n"
                        f"  • Время: {hold_time:.1f}ч\n\n"
                    )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /positions: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mode"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для изменения режима")
                return
            
            args = context.args
            
            if not args:
                current_mode = getattr(self.trading_bot, 'mode', 'unknown')
                message = (
                    f"⚙️ Текущий режим: *{current_mode}*\n\n"
                    "Доступные режимы:\n"
                    "• `auto` - автоматическая торговля\n"
                    "• `signals` - только сигналы\n\n"
                    "Использование: `/mode [auto|signals]`"
                )
            else:
                new_mode = args[0].lower()
                if new_mode in ['auto', 'signals']:
                    self.trading_bot.mode = new_mode
                    message = f"✅ Режим изменен на: *{new_mode}*"
                else:
                    message = "❌ Неверный режим. Используйте: auto или signals"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /mode: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk - краткий статус рисков"""
        try:
            if not hasattr(self.trading_bot, 'risk_manager') or not self.trading_bot.risk_manager:
                await update.message.reply_text("⚠️ Риск-менеджер не инициализирован")
                return
            
            status = await self.trading_bot.risk_manager.get_risk_status()
            
            # Форматируем краткий статус
            enabled = "✅ Включен" if status.get('enabled', False) else "🔴 Выключен"
            trading = "✅ Разрешена" if status.get('can_trade', False) else "🚫 Заблокирована"
            
            daily_stats = status.get('daily', {})
            daily_loss = daily_stats.get('current_loss', 0)
            daily_limit = daily_stats.get('max_loss', 0)
            
            message = (
                "🛡️ *СТАТУС РИСК-МЕНЕДЖМЕНТА*\n\n"
                f"Статус: {enabled}\n"
                f"Торговля: {trading}\n\n"
                f"📊 Дневной убыток: {daily_loss:.2f} / {daily_limit:.2f} USDT\n"
            )
            
            # Добавляем предупреждения если есть
            warnings = status.get('warnings', [])
            if warnings:
                message += "\n⚠️ *Предупреждения:*\n"
                for warning in warnings[:3]:  # Показываем максимум 3
                    message += f"• {warning}\n"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_show_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_show - детальные лимиты"""
        try:
            if not hasattr(self.trading_bot, 'risk_manager') or not self.trading_bot.risk_manager:
                await update.message.reply_text("⚠️ Риск-менеджер не инициализирован")
                return
            
            limits = await self.trading_bot.risk_manager.get_current_limits()
            
            message = "📋 *ДЕТАЛЬНЫЕ ЛИМИТЫ РИСКОВ*\n\n"
            
            # Позиционные лимиты
            message += "*Лимиты позиций:*\n"
            pos_limits = limits.get('position', {})
            message += f"• Max позиций: {pos_limits.get('max_positions', 'N/A')}\n"
            message += f"• Max на символ: {pos_limits.get('max_position_size', 'N/A')} USDT\n"
            message += f"• Max leverage: {pos_limits.get('max_leverage', 'N/A')}x\n\n"
            
            # Дневные лимиты
            message += "*Дневные лимиты:*\n"
            daily = limits.get('daily', {})
            message += f"• Max убыток: {daily.get('max_abs_loss', 'N/A')} USDT\n"
            message += f"• Max убыток %: {daily.get('max_pct_loss', 'N/A')}%\n"
            message += f"• Max сделок: {daily.get('max_trades', 'N/A')}\n\n"
            
            # Недельные лимиты
            message += "*Недельные лимиты:*\n"
            weekly = limits.get('weekly', {})
            message += f"• Max убыток: {weekly.get('max_abs_loss', 'N/A')} USDT\n\n"
            
            # Circuit breaker
            cb = limits.get('circuit_breaker', {})
            if cb.get('enabled'):
                message += "*Circuit Breaker:* ✅\n"
                message += f"• Стоп после {cb.get('consecutive_losses', 'N/A')} убытков подряд\n"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_show: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_set_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_set - изменение лимитов"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для изменения лимитов")
                return
            
            args = context.args
            
            if len(args) < 2:
                message = (
                    "⚙️ *Изменение лимитов рисков*\n\n"
                    "Использование:\n"
                    "`/risk_set <путь> <значение>`\n\n"
                    "Примеры:\n"
                    "• `/risk_set daily.max_abs_loss 250`\n"
                    "• `/risk_set position.max_leverage 5`\n"
                    "• `/risk_set circuit_breaker.enabled true`"
                )
            else:
                path = args[0]
                value = args[1]
                
                # Преобразуем значение в нужный тип
                if value.lower() in ['true', 'false']:
                    value = value.lower() == 'true'
                else:
                    try:
                        value = float(value)
                        if value == int(value):
                            value = int(value)
                    except:
                        pass
                
                # Применяем изменение
                success = await self.trading_bot.risk_manager.update_limit(path, value)
                
                if success:
                    message = f"✅ Лимит {path} изменен на {value}"
                else:
                    message = f"❌ Не удалось изменить лимит {path}"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_set: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_enable_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_enable - включение/выключение риск-менеджмента"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для управления риск-менеджментом")
                return
            
            args = context.args
            
            if not args:
                current = self.trading_bot.risk_manager.enabled
                status = "✅ включен" if current else "🔴 выключен"
                message = (
                    f"🛡️ Риск-менеджмент сейчас: {status}\n\n"
                    "Использование:\n"
                    "`/risk_enable true` - включить\n"
                    "`/risk_enable false` - выключить"
                )
            else:
                enable = args[0].lower() == 'true'
                self.trading_bot.risk_manager.enabled = enable
                status = "✅ включен" if enable else "🔴 выключен"
                message = f"Риск-менеджмент {status}"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_enable: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_reset - сброс счетчиков"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для сброса счетчиков")
                return
            
            args = context.args
            
            if not args:
                message = (
                    "🔄 *Сброс счетчиков рисков*\n\n"
                    "Использование:\n"
                    "`/risk_reset daily` - сбросить дневные\n"
                    "`/risk_reset weekly` - сбросить недельные\n"
                    "`/risk_reset all` - сбросить все"
                )
            else:
                reset_type = args[0].lower()
                
                if reset_type == 'daily':
                    await self.trading_bot.risk_manager.reset_daily_counters()
                    message = "✅ Дневные счетчики сброшены"
                elif reset_type == 'weekly':
                    await self.trading_bot.risk_manager.reset_weekly_counters()
                    message = "✅ Недельные счетчики сброшены"
                elif reset_type == 'all':
                    await self.trading_bot.risk_manager.reset_all_counters()
                    message = "✅ Все счетчики сброшены"
                else:
                    message = "❌ Неверный тип. Используйте: daily, weekly или all"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_reset: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        message = (
            "🆘 *СПРАВКА ПО КОМАНДАМ*\n\n"
            "*Основные команды:*\n"
            "📊 `/status` - Статус системы\n"
            "💰 `/balance` - Баланс аккаунта\n"
            "📈 `/positions` - Открытые позиции\n"
            "⚙️ `/mode [auto|signals]` - Режим работы\n\n"
            
            "*Управление рисками:*\n"
            "🛡️ `/risk` - Краткий статус рисков\n"
            "📋 `/risk_show` - Детальные лимиты\n"
            "⚙️ `/risk_set <path> <value>` - Изменить лимит\n"
            "🔄 `/risk_enable true|false` - Вкл/выкл риски\n"
            "🔄 `/risk_reset daily|weekly|all` - Сброс счетчиков\n\n"
            
            "*Примеры управления рисками:*\n"
            "• `/risk_set daily.max_abs_loss 250`\n"
            "• `/risk_set position.max_leverage 5`\n"
            "• `/risk_enable false`\n"
            "• `/risk_reset daily`\n\n"
            
            "🔘 Используйте кнопки ниже для быстрого доступа"
        )
        
        keyboard = self._get_main_keyboard()
        await update.message.reply_text(
            message,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    async def _handle_buttons(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий кнопок"""
        try:
            text = update.message.text
            
            button_commands = {
                "📊 Статус": self._status_command,
                "💰 Баланс": self._balance_command,
                "📈 Позиции": self._positions_command,
                "🛡️ Риски": self._risk_command,
                "⚙️ Режим": self._mode_command,
                "🆘 Помощь": self._help_command
            }
            
            handler = button_commands.get(text)
            if handler:
                await handler(update, context)
            else:
                await update.message.reply_text(
                    "❓ Неизвестная команда. Используйте кнопки меню или команды."
                )
                
        except Exception as e:
            self.logger.error(f"Ошибка обработки кнопки: {e}")
            await update.message.reply_text(f"❌ Ошибка обработки команды: {e}")
    
    def _get_main_keyboard(self):
        """Создание основного меню"""
        keyboard = [
            [KeyboardButton("📊 Статус"), KeyboardButton("💰 Баланс")],
            [KeyboardButton("📈 Позиции"), KeyboardButton("🛡️ Риски")],
            [KeyboardButton("⚙️ Режим"), KeyboardButton("🆘 Помощь")]
        ]
        
        return ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
            input_field_placeholder="Выберите команду..."
        )
    
    async def _is_admin_user(self, user_id: int) -> bool:
        """Проверка прав администратора"""
        try:
            # Получаем список админов из конфигурации
            telegram_config = config_loader.get_config("telegram")
            admin_ids = telegram_config.get("users", {}).get("admin_users", [])
            
            # Конвертируем в строки для сравнения
            admin_ids_str = [str(aid) for aid in admin_ids]
            
            # Если конфига нет, проверяем через основной chat_id
            if not admin_ids_str:
                return str(user_id) == str(self.chat_id)
            
            return str(user_id) in admin_ids_str
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки прав админа: {e}")
            return False
    
    # ===========================================
    # МЕТОДЫ ЖИЗНЕННОГО ЦИКЛА
    # ===========================================
    
    async def start(self):
        """Запуск Telegram бота"""
        try:
            if not self.application:
                self.logger.warning("Telegram бот не инициализирован")
                return
            
            # Запускаем polling
            await self.application.start()
            await self.application.updater.start_polling()
            
            self.logger.info("✅ Telegram бот запущен и готов к работе")
            
        except Exception as e:
            self.logger.error(f"Ошибка запуска Telegram бота: {e}")
    
    async def stop(self):
        """Остановка Telegram бота"""
        try:
            self.is_running = False
            
            # Останавливаем consumer
            if self.consumer_task and not self.consumer_task.done():
                self.consumer_task.cancel()
                try:
                    await self.consumer_task
                except asyncio.CancelledError:
                    pass
            
            # Останавливаем приложение
            if self.application:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            
            self.logger.info("✅ Telegram бот остановлен")
            
        except Exception as e:
            self.logger.error(f"Ошибка остановки Telegram бота: {e}")
    
    async def run(self):
        """Запуск Telegram бота (для обратной совместимости)"""
        await self.start()
    
    # ===========================================
    # СПЕЦИАЛЬНЫЕ УВЕДОМЛЕНИЯ
    # ===========================================
    
    async def send_startup_message(self):
        """Уведомление о запуске системы"""
        message = (
            "🚀 *Vortex Trading Bot v2.1 запущен!*\n\n"
            f"⚙️ Режим: {getattr(self.trading_bot, 'mode', 'N/A')}\n"
            f"🕒 Время запуска: {datetime.now().strftime('%H:%M:%S')}\n\n"
            "✅ Все системы готовы к работе\n"
            "🛡️ Риск-менеджмент активирован"
        )
        
        return await self.send_message(message)
    
    async def send_shutdown_message(self):
        """Уведомление о завершении работы"""
        uptime = time.time() - getattr(self.trading_bot, 'start_time', time.time())
        uptime_hours = uptime / 3600
        
        message = (
            "🛑 *Vortex Trading Bot завершает работу*\n\n"
            f"⏰ Время работы: {uptime_hours:.1f}ч\n"
            f"📨 Отправлено сообщений: {self.messages_sent}\n"
            f"❌ Ошибок отправки: {self.messages_failed}\n"
            f"🕒 Время остановки: {datetime.now().strftime('%H:%M:%S')}\n\n"
            "✅ Корректное завершение"
        )
        
        return await self.send_message(message)
    
    async def send_trade_notification(self, symbol: str, side: str, size: float, 
                                    price: float, reason: str = ""):
        """Уведомление о торговой операции"""
        side_emoji = "📈" if side == "Buy" else "📉"
        
        message = (
            f"{side_emoji} *СДЕЛКА ВЫПОЛНЕНА*\n\n"
            f"💰 Символ: {symbol}\n"
            f"📊 Направление: {side}\n"
            f"🔢 Размер: {size:.6f}\n"
            f"💵 Цена: {price:.6f}\n"
            f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        if reason:
            message += f"\n📝 Причина: {reason}"
        
        return await self.send_message(message)
    
    async def send_position_notification(self, symbol: str, side: str, size: float, 
                                       entry_price: float, strategy: str = ""):
        """Уведомление об открытии позиции"""
        side_emoji = "📈" if side == "Buy" else "📉"
        
        message = (
            f"{side_emoji} *ПОЗИЦИЯ ОТКРЫТА*\n\n"
            f"💰 Символ: {symbol}\n"
            f"📊 Направление: {side}\n"
            f"🔢 Размер: {size:.6f}\n"
            f"🎯 Цена входа: {entry_price:.6f}\n"
            f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        if strategy:
            message += f"\n🎮 Стратегия: {strategy}"
        
        return await self.send_message(message)
    
    async def send_position_closed_notification(self, symbol: str, side: str, 
                                              pnl: float, pnl_pct: float, reason: str = ""):
        """Уведомление о закрытии позиции"""
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        message = (
            f"{pnl_emoji} *ПОЗИЦИЯ ЗАКРЫТА*\n\n"
            f"💰 Символ: {symbol}\n"
            f"📊 Направление: {side}\n"
            f"💵 P&L: *{pnl:+.2f} USDT*\n"
            f"📊 P&L: *{pnl_pct:+.2f}%*\n"
            f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        if reason:
            message += f"\n📝 Причина: {reason}"
        
        return await self.send_message(message)
    
    async def send_risk_alert(self, alert_type: str, message_text: str):
        """Уведомление о рисках"""
        alert_emoji = {
            "warning": "⚠️",
            "danger": "🚨",
            "info": "ℹ️",
            "critical": "🆘"
        }.get(alert_type, "📢")
        
        message = (
            f"{alert_emoji} *РИСК АЛЕРТ*\n\n"
            f"{message_text}\n\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        
        return await self.send_message(message)