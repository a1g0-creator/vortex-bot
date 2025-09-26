"""
Главная точка входа Vortex Trading Bot v2.1
Интеграция всех компонентов модульной архитектуры
"""

import asyncio
import logging
import signal
import sys
import time
from typing import Optional, Dict, Any
from datetime import datetime

# Основные компоненты системы
from core.trading_engine import TradingEngine
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from exchanges.exchange_factory import exchange_factory, create_bybit_adapter
from config.config_loader import config_loader, get_app_config, get_bybit_credentials
from telegram_bot.telegram_bot import TelegramBot


class VortexTradingBot:
    """
    Главный класс торгового бота - интегратор всех компонентов
    Заменяет старый SimpleTradingBot с модульной архитектурой
    """
    
    def __init__(self):
        self.logger = logging.getLogger("VortexTradingBot")
        
        # Основные компоненты
        self.exchange = None
        self.trading_engine = None
        self.position_manager = None
        self.risk_manager = None
        self.telegram_bot = None
        
        # Состояние системы
        self.is_running = False
        self.start_time = time.time()
        self.shutdown_requested = False
        
        # Конфигурация
        self.app_config = get_app_config()
        self.mode = "signals"  # По умолчанию режим сигналов
        
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
            
            # 1. Инициализация биржевого подключения
            if not await self._initialize_exchange():
                return False
            
            # 2. Инициализация менеджера рисков
            if not await self._initialize_risk_manager():
                return False
            
            # 3. Инициализация менеджера позиций
            if not await self._initialize_position_manager():
                return False
            
            # 4. Инициализация торгового движка
            if not await self._initialize_trading_engine():
                return False
            
            # 5. Инициализация Telegram бота (опционально)
            await self._initialize_telegram_bot()
            
            self.is_running = True
            self.logger.info("✅ Все компоненты успешно инициализированы")
            
            return True
            
        except Exception as e:
            self.logger.error(f"💥 Критическая ошибка инициализации: {e}")
            return False
    
    async def _initialize_exchange(self) -> bool:
        """Инициализация биржевого адаптера"""
        try:
            self.logger.info("📡 Инициализация биржевых подключений...")
        
            # Получаем список включенных бирж
            exchanges_config = config_loader.get_config("exchanges")
            enabled_exchanges = exchanges_config.get("exchanges", {}).get("enabled", ["bybit"])
        
            self.logger.info(f"🔍 Включенные биржи: {enabled_exchanges}")
        
            # Инициализируем Bybit (основная биржа)
            if "bybit" in enabled_exchanges:
                credentials = get_bybit_credentials(testnet=False)
                self.exchange = await create_bybit_adapter(
                    api_key=credentials["api_key"],
                    api_secret=credentials["api_secret"],
                    testnet=True,
                    recv_window=credentials.get("recv_window", 10000),
                    initialize=True
                )
            
                if self.exchange:
                    self.logger.info("✅ Bybit подключен")
                else:
                    self.logger.error("❌ Ошибка подключения к Bybit")
                    return False
        
            # Проверяем Bitget (дополнительная биржа)
            if "bitget" in enabled_exchanges:
                await self._test_bitget_connection()
        
            return True
        
        except Exception as e:
            self.logger.error(f"Ошибка инициализации бирж: {e}")
            return False

    async def _test_bitget_connection(self):
        """Тестирование подключения к Bitget"""
        try:
            from exchanges.exchange_factory import exchange_factory
        
            # Получаем Bitget ключи из конфигурации
            exchanges_config = config_loader.get_config("exchanges")
            bitget_config = exchanges_config.get("bitget", {})
        
            # ✅ ИСПРАВЛЕНО: проверяем testnet вместо mainnet
            testnet_enabled = bitget_config.get("testnet", {}).get("enabled", False)
            if testnet_enabled:
                credentials = bitget_config.get("api_credentials", {}).get("testnet", {})
            else:
                credentials = bitget_config.get("api_credentials", {}).get("mainnet", {})
        
            if not all([credentials.get("api_key"), credentials.get("api_secret"), credentials.get("passphrase")]):
                self.logger.warning("⚠️ Bitget API ключи не настроены")
                return
        
            self.logger.info(f"🔍 Тестируем Bitget: testnet={testnet_enabled}")
        
            # Создаем тестовое подключение
            bitget_adapter = exchange_factory.create_bitget(
                api_key=credentials["api_key"],
                api_secret=credentials["api_secret"],
                passphrase=credentials["passphrase"],
                testnet=testnet_enabled  # ✅ Используем настройку из конфига
            )
        
            if bitget_adapter:
                # Тестируем подключение
                test_result = await exchange_factory.test_connection(bitget_adapter)
                if test_result.get("overall", False):
                    self.logger.info("✅ Bitget подключение протестировано успешно")
                else:
                    self.logger.warning("⚠️ Bitget тест подключения не пройден")
                    self.logger.debug(f"Детали теста: {test_result}")
            
                await bitget_adapter.close()
            else:
                self.logger.warning("⚠️ Не удалось создать Bitget адаптер")
            
        except Exception as e:
            self.logger.warning(f"⚠️ Ошибка тестирования Bitget: {e}")
    
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
            
            # Создаем торговый движок
            self.trading_engine = TradingEngine(
                exchange=self.exchange,
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
        
            # ✅ ИСПРАВЛЕНО: Сначала проверяем telegram.yaml
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
        from exchanges.exchange_factory import exchange_factory, test_all_exchanges
    
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
            
            # 3. Мониторинг рисков
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
        """Цикл мониторинга рисков"""
        while self.is_running and not self.shutdown_requested:
            try:
                # Проверяем риски
                risk_status = await self.risk_manager.check_risks()
                
                # Отправляем алерты если нужно
                if risk_status.get("alerts"):
                    for alert in risk_status["alerts"]:
                        if self.telegram_bot:
                            await self.telegram_bot.send_risk_alert(
                                alert_type=alert.get("type", "warning"),
                                message_text=alert.get("message", "")
                            )
                
                await asyncio.sleep(60)  # Проверяем каждую минуту
            except Exception as e:
                self.logger.error(f"Ошибка мониторинга рисков: {e}")
                await asyncio.sleep(120)
    
    async def _main_monitoring_loop(self):
        """Основной цикл мониторинга системы"""
        while self.is_running and not self.shutdown_requested:
            try:
                # Проверяем состояние системы
                system_status = await self.get_system_status()
                
                # Логируем статус
                self.logger.debug(f"Система работает: {system_status}")
                
                await asyncio.sleep(300)  # Каждые 5 минут
            except Exception as e:
                self.logger.error(f"Ошибка мониторинга системы: {e}")
                await asyncio.sleep(600)
    
    async def shutdown(self):
        """Корректное завершение работы"""
        try:
            self.logger.info("🛑 Начинаем корректное завершение системы...")
            
            self.is_running = False
            
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
            
            self.logger.info("✅ Система корректно завершена")
            
        except Exception as e:
            self.logger.error(f"Ошибка при завершении: {e}")
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Получение статуса системы"""
        try:
            status = {
                "is_running": self.is_running,
                "mode": self.mode,
                "uptime": time.time() - self.start_time,
                "timestamp": time.time()
            }
            
            # Статус биржи
            if self.exchange:
                balance = await self.exchange.get_balance()
                if balance:
                    status["balance"] = {
                        "wallet_balance": balance.wallet_balance,
                        "available_balance": balance.available_balance
                    }
            
            # Статус позиций
            if self.position_manager:
                status["positions"] = self.position_manager.get_positions_summary()
            
            # Статус рисков
            if self.risk_manager:
                status["risk_status"] = self.risk_manager.get_risk_status()
            
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


async def main():
    """Главная функция"""
    # Настраиваем логирование
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Создаем и запускаем бота
    bot = VortexTradingBot()
    
    try:
        # Инициализируем
        if await bot.initialize():
            # Запускаем основной цикл
            await bot.run()
        else:
            logging.error("❌ Не удалось инициализировать систему")
            return 1
            
    except KeyboardInterrupt:
        logging.info("Получен сигнал прерывания")
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
        await bot.shutdown()
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n🛑 Принудительное завершение")
        sys.exit(130)
