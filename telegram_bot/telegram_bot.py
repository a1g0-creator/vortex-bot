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
import traceback
from collections import deque
from typing import Any, Coroutine, Dict, List, Optional, Set

# Try to import telegram libraries, and set a flag if unavailable.
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
    class Application:
        pass
    class Update:
        pass
    class ContextTypes:
        DEFAULT_TYPE = None

    TELEGRAM_AVAILABLE = False

# Assuming config_loader is in a reachable path.
from config.config_loader import config_loader


class TelegramBot:
    """
    Production-ready async Telegram bot for Vortex v2.1.
    """

    def __init__(self, trading_bot_instance: Any):
        """
        Initializes the TelegramBot.

        Args:
            trading_bot_instance: An instance of the main trading bot.
        """
        if not TELEGRAM_AVAILABLE:
            raise ImportError("python-telegram-bot library is not installed.")

        self.logger = logging.getLogger("VortexTelegramBot")
        self.trading_bot = trading_bot_instance

        # Load config safely
        telegram_config = config_loader.get_config("telegram")
        bot_config = telegram_config.get("bot", {})
        
        self.token: Optional[str] = bot_config.get("token")
        self.chat_id: Optional[str] = None # Will be selected during initialization

        # PTB Application
        self.application: Optional[Application] = None

        # Reliable notification queue
        self.notification_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.consumer_task: Optional[asyncio.Task] = None
        self.max_retries = 5
        self.backoff_steps = [0.5, 1.0, 2.0, 5.0, 10.0]

        # Bot state and statistics
        self.is_running = False
        self.start_time: float = 0.0
        self.messages_sent = 0
        self.messages_failed = 0
        self.last_errors: deque = deque(maxlen=50)

    # --- PTB v20+ Lifecycle Management ---

    async def initialize(self) -> bool:
        """
        Initializes the bot application, handlers, and commands.
        Follows the recommended PTB v20+ initialization flow.
        """
        self.logger.info("Initializing Telegram bot...")
        if not self.token:
            self.logger.error("Telegram token is not configured. Initialization failed.")
            return False

        # 1. Select chat_id from config
        self.chat_id = self._select_chat_id_from_config()
        if not self.chat_id:
            self.logger.error("Could not determine chat_id from config. Initialization failed.")
            return False
        self.logger.info(f"Using chat_id: {self.chat_id}")

        try:
            # 2. Create Application instance
            self.application = Application.builder().token(self.token).build()

            # 3. Register all handlers (commands, buttons)
            self._register_handlers()

            # 4. Initialize the application (connects to Telegram, etc.)
            await self.application.initialize()

            # 5. Verify token and get bot info
            bot_info = await self.application.bot.get_me()
            self.logger.info(f"Bot connected: @{bot_info.username}")

            # 6. Set bot commands in the hamburger menu
            await self._set_bot_commands()

            # 7. Start the background notification consumer
            self.is_running = True # Set flag before starting consumer
            self.consumer_task = asyncio.create_task(self._notification_consumer())

            self.start_time = time.time()
            self.logger.info("✅ Telegram bot initialized successfully.")
            return True

        except Exception as e:
            self.logger.critical(f"Fatal error during bot initialization: {e}", exc_info=True)
            self.is_running = False
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
            await self.application.bot.delete_my_commands()
            self.logger.info("Cleared old bot commands.")

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
            self.logger.info(f"Set {len(commands)} bot commands.")
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
        if not self.application or not self.is_running:
            self.logger.error("Cannot start polling: bot is not initialized or not running.")
            return

        updater = self.application.updater
        if not updater:
            self.logger.critical("Updater is not available in the application. Polling cannot start.")
            return

        if updater.running:
            self.logger.info("Bot is already running (polling).")
            return

        try:
            self.logger.info("Deleting any existing webhook...")
            await self.application.bot.delete_webhook(drop_pending_updates=True)
            
            self.logger.info("Starting polling...")
            await updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            self.logger.info("✅ Telegram бот запущен (polling активен)")
            await self.send_startup_message()

        except Exception as e:
            self.logger.critical(f"Failed to start polling: {e}", exc_info=True)

    async def stop(self) -> None:
        """Gracefully stops the bot and its components."""
        if not self.is_running:
            self.logger.info("Bot is not running, no action needed.")
            return

        self.logger.info("Stopping Telegram bot...")
        self.is_running = False # Signal consumer to stop

        # 1. Stop the application and its updater
        if self.application:
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
                self.logger.info("Polling stopped.")
            if self.application.running:
                await self.application.stop()
                self.logger.info("Application stopped.")
                await self.application.shutdown()
                self.logger.info("Application shut down.")

        # 2. Stop the notification consumer task
        if self.consumer_task and not self.consumer_task.done():
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                self.logger.info("Notification consumer task cancelled.")
        
        self.application = None
        self.logger.info("✅ Telegram bot has been stopped.")

    # --- Notification Queue and Sending ---

    async def _notification_consumer(self):
        """
        Asynchronously consumes messages from the notification queue and sends them.
        """
        self.logger.info("Notification consumer started.")
        while self.is_running:
            try:
                notification = await asyncio.wait_for(self.notification_queue.get(), timeout=1.0)
                await self._send_with_retry(notification)
                self.notification_queue.task_done()
            except asyncio.TimeoutError:
                continue  # No message in queue, continue loop
            except asyncio.CancelledError:
                self.logger.info("Notification consumer has been cancelled.")
                break
            except Exception as e:
                self.logger.error(f"Error in notification consumer: {e}", exc_info=True)
                await asyncio.sleep(1) # Avoid busy-looping on unexpected errors
        self.logger.info("Notification consumer stopped.")

    async def _send_with_retry(self, notification: Dict[str, Any]):
        """
        Sends a message from the queue with retries, backoff, and Markdown fallbacks.
        """
        text = notification["message"]
        chat_id = notification.get("chat_id", self.chat_id)
        kwargs = {
            "reply_markup": notification.get("reply_markup"),
            "disable_web_page_preview": notification.get("disable_web_page_preview", True),
        }

        for attempt in range(self.max_retries):
            try:
                # 1. Try with specified parse_mode (defaulting to MarkdownV2)
                await self.application.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=notification.get("parse_mode", ParseMode.MARKDOWN_V2), **kwargs
                )
                self.messages_sent += 1
                self.logger.debug(f"Message sent successfully to {chat_id} on attempt {attempt + 1}.")
                return

            except RetryAfter as e:
                delay = e.retry_after + 0.1
                self.logger.warning(f"Rate limit hit. Waiting for {delay:.2f}s before retrying.")
                await asyncio.sleep(delay)

            except (TimedOut, NetworkError) as e:
                delay = self.backoff_steps[min(attempt, len(self.backoff_steps) - 1)]
                self.logger.warning(f"Network error ('{e}'). Retrying in {delay}s...")
                await asyncio.sleep(delay)

            except TelegramError as e:
                # This often indicates a parsing error.
                self.logger.warning(f"Telegram API error (likely parsing): '{e}'. Retrying with escaped text.")
                # 2. Retry with fully escaped text
                try:
                    escaped_text = self.fmt(text)
                    await self.application.bot.send_message(
                        chat_id=chat_id, text=escaped_text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs
                    )
                    self.messages_sent += 1
                    self.logger.debug("Message sent successfully with escaped Markdown.")
                    return
                except TelegramError as e2:
                     # 3. Final attempt with plain text
                    self.logger.error(f"Escaped Markdown failed: '{e2}'. Sending as plain text.")
                    try:
                        await self.application.bot.send_message(chat_id=chat_id, text=text, **kwargs)
                        self.messages_sent += 1
                        self.logger.debug("Message sent successfully as plain text.")
                        return
                    except Exception as e3:
                        self.logger.critical(f"FATAL: Could not send message even as plain text: {e3}")
                        self.last_errors.append((time.time(), str(e3)))
                        self.messages_failed += 1
                        return # Irrecoverable for this message

            except Exception as e:
                self.logger.error(f"An unexpected error occurred during send: {e}", exc_info=True)
                delay = self.backoff_steps[min(attempt, len(self.backoff_steps) - 1)]
                await asyncio.sleep(delay)

        self.logger.error(f"Failed to send message to {chat_id} after {self.max_retries} attempts. Discarding.")
        self.last_errors.append((time.time(), f"Max retries exceeded for message: {text[:50]}..."))
        self.messages_failed += 1


    async def send_message(
        self, message: str, parse_mode: str = ParseMode.MARKDOWN_V2, escape: bool = False, reply_markup=None
    ) -> bool:
        """
        Public method to queue a message for sending.

        Args:
            message: The text to send.
            parse_mode: Telegram parse mode (defaults to MarkdownV2).
            escape: If True, pre-emptively escape the message for MarkdownV2.
            reply_markup: Optional keyboard markup.

        Returns:
            True if the message was successfully queued, False otherwise.
        """
        if not self.is_running:
            self.logger.warning("Attempted to send message while bot is not running.")
            return False

        if escape and parse_mode == ParseMode.MARKDOWN_V2:
            message = self.fmt(message)

        notification = {
            "message": message,
            "parse_mode": parse_mode,
            "chat_id": self.chat_id,
            "reply_markup": reply_markup,
            "timestamp": time.time(),
        }

        try:
            self.notification_queue.put_nowait(notification)
            self.logger.debug(f"Message queued. Current queue size: {self.notification_queue.qsize()}")
            return True
        except asyncio.QueueFull:
            self.logger.error("Notification queue is full. Message was dropped.")
            self.messages_failed += 1
            return False

    async def notify(self, message: str, parse_mode: str = ParseMode.MARKDOWN_V2, escape: bool = False) -> bool:
        """Alias for send_message for backward compatibility."""
        return await self.send_message(message, parse_mode, escape)

    async def notify_risk(self, message: str, parse_mode: str = ParseMode.MARKDOWN_V2, escape: bool = False) -> bool:
        """Alias for send_message for backward compatibility."""
        return await self.send_message(message, parse_mode, escape)

    async def notify_signal(self, symbol: str, side: str, price: float, reason: str = "") -> bool:
        """Alias for send_signal for backward compatibility."""
        return await self.send_signal(symbol, side, price, reason)

    @staticmethod
    def fmt(value: Any) -> str:
        """
        Escapes special characters in a string for Telegram MarkdownV2.
        Converts the value to a string before escaping.
        """
        # Characters to escape for MarkdownV2
        # As per: https://core.telegram.org/bots/api#markdownv2-style
        escape_chars = r"_*[]()~`>#+-=|{}.!"
        return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", str(value))

    async def _reply(self, update: Update, text: str, **kwargs: Any) -> None:
        """
        Safely replies to a message with a 3-step fallback mechanism.
        1. Try sending with MarkdownV2.
        2. On failure, escape the whole text and retry with MarkdownV2.
        3. On second failure, send as plain text.
        """
        if not update.effective_message:
            self.logger.warning("_reply called without an effective message to reply to.")
            return

        try:
            # Step 1: Try sending with MarkdownV2
            await update.effective_message.reply_text(
                text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs
            )
        except TelegramError as e:
            self.logger.warning(
                f"MarkdownV2 parsing failed, trying escaped fallback. Error: {e}"
            )
            try:
                # Step 2: Escape the whole text and retry
                escaped_text = self.fmt(text)
                await update.effective_message.reply_text(
                    escaped_text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs
                )
            except TelegramError as e2:
                self.logger.error(
                    f"Escaped MarkdownV2 failed, sending as plain text. Error: {e2}"
                )
                try:
                    # Step 3: Send as plain text
                    await update.effective_message.reply_text(text, **kwargs)
                except Exception as e3:
                    self.logger.error(f"Final reply attempt (plain text) failed: {e3}")

    def _get_main_keyboard(self) -> "ReplyKeyboardMarkup":
        """
        Creates the main reply keyboard markup with a 3x2 layout.
        """
        keyboard = [
            [KeyboardButton("📊 Статус"), KeyboardButton("💰 Баланс")],
            [KeyboardButton("📈 Позиции"), KeyboardButton("🛡️ Риски")],
            [KeyboardButton("⚙️ Режим"), KeyboardButton("🆘 Помощь")],
        ]
        return ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True, # Makes the keyboard persistent for users
            input_field_placeholder="Выберите команду...",
        )

    def _select_chat_id_from_config(self) -> Optional[str]:
        """
        Selects the primary chat_id from the configuration file in a specific order.
        Priority: bot.chat_id -> bot.admin_chat_id -> bot.channel_id.
        """
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
                    self.logger.info(f"Found potential chat_id: {cid}")
                    return str(cid)

        except Exception as e:
            self.logger.error(f"Error reading chat_id from telegram.yaml: {e}")

        return None

    async def _is_admin_user(self, user_id: int) -> bool:
        """
        Checks if a user has administrative privileges.
        An admin is a user whose ID is in `commands.allowed_users` or
        `users.admin_users` from telegram.yaml, or matches the main `bot.chat_id`.
        """
        try:
            telegram_cfg = config_loader.get_config("telegram")
            cmd_cfg = telegram_cfg.get("commands", {})
            users_cfg = telegram_cfg.get("users", {})

            # Collect all potential admin IDs from config
            allowed_ids: Set[str] = set()
            
            # 1. From commands.allowed_users
            for uid in cmd_cfg.get("allowed_users", []):
                allowed_ids.add(str(uid))
            
            # 2. From users.admin_users
            for uid in users_cfg.get("admin_users", []):
                allowed_ids.add(str(uid))

            # 3. The main bot chat_id is always an admin
            if self.chat_id:
                allowed_ids.add(str(self.chat_id))

            user_id_str = str(user_id)
            is_admin = user_id_str in allowed_ids
            
            self.logger.debug(
                f"Admin check for user {user_id_str}: {'SUCCESS' if is_admin else 'FAIL'}. "
                f"Allowed IDs: {allowed_ids}"
            )
            return is_admin

        except Exception as e:
            self.logger.error(f"Error during admin check for user {user_id}: {e}")
            return False

    # ===== Command Handlers =====

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /start command."""
        user = update.effective_user
        self.logger.info(f"/start command from {user.username} (ID: {user.id})")

        # Try to remove any lingering keyboard first
        try:
            await update.effective_message.reply_text(
                text="⏳", reply_markup=ReplyKeyboardRemove()
            )
        except Exception as e:
            self.logger.warning(f"Could not remove old keyboard on /start: {e}")

        # Check config status
        config_status = "✅ Конфигурация загружена"
        try:
            cfg = config_loader.get_config("telegram").get("bot", {})
            if not cfg.get("token") or not self.chat_id:
                config_status = "⚠️ Конфигурация неполная"
        except Exception:
            config_status = "❌ Ошибка загрузки конфигурации"

        message = (
            f"👋 Привет, {self.fmt(user.first_name)}\\!\n\n"
            "🚀 *Vortex Trading Bot v2\\.1*\n"
            "Бот готов к работе\\.\n\n"
            f"{self.fmt(config_status)}\n\n"
            "Используйте команды или кнопки ниже\\."
        )
        await self._reply(update, message, reply_markup=self._get_main_keyboard())

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /status command."""
        uptime_seconds = time.time() - self.start_time
        uptime_hours = uptime_seconds / 3600

        bot_status = "🟢 Работает" if self.is_running else "🔴 Остановлен"
        bot_mode = "неизвестно"
        if hasattr(self.trading_bot, 'mode'):
            bot_mode = self.fmt(getattr(self.trading_bot, 'mode', 'N/A'))

        message = (
            f"📊 *СТАТУС СИСТЕМЫ*\n\n"
            f"Состояние: *{bot_status}*\n"
            f"Время работы: *{uptime_hours:.2f} ч*\n"
            f"Режим: `{bot_mode}`\n\n"
            f"📮 Сообщения \\(отправлено/ошибки\\): "
            f"*{self.messages_sent}* / *{self.messages_failed}*"
        )
        await self._reply(update, message)

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /balance command."""
        if not hasattr(self.trading_bot, 'exchange'):
            await self._reply(update, "⚠️ Биржа не подключена или недоступна\\.")
            return

        try:
            balance_data = await self.trading_bot.exchange.get_balance()
            if not balance_data:
                await self._reply(update, "ℹ️ Не удалось получить данные о балансе, или он пуст\\.")
                return

            message = "💰 *БАЛАНС АККАУНТА*\n\n"
            found_assets = False
            
            # Handle dict-like balance
            if isinstance(balance_data, dict):
                for currency, amount in balance_data.items():
                    try:
                        if float(amount) > 0:
                            message += f"*{self.fmt(currency)}*: `{self.fmt(amount)}`\n"
                            found_assets = True
                    except (ValueError, TypeError):
                        continue
            # Handle object-like balance
            elif hasattr(balance_data, '__dict__'):
                for key, value in vars(balance_data).items():
                    try:
                        if isinstance(value, (int, float)) and value > 0:
                            message += f"*{self.fmt(key)}*: `{self.fmt(value)}`\n"
                            found_assets = True
                    except (ValueError, TypeError):
                        continue
            else: # Fallback for other types
                message += f"`{self.fmt(str(balance_data))}`"
                found_assets = True

            if not found_assets:
                message += "Все балансы нулевые\\."

            await self._reply(update, message)

        except Exception as e:
            self.logger.error(f"Error in /balance command: {e}", exc_info=True)
            await self._reply(update, f"❌ Произошла ошибка при получении баланса\\: {self.fmt(e)}")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /positions command."""
        if not hasattr(self.trading_bot, 'exchange'):
            await self._reply(update, "⚠️ Биржа не подключена или недоступна\\.")
            return

        try:
            positions = await self.trading_bot.exchange.get_positions()
            if not positions:
                await self._reply(update, "📈 Нет открытых позиций\\.")
                return
            
            message = "📈 *ОТКРЫТЫЕ ПОЗИЦИИ*\n\n"
            found_positions = False
            for pos in positions:
                # Assuming pos is a dict, use .get() for safety
                size = float(pos.get('size', 0))
                if size != 0:
                    found_positions = True
                    symbol = self.fmt(pos.get('symbol', 'N/A'))
                    side = self.fmt(pos.get('side', 'N/A'))
                    pnl = float(pos.get('unrealizedPnl', 0))
                    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                    
                    message += (
                        f"*{symbol}* \\| `{side}` \\| Размер: `{self.fmt(size)}`\n"
                        f"  {pnl_emoji} PnL: `{self.fmt(f'{pnl:.2f}')}` USDT\n\n"
                    )

            if not found_positions:
                message += "Нет позиций с ненулевым размером\\."

            await self._reply(update, message)

        except Exception as e:
            self.logger.error(f"Error in /positions command: {e}", exc_info=True)
            await self._reply(update, f"❌ Произошла ошибка при получении позиций\\: {self.fmt(e)}")

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /mode command."""
        current_mode = "неизвестно"
        if hasattr(self.trading_bot, 'mode'):
            current_mode = self.fmt(getattr(self.trading_bot, 'mode', 'N/A'))

        message = (
            f"⚙️ *РЕЖИМ РАБОТЫ*\n\n"
            f"Текущий режим: `{current_mode}`\n\n"
            "*Доступные режимы:*\n"
            "▫️ `signals` \\- только сигналы\n"
            "▫️ `auto` \\- автоматическая торговля\n"
            "▫️ `paper` \\- торговля на бумаге"
        )
        await self._reply(update, message)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /help command."""
        message = (
            "🆘 *СПРАВКА ПО КОМАНДАМ*\n\n"
            "*Основные:*\n"
            "`/start` \\- Перезапустить бота и меню\n"
            "`/status` \\- Статус и статистика\n"
            "`/balance` \\- Баланс счета\n"
            "`/positions` \\- Открытые позиции\n"
            "`/mode` \\- Режим работы\n\n"
            "*Управление рисками:*\n"
            "`/risk` \\- Краткий статус\n"
            "`/risk_show` \\- Детальные лимиты\n"
            "`/risk_enable` \\- Включить RM \\(админ\\)\n"
            "`/risk_disable` \\- Выключить RM \\(админ\\)\n"
            "`/risk_reset` \\- Сброс счетчиков \\(админ\\)"
        )
        await self._reply(update, message)

    # --- Risk Management Commands ---

    def _get_risk_manager_adapter(self) -> Optional[Any]:
        """Safely gets the risk manager instance from the trading bot."""
        if hasattr(self.trading_bot, 'risk_manager'):
            return self.trading_bot.risk_manager
        self.logger.warning("Attempted to access risk_manager, but it was not found.")
        return None

    async def _cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Brief status of the risk manager."""
        rm = self._get_risk_manager_adapter()
        if not rm:
            await self._reply(update, "⚠️ Риск\\-менеджер недоступен\\.")
            return

        enabled = "неизвестно"
        try:
            if hasattr(rm, 'get_risk_enabled'):
                is_enabled = await rm.get_risk_enabled()
            elif hasattr(rm, 'enabled'):
                is_enabled = rm.enabled
            else:
                is_enabled = None
            
            if is_enabled is True:
                enabled = "✅ включен"
            elif is_enabled is False:
                enabled = "⛔ выключен"

        except Exception as e:
            self.logger.warning(f"Could not get risk status: {e}")

        message = (
            f"🛡️ *СТАТУС РИСК\\-МЕНЕДЖМЕНТА*\n\n"
            f"Состояние: *{enabled}*\n\n"
            "Детали: `/risk_show`"
        )
        await self._reply(update, message)

    async def _cmd_risk_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Shows detailed risk limits."""
        rm = self._get_risk_manager_adapter()
        if not rm:
            await self._reply(update, "⚠️ Риск\\-менеджер недоступен\\.")
            return

        status_info = None
        try:
            if hasattr(rm, 'get_risk_status'):
                status_info = await rm.get_risk_status()
            elif hasattr(rm, 'get_current_status'):
                status_info = await rm.get_current_status()
        except Exception as e:
            self.logger.warning(f"Failed to get detailed risk status: {e}")

        if not isinstance(status_info, dict):
            await self._reply(update, "ℹ️ Детальная информация о рисках недоступна\\.")
            return

        message = "📋 *ДЕТАЛЬНЫЕ ЛИМИТЫ РИСКОВ*\n\n"
        enabled = status_info.get('enabled')
        message += f"Статус: *{'✅ включен' if enabled else '⛔ выключен'}*\n\n"

        # Dynamically format any found dictionary, wrapping keys in backticks
        for section, details in status_info.items():
            if isinstance(details, dict):
                message += f"*{self.fmt(section.capitalize())}:*\n"
                for key, value in details.items():
                    message += f"▫️ `{self.fmt(key)}`: `{self.fmt(value)}`\n"
                message += "\n"

        await self._reply(update, message)

    async def _cmd_risk_enable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enables the risk manager (admin only)."""
        if not await self._is_admin_user(update.effective_user.id):
            await self._reply(update, "❌ У вас нет прав для выполнения этой команды\\.")
            return

        rm = self._get_risk_manager_adapter()
        if not rm:
            await self._reply(update, "⚠️ Риск\\-менеджер недоступен\\.")
            return

        try:
            if hasattr(rm, 'enable'): await rm.enable()
            elif hasattr(rm, 'set_risk_enabled'): await rm.set_risk_enabled(True)
            elif hasattr(rm, 'enabled'): rm.enabled = True
            else:
                await self._reply(update, "❌ Не найден метод для включения риск\\-менеджера\\.")
                return

            self.logger.info(f"Risk manager enabled by {update.effective_user.id}.")
            await self._reply(update, "✅ Риск\\-менеджмент *включен*\\.")
        except Exception as e:
            self.logger.error(f"Error enabling risk manager: {e}")
            await self._reply(update, f"❌ Ошибка при включении: {self.fmt(e)}")

    async def _cmd_risk_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Disables the risk manager (admin only)."""
        if not await self._is_admin_user(update.effective_user.id):
            await self._reply(update, "❌ У вас нет прав для выполнения этой команды\\.")
            return

        rm = self._get_risk_manager_adapter()
        if not rm:
            await self._reply(update, "⚠️ Риск\\-менеджер недоступен\\.")
            return

        try:
            if hasattr(rm, 'disable'): await rm.disable()
            elif hasattr(rm, 'set_risk_enabled'): await rm.set_risk_enabled(False)
            elif hasattr(rm, 'enabled'): rm.enabled = False
            else:
                await self._reply(update, "❌ Не найден метод для выключения риск\\-менеджера\\.")
                return

            self.logger.info(f"Risk manager disabled by {update.effective_user.id}.")
            await self._reply(update, "⛔ Риск\\-менеджмент *выключен*\\.")
        except Exception as e:
            self.logger.error(f"Error disabling risk manager: {e}")
            await self._reply(update, f"❌ Ошибка при выключении: {self.fmt(e)}")

    async def _cmd_risk_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Resets risk counters (admin only)."""
        if not await self._is_admin_user(update.effective_user.id):
            await self._reply(update, "❌ У вас нет прав для выполнения этой команды\\.")
            return

        rm = self._get_risk_manager_adapter()
        if not rm:
            await self._reply(update, "⚠️ Риск\\-менеджер недоступен\\.")
            return

        reset_type = context.args[0].lower() if context.args else "daily"

        response_msg = ""
        try:
            if reset_type == "daily" and hasattr(rm, 'reset_daily_counters'):
                await rm.reset_daily_counters()
                response_msg = "✅ Дневные счетчики сброшены\\."
            elif reset_type == "weekly" and hasattr(rm, 'reset_weekly_counters'):
                await rm.reset_weekly_counters()
                response_msg = "✅ Недельные счетчики сброшены\\."
            elif reset_type == "all":
                if hasattr(rm, 'reset_daily_counters'): await rm.reset_daily_counters()
                if hasattr(rm, 'reset_weekly_counters'): await rm.reset_weekly_counters()
                response_msg = "✅ Все доступные счетчики сброшены\\."
            else:
                response_msg = f"❓ Неизвестный тип сброса: `{self.fmt(reset_type)}`\\. Доступно: daily, weekly, all\\."

            self.logger.info(f"Risk counters '{reset_type}' reset by {update.effective_user.id}.")
            await self._reply(update, response_msg)
        except Exception as e:
            self.logger.error(f"Error resetting risk counters: {e}")
            await self._reply(update, f"❌ Ошибка при сбросе: {self.fmt(e)}")

    # --- Button Click Handler ---

    async def _on_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles clicks on the reply keyboard."""
        text = update.message.text
        self.logger.debug(f"Button click detected: '{text}'")

        # Map button text to the corresponding command handler
        button_map: Dict[str, Coroutine] = {
            "📊 Статус": self._cmd_status,
            "💰 Баланс": self._cmd_balance,
            "📈 Позиции": self._cmd_positions,
            "🛡️ Риски": self._cmd_risk_show,  # Show detailed risk info by default
            "⚙️ Режим": self._cmd_mode,
            "🆘 Помощь": self._cmd_help,
        }

        handler = button_map.get(text)
        if handler:
            await handler(update, context)
        else:
            await self._reply(update, "❓ Неизвестная команда\\. Пожалуйста, используйте кнопки или меню команд\\.")

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
        if reason:
            message += f"\n▫️ Причина: _{self.fmt(reason)}_"
        
        return await self.send_message(message)

    async def send_startup_message(self) -> bool:
        """Sends a startup message and ensures the keyboard is set."""
        if not self.application:
            self.logger.warning("Cannot send startup message: bot not initialized.")
            return False

        # This is a fire-and-forget message, errors are logged in send_message
        msg = (
            "🤖 *Vortex Trading Bot v2\\.1*\n"
            "Бот запущен и готов к работе\\.\n\n"
            "Используйте `/status` или кнопки ниже для проверки\\."
        )
        return await self.send_message(
            msg, reply_markup=self._get_main_keyboard()
        )

    async def notify_system(self, message: str) -> bool:
        """Queues a system notification. For sync contexts, wraps in a task."""
        formatted_message = f"🔧 *СИСТЕМНОЕ УВЕДОМЛЕНИЕ*\n\n`{self.fmt(message)}`"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.send_message(formatted_message))
            return True
        except RuntimeError: # No running loop
            # This is a fallback for rare cases where the bot is called from a non-async context
            # after the main loop has started. It's not ideal but better than crashing.
            self.logger.warning("notify_system called from a non-async context without a running loop.")
            asyncio.run(self.send_message(formatted_message))
            return True
        except Exception as e:
            self.logger.error(f"Failed to queue system notification: {e}")
            return False