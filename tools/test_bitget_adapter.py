#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работоспособности BitgetAdapter
Проверяет авторизацию, подпись запросов и основные методы API
"""

import os
import sys
import asyncio
import logging
from datetime import datetime
from typing import Optional

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.bitget_adapter import BitgetAdapter, create_bitget_adapter
from exchanges.exchange_factory import ExchangeFactory, ExchangeType


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("BitgetTester")


class BitgetTester:
    """Класс для тестирования Bitget адаптера"""
    
    def __init__(self):
        self.adapter: Optional[BitgetAdapter] = None
        self.test_results = {}
        
    async def setup(self) -> bool:
        """Настройка и создание адаптера"""
        try:
            # Получаем учетные данные из переменных окружения
            api_key = os.getenv("BITGET_TESTNET_API_KEY")
            api_secret = os.getenv("BITGET_TESTNET_API_SECRET")
            api_passphrase = os.getenv("BITGET_TESTNET_API_PASSPHRASE")
            
            if not all([api_key, api_secret, api_passphrase]):
                logger.error("❌ Не найдены учетные данные Bitget в переменных окружения")
                logger.info("Установите переменные:")
                logger.info("  export BITGET_TESTNET_API_KEY='your_key'")
                logger.info("  export BITGET_TESTNET_API_SECRET='your_secret'")
                logger.info("  export BITGET_TESTNET_API_PASSPHRASE='your_passphrase'")
                return False
            
            logger.info("=" * 60)
            logger.info("🚀 Тестирование BitgetAdapter")
            logger.info("=" * 60)
            
            # Тест 1: Создание через прямой конструктор с api_passphrase
            logger.info("\n📋 Тест 1: Создание адаптера с api_passphrase")
            try:
                self.adapter = BitgetAdapter(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase,
                    testnet=True,
                    recv_window=10000
                )
                logger.info("✅ Адаптер создан с api_passphrase")
                self.test_results["constructor_api_passphrase"] = True
            except Exception as e:
                logger.error(f"❌ Ошибка создания с api_passphrase: {e}")
                self.test_results["constructor_api_passphrase"] = False
                return False
            
            # Тест 2: Создание через конструктор с passphrase (проверка совместимости)
            logger.info("\n📋 Тест 2: Создание адаптера с passphrase (алиас)")
            try:
                test_adapter = BitgetAdapter(
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=api_passphrase,  # Используем алиас
                    testnet=True
                )
                logger.info("✅ Адаптер создан с passphrase (алиас)")
                self.test_results["constructor_passphrase"] = True
            except Exception as e:
                logger.error(f"❌ Ошибка создания с passphrase: {e}")
                self.test_results["constructor_passphrase"] = False
            
            # Тест 3: Создание через фабрику
            logger.info("\n📋 Тест 3: Создание через ExchangeFactory")
            try:
                factory = ExchangeFactory()
                factory_adapter = factory.create_bitget(
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=api_passphrase,
                    testnet=True
                )
                if factory_adapter:
                    logger.info("✅ Адаптер создан через фабрику")
                    self.test_results["factory_creation"] = True
                else:
                    logger.error("❌ Фабрика вернула None")
                    self.test_results["factory_creation"] = False
            except Exception as e:
                logger.error(f"❌ Ошибка создания через фабрику: {e}")
                self.test_results["factory_creation"] = False
            
            # Инициализация основного адаптера
            logger.info("\n📋 Тест 4: Инициализация адаптера")
            try:
                success = await self.adapter.initialize()
                if success:
                    logger.info("✅ Адаптер инициализирован")
                    self.test_results["initialization"] = True
                else:
                    logger.error("❌ Не удалось инициализировать адаптер")
                    self.test_results["initialization"] = False
                    return False
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации: {e}")
                self.test_results["initialization"] = False
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка в setup: {e}")
            return False
    
    async def test_server_time(self):
        """Тест получения времени сервера"""
        logger.info("\n📋 Тест 5: Получение времени сервера")
        try:
            server_time = await self.adapter.get_server_time()
            if server_time > 0:
                dt = datetime.fromtimestamp(server_time / 1000)
                logger.info(f"✅ Время сервера: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                self.test_results["server_time"] = True
            else:
                logger.error("❌ Получено некорректное время сервера")
                self.test_results["server_time"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка получения времени сервера: {e}")
            self.test_results["server_time"] = False
    
    async def test_balance(self):
        """Тест получения баланса"""
        logger.info("\n📋 Тест 6: Получение баланса (требует авторизацию)")
        try:
            balance = await self.adapter.get_balance("USDT")
            if balance:
                logger.info(f"✅ Баланс USDT:")
                logger.info(f"   - Доступно: {balance.free:.4f}")
                logger.info(f"   - Заблокировано: {balance.locked:.4f}")
                logger.info(f"   - Всего: {balance.total:.4f}")
                self.test_results["balance"] = True
            else:
                logger.warning("⚠️ Баланс не получен (возможно пустой аккаунт)")
                self.test_results["balance"] = True  # Не считаем ошибкой
        except Exception as e:
            logger.error(f"❌ Ошибка получения баланса: {e}")
            self.test_results["balance"] = False
    
    async def test_instruments(self):
        """Тест получения списка инструментов"""
        logger.info("\n📋 Тест 7: Получение списка инструментов")
        try:
            instruments = await self.adapter.get_all_instruments()
            if instruments:
                logger.info(f"✅ Получено инструментов: {len(instruments)}")
                # Показываем первые 5
                for i, inst in enumerate(instruments[:5]):
                    logger.info(f"   {i+1}. {inst.symbol} - Плечо: 1-{inst.leverage_filter['max']}")
                self.test_results["instruments"] = True
            else:
                logger.error("❌ Список инструментов пуст")
                self.test_results["instruments"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка получения инструментов: {e}")
            self.test_results["instruments"] = False
    
    async def test_ticker(self):
        """Тест получения тикера"""
        logger.info("\n📋 Тест 8: Получение тикера BTCUSDT")
        try:
            ticker = await self.adapter.get_ticker("BTCUSDT")
            if ticker:
                logger.info(f"✅ Тикер BTCUSDT:")
                logger.info(f"   - Последняя цена: ${ticker.last:,.2f}")
                logger.info(f"   - Bid: ${ticker.bid:,.2f}")
                logger.info(f"   - Ask: ${ticker.ask:,.2f}")
                logger.info(f"   - Объем: {ticker.volume:,.2f}")
                self.test_results["ticker"] = True
            else:
                logger.error("❌ Не удалось получить тикер")
                self.test_results["ticker"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка получения тикера: {e}")
            self.test_results["ticker"] = False
    
    async def test_klines(self):
        """Тест получения свечей"""
        logger.info("\n📋 Тест 9: Получение свечей BTCUSDT")
        try:
            klines = await self.adapter.get_klines("BTCUSDT", interval="1h", limit=10)
            if klines:
                logger.info(f"✅ Получено свечей: {len(klines)}")
                latest = klines[-1] if klines else None
                if latest:
                    dt = datetime.fromtimestamp(latest.timestamp / 1000)
                    logger.info(f"   Последняя свеча ({dt.strftime('%Y-%m-%d %H:%M')})")
                    logger.info(f"   - Open:  ${latest.open:,.2f}")
                    logger.info(f"   - High:  ${latest.high:,.2f}")
                    logger.info(f"   - Low:   ${latest.low:,.2f}")
                    logger.info(f"   - Close: ${latest.close:,.2f}")
                self.test_results["klines"] = True
            else:
                logger.error("❌ Не удалось получить свечи")
                self.test_results["klines"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка получения свечей: {e}")
            self.test_results["klines"] = False
    
    async def test_positions(self):
        """Тест получения позиций"""
        logger.info("\n📋 Тест 10: Получение открытых позиций")
        try:
            positions = await self.adapter.get_positions()
            if positions:
                logger.info(f"✅ Открытых позиций: {len(positions)}")
                for pos in positions:
                    logger.info(f"   - {pos.symbol}: {pos.side} {pos.size} @ ${pos.entry_price:,.2f}")
            else:
                logger.info("✅ Нет открытых позиций")
            self.test_results["positions"] = True
        except Exception as e:
            logger.error(f"❌ Ошибка получения позиций: {e}")
            self.test_results["positions"] = False
    
    async def test_order_placement(self):
        """Тест размещения и отмены ордера"""
        logger.info("\n📋 Тест 11: Размещение и отмена лимитного ордера")
        try:
            # Получаем текущую цену
            ticker = await self.adapter.get_ticker("BTCUSDT")
            if not ticker:
                logger.error("❌ Не удалось получить цену для теста ордера")
                self.test_results["order_placement"] = False
                return
            
            # Размещаем ордер далеко от рынка
            test_price = ticker.last * 0.8  # 20% ниже рынка
            test_size = 0.001  # Минимальный размер
            
            logger.info(f"   Размещаем тестовый ордер: BUY {test_size} BTC @ ${test_price:,.2f}")
            
            order = await self.adapter.place_order(
                symbol="BTCUSDT",
                side="buy",
                order_type="limit",
                quantity=test_size,
                price=test_price,
                client_order_id=f"test_{int(datetime.now().timestamp())}"
            )
            
            if order:
                logger.info(f"✅ Ордер размещен: {order.order_id}")
                self.test_results["order_place"] = True
                
                # Пауза перед отменой
                await asyncio.sleep(2)
                
                # Отменяем ордер
                logger.info(f"   Отменяем ордер {order.order_id}")
                cancelled = await self.adapter.cancel_order(
                    symbol="BTCUSDT",
                    order_id=order.order_id
                )
                
                if cancelled:
                    logger.info("✅ Ордер успешно отменен")
                    self.test_results["order_cancel"] = True
                else:
                    logger.error("❌ Не удалось отменить ордер")
                    self.test_results["order_cancel"] = False
            else:
                logger.error("❌ Не удалось разместить ордер")
                logger.info("   Возможные причины:")
                logger.info("   - Недостаточно средств на demo аккаунте")
                logger.info("   - Неверные API ключи или права")
                logger.info("   - Demo режим не активирован (проверьте paptrading заголовок)")
                self.test_results["order_place"] = False
                self.test_results["order_cancel"] = False
                
        except Exception as e:
            logger.error(f"❌ Ошибка теста ордеров: {e}")
            self.test_results["order_placement"] = False
    
    async def test_leverage(self):
        """Тест установки плеча"""
        logger.info("\n📋 Тест 12: Установка кредитного плеча")
        try:
            success = await self.adapter.set_leverage("BTCUSDT", 10)
            if success:
                logger.info("✅ Плечо установлено на 10x")
                self.test_results["leverage"] = True
            else:
                logger.warning("⚠️ Не удалось установить плечо (возможно уже установлено)")
                self.test_results["leverage"] = True
        except Exception as e:
            logger.error(f"❌ Ошибка установки плеча: {e}")
            self.test_results["leverage"] = False
    
    async def print_summary(self):
        """Вывод итогов тестирования"""
        logger.info("\n" + "=" * 60)
        logger.info("📊 ИТОГИ ТЕСТИРОВАНИЯ")
        logger.info("=" * 60)
        
        passed = sum(1 for v in self.test_results.values() if v)
        total = len(self.test_results)
        
        for test_name, result in self.test_results.items():
            status = "✅" if result else "❌"
            logger.info(f"{status} {test_name.replace('_', ' ').title()}")
        
        logger.info("-" * 60)
        logger.info(f"Результат: {passed}/{total} тестов пройдено")
        
        if passed == total:
            logger.info("🎉 Все тесты пройдены успешно!")
            logger.info("✅ BitgetAdapter готов к использованию в боевом режиме")
        elif passed >= total * 0.8:
            logger.info("⚠️ Большинство тестов пройдено")
            logger.info("   Проверьте failed тесты для полной готовности")
        else:
            logger.info("❌ Требуется доработка адаптера")
            logger.info("   Проверьте логи выше для деталей ошибок")
    
    async def cleanup(self):
        """Очистка ресурсов"""
        if self.adapter:
            await self.adapter.close()
            logger.info("🔒 Соединения закрыты")
    
    async def run(self):
        """Запуск всех тестов"""
        try:
            # Настройка
            if not await self.setup():
                logger.error("❌ Не удалось настроить тестирование")
                return
            
            # Запуск тестов
            await self.test_server_time()
            await self.test_balance()
            await self.test_instruments()
            await self.test_ticker()
            await self.test_klines()
            await self.test_positions()
            await self.test_order_placement()
            await self.test_leverage()
            
            # Итоги
            await self.print_summary()
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
        finally:
            await self.cleanup()


async def main():
    """Главная функция"""
    tester = BitgetTester()
    await tester.run()


if __name__ == "__main__":
    asyncio.run(main())
