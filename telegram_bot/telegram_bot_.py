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
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand, ReplyKeyboardRemove
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
        
        self.application: Optional[Application] = None
        self.logger = logging.getLogger("TelegramBot")
        
        # Очередь уведомлений и consumer
        self.notification_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.consumer_task: Optional[asyncio.Task] = None
        
        # Ретраи
        self.max_retries = 5
        self.backoff_steps = [0.5, 1.0, 2.0, 5.0, 10.0]
        
        # Статистика
        self.messages_sent = 0
        self.messages_failed = 0
        
        # Флаг состояния
        self.is_running = False
        
        # За последние ошибки (для диагностики)
        self.last_errors = deque(maxlen=50)
        
        # Для /status
        self.start_time = 0
        
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
            
            # Подбираем chat_id из конфига при отсутствии
            if not self.chat_id:
                selected = self._select_chat_id_from_config()
                if selected:
                    self.chat_id = selected
                    self.logger.info(f"📱 Выбран chat_id из конфига: {self.chat_id}")
                else:
                    self.logger.error("Не найден chat_id в telegram.yaml (bot.chat_id/admin_chat_id/channel_id)")
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
    
    # ===== Backward-compat public API (ожидался старым кодом/тестами) =====
    async def notify(self, message: str, parse_mode: str = 'Markdown', escape: bool = False):
        """Совместимость со старым интерфейсом: отправка через очередь + ретраи"""
        return await self.send_message(message, parse_mode=parse_mode, escape=escape)

    async def notify_risk(self, message: str, parse_mode: str = 'Markdown', escape: bool = False):
        """Уведомления о рисках — тот же надёжный путь доставки"""
        return await self.send_message(message, parse_mode=parse_mode, escape=escape)

    async def notify_signal(self, symbol: str, side: str, price: float, reason: str = ""):
        """Совместимость с прежним именем — alias к send_signal()"""
        return await self.send_signal(symbol, side, price, reason)


    def _select_chat_id_from_config(self) -> Optional[str]:
        """Выбор chat_id по приоритету из telegram.yaml: chat_id → admin_chat_id → channel_id"""
        try:
            telegram_cfg = config_loader.get_config("telegram")
            bot_cfg = telegram_cfg.get("bot", {})
            candidates = [
                bot_cfg.get("chat_id"),
                bot_cfg.get("admin_chat_id"),
                bot_cfg.get("channel_id"),
            ]
            for cid in candidates:
                if cid:
                    return str(cid)
        except Exception as e:
            self.logger.error(f"Ошибка чтения telegram.yaml: {e}")
        return None

    async def _set_bot_commands(self):
        """Установка команд бота в меню Telegram"""
        try:
            # Сначала очищаем ВСЕ старые команды для всех scopes
            await self.application.bot.delete_my_commands(scope=None, language_code=None)
            await self.application.bot.delete_my_commands(scope=None, language_code="ru")
            await self.application.bot.delete_my_commands(scope=None, language_code="en")
            self.logger.info("🗑️ Старые команды удалены для всех языков")
            
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
                BotCommand("risk_enable", "✅ Включить риски"),
                BotCommand("risk_disable", "⛔ Выключить риски"),
                BotCommand("risk_reset", "🔄 Сброс счетчиков"),
                BotCommand("help", "🆘 Справка"),
            ]
            
            await self.application.bot.set_my_commands(commands)
            self.logger.info(f"✅ Установлено {len(commands)} команд в меню Telegram")
            
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
            self.application.add_handler(CommandHandler("risk_disable", self._risk_disable_command))
            self.application.add_handler(CommandHandler("risk_reset", self._risk_reset_command))
            
            # Обработчик текстовых сообщений (кнопки)
            self.application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_buttons)
            )
            
            self.logger.info("✅ Зарегистрировано 12 команд и 1 обработчик кнопок")
            
        except Exception as e:
            self.logger.error(f"Ошибка регистрации обработчиков: {e}")
    
    async def _start_notification_consumer(self):
        """Запуск фонового consumer'а для обработки очереди уведомлений"""
        try:
            if self.consumer_task and not self.consumer_task.done():
                self.logger.warning("Consumer уже запущен")
                return
            
            self.consumer_task = asyncio.create_task(self._notification_consumer())
            self.logger.info("🔧 Фоновый consumer уведомлений создан")
            
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
                self.logger.debug("Consumer уведомлений остановлен")
                break
            except Exception as e:
                self.logger.error(f"Ошибка в consumer'е уведомлений: {e}")
                await asyncio.sleep(1)
    
    async def _send_with_retry(self, notification: Dict[str, Any]):
        """Отправка сообщения с retry и exponential backoff"""
        message = notification.get('message', '')
        parse_mode = notification.get('parse_mode', 'Markdown')
        chat_id = notification.get('chat_id', self.chat_id)
        reply_markup = notification.get('reply_markup', None)

        for attempt in range(self.max_retries):
            try:
                result = await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
                if result:
                    self.messages_sent += 1
                    self.logger.info(f"✅ Сообщение отправлено (попытка {attempt + 1})")
                    return
                else:
                    self.logger.warning("⚠️ send_message вернул пустой результат")
            except RetryAfter as e:
                delay = max(self.backoff_steps[min(attempt, len(self.backoff_steps)-1)], float(getattr(e, "retry_after", 1.0)))
                self.logger.warning(f"⏳ 429 RetryAfter: ждём {delay} сек")
                await asyncio.sleep(delay)
            except (TimedOut, NetworkError) as e:
                delay = self.backoff_steps[min(attempt, len(self.backoff_steps)-1)]
                self.logger.warning(f"🌐 Сетевая ошибка: {e}. Ретрай через {delay} сек")
                await asyncio.sleep(delay)
            except TelegramError as e:
                self.messages_failed += 1
                self.last_errors.append((time.time(), str(e)))
                self.logger.error(f"❌ TelegramError без ретрая: {e}")
                return
            except Exception as e:
                delay = self.backoff_steps[min(attempt, len(self.backoff_steps)-1)]
                self.logger.error(f"❌ Ошибка отправки: {e}. Ретрай через {delay} сек")
                await asyncio.sleep(delay)

        self.messages_failed += 1
        self.logger.error("❌ Исчерпаны попытки отправки сообщения")

    
    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Экранирование специальных символов для Markdown"""
        # Символы, требующие экранирования в MarkdownV2
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        
        return text
    
    async def send_startup_message(self):
        """
        Публичный метод для ядра: отправляет приветствие и принудительно ставит нашу нижнюю клавиатуру.
        Идемпотентен — можно звать повторно.
        """
        if not self.application:
            self.logger.warning("send_startup_message: приложение Telegram не инициализировано")
            return False

        # 1) Удалить старую (залипшую) клавиатуру у клиента
        try:
            await self._send_with_retry({
                'message': "⏳ Обновляю меню…",
                'parse_mode': 'Markdown',
                'chat_id': self.chat_id,
                'reply_markup': ReplyKeyboardRemove()
            })
        except Exception as e:
            self.logger.warning(f"send_startup_message: не удалось удалить старое меню: {e}")

        # 2) Отправить приветствие с нашей клавиатурой
        msg = (
            "🤖 *Vortex Trading Bot v2.1*\n"
            "Бот запущен и готов к работе.\n\n"
            "Ниже — основное меню. Для справки: /help"
        )
        keyboard = self._get_main_keyboard()
        return await self.send_message(msg, parse_mode='Markdown', reply_markup=keyboard)


    async def send_message(self, message: str, parse_mode: str = 'Markdown', escape: bool = False, reply_markup=None):
        """Публичный метод для отправки сообщения (кладёт в очередь). Поддерживает reply_markup."""
        try:
            if not self.is_running or not self.application:
                self.logger.warning("Telegram бот не запущен, сообщение не отправлено")
                return False

            if escape and parse_mode in ['Markdown', 'MarkdownV2']:
                message = self._escape_markdown(message)

            notification = {
                'message': message,
                'parse_mode': parse_mode,
                'chat_id': self.chat_id,
                'reply_markup': reply_markup,
                'timestamp': time.time()
            }
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
        if price >= 1000:
            price_str = f"{price:,.2f}"
        elif price >= 1:
            price_str = f"{price:.2f}"
        else:
            price_str = f"{price:.6f}"
        
        message = (
            f"📣 *СИГНАЛ ТОРГОВЛИ*\n"
            f"📌 Символ: {symbol}\n"
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
    
    async def send_trading_engine_signal(self, symbol: str, signal_type: str, **kwargs):
        """
        Совместимость с ядром: уведомление о сигнале из TradingEngine
        """
        side_map = {
            'open_long': 'Открыть LONG',
            'open_short': 'Открыть SHORT',
            'close_long': 'Закрыть LONG',
            'close_short': 'Закрыть SHORT',
            'tp': 'Take Profit',
            'sl': 'Stop Loss'
        }
        side = side_map.get(signal_type, signal_type.upper())
        price = kwargs.get('price', 0.0)
        reason = kwargs.get('reason', '')
        return await self.send_signal(symbol, side, price, reason)
    
    async def send_position_update(self, symbol: str, side: str, size: float, pnl: float):
        """Уведомление об изменении позиции"""
        pnl_sign = "🟢" if pnl >= 0 else "🔴"
        message = (
            f"📈 *ПОЗИЦИЯ ОБНОВЛЕНА*\n"
            f"📌 {symbol} | {side}\n"
            f"📦 Размер: {size}\n"
            f"{pnl_sign} PnL: {pnl:.2f} USDT"
        )
        return await self.send_message(message)
    
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
            
            # Убираем залипшее старое меню у клиента
            try:
                await update.message.reply_text("⏳ Обновляю меню…", reply_markup=ReplyKeyboardRemove())
            except Exception as _:
                pass

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
     
            # Сообщение статуса
            message = (
                f"📊 *СТАТУС СИСТЕМЫ*\n\n"
                f"Состояние: {status}\n"
                f"Uptime: {uptime_hours:.2f} ч\n"
                f"⏱️ Сообщения: OK={self.messages_sent} / FAIL={self.messages_failed}"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка команды /status: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /balance"""
        try:
            if not self.trading_bot or not hasattr(self.trading_bot, 'get_balance'):
                await update.message.reply_text("⚠️ Торговое ядро недоступно")
                return
            
            balance = await self.trading_bot.get_balance()
            if not balance:
                await update.message.reply_text("⚠️ Баланс недоступен")
                return
            
            wallet = getattr(balance, 'wallet_balance', None)
            available = getattr(balance, 'available_balance', None)
            
            message = (
                "💰 *БАЛАНС АККАУНТА*\n\n"
                f"Кошелек: {wallet if wallet is not None else '—'} USDT\n"
                f"Доступно: {available if available is not None else '—'} USDT"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка команды /balance: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /positions"""
        try:
            if not self.trading_bot or not hasattr(self.trading_bot, 'get_positions'):
                await update.message.reply_text("⚠️ Торговое ядро недоступно")
                return
            
            positions = await self.trading_bot.get_positions()
            if not positions:
                await update.message.reply_text("📭 Открытых позиций нет")
                return
            
            lines = ["📈 *ОТКРЫТЫЕ ПОЗИЦИИ*"]
            for p in positions:
                sym = getattr(p, 'symbol', '?')
                side = getattr(p, 'side', '?')
                sz = getattr(p, 'size', 0)
                entry = getattr(p, 'entry_price', 0.0)
                pnl = getattr(p, 'unrealized_pnl', 0.0)
                pnl_sign = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"• {sym} | {side} | {sz} @ {entry} | {pnl_sign} {pnl:.2f}")
            
            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка команды /positions: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mode"""
        try:
            if not self.trading_bot or not hasattr(self.trading_bot, 'get_mode'):
                await update.message.reply_text("⚠️ Торговое ядро недоступно")
                return
            
            mode = await self.trading_bot.get_mode()
            await update.message.reply_text(f"⚙️ Текущий режим: {mode}")
        except Exception as e:
            self.logger.error(f"Ошибка команды /mode: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk - короткий статус риск-менеджмента"""
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
                f"Состояние: {enabled}\n"
                f"Торговля: {trading}\n"
                f"Дневной PnL: {daily_loss:.2f} / Лимит: {daily_limit:.2f}\n\n"
                "Подробнее: /risk_show"
            )
            
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
            
            message = "📋 *ДЕТАЛЬНЫЕ ЛИМИТЫ*\n\n"
            for k, v in limits.items():
                message += f"• `{k}`: {v}\n"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_show: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_set_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_set <path> <value> - изменение лимита"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для изменения лимитов")
                return
            
            if not hasattr(self.trading_bot, 'risk_manager') or not self.trading_bot.risk_manager:
                await update.message.reply_text("⚠️ Риск-менеджер не инициализирован")
                return
            
            args = context.args
            if len(args) < 2:
                await update.message.reply_text(
                    "Использование: `/risk_set <path> <value>`", parse_mode='Markdown'
                )
                return
            
            path = args[0]
            raw = args[1]
            
            # Пытаемся привести значение к числу, иначе строка
            try:
                if '.' in raw or 'e' in raw.lower():
                    value = float(raw)
                else:
                    value = int(raw)
            except:
                value = raw
            
            # Обновление лимита
            if hasattr(self.trading_bot.risk_manager, "update_limit"):
                success = await self.trading_bot.risk_manager.update_limit(path, value)
                
                if success:
                    message = f"✅ Лимит {path} изменен на {value}"
                else:
                    message = f"❌ Не удалось изменить лимит {path}"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_set: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def _risk_disable_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_disable - выключение риск-менеджмента (явная форма)"""
        try:
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для управления риск-менеджментом")
                return
            if not hasattr(self.trading_bot, 'risk_manager') or not self.trading_bot.risk_manager:
                await update.message.reply_text("⚠️ Риск-менеджер не инициализирован")
                return
            # Явно отключаем
            self.trading_bot.risk_manager.enabled = False
            await update.message.reply_text("🛡️ Риск-менеджмент ⛔ выключен")
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_disable: {e}")
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
        """Команда /risk_reset daily|weekly|all"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для сброса счетчиков")
                return
            
            if not hasattr(self.trading_bot, 'risk_manager') or not self.trading_bot.risk_manager:
                await update.message.reply_text("⚠️ Риск-менеджер не инициализирован")
                return
            
            args = context.args
            if not args:
                message = (
                    "Использование: `/risk_reset <type>`\n"
                    "Где `<type>`: `daily`, `weekly`, `all`"
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
            "⚙️ `/mode` - Текущий режим\n\n"
            
            "*Управление рисками:*\n"
            "🛡️ `/risk` - Краткий статус рисков\n"
            "📋 `/risk_show` - Детальные лимиты\n"
            "⚙️ `/risk_set <path> <value>` - Изменить лимит\n"
            "✅ `/risk_enable true` - Включить риски\n"
            "⛔ `/risk_disable` - Выключить риски\n"
            "🔄 `/risk_reset daily|weekly|all` - Сброс счетчиков\n\n"
            
            "*Примеры управления рисками:*\n"
            "• `/risk_set daily.max_abs_loss 250`\n"
            "• `/risk_set position.max_leverage 5`\n"
            "• `/risk_enable false`\n"
            "• `/risk_reset daily`\n\n"
            
            "🔘 Используйте кнопки ниже для быстрого доступа к основным действиям."
        )
        await update.message.reply_text(message, parse_mode='Markdown')
    
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
            admin_ids = [str(uid) for uid in admin_ids]
            
            return str(user_id) in admin_ids or str(user_id) == str(self.chat_id)
        except Exception as e:
            self.logger.error(f"Ошибка проверки прав администратора: {e}")
            return False
    
    async def run(self):
        """
        Публичный метод (ожидается ядром): идемпотентно инициализирует, запускает polling
        и отправляет стартовое сообщение с клавиатурой.
        """
        # initialize() уже вызывается в ядре, но делаем безопасно
        if not self.application:
            ok = await self.initialize()
            if not ok:
                raise RuntimeError("TelegramBot.run(): initialize() failed")

        # Старт polling
        await self.start()

        # Стартовое приветствие/клава (не падаем по ошибкам)
        try:
            await self.send_startup_message()
        except Exception as e:
            self.logger.warning(f"run(): не удалось отправить стартовое сообщение: {e}")


    async def start(self):
        """Запуск Telegram бота (идемпотентно)"""
        try:
            if not self.application:
                self.logger.warning("Telegram бот не инициализирован")
                return

            updater = getattr(self.application, "updater", None)
            if updater and getattr(updater, "running", False):
                self.logger.info("Telegram бот уже запущен")
                self.is_running = True
                return

            await self.application.start()
            if updater:
                await updater.start_polling()

            self.is_running = True
            self.logger.info("✅ Telegram бот запущен и готов к работе")
        except Exception as e:
            self.logger.error(f"Ошибка запуска Telegram бота: {e}")

    
    
    async def stop(self):
        """Остановка Telegram бота (идемпотентно и безопасно)"""
        try:
            # 1) Остановить consumer, если жив
            if self.consumer_task and not self.consumer_task.done():
                self.consumer_task.cancel()
                try:
                    await self.consumer_task
                except asyncio.CancelledError:
                    pass
                self.logger.debug("Consumer уведомлений остановлен")
            self.consumer_task = None

            if not self.application:
                self.is_running = False
                self.logger.info("Telegram бот не инициализирован — нечего останавливать")
                return

            # 2) Остановить polling, если он реально запущен
            updater = getattr(self.application, "updater", None)
            if updater and getattr(updater, "running", False):
                try:
                    await updater.stop()
                except Exception as e:
                    self.logger.warning(f"Updater.stop() вернул исключение и будет проигнорирован: {e}")
            else:
                self.logger.info("Updater отсутствует или уже остановлен")

            # 3) Корректно завершить Application
            try:
                await self.application.stop()
            except Exception as e:
                self.logger.warning(f"Application.stop(): {e}")

            try:
                await self.application.shutdown()
            except Exception as e:
                self.logger.warning(f"Application.shutdown(): {e}")

            self.is_running = False
            self.logger.info("🛑 Telegram бот остановлен корректно")
        except Exception as e:
            self.logger.error(f"Ошибка остановки Telegram бота: {e}")
            self.is_running = False

