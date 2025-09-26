"""
Главная точка входа Vortex Trading Bot v2.1
Интеграция всех компонентов модульной архитектуры с риск-менеджментом
"""

import asyncio
import logging
import signal
import sys
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import traceback

# Основные компоненты системы
from core.trading_engine import TradingEngine
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from exchanges.exchange_manager import exchange_manager
from exchanges.exchange_factory import exchange_factory, create_bybit_adapter, test_all_exchanges
from config.config_loader import config_loader, get_app_config, get_bybit_credentials
from telegram_bot.telegram_bot import TelegramBot


class VortexTradingBot:
    """
    Главный класс торгового бота - интегратор всех компонентов
    Модульная архитектура с интегрированным риск-менеджментом
    """
    
    def __init__(self):
        self.logger = logging.getLogger("VortexTradingBot")
        
        # Основные компоненты
        self.exchange = None
        self.exchange_manager = exchange_manager
        self.trading_engine = None
        self.position_manager = None
        self.risk_manager = None
        self.telegram_bot = None
        
        # Состояние системы
        self.is_running = False
        self.start_time = time.time()
        self.shutdown_requested = False
        self.trading_enabled = True
        
        # Конфигурация
        self.app_config = get_app_config()
        self.mode = "signals"  # По умолчанию режим сигналов
        
        # Счетчики и метрики
        self.signals_count = 0
        self.trades_count = 0
        self._last_warning_time = 0  # Для ограничения частоты предупреждений
        
        # Настройка обработчиков сигналов
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Настройка обработчиков системных сигналов"""
        def signal_handler(signum, frame):
            self.logger.info(f"Получен сигнал {signum}, начинаем корректное завершение...")
            self.shutdown_requested = True
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def initialize(self) -> bool:
        """
        Инициализация всех компонентов системы
        
        Returns:
            True если все компоненты успешно инициализированы
        """
        try:
            self.logger.info("🚀 Инициализация Vortex Trading Bot v2.1...")
            
            # 1. Инициализация мультибиржевого менеджера
            if not await self._initialize_exchange_manager():
                return False
            
            # 2. Получение основной биржи
            self.exchange = await self.exchange_manager.get_primary_exchange()
            if not self.exchange:
                self.logger.error("Не удалось получить основную биржу")
                return False
            
            # 3. Инициализация менеджера рисков
            if not await self._initialize_risk_manager():
                return False
            
            # 4. Инициализация менеджера позиций
            if not await self._initialize_position_manager():
                return False
            
            # 5. Инициализация торгового движка
            if not await self._initialize_trading_engine():
                return False
            
            # 6. Инициализация Telegram бота (опционально)
            await self._initialize_telegram_bot()
            
            self.is_running = True
            self.logger.info("✅ Все компоненты успешно инициализированы")
            
            return True
            
        except Exception as e:
            self.logger.error(f"💥 Критическая ошибка инициализации: {e}")
            return False
    
    async def _initialize_exchange_manager(self) -> bool:
        """Инициализация мультибиржевого менеджера"""
        try:
            self.logger.info("📡 Инициализация биржевых подключений...")
        
            # Получаем список включенных бирж
            exchanges_config = config_loader.get_config("exchanges")
            enabled_exchanges = exchanges_config.get("exchanges", {}).get("enabled", ["bybit"])
        
            self.logger.info(f"🔍 Включенные биржи: {enabled_exchanges}")
        
            # Инициализируем менеджер бирж
            if not await self.exchange_manager.initialize():
                self.logger.error("❌ Ошибка инициализации менеджера бирж")
                return False
        
            # Проверяем подключения
            if not await self._test_exchange_connections():
                self.logger.warning("⚠️ Некоторые биржи не прошли тест подключения")
                # Не фейлим инициализацию, если хотя бы одна биржа работает
        
            self.logger.info("✅ Менеджер бирж инициализирован")
            return True
        
        except Exception as e:
            self.logger.error(f"Ошибка инициализации менеджера бирж: {e}")
            return False
    
    async def _test_exchange_connections(self) -> bool:
        """Тестирование подключений к биржам через exchange_manager"""
        try:
            self.logger.info("📡 Тестируем подключения к биржам...")
        
            all_ok = True
        
            # Если exchange_manager уже инициализирован, проверяем через него
            if hasattr(self, 'exchange_manager') and self.exchange_manager:
                for exchange_name, adapter in self.exchange_manager.exchanges.items():
                    try:
                        # Проверяем базовое подключение
                        server_time = await adapter.get_server_time()
                        if server_time:
                            self.logger.info(f"✅ {exchange_name}: подключение установлено")
                        
                            # Пробуем получить баланс для полной проверки
                            try:
                                balance = await adapter.get_balance()
                                if balance:
                                    self.logger.info(f"   Баланс: {balance.wallet_balance:.2f} USDT")
                            except Exception as e:
                                self.logger.debug(f"   Не удалось получить баланс: {e}")
                        else:
                            self.logger.warning(f"⚠️ {exchange_name}: не удалось получить время сервера")
                            all_ok = False
                    except Exception as e:
                        self.logger.error(f"❌ {exchange_name}: ошибка подключения: {e}")
                        all_ok = False
        
            # Fallback на старый метод если exchange_manager не доступен
            else:
                # Получаем список включенных бирж
                enabled_exchanges = config_loader.get_enabled_exchanges()
            
                # Проверяем Bybit
                if "bybit" in enabled_exchanges:
                    bybit_config = config_loader.get_active_exchange_config("bybit")
                    credentials = bybit_config
                
                    if not all([credentials.get("api_key"), credentials.get("api_secret")]):
                        self.logger.warning("⚠️ Bybit API ключи не настроены")
                        all_ok = False
                    else:
                        self.logger.info(f"🔍 Тестируем Bybit: env={bybit_config.get('environment', 'demo')}")
                    
                        try:
                            # Создаем тестовое подключение
                            test_exchange = BybitAdapter(
                                api_key=credentials["api_key"],
                                api_secret=credentials["api_secret"],
                                testnet=bybit_config.get('environment') == 'demo',
                                recv_window=credentials.get("recv_window", 10000)
                            )
                        
                            if await test_exchange.initialize():
                                self.logger.info("✅ Bybit подключен")
                            else:
                                self.logger.error("❌ Ошибка подключения к Bybit")
                                all_ok = False
                        
                            await test_exchange.close()
                        
                        except Exception as e:
                            self.logger.error(f"❌ Ошибка тестирования Bybit: {e}")
                            all_ok = False
            
                # Проверяем Bitget
                if "bitget" in enabled_exchanges:
                    if not await self._test_bitget_connection():
                        all_ok = False
        
            return all_ok
        
        except Exception as e:
            self.logger.error(f"Ошибка тестирования подключений: {e}")
            return False

    async def _test_bitget_connection(self) -> bool:
        """Тестирование подключения к Bitget"""
        try:
            # Получаем Bitget конфигурацию через новый метод
            bitget_config = config_loader.get_active_exchange_config("bitget")
        
            if not bitget_config:
                self.logger.warning("⚠️ Bitget конфигурация не найдена")
                return False
        
            # Проверяем наличие всех ключей
            if not all([bitget_config.get("api_key"), 
                       bitget_config.get("api_secret"), 
                       bitget_config.get("api_passphrase")]):
                self.logger.warning("⚠️ Bitget API ключи не настроены")
                return False
        
            is_testnet = bitget_config.get("environment") == "demo"
            self.logger.info(f"🔍 Тестируем Bitget: testnet={is_testnet}")
        
            # Создаем тестовое подключение
            try:
                from exchanges.bitget_adapter import BitgetAdapter
            
                bitget_adapter = BitgetAdapter(
                    api_key=bitget_config["api_key"],
                    api_secret=bitget_config["api_secret"],
                    api_passphrase=bitget_config["api_passphrase"],
                    testnet=is_testnet
                )
            
                # Инициализируем адаптер
                if await bitget_adapter.initialize():
                    # Тестируем базовое подключение
                    server_time = await bitget_adapter.get_server_time()
                    if server_time:
                        self.logger.info("✅ Bitget подключение успешно")
                    
                        # Пробуем получить баланс
                        try:
                            balance = await bitget_adapter.get_balance()
                            if balance:
                                self.logger.info(f"   Баланс: {balance.wallet_balance:.2f} USDT")
                        except Exception as e:
                            self.logger.debug(f"   Не удалось получить баланс: {e}")
                    
                        await bitget_adapter.close()
                        return True
                    else:
                        self.logger.warning("⚠️ Bitget: не удалось получить время сервера")
                        await bitget_adapter.close()
                        return False
                else:
                    self.logger.warning("⚠️ Не удалось инициализировать Bitget адаптер")
                    return False
                
            except Exception as e:
                self.logger.error(f"❌ Ошибка создания Bitget адаптера: {e}")
                return False
            
        except Exception as e:
            self.logger.warning(f"⚠️ Ошибка тестирования Bitget: {e}")
            return False
    
    async def _initialize_risk_manager(self) -> bool:
        """Инициализация менеджера рисков"""
        try:
            self.logger.info("🛡️ Инициализация менеджера рисков...")
            
            # Получаем начальный капитал
            balance = await self.exchange.get_balance()
            initial_capital = balance.wallet_balance if balance else 10000.0
            
            # Создаем менеджер рисков
            self.risk_manager = RiskManager(
                exchange=self.exchange,
                initial_capital=initial_capital
            )
            
            # Инициализируем
            if not await self.risk_manager.initialize():
                self.logger.error("Не удалось инициализировать менеджер рисков")
                return False
            
            self.logger.info("✅ Менеджер рисков инициализирован")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации менеджера рисков: {e}")
            return False
    
    async def _initialize_position_manager(self) -> bool:
        """Инициализация менеджера позиций"""
        try:
            self.logger.info("📊 Инициализация менеджера позиций...")
            
            # Создаем менеджер позиций
            self.position_manager = PositionManager(exchange=self.exchange)
            
            # Синхронизируем с биржей
            await self.position_manager.update_positions()
            
            # Получаем сводку по позициям
            positions_summary = self.position_manager.get_positions_summary()
            self.logger.info(
                f"📈 Найдено {positions_summary['total_positions']} активных позиций, "
                f"общий P&L: {positions_summary['total_pnl_percent']:+.2f}%"
            )
            
            self.logger.info("✅ Менеджер позиций инициализирован")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации менеджера позиций: {e}")
            return False
    
    async def _initialize_trading_engine(self) -> bool:
        """Инициализация торгового движка"""
        try:
            self.logger.info("⚙️ Инициализация торгового движка...")
            
            # Получаем режим работы из конфигурации
            strategies_config = config_loader.get_config("strategies")
            self.mode = strategies_config.get("active_strategies", {}).get("mode", "signals")
            
            # Создаем торговый движок, передавая существующий risk_manager
            self.trading_engine = TradingEngine(
                exchange=self.exchange,
                risk_manager=self.risk_manager,
                mode=self.mode
            )
            
            # Инициализируем
            if not await self.trading_engine.initialize():
                self.logger.error("Не удалось инициализировать торговый движок")
                return False
            
            self.logger.info(f"✅ Торговый движок инициализирован в режиме: {self.mode}")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации торгового движка: {e}")
            return False
    
    async def _initialize_telegram_bot(self) -> bool:
        """Инициализация Telegram бота (опционально)"""
        try:
            self.logger.info("📱 Инициализация Telegram бота...")
            
            # Проверяем telegram.yaml
            telegram_config = config_loader.get_config("telegram")
            bot_config = telegram_config.get("bot", {})
            
            token = bot_config.get("token")
            chat_id = bot_config.get("chat_id")
            enabled = bot_config.get("enabled", True)
            
            if token and chat_id and enabled:
                self.logger.info(f"📱 Найдена конфигурация Telegram: token={token[:10]}..., chat_id={chat_id}")
                
                self.telegram_bot = TelegramBot(
                    token=token,
                    chat_id=chat_id,
                    trading_bot_instance=self
                )
                
                if await self.telegram_bot.initialize():
                    self.logger.info("✅ Telegram бот инициализирован (telegram.yaml)")
                    return True
                else:
                    self.logger.error("❌ Не удалось инициализировать Telegram бота")
                    return True  # Не критичная ошибка
            
            # Fallback: пытаемся загрузить из старых конфигов
            elif not token or not chat_id:
                try:
                    from telegram_cfg import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
                    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                        self.telegram_bot = TelegramBot(
                            token=TELEGRAM_TOKEN,
                            chat_id=TELEGRAM_CHAT_ID,
                            trading_bot_instance=self
                        )
                        
                        if await self.telegram_bot.initialize():
                            self.logger.info("✅ Telegram бот инициализирован (legacy config)")
                            return True
                        else:
                            self.logger.error("❌ Не удалось инициализировать Telegram бота (legacy)")
                            return True
                    else:
                        self.logger.info("⚠️ Telegram токен или chat_id отсутствуют в telegram_cfg.py")
                        return True
                except ImportError:
                    self.logger.info("⚠️ Файл telegram_cfg.py не найден и telegram.yaml неполный")
                    return True
            else:
                self.logger.info("⚠️ Telegram бот отключен в конфигурации")
                return True
                
        except Exception as e:
            self.logger.warning(f"Telegram бот не запущен: {e}")
            return True  # Не критичная ошибка
    
    async def test_all_exchanges(self):
        """Тестирование всех подключений"""
        self.logger.info("🔍 Тестирование всех биржевых подключений...")
        results = await test_all_exchanges()
        
        for exchange_name, result in results.items():
            status = "✅" if result.get("overall") else "❌"
            self.logger.info(f"{status} {exchange_name}: {result}")
    
    async def run(self):
        """Основной цикл работы системы"""
        try:
            self.logger.info("🎯 Запуск основного цикла торгового бота...")
            
            # Отправляем уведомление о запуске
            if self.telegram_bot:
                await self.telegram_bot.send_startup_message()
            
            # Запускаем основные компоненты параллельно
            tasks = []
            
            # 1. Торговый движок
            trading_task = asyncio.create_task(self.trading_engine.run())
            tasks.append(trading_task)
            
            # 2. Периодическое обновление позиций
            positions_task = asyncio.create_task(self._position_update_loop())
            tasks.append(positions_task)
            
            # 3. Мониторинг рисков (УЛУЧШЕННАЯ ВЕРСИЯ)
            risk_task = asyncio.create_task(self._risk_monitoring_loop())
            tasks.append(risk_task)
            
            # 4. Telegram бот (если настроен)
            if self.telegram_bot:
                telegram_task = asyncio.create_task(self.telegram_bot.run())
                tasks.append(telegram_task)
            
            # 5. Основной цикл мониторинга
            monitor_task = asyncio.create_task(self._main_monitoring_loop())
            tasks.append(monitor_task)
            
            # Ожидаем завершения любой из задач
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            self.logger.error(f"Ошибка в основном цикле: {e}")
        finally:
            await self.shutdown()
    
    async def _position_update_loop(self):
        """Цикл обновления позиций"""
        while self.is_running and not self.shutdown_requested:
            try:
                await self.position_manager.update_positions()
                await asyncio.sleep(30)  # Обновляем каждые 30 секунд
            except Exception as e:
                self.logger.error(f"Ошибка обновления позиций: {e}")
                await asyncio.sleep(60)
    
    async def _risk_monitoring_loop(self):
        """Цикл мониторинга рисков (УЛУЧШЕННАЯ ВЕРСИЯ)"""
        try:
            while not self.shutdown_requested and self.is_running:
                try:
                    # Получаем статус рисков через торговый движок
                    risk_status = await self.trading_engine.get_risk_status()
                    
                    # Проверяем критические состояния
                    if not risk_status.get("trading_allowed", True):
                        halt_reason = risk_status.get("halt_reason", "Unknown")
                        self.logger.warning(f"⚠️ Торговля заблокирована: {halt_reason}")
                        
                        # Отправляем уведомление в Telegram
                        if self.telegram_bot:
                            await self._send_risk_alert(halt_reason)
                    
                    # Проверяем использование лимитов
                    await self._check_risk_usage_warnings(risk_status)
                    
                    await asyncio.sleep(300)  # Проверяем каждые 5 минут
                    
                except Exception as e:
                    self.logger.error(f"Ошибка мониторинга рисков: {e}")
                    await asyncio.sleep(60)
                    
        except asyncio.CancelledError:
            self.logger.info("Цикл мониторинга рисков остановлен")
    
    async def _check_risk_usage_warnings(self, risk_status: Dict[str, Any]):
        """Проверка использования лимитов и отправка предупреждений"""
        try:
            if not self.telegram_bot:
                return
            
            daily = risk_status.get("daily", {})
            weekly = risk_status.get("weekly", {})
            positions = risk_status.get("positions", {})
            
            warnings = []
            
            # Проверяем дневные лимиты (предупреждение при 80%+)
            if daily.get("loss_usage_pct", 0) >= 80:
                warnings.append(f"⚠️ Дневной лимит убытка: {daily.get('loss_usage_pct', 0):.0f}%")
            
            if daily.get("drawdown_usage_pct", 0) >= 80:
                warnings.append(f"⚠️ Дневная просадка: {daily.get('drawdown_usage_pct', 0):.0f}%")
            
            if daily.get("trades_usage_pct", 0) >= 80:
                warnings.append(f"⚠️ Дневные сделки: {daily.get('trades_usage_pct', 0):.0f}%")
            
            # Проверяем недельные лимиты (предупреждение при 80%+)
            if weekly.get("loss_usage_pct", 0) >= 80:
                warnings.append(f"⚠️ Недельный лимит убытка: {weekly.get('loss_usage_pct', 0):.0f}%")
            
            if weekly.get("drawdown_usage_pct", 0) >= 80:
                warnings.append(f"⚠️ Недельная просадка: {weekly.get('drawdown_usage_pct', 0):.0f}%")
            
            # Проверяем позиции (предупреждение при 90%+)
            if positions.get("count_usage_pct", 0) >= 90:
                warnings.append(f"⚠️ Количество позиций: {positions.get('count_usage_pct', 0):.0f}%")
            
            # Отправляем предупреждения, если есть
            if warnings:
                await self._send_risk_warnings(warnings)
                
        except Exception as e:
            self.logger.error(f"Ошибка проверки предупреждений о рисках: {e}")
    
    async def _send_risk_alert(self, halt_reason: str):
        """Отправка критического уведомления о блокировке торговли"""
        try:
            if not self.telegram_bot:
                return
            
            message = (
                "🚨 *КРИТИЧЕСКОЕ УВЕДОМЛЕНИЕ*\n\n"
                "🛑 **ТОРГОВЛЯ ЗАБЛОКИРОВАНА**\n\n"
                f"**Причина:** {halt_reason}\n\n"
                "Используйте `/risk` для проверки статуса или "
                "`/risk_reset` для сброса счетчиков (только админы)"
            )
            
            await self.telegram_bot.application.bot.send_message(
                chat_id=self.telegram_bot.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки критического уведомления: {e}")
    
    async def _send_risk_warnings(self, warnings: List[str]):
        """Отправка предупреждений о приближении к лимитам"""
        try:
            if not self.telegram_bot or not warnings:
                return
            
            # Чтобы не спамить, ограничиваем частоту предупреждений
            current_time = time.time()
            if current_time - self._last_warning_time < 3600:  # 1 час
                return
            
            self._last_warning_time = current_time
            
            message = (
                "⚠️ *ПРЕДУПРЕЖДЕНИЕ О РИСКАХ*\n\n"
                "Приближение к лимитам:\n\n"
            )
            
            for warning in warnings:
                message += f"• {warning}\n"
            
            message += (
                "\nИспользуйте `/risk` для детального просмотра или "
                "`/risk_set` для изменения лимитов"
            )
            
            await self.telegram_bot.application.bot.send_message(
                chat_id=self.telegram_bot.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки предупреждений о рисках: {e}")
    
    async def _main_monitoring_loop(self):
        """Основной цикл мониторинга системы"""
        while self.is_running and not self.shutdown_requested:
            try:
                # Проверяем состояние системы
                await self._log_system_status()
                
                await asyncio.sleep(300)  # Каждые 5 минут
            except Exception as e:
                self.logger.error(f"Ошибка мониторинга системы: {e}")
                await asyncio.sleep(600)
    
    async def _log_system_status(self):
        """Логирование статуса системы с информацией о рисках"""
        try:
            # Основная информация о системе
            uptime = time.time() - self.start_time
            self.logger.info(
                f"📊 System Status: "
                f"Uptime: {uptime/3600:.1f}h, "
                f"Mode: {self.mode}, "
                f"Trading: {'✅' if self.trading_enabled else '🛑'}"
            )
            
            # Добавляем информацию о рисках
            if self.trading_engine:
                risk_status = await self.trading_engine.get_risk_status()
                
                if risk_status and "error" not in risk_status:
                    daily = risk_status.get("daily", {})
                    weekly = risk_status.get("weekly", {})
                    
                    self.logger.info(
                        f"📊 Risk Status: "
                        f"Daily P&L: {daily.get('pnl', 0):+.2f}, "
                        f"Weekly P&L: {weekly.get('pnl', 0):+.2f}, "
                        f"Trading: {'✅' if risk_status.get('trading_allowed', True) else '🛑'}"
                    )
                    
                    # Логируем использование лимитов
                    if daily.get("loss_usage_pct", 0) > 50 or weekly.get("loss_usage_pct", 0) > 50:
                        self.logger.warning(
                            f"⚠️ High risk usage: "
                            f"Daily loss: {daily.get('loss_usage_pct', 0):.0f}%, "
                            f"Weekly loss: {weekly.get('loss_usage_pct', 0):.0f}%"
                        )
                        
        except Exception as e:
            self.logger.error(f"Ошибка логирования статуса рисков: {e}")
    
    async def shutdown(self):
        """Корректное завершение работы с сохранением состояния рисков"""
        try:
            self.logger.info("🛑 Инициирование завершения работы...")
            
            # Устанавливаем флаги завершения
            self.shutdown_requested = True
            self.is_running = False
            self.trading_enabled = False
            
            # Сохраняем финальное состояние рисков
            if self.risk_manager and self.trading_engine:
                try:
                    final_status = await self.trading_engine.get_risk_status()
                    if final_status and "error" not in final_status:
                        daily_pnl = final_status.get("daily", {}).get("pnl", 0)
                        weekly_pnl = final_status.get("weekly", {}).get("pnl", 0)
                        
                        self.logger.info(
                            f"📊 Final Risk Status: "
                            f"Daily P&L: {daily_pnl:+.2f}, "
                            f"Weekly P&L: {weekly_pnl:+.2f}"
                        )
                except Exception as e:
                    self.logger.error(f"Ошибка сохранения финального состояния рисков: {e}")
            
            # Отправляем уведомление о завершении
            if self.telegram_bot:
                await self.telegram_bot.send_shutdown_message()
            
            # Закрываем торговый движок
            if self.trading_engine:
                await self.trading_engine.stop()
            
            # Закрываем биржевое соединение
            if self.exchange:
                await self.exchange.close()
            
            # Закрываем Telegram бота
            if self.telegram_bot:
                await self.telegram_bot.stop()
            
            # Ждем завершения задач
            await asyncio.sleep(2)
            
            self.logger.info("✅ Система корректно завершена")
            
        except Exception as e:
            self.logger.error(f"Ошибка при завершении: {e}")
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Получение статуса системы"""
        try:
            status = {
                "is_running": self.is_running,
                "mode": self.mode,
                "uptime_seconds": time.time() - self.start_time,
                "timestamp": time.time(),
                "exchange": "Bybit" if self.exchange else "N/A",
                "signals_count": self.signals_count,
                "positions_count": 0
            }
            
            # Статус биржи
            if self.exchange:
                try:
                    balance = await self.exchange.get_balance()
                    if balance:
                        status["balance"] = {
                            "wallet_balance": balance.wallet_balance,
                            "available_balance": balance.available_balance
                        }
                except Exception as e:
                    self.logger.debug(f"Не удалось получить баланс для статуса: {e}")
            
            # Статус позиций
            if self.position_manager:
                try:
                    positions_summary = self.position_manager.get_positions_summary()
                    status["positions_count"] = positions_summary.get("total_positions", 0)
                    status["positions"] = positions_summary
                except Exception as e:
                    self.logger.debug(f"Не удалось получить позиции для статуса: {e}")
            
            # Статус рисков
            if self.trading_engine:
                try:
                    risk_status = await self.trading_engine.get_risk_status()
                    status["risk_status"] = risk_status
                except Exception as e:
                    self.logger.debug(f"Не удалось получить статус рисков: {e}")
            
            return status
            
        except Exception as e:
            self.logger.error(f"Ошибка получения статуса: {e}")
            return {"error": str(e)}
    
    def set_mode(self, new_mode: str) -> bool:
        """Изменение режима работы"""
        try:
            if new_mode not in ["auto", "signals"]:
                return False
            
            old_mode = self.mode
            self.mode = new_mode
            
            if self.trading_engine:
                self.trading_engine.mode = new_mode
            
            self.logger.info(f"Режим изменен: {old_mode} → {new_mode}")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка изменения режима: {e}")
            return False
    
    # МЕТОДЫ ДЛЯ ИНТЕГРАЦИИ С TELEGRAM БОТОМ
    async def get_risk_status(self) -> Dict[str, Any]:
        """Получение статуса рисков для Telegram бота"""
        try:
            if self.trading_engine:
                return await self.trading_engine.get_risk_status()
            else:
                return {"error": "Trading engine not initialized"}
        except Exception as e:
            self.logger.error(f"Ошибка получения статуса рисков: {e}")
            return {"error": str(e)}
    
    async def set_risk_parameter(self, path: str, value: Any) -> Tuple[bool, str]:
        """Установка параметра риска"""
        try:
            if self.trading_engine:
                return await self.trading_engine.set_risk_parameter(path, value)
            else:
                return False, "Trading engine not initialized"
        except Exception as e:
            self.logger.error(f"Ошибка установки параметра риска: {e}")
            return False, f"Error: {e}"
    
    async def reset_daily_risk_counters(self) -> bool:
        """Сброс дневных счетчиков рисков"""
        try:
            if self.trading_engine:
                return await self.trading_engine.reset_daily_risk_counters()
            else:
                return False
        except Exception as e:
            self.logger.error(f"Ошибка сброса дневных счетчиков: {e}")
            return False
    
    async def reset_weekly_risk_counters(self) -> bool:
        """Сброс недельных счетчиков рисков"""
        try:
            if self.trading_engine:
                return await self.trading_engine.reset_weekly_risk_counters()
            else:
                return False
        except Exception as e:
            self.logger.error(f"Ошибка сброса недельных счетчиков: {e}")
            return False


async def main():
    """Главная функция"""
    # 1. Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    logger = logging.getLogger("Main")
    logger.info("🚀 Запуск Vortex Trading Bot v2.1...")
    
    bot = None
    try:
        # 2. Загрузка конфигураций
        try:
            config_loader.load_all_configs()
            logger.info("✅ Конфигурации загружены")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки конфигураций: {e}")
            return 1
        
        # 3. Создание и инициализация бота
        bot = VortexTradingBot()
        
        if not await bot.initialize():
            logger.error("❌ Не удалось инициализировать бота")
            return 1
        
        # 4. Запуск основного цикла
        logger.info("▶️ Запуск основного цикла...")
        await bot.run()
        
        logger.info("✅ Завершение работы")
        return 0
        
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал остановки (Ctrl+C)")
        return 0
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        traceback.print_exc()
        return 1
    finally:
        if bot:
            await bot.shutdown()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n🛑 Принудительное завершение")
        sys.exit(130)
    except Exception as e:
        print(f"💥 Фатальная ошибка: {e}")
        sys.exit(1)