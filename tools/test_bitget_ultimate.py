#!/usr/bin/env python3
"""
Окончательный тест BitgetAdapter с правильными символами
"""

import os
import sys
import asyncio
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.bitget_adapter import BitgetAdapter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("BitgetUltimateTest")


class BitgetUltimateTester:
    def __init__(self):
        self.adapter = None
        self.test_results = {}
        # Используем обычные символы, адаптер сам их преобразует
        self.test_symbol = "BTCUSDT"
        
    async def setup(self) -> bool:
        """Настройка адаптера"""
        try:
            api_key = os.getenv("BITGET_TESTNET_API_KEY")
            api_secret = os.getenv("BITGET_TESTNET_API_SECRET")
            api_passphrase = os.getenv("BITGET_TESTNET_API_PASSPHRASE")
            
            if not all([api_key, api_secret, api_passphrase]):
                logger.error("❌ API ключи не установлены")
                return False
            
            logger.info("=" * 60)
            logger.info("🚀 ОКОНЧАТЕЛЬНОЕ ТЕСТИРОВАНИЕ BITGET ADAPTER")
            logger.info("=" * 60)
            logger.info("Используем символ BTCUSDT для всех тестов")
            logger.info("Адаптер автоматически преобразует:")
            logger.info("  - BTCUSDT -> SBTCSUSDT для маркет данных")
            logger.info("  - BTCUSDT -> BTCUSDT для торговых операций")
            
            self.adapter = BitgetAdapter(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                testnet=True
            )
            
            success = await self.adapter.initialize()
            if success:
                logger.info("✅ Адаптер инициализирован")
                self.test_results["initialization"] = True
            else:
                logger.error("❌ Ошибка инициализации")
                self.test_results["initialization"] = False
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка setup: {e}")
            return False
    
    async def test_balance(self):
        """Тест баланса"""
        logger.info("\n📋 Тест 1: Баланс")
        try:
            balance = await self.adapter.get_balance("USDT")
            if balance:
                logger.info(f"✅ Баланс USDT: {balance.available_balance:.2f}")
                self.test_results["balance"] = True
            else:
                logger.error("❌ Не удалось получить баланс")
                self.test_results["balance"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["balance"] = False
    
    async def test_ticker(self):
        """Тест тикера с обычным символом"""
        logger.info(f"\n📋 Тест 2: Тикер {self.test_symbol}")
        try:
            ticker = await self.adapter.get_ticker(self.test_symbol)
            if ticker and ticker.last_price > 0:
                logger.info(f"✅ Цена {self.test_symbol}: ${ticker.last_price:,.2f}")
                self.test_results["ticker"] = True
            else:
                logger.error(f"❌ Не удалось получить тикер")
                self.test_results["ticker"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["ticker"] = False
    
    async def test_klines(self):
        """Тест свечей"""
        logger.info(f"\n📋 Тест 3: Свечи {self.test_symbol}")
        try:
            klines = await self.adapter.get_klines(self.test_symbol, interval="1H", limit=5)
            if klines:
                logger.info(f"✅ Получено {len(klines)} свечей")
                latest = klines[-1]
                logger.info(f"   Последняя: Open=${latest.open:,.2f}, Close=${latest.close:,.2f}")
                self.test_results["klines"] = True
            else:
                logger.error("❌ Не удалось получить свечи")
                self.test_results["klines"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["klines"] = False
    
    async def test_leverage(self):
        """Тест установки плеча"""
        logger.info(f"\n📋 Тест 4: Установка плеча для {self.test_symbol}")
        try:
            success = await self.adapter.set_leverage(self.test_symbol, 10)
            if success:
                logger.info("✅ Плечо установлено на 10x")
                self.test_results["leverage"] = True
            else:
                logger.error("❌ Не удалось установить плечо")
                self.test_results["leverage"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["leverage"] = False
    
    async def test_margin_mode(self):
        """Тест режима маржи"""
        logger.info(f"\n📋 Тест 5: Режим маржи для {self.test_symbol}")
        try:
            success = await self.adapter.set_margin_mode(self.test_symbol, "CROSS")
            if success:
                logger.info("✅ Режим маржи установлен на CROSS")
                self.test_results["margin_mode"] = True
            else:
                logger.error("❌ Не удалось установить режим маржи")
                self.test_results["margin_mode"] = False
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["margin_mode"] = False
    
    async def test_order(self):
        """Тест размещения и отмены ордера"""
        logger.info(f"\n📋 Тест 6: Ордер {self.test_symbol}")
        try:
            # Получаем цену
            ticker = await self.adapter.get_ticker(self.test_symbol)
            if not ticker:
                logger.error("❌ Не удалось получить цену")
                self.test_results["order"] = False
                return
            
            # Размещаем лимитный ордер далеко от рынка
            test_price = ticker.last_price * 0.5  # 50% ниже рынка
            test_size = 0.001
            
            logger.info(f"   Размещаем: BUY {test_size} @ ${test_price:,.2f}")
            
            order = await self.adapter.place_order(
                symbol=self.test_symbol,
                side="buy",
                order_type="limit",
                quantity=test_size,
                price=test_price
            )
            
            if order:
                logger.info(f"✅ Ордер размещен: {order.order_id}")
                
                # Отменяем
                await asyncio.sleep(1)
                cancelled = await self.adapter.cancel_order(
                    symbol=self.test_symbol,
                    order_id=order.order_id
                )
                
                if cancelled:
                    logger.info("✅ Ордер отменен")
                    self.test_results["order"] = True
                else:
                    logger.error("❌ Не удалось отменить")
                    self.test_results["order"] = False
            else:
                logger.error("❌ Не удалось разместить ордер")
                self.test_results["order"] = False
                
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["order"] = False
    
    async def test_positions(self):
        """Тест позиций"""
        logger.info("\n📋 Тест 7: Позиции")
        try:
            positions = await self.adapter.get_positions()
            logger.info(f"✅ Позиций: {len(positions)}")
            self.test_results["positions"] = True
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            self.test_results["positions"] = False
    
    async def print_summary(self):
        """Итоги"""
        logger.info("\n" + "=" * 60)
        logger.info("📊 ИТОГИ")
        logger.info("=" * 60)
        
        passed = sum(1 for v in self.test_results.values() if v)
        total = len(self.test_results)
        
        for test_name, result in self.test_results.items():
            status = "✅" if result else "❌"
            logger.info(f"{status} {test_name}")
        
        logger.info("-" * 60)
        percentage = (passed / total * 100) if total > 0 else 0
        logger.info(f"Результат: {passed}/{total} ({percentage:.0f}%)")
        
        if passed == total:
            logger.info("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
            logger.info("✅ BitgetAdapter полностью готов к использованию")
        elif passed >= total * 0.8:
            logger.info("✅ Адаптер работоспособен")
        else:
            logger.info("❌ Требуется доработка")
    
    async def cleanup(self):
        """Очистка"""
        if self.adapter:
            await self.adapter.close()
            logger.info("🔒 Закрыто")
    
    async def run(self):
        """Запуск тестов"""
        try:
            if not await self.setup():
                return
            
            await self.test_balance()
            await self.test_ticker()
            await self.test_klines()
            await self.test_leverage()
            await self.test_margin_mode()
            await self.test_order()
            await self.test_positions()
            
            await self.print_summary()
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
        finally:
            await self.cleanup()


if __name__ == "__main__":
    asyncio.run(BitgetUltimateTester().run())
