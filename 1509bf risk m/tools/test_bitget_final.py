#!/usr/bin/env python3
"""
Финальный тестовый скрипт для BitgetAdapter
С учетом всех особенностей demo режима
"""

import os
import sys
import asyncio
import logging
from datetime import datetime

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.bitget_adapter import BitgetAdapter
from exchanges.exchange_factory import ExchangeFactory, ExchangeType


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("BitgetFinalTest")


class BitgetFinalTester:
    """Финальный тестер BitgetAdapter"""
    
    def __init__(self):
        self.adapter = None
        self.test_results = {}
        # Правильные символы для demo (с префиксом S)
        self.demo_symbols = ["SBTCSUSDT", "SETHSUSDT", "SXRPSUSDT"]
        self.test_symbol = "SBTCSUSDT"  # Основной символ для тестов
        
    async def setup(self) -> bool:
        """Настройка и создание адаптера"""
        try:
            api_key = os.getenv("BITGET_TESTNET_API_KEY")
            api_secret = os.getenv("BITGET_TESTNET_API_SECRET")
            api_passphrase = os.getenv("BITGET_TESTNET_API_PASSPHRASE")
            
            if not all([api_key, api_secret, api_passphrase]):
                logger.error("❌ Не найдены учетные данные Bitget")
                return False
            
            logger.info("=" * 60)
            logger.info("🚀 ФИНАЛЬНОЕ ТЕСТИРОВАНИЕ BITGET ADAPTER")
            logger.info("=" * 60)
            
            # Создание адаптера
            self.adapter = BitgetAdapter(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                testnet=True,
                recv_window=10000
            )
            
            # Инициализация
            success = await self.adapter.initialize()
            if success:
                logger.info("✅ Адаптер инициализирован")
                self.test_results["initialization"] = True
            else:
                logger.error("❌ Не удалось инициализировать адаптер")
                self.test_results["initialization"] = False
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка в setup: {e}")
            return False
    
    async def test_server_time(self):
        """Тест получения времени сервера"""
        logger.info("\n📋 Тест 1: Время сервера")
        try:
            server_time = await self.adapter.get_server_time()
            if server_time > 0:
                dt = datetime.fromtimestamp(server_time / 1000)
                logger.info(f"✅ Время сервера: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                self.test_results["server_time"] = True
            else:
                logger.error("❌ Некорректное время сервера")
                self.test_results["server_time"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["server_time"] = False
    
    async def test_balance(self):
        """Тест получения баланса"""
        logger.info("\n📋 Тест 2: Баланс DEMO счета")
        try:
            balance = await self.adapter.get_balance("USDT")
            if balance:
                logger.info(f"✅ Баланс USDT:")
                logger.info(f"   - Доступно: {balance.available_balance:.4f}")
                logger.info(f"   - Используется: {balance.used_balance:.4f}")
                logger.info(f"   - Всего: {balance.wallet_balance:.4f}")
                logger.info(f"   - Нереализованный P&L: {balance.unrealized_pnl:.4f}")
                self.test_results["balance"] = True
            else:
                logger.error("❌ Не удалось получить баланс")
                self.test_results["balance"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["balance"] = False
    
    async def test_instruments(self):
        """Тест получения инструментов"""
        logger.info("\n📋 Тест 3: Список инструментов")
        try:
            instruments = await self.adapter.get_all_instruments()
            if instruments:
                logger.info(f"✅ Получено инструментов: {len(instruments)}")
                for i, inst in enumerate(instruments[:5]):
                    logger.info(f"   {i+1}. {inst.symbol} - Плечо: {inst.leverage_filter['min']}-{inst.leverage_filter['max']}")
                self.test_results["instruments"] = True
            else:
                logger.error("❌ Список инструментов пуст")
                self.test_results["instruments"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["instruments"] = False
    
    async def test_ticker(self):
        """Тест получения тикера"""
        logger.info(f"\n📋 Тест 4: Тикер {self.test_symbol}")
        try:
            ticker = await self.adapter.get_ticker(self.test_symbol)
            if ticker:
                logger.info(f"✅ Тикер {self.test_symbol}:")
                logger.info(f"   - Последняя цена: ${ticker.last_price:,.2f}")
                logger.info(f"   - Bid: ${ticker.bid_price:,.2f}")
                logger.info(f"   - Ask: ${ticker.ask_price:,.2f}")
                logger.info(f"   - Объем 24ч: {ticker.volume_24h:,.2f}")
                self.test_results["ticker"] = True
            else:
                logger.error(f"❌ Не удалось получить тикер для {self.test_symbol}")
                self.test_results["ticker"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["ticker"] = False
    
    async def test_klines(self):
        """Тест получения свечей"""
        logger.info(f"\n📋 Тест 5: Свечи {self.test_symbol}")
        try:
            # Используем правильный интервал с заглавной H
            klines = await self.adapter.get_klines(self.test_symbol, interval="1H", limit=10)
            if klines:
                logger.info(f"✅ Получено свечей: {len(klines)}")
                if klines:
                    latest = klines[-1]
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
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["klines"] = False
    
    async def test_positions(self):
        """Тест получения позиций"""
        logger.info("\n📋 Тест 6: Открытые позиции")
        try:
            positions = await self.adapter.get_positions()
            if positions:
                logger.info(f"✅ Открытых позиций: {len(positions)}")
                for pos in positions:
                    logger.info(f"   - {pos.symbol}: {pos.side} {pos.size} @ ${pos.entry_price:,.2f}")
            else:
                logger.info("✅ Нет открытых позиций (нормально для чистого demo)")
            self.test_results["positions"] = True
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["positions"] = False
    
    async def test_order_placement(self):
        """Тест размещения и отмены ордера"""
        logger.info(f"\n📋 Тест 7: Размещение ордера на {self.test_symbol}")
        try:
            # Получаем текущую цену
            ticker = await self.adapter.get_ticker(self.test_symbol)
            if not ticker:
                logger.error(f"❌ Не удалось получить цену для {self.test_symbol}")
                self.test_results["order_placement"] = False
                return
            
            # Размещаем ордер далеко от рынка
            test_price = ticker.last_price * 0.8  # 20% ниже рынка
            test_size = 0.001  # Минимальный размер
            
            logger.info(f"   Размещаем лимитный ордер: BUY {test_size} @ ${test_price:,.2f}")
            
            order = await self.adapter.place_order(
                symbol=self.test_symbol,
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
                    symbol=self.test_symbol,
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
                logger.info("   - Неправильный символ для demo")
                logger.info("   - Недостаточно средств")
                logger.info("   - Неверные параметры ордера")
                self.test_results["order_place"] = False
                self.test_results["order_cancel"] = False
                
        except Exception as e:
            logger.error(f"❌ Ошибка теста ордеров: {e}")
            self.test_results["order_placement"] = False
    
    async def test_leverage(self):
        """Тест установки плеча"""
        logger.info(f"\n📋 Тест 8: Установка плеча для {self.test_symbol}")
        try:
            success = await self.adapter.set_leverage(self.test_symbol, 10)
            if success:
                logger.info("✅ Плечо установлено на 10x")
                self.test_results["leverage"] = True
            else:
                logger.warning("⚠️ Не удалось установить плечо")
                self.test_results["leverage"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["leverage"] = False
    
    async def test_margin_mode(self):
        """Тест установки режима маржи"""
        logger.info(f"\n📋 Тест 9: Установка режима маржи для {self.test_symbol}")
        try:
            success = await self.adapter.set_margin_mode(self.test_symbol, "CROSS")
            if success:
                logger.info("✅ Режим маржи установлен на CROSS")
                self.test_results["margin_mode"] = True
            else:
                logger.warning("⚠️ Не удалось установить режим маржи")
                self.test_results["margin_mode"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["margin_mode"] = False
    
    async def print_summary(self):
        """Вывод итогов"""
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
            logger.info("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
            logger.info("✅ BitgetAdapter полностью готов к использованию")
        elif passed >= total * 0.8:
            logger.info("⚠️ Большинство тестов пройдено")
            logger.info("   Проверьте failed тесты")
        else:
            logger.info("❌ Требуется доработка")
        
        # Специальные рекомендации для demo
        logger.info("\n📝 ВАЖНО ДЛЯ DEMO РЕЖИМА:")
        logger.info("1. Используйте символы с префиксом S (SBTCSUSDT, SETHSUSDT)")
        logger.info("2. productType должен быть 'susdt-futures'")
        logger.info("3. marginCoin должен быть 'USDT'")
        logger.info("4. Интервалы свечей с заглавными буквами (1H, не 1h)")
    
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
            await self.test_margin_mode()
            
            # Итоги
            await self.print_summary()
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
        finally:
            await self.cleanup()


async def main():
    """Главная функция"""
    tester = BitgetFinalTester()
    await tester.run()


if __name__ == "__main__":
    asyncio.run(main())
