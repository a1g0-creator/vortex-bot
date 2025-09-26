"""
Telegram бот для управления торговой системой
Базовая реализация для интеграции с новой архитектурой
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any
from datetime import datetime

try:
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class TelegramBot:
    """
    Telegram бот для управления торговой системой
    Интегрируется с новой модульной архитектурой
    """
    
    def __init__(self, token: str, chat_id: str, trading_bot_instance):
        self.token = token
        self.chat_id = chat_id
        self.trading_bot = trading_bot_instance
        
        self.logger = logging.getLogger("TelegramBot")
        self.application = None
        self.is_running = False
        
        if not TELEGRAM_AVAILABLE:
            self.logger.warning("Telegram библиотека недоступна")
    
    async def initialize(self) -> bool:
        """
        Инициализация Telegram бота
        
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
            
            # Регистрируем обработчики
            self._register_handlers()
            
            self.logger.info("✅ Telegram бот инициализирован")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации Telegram бота: {e}")
            return False
    
    def _register_handlers(self):
        """Регистрация обработчиков команд"""
        try:
            # Основные команды
            self.application.add_handler(CommandHandler("start", self._start_command))
            self.application.add_handler(CommandHandler("status", self._status_command))
            self.application.add_handler(CommandHandler("balance", self._balance_command))
            self.application.add_handler(CommandHandler("positions", self._positions_command))
            self.application.add_handler(CommandHandler("risk", self._risk_command))
            self.application.add_handler(CommandHandler("mode", self._mode_command))
            self.application.add_handler(CommandHandler("help", self._help_command))
            
            # Обработчик текстовых сообщений (кнопки)
            self.application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_buttons)
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка регистрации обработчиков: {e}")
    
    async def run(self):
        """Запуск Telegram бота"""
        try:
            if not self.application:
                self.logger.warning("Telegram бот не инициализирован")
                return
            
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)
            
            self.is_running = True
            self.logger.info("🤖 Telegram бот запущен")
            
            # Ожидаем остановки
            while self.is_running:
                await asyncio.sleep(1)
                
        except Exception as e:
            self.logger.error(f"Ошибка работы Telegram бота: {e}")
        finally:
            if self.application:
                await self.application.stop()
    
    async def stop(self):
        """Остановка Telegram бота"""
        self.is_running = False
        if self.application:
            await self.application.stop()
    
    # Обработчики команд
    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        try:
            keyboard = self._get_main_keyboard()
            
            message = (
                "🚀 *Vortex Trading Bot v2.1*\n\n"
                "Добро пожаловать в новую версию торгового бота!\n\n"
                "🎯 *Возможности:*\n"
                "• Модульная архитектура\n"
                "• Улучшенный риск-менеджмент\n"
                "• Vortex Bands стратегия\n"
                "• Real-time мониторинг\n\n"
                "Используйте кнопки ниже для управления:"
            )
            
            await update.message.reply_text(
                message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
    
    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /status"""
        try:
            # ✅ ИСПРАВЛЕНО: добавлен await
            status = await self.trading_bot.get_system_status()
            
            uptime_hours = status.get("uptime", 0) / 3600
            
            message = (
                f"📊 *СТАТУС СИСТЕМЫ*\n\n"
                f"🟢 Статус: {'Работает' if status.get('is_running', False) else 'Остановлен'}\n"
                f"⚙️ Режим: *{status.get('mode', 'Unknown')}*\n"
                f"⏰ Время работы: *{uptime_hours:.1f}ч*\n"
                f"💰 Баланс: {status.get('balance', {}).get('wallet_balance', 0):.2f} USDT\n"
                f"📈 Позиций: {status.get('positions', {}).get('total_positions', 0)}\n\n"
                f"🕒 Обновлено: {datetime.now().strftime('%H:%M:%S')}"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            await update.message.reply_text(f"Ошибка получения статуса: {e}")
    
    async def _balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /balance"""
        try:
            balance_info = await self.trading_bot.get_balance_info()
            
            if "error" in balance_info:
                await update.message.reply_text(f"❌ Ошибка: {balance_info['error']}")
                return
            
            pnl_emoji = "📈" if balance_info.get("total_pnl", 0) >= 0 else "📉"
            
            message = (
                f"💰 *БАЛАНС АККАУНТА*\n\n"
                f"💵 Текущий: *{balance_info.get('current_balance', 0):.2f} USDT*\n"
                f"💸 Доступный: {balance_info.get('available_balance', 0):.2f} USDT\n"
                f"🔒 Заблокирован: {balance_info.get('used_balance', 0):.2f} USDT\n"
                f"💰 Нереализованный P&L: {balance_info.get('unrealized_pnl', 0):.2f} USDT\n\n"
                f"🏦 Начальный капитал: {balance_info.get('initial_capital', 0):.2f} USDT\n"
                f"{pnl_emoji} Общий P&L: *{balance_info.get('total_pnl', 0):+.2f} USDT* "
                f"({balance_info.get('total_pnl_percent', 0):+.2f}%)\n\n"
                f"🔄 Обновлено: {datetime.now().strftime('%H:%M:%S')}"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            await update.message.reply_text(f"Ошибка получения баланса: {e}")
    
    async def _positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /positions"""
        try:
            # ✅ ИСПРАВЛЕНО: добавлен await
            status = await self.trading_bot.get_system_status()
            positions_summary = status.get("positions", {})
            
            if positions_summary.get("total_positions", 0) == 0:
                await update.message.reply_text("📭 Открытых позиций нет")
                return
            
            message = "📊 *ОТКРЫТЫЕ ПОЗИЦИИ*\n\n"
            
            # Проверяем наличие позиций в данных
            positions_list = positions_summary.get("positions", [])
            if not positions_list:
                await update.message.reply_text("📭 Детали позиций недоступны")
                return
            
            for pos in positions_list:
                symbol = pos.get("symbol", "Unknown")
                side = pos.get("side", "Unknown")
                pnl_pct = pos.get("pnl_percentage", 0)
                duration = pos.get("duration_minutes", 0)
                
                side_emoji = "📈" if side == "Buy" else "📉"
                pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
                
                message += (
                    f"{side_emoji} *{symbol}* - {side}\n"
                    f"💰 Размер: {pos.get('size', 0):.6f}\n"
                    f"🎯 Вход: {pos.get('entry_price', 0):.6f}\n"
                    f"💹 Текущая: {pos.get('current_price', 0):.6f}\n"
                    f"{pnl_emoji} P&L: *{pnl_pct:+.2f}%*\n"
                    f"⏱ Время: {duration:.0f}м\n\n"
                )
            
            message += f"📈 Общий P&L: *{positions_summary.get('total_pnl_percent', 0):+.2f}%*"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            await update.message.reply_text(f"Ошибка получения позиций: {e}")
    
    async def _risk_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk"""
        try:
            # ✅ ИСПРАВЛЕНО: добавлен await
            status = await self.trading_bot.get_system_status()
            risk_status = status.get("risk_status", {})
            
            if not risk_status:
                await update.message.reply_text("❌ Данные о рисках недоступны")
                return
            
            metrics = risk_status.get("metrics", {})
            limits = risk_status.get("limits", {})
            
            trading_allowed = risk_status.get("trading_allowed", True)
            status_emoji = "✅" if trading_allowed else "❌"
            
            message = (
                f"🛡️ *СТАТУС РИСКОВ*\n\n"
                f"{status_emoji} Торговля: {'Разрешена' if trading_allowed else 'Заблокирована'}\n"
                f"📊 Риск-скор: *{metrics.get('risk_score', 0):.1f}/100*\n\n"
                f"📉 Текущая просадка: {metrics.get('current_drawdown_percent', 0):.2f}%\n"
                f"📉 Макс. просадка: {metrics.get('max_drawdown_percent', 0):.2f}%\n"
                f"💰 Дневной P&L: {metrics.get('daily_pnl', 0):+.2f} USDT\n"
                f"🎯 Винрейт: {metrics.get('win_rate', 0):.1f}%\n"
                f"📈 Профит-фактор: {metrics.get('profit_factor', 0):.2f}\n\n"
                f"🚫 Лимиты:\n"
                f"• Просадка: {limits.get('max_drawdown_percent', 20):.1f}%\n"
                f"• Дневные потери: {limits.get('daily_loss_limit', 500):.0f} USDT\n"
                f"• Сделки в день: {limits.get('trades_today', 0)}/{limits.get('max_trades_per_day', 20)}"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            await update.message.reply_text(f"Ошибка получения данных о рисках: {e}")
    
    async def _mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /mode"""
        try:
            args = context.args
            
            if not args:
                current_mode = self.trading_bot.mode
                message = (
                    f"⚙️ *РЕЖИМ РАБОТЫ*\n\n"
                    f"Текущий режим: *{current_mode}*\n\n"
                    f"🤖 *auto* - Автоматическая торговля\n"
                    f"🔍 *signals* - Только сигналы\n\n"
                    f"Для изменения: `/mode auto` или `/mode signals`"
                )
                await update.message.reply_text(message, parse_mode='Markdown')
                return
            
            new_mode = args[0].lower()
            if new_mode not in ["auto", "signals"]:
                await update.message.reply_text(
                    "❌ Неверный режим. Используйте: `auto` или `signals`",
                    parse_mode='Markdown'
                )
                return
            
            if self.trading_bot.set_mode(new_mode):
                mode_emoji = "🤖" if new_mode == "auto" else "🔍"
                await update.message.reply_text(
                    f"✅ Режим изменен на: {mode_emoji} *{new_mode}*",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Не удалось изменить режим")
                
        except Exception as e:
            await update.message.reply_text(f"Ошибка изменения режима: {e}")
    
    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        message = (
            "🆘 *СПРАВКА ПО КОМАНДАМ*\n\n"
            "📊 `/status` - Статус системы\n"
            "💰 `/balance` - Баланс аккаунта\n"
            "📈 `/positions` - Открытые позиции\n"
            "🛡️ `/risk` - Статус рисков\n"
            "⚙️ `/mode [auto|signals]` - Режим работы\n"
            "🆘 `/help` - Эта справка\n\n"
            "🔘 Используйте кнопки ниже для быстрого доступа к функциям"
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
            
            if text == "📊 Статус":
                await self._status_command(update, context)
            elif text == "💰 Баланс":
                await self._balance_command(update, context)
            elif text == "📈 Позиции":
                await self._positions_command(update, context)
            elif text == "🛡️ Риски":
                await self._risk_command(update, context)
            elif text == "⚙️ Режим":
                await self._mode_command(update, context)
            elif text == "🆘 Помощь":
                await self._help_command(update, context)
            else:
                await update.message.reply_text(
                    "❓ Неизвестная команда. Используйте кнопки меню или команды."
                )
                
        except Exception as e:
            await update.message.reply_text(f"Ошибка обработки команды: {e}")
    
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
    
    # Методы для отправки уведомлений
    async def send_startup_message(self):
        """Уведомление о запуске системы"""
        try:
            if not self.application or not TELEGRAM_AVAILABLE:
                return
            
            message = (
                "🚀 *Vortex Trading Bot v2.1 запущен!*\n\n"
                f"⚙️ Режим: {self.trading_bot.mode}\n"
                f"🕒 Время запуска: {datetime.now().strftime('%H:%M:%S')}\n\n"
                "✅ Все системы готовы к работе"
            )
            
            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки стартового сообщения: {e}")
    
    async def send_shutdown_message(self):
        """Уведомление о завершении работы"""
        try:
            if not self.application or not TELEGRAM_AVAILABLE:
                return
            
            uptime = time.time() - self.trading_bot.start_time
            uptime_hours = uptime / 3600
            
            message = (
                "🛑 *Vortex Trading Bot завершает работу*\n\n"
                f"⏰ Время работы: {uptime_hours:.1f}ч\n"
                f"🕒 Время остановки: {datetime.now().strftime('%H:%M:%S')}\n\n"
                "✅ Корректное завершение"
            )
            
            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки сообщения о завершении: {e}")
    
    async def send_trade_notification(self, symbol: str, side: str, size: float, 
                                    price: float, reason: str = ""):
        """Уведомление о торговой операции"""
        try:
            if not self.application or not TELEGRAM_AVAILABLE:
                return
            
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
            
            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки торгового уведомления: {e}")
    
    async def send_risk_alert(self, alert_type: str, message_text: str):
        """Уведомление о рисках"""
        try:
            if not self.application or not TELEGRAM_AVAILABLE:
                return
            
            alert_emoji = {
                "warning": "⚠️",
                "danger": "🚨",
                "info": "ℹ️"
            }.get(alert_type, "📢")
            
            message = (
                f"{alert_emoji} *РИСК АЛЕРТ*\n\n"
                f"{message_text}\n\n"
                f"🕒 {datetime.now().strftime('%H:%M:%S')}"
            )
            
            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки риск алерта: {e}")
