# -*- coding: utf-8 -*-
"""
Vortex Trading Bot: Production-Ready Telegram Bot (PTB v20+)

This module provides a robust, asynchronous Telegram bot for interaction
with the Vortex trading system. It features a reliable message delivery queue,
graceful error handling, MarkdownV2 formatting with fallbacks, and a clean
lifecycle management for python-telegram-bot v20+.
"""
import asyncio
import logging
import re
import time
from collections import deque
from typing import Any, Coroutine, Dict, Optional, Set

try:
    from telegram import (
        BotCommand,
        KeyboardButton,
        ReplyKeyboardMarkup,
        ReplyKeyboardRemove,
        Update,
    )
    from telegram.constants import ParseMode
    from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    TELEGRAM_AVAILABLE = True
except ImportError:
    # Mock classes for type hinting if telegram is not installed.
    class Application: pass
    class Update: pass
    class ContextTypes: DEFAULT_TYPE = None
    TELEGRAM_AVAILABLE = False

from config.config_loader import config_loader


class TelegramBot:
    """
    Production-ready async Telegram bot for Vortex v2.1.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        trading_bot_instance: Optional[Any] = None,
    ):
        """
        Initializes the TelegramBot.
        Can be initialized with explicit token/chat_id or load them from config.
        """
        self.logger = logging.getLogger("VortexTelegramBot")
        
        if not TELEGRAM_AVAILABLE:
            self.logger.warning("python-telegram-bot is not installed. Telegram integration will be disabled.")
            self.token = None
            self.chat_id = None
        else:
            telegram_config = config_loader.get_config("telegram").get("bot", {})
            self.token = token or telegram_config.get("token")
            self.chat_id = chat_id # Will be selected/overwritten in initialize

        self.trading_bot = trading_bot_instance
        self.application: Optional[Application] = None
        self.notification_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.consumer_task: Optional[asyncio.Task] = None
        self.max_retries = 5
        self.backoff_steps = [0.5, 1.0, 2.0, 5.0, 10.0]
        self.is_running = False
        self.start_time: float = 0.0
        self.messages_sent = 0
        self.messages_failed = 0
        self.last_errors: deque = deque(maxlen=50)

    # --- PTB v20+ Lifecycle Management ---

    async def initialize(self) -> bool:
        """Initializes the bot application, handlers, and commands."""
        if not TELEGRAM_AVAILABLE:
            return False

        self.logger.info("Initializing Telegram bot...")
        if not self.token:
            self.logger.error("Telegram token is not configured. Initialization failed.")
            return False

        # Select chat_id from config if not provided in __init__
        if not self.chat_id:
            self.chat_id = self._select_chat_id_from_config()

        if not self.chat_id:
            self.logger.error("Could not determine chat_id. Initialization failed.")
            return False

        self.logger.info(f"Using chat_id: {self.chat_id}")

        try:
            self.application = Application.builder().token(self.token).build()
            self._register_handlers()
            await self.application.initialize()
            bot_info = await self.application.bot.get_me()
            self.logger.info(f"Bot connected: @{bot_info.username}")
            await self._set_bot_commands()
            self.consumer_task = asyncio.create_task(self._notification_consumer())
            self.start_time = time.time()
            self.logger.info("✅ Telegram bot initialized successfully and consumer is ready.")
            return True
        except Exception as e:
            self.logger.critical(f"Fatal error during bot initialization: {e}", exc_info=True)
            if self.consumer_task:
                self.consumer_task.cancel()
            return False

    def _register_handlers(self):
        """Registers all command and message handlers."""
        handlers = [
            CommandHandler("start", self._cmd_start),
            CommandHandler("status", self._cmd_status),
            CommandHandler("balance", self._cmd_balance),
            CommandHandler("positions", self._cmd_positions),
            CommandHandler("mode", self._cmd_mode),
            CommandHandler("help", self._cmd_help),
            CommandHandler("risk", self._cmd_risk),
            CommandHandler("risk_show", self._cmd_risk_show),
            CommandHandler("risk_enable", self._cmd_risk_enable),
            CommandHandler("risk_disable", self._cmd_risk_disable),
            CommandHandler("risk_reset", self._cmd_risk_reset),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_button_click),
        ]
        self.application.add_handlers(handlers)
        self.logger.info(f"Registered {len(handlers)} handlers.")

    async def _set_bot_commands(self):
        """Clears old commands and sets the new ones for the bot menu."""
        try:
            commands = [
                BotCommand("start", "🚀 Запуск и меню"),
                BotCommand("status", "📊 Статус системы"),
                BotCommand("balance", "💰 Баланс"),
                BotCommand("positions", "📈 Открытые позиции"),
                BotCommand("mode", "⚙️ Режим работы"),
                BotCommand("help", "🆘 Справка"),
                BotCommand("risk", "🛡️ Статус рисков"),
                BotCommand("risk_show", "📋 Детальные лимиты"),
                BotCommand("risk_enable", "✅ Включить RM (админ)"),
                BotCommand("risk_disable", "⛔ Выключить RM (админ)"),
                BotCommand("risk_reset", "🔄 Сброс счетчиков (админ)"),
            ]
            await self.application.bot.set_my_commands(commands)
            self.logger.info(f"✅ Установлено {len(commands)} команд в меню Telegram")
        except Exception as e:
            self.logger.error(f"Failed to set bot commands: {e}")

    async def run(self) -> None:
        """The main entry point to initialize and start the bot."""
        if await self.initialize():
            await self.start()
        else:
            self.logger.critical("Bot run sequence aborted due to initialization failure.")

    async def start(self) -> None:
        """Starts the polling process."""
        if not TELEGRAM_AVAILABLE or not self.application:
            self.logger.error("Cannot start polling: bot is not available or not initialized.")
            return

        if self.is_running:
            self.logger.info("Bot is already running (polling).")
            return

        try:
            self.logger.info("Deleting any existing webhook...")
            await self.application.bot.delete_webhook(drop_pending_updates=True)
            
            self.logger.info("Starting polling...")
            await self.application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            self.is_running = True
            self.logger.info("✅ Telegram бот запущен (polling активен)")
            await self.send_startup_message()

        except Exception as e:
            self.logger.critical(f"Failed to start polling: {e}", exc_info=True)
            self.is_running = False
            await self.stop()

    async def stop(self) -> None:
        """Gracefully stops the bot and its components."""
        self.is_running = False
        self.logger.info("Stopping Telegram bot...")

        if self.consumer_task and not self.consumer_task.done():
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass # Expected
        self.logger.info("Notification consumer stopped.")

        if self.application:
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
                self.logger.info("Polling stopped.")
            if self.application.running:
                await self.application.stop()
                await self.application.shutdown()
                self.logger.info("Application shut down.")
        
        self.application = None
        self.logger.info("✅ Telegram bot has been stopped.")

    # --- Notification Queue and Sending ---

    async def _notification_consumer(self):
        """Asynchronously consumes messages from the notification queue and sends them."""
        self.logger.info("Notification consumer started.")
        while True:
            try:
                notification = await self.notification_queue.get()
                if not self.is_running and self.notification_queue.empty():
                    self.notification_queue.task_done()
                    break
                await self._send_with_retry(notification)
                self.notification_queue.task_done()
            except asyncio.CancelledError:
                self.logger.info("Notification consumer has been cancelled.")
                break
            except Exception as e:
                self.logger.error(f"Error in notification consumer: {e}", exc_info=True)
                await asyncio.sleep(1)
        self.logger.info("Notification consumer finished.")

    async def _send_with_retry(self, notification: Dict[str, Any]):
        """Sends a message from the queue with retries, backoff, and Markdown fallbacks."""
        text = notification["message"]
        kwargs = {"reply_markup": notification.get("reply_markup")}

        for attempt in range(self.max_retries):
            try:
                await self.application.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs
                )
                self.messages_sent += 1
                return
            except RetryAfter as e:
                delay = e.retry_after + 0.1
                self.logger.warning(f"Rate limit hit. Waiting for {delay:.2f}s.")
                await asyncio.sleep(delay)
            except (TimedOut, NetworkError) as e:
                delay = self.backoff_steps[min(attempt, len(self.backoff_steps) - 1)]
                self.logger.warning(f"Network error ('{e}'). Retrying in {delay}s...")
                await asyncio.sleep(delay)
            except TelegramError as e:
                self.logger.warning(f"MarkdownV2 error: '{e}'. Retrying with escaped text.")
                try:
                    escaped_text = self.fmt(text)
                    await self.application.bot.send_message(
                        chat_id=self.chat_id, text=escaped_text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs
                    )
                    self.messages_sent += 1
                    return
                except TelegramError as e2:
                    self.logger.error(f"Escaped Markdown failed: '{e2}'. Sending as plain text.")
                    try:
                        await self.application.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)
                        self.messages_sent += 1
                        return
                    except Exception as e3:
                        self.logger.critical(f"FATAL: Could not send message as plain text: {e3}")
                        self.last_errors.append((time.time(), str(e3)))
                        self.messages_failed += 1
                        return

        self.logger.error(f"Failed to send message after {self.max_retries} attempts. Discarding.")
        self.last_errors.append((time.time(), f"Max retries exceeded: {text[:50]}..."))
        self.messages_failed += 1

    async def send_message(
        self, message: str, parse_mode: str = ParseMode.MARKDOWN_V2, escape: bool = False, reply_markup=None
    ) -> bool:
        """Public method to queue a message for sending."""
        if not self.application:
            return False
        if escape:
            message = self.fmt(message)
        try:
            self.notification_queue.put_nowait({"message": message, "reply_markup": reply_markup})
            return True
        except asyncio.QueueFull:
            self.logger.error("Notification queue is full. Message dropped.")
            return False

    async def notify(self, message: str, **kwargs) -> bool:
        return await self.send_message(message, **kwargs)

    async def notify_risk(self, message: str, **kwargs) -> bool:
        return await self.send_message(message, **kwargs)

    async def notify_signal(self, symbol: str, side: str, price: float, reason: str = "") -> bool:
        return await self.send_signal(symbol, side, price, reason)

    # --- Formatting and Reply Utils ---

    @staticmethod
    def fmt(value: Any) -> str:
        """Escapes special characters in a string for Telegram MarkdownV2."""
        return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", str(value))

    async def _reply(self, update: Update, text: str, **kwargs: Any) -> None:
        """Safely replies to a message with a 3-step fallback mechanism."""
        try:
            await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs)
        except TelegramError:
            try:
                await update.effective_message.reply_text(self.fmt(text), parse_mode=ParseMode.MARKDOWN_V2, **kwargs)
            except TelegramError as e:
                self.logger.error(f"Failed to send even escaped message: {e}")
                await update.effective_message.reply_text(text, **kwargs)

    def _get_main_keyboard(self) -> "ReplyKeyboardMarkup":
        """Creates the main reply keyboard markup with a 3x2 layout."""
        keyboard = [
            [KeyboardButton("📊 Статус"), KeyboardButton("💰 Баланс")],
            [KeyboardButton("📈 Позиции"), KeyboardButton("🛡️ Риски")],
            [KeyboardButton("⚙️ Режим"), KeyboardButton("🆘 Помощь")],
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

    def _select_chat_id_from_config(self) -> Optional[str]:
        """Selects the primary chat_id from the configuration file in a specific order."""
        try:
            bot_cfg = config_loader.get_config("telegram").get("bot", {})
            for cid in [bot_cfg.get("chat_id"), bot_cfg.get("admin_chat_id"), bot_cfg.get("channel_id")]:
                if cid: return str(cid)
        except Exception as e:
            self.logger.error(f"Error reading chat_id from telegram.yaml: {e}")
        return None

    async def _is_admin_user(self, user_id: int) -> bool:
        """Checks if a user has administrative privileges."""
        try:
            cfg = config_loader.get_config("telegram")
            allowed = set(map(str, cfg.get("commands", {}).get("allowed_users", [])))
            allowed.update(map(str, cfg.get("users", {}).get("admin_users", [])))
            if self.chat_id: allowed.add(self.chat_id)
            return str(user_id) in allowed
        except Exception as e:
            self.logger.error(f"Error during admin check for user {user_id}: {e}")
            return False

    # ===== Command Handlers =====

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /start command."""
        user = update.effective_user
        await self._reply(update, f"👋 Привет, {self.fmt(user.first_name)}\\!", reply_markup=self._get_main_keyboard())

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /status command."""
        uptime = (time.time() - self.start_time) / 3600
        mode = self.fmt(getattr(self.trading_bot, 'mode', 'N/A'))
        text = (
            f"📊 *СТАТУС СИСТЕМЫ*\n\n"
            f"Состояние: *{'🟢 Работает' if self.is_running else '🔴 Остановлен'}*\n"
            f"Время работы: *{uptime:.2f} ч*\n"
            f"Режим: `{mode}`\n"
            f"Сообщения \\(sent/failed\\): *{self.messages_sent}*/`{self.messages_failed}`"
        )
        await self._reply(update, text)

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /balance command."""
        try:
            balance_data = await self.trading_bot.exchange.get_balance()
            if not balance_data:
                await self._reply(update, "ℹ️ Баланс пуст или не получен\\.")
                return
            
            message = "💰 *БАЛАНС АККАУНТА*\n\n"
            assets = {k: v for k, v in balance_data.items() if float(v) > 0}
            if not assets:
                message += "Все балансы нулевые\\."
            else:
                for currency, amount in assets.items():
                    message += f"*{self.fmt(currency)}*: `{self.fmt(amount)}`\n"
            await self._reply(update, message)
        except Exception as e:
            await self._reply(update, f"❌ Ошибка получения баланса: {self.fmt(e)}")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /positions command."""
        try:
            positions = await self.trading_bot.exchange.get_positions()
            if not positions:
                await self._reply(update, "📈 Нет открытых позиций\\.")
                return
            
            message = "📈 *ОТКРЫТЫЕ ПОЗИЦИИ*\n\n"
            for pos in positions:
                pnl = float(pos.get('unrealizedPnl', 0))
                message += (
                    f"*{self.fmt(pos.get('symbol'))}* \\| {self.fmt(pos.get('side'))} \\| `{self.fmt(pos.get('size'))}`\n"
                    f"  {'🟢' if pnl >= 0 else '🔴'} PnL: `{self.fmt(f'{pnl:.2f}')}` USDT\n\n"
                )
            await self._reply(update, message)
        except Exception as e:
            await self._reply(update, f"❌ Ошибка получения позиций: {self.fmt(e)}")

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /mode command."""
        mode = self.fmt(getattr(self.trading_bot, 'mode', 'N/A'))
        text = f"⚙️ *РЕЖИМ РАБОТЫ*\n\nТекущий: `{mode}`"
        await self._reply(update, text)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /help command."""
        text = "🆘 *СПРАВКА*\n\nИспользуйте кнопки и команды из меню \\(`/`\\)\\."
        await self._reply(update, text)

    # --- Risk Management Commands ---

    async def _cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Brief status of the risk manager."""
        rm = self.trading_bot.risk_manager
        enabled = "неизвестно"
        try:
            is_enabled = rm.enabled if hasattr(rm, 'enabled') else await rm.get_risk_enabled()
            enabled = "✅ включен" if is_enabled else "⛔ выключен"
        except Exception as e:
            self.logger.warning(f"Could not get risk status: {e}")
        await self._reply(update, f"🛡️ *РИСК\\-МЕНЕДЖМЕНТ*\n\nСостояние: *{enabled}*")

    async def _cmd_risk_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Shows detailed risk limits."""
        rm = self.trading_bot.risk_manager
        try:
            status = await rm.get_risk_status() if hasattr(rm, 'get_risk_status') else await rm.get_current_status()
            message = "📋 *ДЕТАЛЬНЫЕ ЛИМИТЫ*\n\n"
            for section, details in status.items():
                if isinstance(details, dict):
                    message += f"*{self.fmt(section.capitalize())}:*\n"
                    for k, v in details.items(): message += f"▫️ `{self.fmt(k)}`: `{self.fmt(v)}`\n"
            await self._reply(update, message)
        except Exception as e:
            await self._reply(update, f"❌ Ошибка получения лимитов: {self.fmt(e)}")

    async def _run_admin_rm_command(self, update: Update, command: Coroutine):
        """Wrapper for admin-only risk manager commands."""
        if not await self._is_admin_user(update.effective_user.id):
            await self._reply(update, "❌ Нет прав для этой команды\\.")
            return
        try:
            await command
            await self._reply(update, "✅ Команда выполнена\\.")
        except Exception as e:
            await self._reply(update, f"❌ Ошибка выполнения: {self.fmt(e)}")

    async def _cmd_risk_enable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        rm = self.trading_bot.risk_manager
        await self._run_admin_rm_command(update, rm.enable() if hasattr(rm, 'enable') else rm.set_risk_enabled(True))

    async def _cmd_risk_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        rm = self.trading_bot.risk_manager
        await self._run_admin_rm_command(update, rm.disable() if hasattr(rm, 'disable') else rm.set_risk_enabled(False))

    async def _cmd_risk_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        rm = self.trading_bot.risk_manager
        reset_type = (context.args[0] if context.args else "daily").lower()
        if reset_type == "daily": cmd = rm.reset_daily_counters()
        elif reset_type == "weekly": cmd = rm.reset_weekly_counters()
        else: await self._reply(update, "❓ Тип сброса: daily или weekly"); return
        await self._run_admin_rm_command(update, cmd)

    # --- Button Click Handler ---

    async def _on_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles clicks on the reply keyboard."""
        button_map = {
            "📊 Статус": self._cmd_status, "💰 Баланс": self._cmd_balance,
            "📈 Позиции": self._cmd_positions, "🛡️ Риски": self._cmd_risk_show,
            "⚙️ Режим": self._cmd_mode, "🆘 Помощь": self._cmd_help,
        }
        if handler := button_map.get(update.message.text):
            await handler(update, context)

    # --- Public Methods for Core Integration ---

    async def send_signal(self, symbol: str, side: str, price: float, reason: str = "") -> bool:
        """Formats and sends a trading signal."""
        emoji = "🟢" if side.upper() == "BUY" else "🔴"
        message = (
            f"{emoji} *ТОРГОВЫЙ СИГНАЛ*\n\n"
            f"▫️ Инструмент: `{self.fmt(symbol)}`\n"
            f"▫️ Направление: `{self.fmt(side.upper())}`\n"
            f"▫️ Цена: `{self.fmt(price)}`"
        )
        if reason: message += f"\n▫️ Причина: _{self.fmt(reason)}_"
        return await self.send_message(message)

    async def send_startup_message(self) -> bool:
        """Sends a startup message and ensures the keyboard is set."""
        msg = "🤖 *Vortex Trading Bot v2\\.1* запущен\\."
        return await self.send_message(msg, reply_markup=self._get_main_keyboard())

    async def notify_system(self, message: str) -> bool:
        """Queues a system notification."""
        return await self.send_message(f"🔧 *СИСТЕМА*\n\n`{self.fmt(message)}`")