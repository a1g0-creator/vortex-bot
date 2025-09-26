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
        self.start_time = time.time()
        
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
            self.start_time = time.time()
            self.logger.info("✅ Telegram бот полностью инициализирован")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации Telegram бота: {e}")
            self.logger.error(traceback.format_exc())
            return False

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
            self.application.add_handler(CommandHandler("start", self._cmd_start))
            self.application.add_handler(CommandHandler("status", self._cmd_status))
            self.application.add_handler(CommandHandler("balance", self._cmd_balance))
            self.application.add_handler(CommandHandler("positions", self._cmd_positions))
            self.application.add_handler(CommandHandler("mode", self._cmd_mode))
            self.application.add_handler(CommandHandler("help", self._cmd_help))

            # Команды риск-менеджмента
            self.application.add_handler(CommandHandler("risk", self._cmd_risk))
            self.application.add_handler(CommandHandler("risk_show", self._cmd_risk_show))
            self.application.add_handler(CommandHandler("risk_enable", self._cmd_risk_enable))
            self.application.add_handler(CommandHandler("risk_disable", self._cmd_risk_disable))
            self.application.add_handler(CommandHandler("risk_reset", self._cmd_risk_reset))

            # Обработчик текстовых сообщений (кнопки)
            self.application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_button_click)
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
                        self.notification_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Отправляем через надёжный механизм
                await self._send_with_retry(notification)
                
                # Помечаем задачу как выполненную
                self.notification_queue.task_done()
                
            except asyncio.CancelledError:
                self.logger.info("Consumer уведомлений отменён")
                break
            except Exception as e:
                self.logger.error(f"Ошибка в consumer: {e}")
                await asyncio.sleep(1)
        
        self.logger.info("🛑 Consumer уведомлений завершил работу")

    async def _send_with_retry(self, notification: Dict[str, Any]):
        """Отправка сообщения с ретраями и экспоненциальным backoff"""
        message = notification.get('message', '')
        parse_mode = notification.get('parse_mode', 'Markdown')
        chat_id = notification.get('chat_id', self.chat_id)
        reply_markup = notification.get('reply_markup')
        
        for attempt in range(self.max_retries):
            try:
                # Отправляем сообщение
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
                
                self.messages_sent += 1
                self.logger.debug(f"✅ Сообщение отправлено (попытка {attempt + 1})")
                return
                
            except RetryAfter as e:
                # Telegram просит подождать
                delay = e.retry_after + 0.1
                self.logger.warning(f"⏱️ Rate limit: жду {delay} сек")
                await asyncio.sleep(delay)
            except (TimedOut, NetworkError) as e:
                # Временные проблемы сети - ретраи с backoff
                delay = self.backoff_steps[min(attempt, len(self.backoff_steps)-1)]
                self.logger.warning(f"🔄 Сетевая ошибка: {e}. Ретрай через {delay} сек")
                await asyncio.sleep(delay)
            except TelegramError as e:
                # Другие ошибки Telegram - не ретраим
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

    def _get_main_keyboard(self) -> ReplyKeyboardMarkup:
        """Создание основного меню с правильной раскладкой"""
        keyboard = [
            [KeyboardButton("📊 Статус"), KeyboardButton("💰 Баланс")],
            [KeyboardButton("📈 Позиции"), KeyboardButton("🛡️ Риски")],
            [KeyboardButton("⚙️ Режим"), KeyboardButton("🆘 Помощь")]
        ]
        
        return ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
            # Добавляем is_persistent если доступно в PTB v20+
            # is_persistent=True,  # раскомментировать если поддерживается
            selective=False,
            input_field_placeholder="Выберите команду..."
        )

    # ===== ПУБЛИЧНЫЕ МЕТОДЫ ДЛЯ ЯДРА =====

    async def send_message(self, message: str, parse_mode: str = 'Markdown', escape: bool = False, reply_markup=None) -> bool:
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
            self.logger.error(f"Ошибка добавления в очередь: {e}")
            return False

    # Обратно-совместимые публичные методы
    async def notify(self, message: str, parse_mode: str = 'Markdown', escape: bool = False) -> bool:
        """Совместимость со старым интерфейсом: отправка через очередь + ретраи"""
        return await self.send_message(message, parse_mode=parse_mode, escape=escape)

    async def notify_risk(self, message: str, parse_mode: str = 'Markdown', escape: bool = False) -> bool:
        """Уведомления о рисках — тот же надёжный путь доставки"""
        return await self.send_message(message, parse_mode=parse_mode, escape=escape)

    async def notify_signal(self, symbol: str, side: str, price: float, reason: str = "") -> bool:
        """Совместимость с прежним именем — alias к send_signal()"""
        return await self.send_signal(symbol, side, price, reason)

    async def send_signal(self, symbol: str, side: str, price: float, reason: str = "") -> bool:
        """Отправка торгового сигнала"""
        emoji = "🟢" if side.upper() == "BUY" else "🔴"
        message = (
            f"{emoji} *ТОРГОВЫЙ СИГНАЛ*\n"
            f"📊 {symbol}\n"
            f"🎯 {side.upper()}\n"
            f"💰 Цена: {price}\n"
        )
        if reason:
            message += f"📝 Причина: {reason}"
        
        return await self.send_message(message)

    async def send_startup_message(self) -> bool:
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

    # ===== КОМАНДЫ =====

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            except Exception:
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

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /status"""
        try:
            bot = self.trading_bot
            
            # Получаем статус системы
            status = "🟢 Работает" if bot.is_running else "🔴 Остановлен"
            uptime = time.time() - self.start_time
            uptime_hours = uptime / 3600
     
            # Сообщение статуса
            message = (
                f"📊 *СТАТУС СИСТЕМЫ*\n\n"
                f"Состояние: {status}\n"
                f"Uptime: {uptime_hours:.2f} ч\n"
                f"⏱️ Сообщения: OK={self.messages_sent} / FAIL={self.messages_failed}"
            )
            
            # Добавляем информацию о режиме работы
            if hasattr(bot, 'mode'):
                message += f"\n🔧 Режим: {bot.mode}"
            
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка команды /status: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /balance"""
        try:
            # Пытаемся получить баланс через exchange
            if hasattr(self.trading_bot, 'exchange') and self.trading_bot.exchange:
                try:
                    balance_info = await self.trading_bot.exchange.get_balance()
                    if balance_info:
                        message = "💰 *БАЛАНС АККАУНТА*\n\n"
                        
                        # Проверяем тип ответа - может быть dict или объект
                        if hasattr(balance_info, 'items'):
                            # Это словарь
                            for currency, amount in balance_info.items():
                                if float(amount) > 0:
                                    message += f"{currency}: {amount}\n"
                        elif hasattr(balance_info, '__dict__'):
                            # Это объект - получаем его атрибуты
                            for key, value in balance_info.__dict__.items():
                                if isinstance(value, (int, float, str)) and str(value) != '0' and str(value) != '0.0':
                                    message += f"{key}: {value}\n"
                        elif isinstance(balance_info, (int, float)):
                            # Это число - общий баланс
                            message += f"USDT: {balance_info}\n"
                        else:
                            # Попробуем преобразовать в строку
                            message += f"Баланс: {balance_info}\n"
                            
                        if message == "💰 *БАЛАНС АККАУНТА*\n\n":
                            message += "Нет доступных балансов или все балансы равны нулю"
                    else:
                        message = "⚠️ Не удалось получить информацию о балансе"
                except Exception as e:
                    message = f"❌ Ошибка получения баланса: {e}"
            else:
                message = "⚠️ Биржа не подключена"
            
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка команды /balance: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /positions"""
        try:
            # Пытаемся получить позиции
            if hasattr(self.trading_bot, 'exchange') and self.trading_bot.exchange:
                try:
                    positions = await self.trading_bot.exchange.get_positions()
                    if positions:
                        message = "📈 *ОТКРЫТЫЕ ПОЗИЦИИ*\n\n"
                        for pos in positions:
                            if float(pos.get('size', 0)) != 0:
                                symbol = pos.get('symbol', 'N/A')
                                side = pos.get('side', 'N/A')
                                size = pos.get('size', 'N/A')
                                pnl = pos.get('unrealizedPnl', 0)
                                pnl_emoji = "🟢" if float(pnl) >= 0 else "🔴"
                                message += f"• {symbol} {side} {size}\n  {pnl_emoji} PnL: {pnl} USDT\n\n"
                        
                        if message == "📈 *ОТКРЫТЫЕ ПОЗИЦИИ*\n\n":
                            message += "Нет открытых позиций"
                    else:
                        message = "📈 *ОТКРЫТЫЕ ПОЗИЦИИ*\n\nНет открытых позиций"
                except Exception as e:
                    message = f"❌ Ошибка получения позиций: {e}"
            else:
                message = "⚠️ Биржа не подключена"
            
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка команды /positions: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mode"""
        try:
            bot = self.trading_bot
            current_mode = getattr(bot, 'mode', 'unknown')
            
            message = (
                f"⚙️ *РЕЖИМ РАБОТЫ*\n\n"
                f"Текущий режим: *{current_mode}*\n\n"
                "Доступные режимы:\n"
                "• signals - только сигналы\n"
                "• auto - автоматическая торговля\n"
                "• paper - бумажная торговля"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка команды /mode: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    # ===== КОМАНДЫ РИСК-МЕНЕДЖМЕНТА =====

    def _get_risk_manager_adapter(self):
        """Безопасный адаптер для доступа к риск-менеджеру"""
        try:
            if not hasattr(self.trading_bot, 'risk_manager') or not self.trading_bot.risk_manager:
                return None
            return self.trading_bot.risk_manager
        except Exception:
            return None

    async def _cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk - краткий статус рисков"""
        try:
            rm = self._get_risk_manager_adapter()
            if not rm:
                await update.message.reply_text("⚠️ Риск-менеджер недоступен")
                return
            
            # Безопасно проверяем enabled
            try:
                enabled = getattr(rm, 'enabled', None)
                if enabled is None:
                    # Пытаемся вызвать функциональный API
                    if hasattr(rm, 'get_risk_enabled'):
                        enabled = await rm.get_risk_enabled()
                    else:
                        enabled = "неизвестно"
            except Exception:
                enabled = "неизвестно"
            
            status_emoji = "✅" if enabled is True else "⛔" if enabled is False else "❓"
            status_text = "включен" if enabled is True else "выключен" if enabled is False else str(enabled)
            
            message = (
                f"🛡️ *СТАТУС РИСК-МЕНЕДЖМЕНТА*\n\n"
                f"Состояние: {status_emoji} {status_text}\n\n"
                "Для детальной информации: /risk_show\n"
                "Управление: /risk_enable | /risk_disable"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_risk_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_show - детальные лимиты"""
        try:
            rm = self._get_risk_manager_adapter()
            if not rm:
                await update.message.reply_text("⚠️ Риск-менеджер недоступен")
                return
            
            # Пытаемся получить статус через различные методы
            status_info = None
            try:
                if hasattr(rm, 'get_risk_status'):
                    status_info = await rm.get_risk_status()
                elif hasattr(rm, 'get_current_status'):
                    status_info = await rm.get_current_status()
            except Exception as e:
                self.logger.warning(f"Не удалось получить детальный статус: {e}")
            
            if status_info and isinstance(status_info, dict) and 'error' not in status_info:
                # Форматируем полученную информацию
                message = "📋 *ДЕТАЛЬНЫЕ ЛИМИТЫ РИСКОВ*\n\n"
                
                # Общий статус
                enabled = status_info.get('enabled', 'неизвестно')
                message += f"Статус: {'✅ включен' if enabled else '⛔ выключен'}\n\n"
                
                # Дневные лимиты
                daily = status_info.get('daily', {})
                if daily:
                    message += "*Дневные лимиты:*\n"
                    if 'max_abs_loss' in daily:
                        message += f"• Макс. убыток: {daily['max_abs_loss']} USDT\n"
                    if 'max_trades' in daily:
                        message += f"• Макс. сделок: {daily['max_trades']}\n"
                    message += "\n"
                
                # Позиционные лимиты
                position = status_info.get('position', {})
                if position:
                    message += "*Позиционные лимиты:*\n"
                    if 'max_leverage' in position:
                        message += f"• Макс. плечо: {position['max_leverage']}x\n"
                    if 'max_concurrent_positions' in position:
                        message += f"• Макс. позиций: {position['max_concurrent_positions']}\n"
            else:
                # Fallback - базовая информация
                message = (
                    "📋 *РИСК-МЕНЕДЖМЕНТ*\n\n"
                    "Риск-менеджер активен, но детальная\n"
                    "информация недоступна.\n\n"
                    "Используйте /risk для базового статуса"
                )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_show: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_risk_enable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_enable"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для управления риск-менеджментом")
                return
            
            rm = self._get_risk_manager_adapter()
            if not rm:
                await update.message.reply_text("⚠️ Риск-менеджер недоступен")
                return
            
            try:
                # Пытаемся включить через различные методы
                success = False
                if hasattr(rm, 'enable'):
                    success = await rm.enable()
                elif hasattr(rm, 'set_risk_enabled'):
                    success = await rm.set_risk_enabled(True)
                elif hasattr(rm, 'enabled'):
                    rm.enabled = True
                    success = True
                
                if success:
                    await update.message.reply_text("✅ Риск-менеджмент включен")
                    self.logger.info("Риск-менеджмент включен через Telegram")
                else:
                    await update.message.reply_text("⚠️ Не удалось включить риск-менеджмент")
                    
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка включения: {e}")
                
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_enable: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_risk_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_disable"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для управления риск-менеджментом")
                return
            
            rm = self._get_risk_manager_adapter()
            if not rm:
                await update.message.reply_text("⚠️ Риск-менеджер недоступен")
                return
            
            try:
                # Пытаемся выключить через различные методы
                success = False
                if hasattr(rm, 'disable'):
                    success = await rm.disable()
                elif hasattr(rm, 'set_risk_enabled'):
                    success = await rm.set_risk_enabled(False)
                elif hasattr(rm, 'enabled'):
                    rm.enabled = False
                    success = True
                
                if success:
                    await update.message.reply_text("⛔ Риск-менеджмент выключен")
                    self.logger.info("Риск-менеджмент выключен через Telegram")
                else:
                    await update.message.reply_text("⚠️ Не удалось выключить риск-менеджмент")
                    
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка выключения: {e}")
                
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_disable: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_risk_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk_reset"""
        try:
            # Проверяем права админа
            user_id = update.effective_user.id
            if not await self._is_admin_user(user_id):
                await update.message.reply_text("❌ У вас нет прав для управления риск-менеджментом")
                return
            
            rm = self._get_risk_manager_adapter()
            if not rm:
                await update.message.reply_text("⚠️ Риск-менеджер недоступен")
                return
            
            args = context.args
            reset_type = args[0].lower() if args else "daily"
            
            success = False
            if reset_type == "daily":
                if hasattr(rm, 'reset_daily_counters'):
                    success = await rm.reset_daily_counters()
                message = "📊 Дневные счетчики сброшены" if success else "❌ Не удалось сбросить дневные счетчики"
            elif reset_type == "weekly":
                if hasattr(rm, 'reset_weekly_counters'):
                    success = await rm.reset_weekly_counters()
                message = "📊 Недельные счетчики сброшены" if success else "❌ Не удалось сбросить недельные счетчики"
            elif reset_type == "all":
                daily_ok = False
                weekly_ok = False
                if hasattr(rm, 'reset_daily_counters'):
                    daily_ok = await rm.reset_daily_counters()
                if hasattr(rm, 'reset_weekly_counters'):
                    weekly_ok = await rm.reset_weekly_counters()
                success = daily_ok or weekly_ok
                message = "📊 Все счетчики сброшены" if success else "❌ Не удалось сбросить счетчики"
            else:
                message = "❓ Неверный тип сброса. Используйте: daily, weekly или all"
            
            if success:
                self.logger.info(f"Сброшены счетчики риск-менеджера: {reset_type}")
            
            await update.message.reply_text(message)
            
        except Exception as e:
            self.logger.error(f"Ошибка команды /risk_reset: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            "✅ `/risk_enable` - Включить риски\n"
            "⛔ `/risk_disable` - Выключить риски\n"
            "🔄 `/risk_reset daily|weekly|all` - Сброс счетчиков\n\n"
            
            "📘 Используйте кнопки ниже для быстрого доступа к основным действиям."
        )
        await update.message.reply_text(message, parse_mode='Markdown')

    async def _on_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий кнопок"""
        try:
            text = update.message.text
            
            # Маппинг кнопок на команды
            button_commands = {
                "📊 Статус": self._cmd_status,
                "💰 Баланс": self._cmd_balance,
                "📈 Позиции": self._cmd_positions,
                "🛡️ Риски": self._cmd_risk_show,  # Показываем детальные риски
                "⚙️ Режим": self._cmd_mode,
                "🆘 Помощь": self._cmd_help
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

    # ===== ЖИЗНЕННЫЙ ЦИКЛ =====

    async def run(self):
        """
        Публичный метод (ожидается ядром): идемпотентно инициализирует, запускает polling
        и отправляет стартовое сообщение с клавиатурой.
        """
        try:
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

        except Exception as e:
            self.logger.error(f"Ошибка в telegram_bot.run(): {e}")
            raise

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

            # 2) Остановить polling, если живо
            updater = getattr(self.application, "updater", None)
            if updater and getattr(updater, "running", False):
                try:
                    await updater.stop()
                    self.logger.debug("Telegram polling остановлен")
                except Exception as e:
                    self.logger.warning(f"Ошибка остановки polling: {e}")

            # 3) Остановить application
            try:
                await self.application.stop()
                self.logger.debug("Telegram application остановлен")
            except Exception as e:
                self.logger.warning(f"Ошибка остановки application: {e}")

            # 4) Закрыть application
            try:
                await self.application.shutdown()
                self.logger.debug("Telegram application закрыт")
            except Exception as e:
                self.logger.warning(f"Ошибка закрытия application: {e}")

            self.is_running = False
            self.application = None
            self.logger.info("✅ Telegram бот корректно остановлен")

        except Exception as e:
            self.logger.error(f"Ошибка остановки Telegram бота: {e}")
            self.is_running = False

    # ===== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ =====

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Экранирование специальных символов для Markdown"""
        # Символы, требующие экранирования в MarkdownV2
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        
        return text

    def notify_system(self, message: str) -> bool:
        """Системные уведомления - синхронная версия для совместимости"""
        try:
            # Создаем задачу в event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.send_message(f"🔧 СИСТЕМА: {message}"))
            else:
                loop.run_until_complete(self.send_message(f"🔧 СИСТЕМА: {message}"))
            return True
        except Exception as e:
            self.logger.error(f"Ошибка системного уведомления: {e}")
            return False