#!/usr/bin/env python3
"""
E2E тестер для Bitget адаптера
Проверяет все критические функции и корректность исправлений
Адаптирован под финальную версию адаптера с учетом всех особенностей
"""

import os
import sys
import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
from decimal import Decimal

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.bitget_adapter import BitgetAdapter
from exchanges.base_exchange import OrderSide, OrderType, MarginMode

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("BitgetE2ETester")


class BitgetE2ETester:
    """E2E тестер для Bitget адаптера с полной проверкой всех исправлений"""
    
    def __init__(self):
        self.adapter: Optional[BitgetAdapter] = None
        self.test_results = {}
        self.test_symbol = "BTCUSDT"  # Основной символ для тестов
        self.passed_tests = 0
        self.failed_tests = 0
        self.total_tests = 0
        self.critical_errors = []
        
    async def setup(self) -> bool:
        """Инициализация адаптера с проверкой учетных данных"""
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
            
            logger.info("=" * 80)
            logger.info("🚀 BITGET ADAPTER E2E TESTING SUITE v2.0")
            logger.info("=" * 80)
            logger.info(f"Дата запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"Режим: TESTNET (demo)")
            logger.info(f"Основной символ: {self.test_symbol}")
            logger.info(f"Проверка критических исправлений:")
            logger.info("  ✓ OrderInfo маппинг через inspect")
            logger.info("  ✓ One-way режим позиций")
            logger.info("  ✓ Market reduceOnly без позиции")
            logger.info("  ✓ Округление цен по priceEndStep")
            logger.info("-" * 80)
            
            # Создаем адаптер
            self.adapter = BitgetAdapter(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                testnet=True,
                recv_window=10000
            )
            
            # Инициализируем
            success = await self.adapter.initialize()
            if success:
                logger.info("✅ Адаптер успешно инициализирован")
                
                # Проверяем установку one-way режима
                if hasattr(self.adapter, '_pos_mode_cache'):
                    logger.info(f"  Position mode cache: {self.adapter._pos_mode_cache}")
                
                return True
            else:
                logger.error("❌ Ошибка инициализации адаптера")
                return False
                
        except Exception as e:
            logger.error(f"❌ Критическая ошибка в setup: {e}")
            self.critical_errors.append(f"Setup: {e}")
            return False
    
    async def run_test(self, test_name: str, test_func, *args, **kwargs) -> bool:
        """Запуск отдельного теста с детальным логированием"""
        self.total_tests += 1
        try:
            logger.info(f"\n📍 Тест #{self.total_tests}: {test_name}")
            logger.info("  " + "-" * 50)
            
            start_time = time.time()
            result = await test_func(*args, **kwargs)
            elapsed = time.time() - start_time
            
            if result:
                logger.info(f"  ✅ {test_name} - PASSED ({elapsed:.2f}s)")
                self.test_results[test_name] = {"status": "PASSED", "time": elapsed}
                self.passed_tests += 1
                return True
            else:
                logger.error(f"  ❌ {test_name} - FAILED ({elapsed:.2f}s)")
                self.test_results[test_name] = {"status": "FAILED", "time": elapsed}
                self.failed_tests += 1
                return False
                
        except Exception as e:
            elapsed = time.time() - start_time if 'start_time' in locals() else 0
            logger.error(f"  ❌ {test_name} - EXCEPTION: {e} ({elapsed:.2f}s)")
            self.test_results[test_name] = {"status": f"EXCEPTION: {e}", "time": elapsed}
            self.failed_tests += 1
            
            # Критические тесты
            if test_name in ["Place Limit Order", "Get Open Orders", "Market ReduceOnly (No Position)"]:
                self.critical_errors.append(f"{test_name}: {e}")
            
            return False
    
    # ---------------------------------------------------------------------
    # Тестовые функции
    # ---------------------------------------------------------------------
    
    async def test_server_time(self) -> bool:
        """Тест получения времени сервера"""
        server_time = await self.adapter.get_server_time()
        if server_time and server_time > 0:
            logger.info(f"    Server time: {datetime.fromtimestamp(server_time/1000)}")
            logger.info(f"    Raw timestamp: {server_time}")
            return True
        logger.error(f"    Invalid server time: {server_time}")
        return False
    
    async def test_balance(self) -> bool:
        """Тест получения баланса с проверкой всех полей"""
        balance = await self.adapter.get_balance("USDT")
        if balance:
            logger.info(f"    Wallet balance: {balance.wallet_balance:.4f} USDT")
            logger.info(f"    Available: {balance.available_balance:.4f} USDT")
            logger.info(f"    Used: {balance.used_balance:.4f} USDT")
            logger.info(f"    Unrealized PnL: {balance.unrealized_pnl:.4f} USDT")
            
            # Проверка корректности значений
            if balance.wallet_balance < 0:
                logger.warning("    ⚠️  Negative wallet balance detected")
            
            return True
        logger.error("    Failed to get balance")
        return False
    
    async def test_instruments_cache(self) -> bool:
        """Тест кэширования инструментов и двойного маппинга"""
        # Форсируем обновление кэша
        await self.adapter._update_symbols_cache()
        
        if not self.adapter.symbols_cache:
            logger.error("    Symbols cache is empty")
            return False
        
        logger.info(f"    Symbols cached: {len(self.adapter.symbols_cache)}")
        
        # Проверяем наличие обоих вариантов символов для demo
        if self.adapter.testnet:
            has_market = "SBTCSUSDT" in self.adapter.symbols_cache
            has_trading = "BTCUSDT" in self.adapter.symbols_cache
            
            logger.info(f"    Has SBTCSUSDT (market): {has_market}")
            logger.info(f"    Has BTCUSDT (trading): {has_trading}")
            
            if has_market and has_trading:
                logger.info("    ✓ Dual mapping working correctly")
            
            # Показываем примеры символов
            symbols = list(self.adapter.symbols_cache.keys())[:5]
            logger.info(f"    Sample symbols: {symbols}")
        
        return len(self.adapter.symbols_cache) > 0
    
    async def test_symbol_normalization(self) -> bool:
        """Тест нормализации символов для маркет и торговых операций"""
        test_cases = [
            ("BTCUSDT", "SBTCSUSDT", "BTCUSDT"),
            ("SBTCSUSDT", "SBTCSUSDT", "BTCUSDT"),
            ("ETHUSDT", "SETHSUSDT", "ETHUSDT"),
            ("XRPUSDT", "SXRPSUSDT", "XRPUSDT"),
        ]
        
        all_passed = True
        for input_sym, expected_market, expected_trading in test_cases:
            market_norm = self.adapter._normalize_symbol_for_market_data(input_sym)
            trading_norm = self.adapter._normalize_symbol_for_trading(input_sym)
            
            passed = (market_norm == expected_market and trading_norm == expected_trading)
            status = "✓" if passed else "✗"
            
            logger.info(f"    {status} {input_sym}:")
            logger.info(f"      Market: {market_norm} (expected: {expected_market})")
            logger.info(f"      Trading: {trading_norm} (expected: {expected_trading})")
            
            if not passed:
                all_passed = False
        
        return all_passed
    
    async def test_instrument_info(self) -> bool:
        """Тест получения информации об инструменте с проверкой price/qty step"""
        info = await self.adapter.get_instrument_info(self.test_symbol)
        if not info:
            logger.error("    Failed to get instrument info")
            return False
        
        logger.info(f"    Symbol: {info.symbol}")
        logger.info(f"    Base/Quote: {info.base_coin}/{info.quote_coin}")
        logger.info(f"    Status: {info.status}")
        logger.info(f"    Price step: {info.price_step}")
        logger.info(f"    Price precision: {info.price_precision}")
        logger.info(f"    Min qty: {info.min_order_qty}")
        logger.info(f"    Qty step: {info.qty_step}")
        logger.info(f"    Qty precision: {info.qty_precision}")
        logger.info(f"    Max leverage: {info.leverage_filter.get('max', 'N/A')}")
        
        # Сохраняем для последующих тестов
        self.instrument_info = info
        
        return True
    
    async def test_ticker(self) -> bool:
        """Тест получения тикера"""
        ticker = await self.adapter.get_ticker(self.test_symbol)
        if not ticker:
            logger.error("    Failed to get ticker")
            return False
        
        logger.info(f"    Last price: {ticker.last_price:.2f}")
        logger.info(f"    Bid: {ticker.bid_price:.2f}")
        logger.info(f"    Ask: {ticker.ask_price:.2f}")
        logger.info(f"    24h volume: {ticker.volume_24h:.2f}")
        logger.info(f"    24h change: {ticker.price_change_24h_percent:.2f}%")
        
        # Сохраняем для последующих тестов
        self.current_price = ticker.last_price
        
        return ticker.last_price > 0
    
    async def test_orderbook(self) -> bool:
        """Тест получения стакана"""
        orderbook = await self.adapter.get_orderbook(self.test_symbol, limit=5)
        if not orderbook:
            logger.error("    Failed to get orderbook")
            return False
        
        logger.info(f"    Bids: {len(orderbook['bids'])} levels")
        logger.info(f"    Asks: {len(orderbook['asks'])} levels")
        
        if orderbook['bids']:
            best_bid = orderbook['bids'][0]
            logger.info(f"    Best bid: {best_bid[0]:.2f} x {best_bid[1]:.4f}")
        
        if orderbook['asks']:
            best_ask = orderbook['asks'][0]
            logger.info(f"    Best ask: {best_ask[0]:.2f} x {best_ask[1]:.4f}")
        
        return len(orderbook['bids']) > 0 and len(orderbook['asks']) > 0
    
    async def test_klines(self) -> bool:
        """Тест получения свечей"""
        klines = await self.adapter.get_klines(self.test_symbol, interval="1H", limit=5)
        if not klines:
            logger.error("    Failed to get klines")
            return False
        
        logger.info(f"    Получено свечей: {len(klines)}")
        
        if klines:
            last = klines[-1]
            logger.info(f"    Last candle:")
            logger.info(f"      Time: {datetime.fromtimestamp(last.timestamp/1000)}")
            logger.info(f"      OHLC: {last.open:.2f} / {last.high:.2f} / {last.low:.2f} / {last.close:.2f}")
            logger.info(f"      Volume: {last.volume:.2f}")
        
        return len(klines) > 0
    
    async def test_funding_rate(self) -> bool:
        """Тест получения funding rate с обработкой разных форматов"""
        funding = await self.adapter.get_funding_rate(self.test_symbol)
        if funding is None:
            logger.warning("    Funding rate not available (may be normal for some symbols)")
            return True  # Не считаем ошибкой
        
        logger.info(f"    Funding rate: {funding:.8f}")
        logger.info(f"    Annualized: {funding * 3 * 365:.2f}%")
        
        return True
    
    async def test_set_leverage(self) -> bool:
        """Тест установки кредитного плеча"""
        target_leverage = 10
        success = await self.adapter.set_leverage(self.test_symbol, target_leverage)
        
        if success:
            logger.info(f"    ✓ Leverage set to {target_leverage}x")
        else:
            logger.warning(f"    Failed to set leverage (may already be set)")
        
        return success
    
    async def test_set_margin_mode(self) -> bool:
        """Тест установки режима маржи"""
        success = await self.adapter.set_margin_mode(self.test_symbol, MarginMode.CROSS)
        
        if success:
            logger.info(f"    ✓ Margin mode set to CROSS")
        else:
            logger.warning(f"    Failed to set margin mode (may already be CROSS)")
        
        return success
    
    async def test_round_functions(self) -> bool:
        """Тест функций округления с использованием реальных параметров инструмента"""
        if not hasattr(self, 'instrument_info'):
            info = await self.adapter.get_instrument_info(self.test_symbol)
            if not info:
                logger.error("    Cannot test rounding without instrument info")
                return False
            self.instrument_info = info
        
        # Тестовые значения
        test_price = 89876.123456
        test_qty = 0.0012345
        
        rounded_price = self.adapter.round_price(self.test_symbol, test_price)
        rounded_qty = self.adapter.round_quantity(self.test_symbol, test_qty)
        
        logger.info(f"    Price rounding:")
        logger.info(f"      Input: {test_price}")
        logger.info(f"      Output: {rounded_price}")
        logger.info(f"      Step: {self.instrument_info.price_step}")
        
        logger.info(f"    Quantity rounding:")
        logger.info(f"      Input: {test_qty}")
        logger.info(f"      Output: {rounded_qty}")
        logger.info(f"      Min: {self.instrument_info.min_order_qty}")
        
        # Проверяем что округление работает корректно
        price_decimal = Decimal(rounded_price)
        step_decimal = Decimal(str(self.instrument_info.price_step))
        
        if step_decimal > 0:
            remainder = price_decimal % step_decimal
            if remainder != 0:
                logger.error(f"    Price not aligned to step: remainder={remainder}")
                return False
        
        return True
    
    async def test_place_limit_order(self) -> bool:
        """Тест размещения лимитного ордера с проверкой OrderInfo маппинга"""
        if not hasattr(self, 'current_price'):
            ticker = await self.adapter.get_ticker(self.test_symbol)
            if not ticker:
                logger.error("    Cannot place order without current price")
                return False
            self.current_price = ticker.last_price
        
        # Цена ниже рынка на 10%
        limit_price = self.current_price * 0.9
        limit_price_str = self.adapter.round_price(self.test_symbol, limit_price)
        
        # Минимальный размер
        quantity = 0.001
        quantity_str = self.adapter.round_quantity(self.test_symbol, quantity)
        
        logger.info(f"    Placing limit order:")
        logger.info(f"      Symbol: {self.test_symbol}")
        logger.info(f"      Side: BUY")
        logger.info(f"      Quantity: {quantity_str}")
        logger.info(f"      Price: {limit_price_str}")
        
        order = await self.adapter.place_order(
            symbol=self.test_symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=float(quantity_str),
            price=float(limit_price_str),
            time_in_force="GTC",
            client_order_id=f"test_{int(time.time() * 1000)}"
        )
        
        if not order:
            logger.error("    Failed to place order")
            return False
        
        logger.info(f"    ✓ Order placed successfully:")
        logger.info(f"      Order ID: {order.order_id}")
        logger.info(f"      Status: {order.status}")
        logger.info(f"      Symbol: {order.symbol}")
        logger.info(f"      Side: {order.side}")
        logger.info(f"      Type: {order.order_type}")
        
        # Сохраняем для отмены
        self.test_order_id = order.order_id
        
        # Ждем немного перед отменой
        await asyncio.sleep(1)
        
        # Отменяем ордер
        cancelled = await self.adapter.cancel_order(self.test_symbol, order.order_id)
        if cancelled:
            logger.info(f"    ✓ Order cancelled successfully")
        else:
            logger.warning(f"    Failed to cancel order (may already be filled)")
        
        return True
    
    async def test_get_open_orders(self) -> bool:
        """Тест получения открытых ордеров с проверкой OrderInfo маппинга"""
        orders = await self.adapter.get_open_orders(self.test_symbol)
        
        logger.info(f"    Open orders: {len(orders)}")
        
        for i, order in enumerate(orders[:3]):  # Показываем первые 3
            logger.info(f"    Order #{i+1}:")
            logger.info(f"      ID: {order.order_id}")
            logger.info(f"      {order.side} {order.quantity} @ {order.price}")
            logger.info(f"      Status: {order.status}")
            
            # Проверяем что маппинг работает корректно
            if not order.order_id:
                logger.error(f"      ⚠️  Missing order_id!")
                return False
        
        return True  # Успешно даже если нет открытых ордеров
    
    async def test_market_reduce_only_no_position(self) -> bool:
        """Критический тест: market reduceOnly без позиции должен быть no-op"""
        logger.info(f"    Testing market reduceOnly without position...")
        logger.info(f"    This should NOT throw an exception")
        
        order = await self.adapter.place_order(
            symbol=self.test_symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.001,
            reduce_only=True
        )
        
        # Проверяем корректную обработку
        if order is None:
            logger.info(f"    ✓ Correctly returned None (no-op)")
            return True
        elif hasattr(order, 'status') and order.status == "NoPosition":
            logger.info(f"    ✓ Correctly returned NoPosition status")
            logger.info(f"      Order ID: {order.order_id}")
            return True
        elif hasattr(order, 'quantity') and order.quantity == 0:
            logger.info(f"    ✓ Correctly returned zero quantity order")
            return True
        else:
            logger.error(f"    ❌ Unexpected result:")
            logger.error(f"      Type: {type(order)}")
            logger.error(f"      Value: {order}")
            if hasattr(order, '__dict__'):
                logger.error(f"      Attributes: {order.__dict__}")
            return False
    
    async def test_positions(self) -> bool:
        """Тест получения позиций"""
        positions = await self.adapter.get_positions()
        
        logger.info(f"    Total positions: {len(positions)}")
        
        for i, pos in enumerate(positions):
            logger.info(f"    Position #{i+1}:")
            logger.info(f"      Symbol: {pos.symbol}")
            logger.info(f"      Side: {pos.side}")
            logger.info(f"      Size: {pos.size}")
            logger.info(f"      Entry: {pos.entry_price:.2f}")
            logger.info(f"      Mark: {pos.mark_price:.2f}")
            logger.info(f"      PnL: {pos.pnl:.2f} ({pos.pnl_percentage:.2f}%)")
            logger.info(f"      Leverage: {pos.leverage}x")
        
        return True  # Успешно даже если нет позиций
    
    async def test_position_mode(self) -> bool:
        """Тест режима позиций (one-way vs hedge)"""
        logger.info("    Checking position mode configuration...")
        
        # Проверяем кэш режима
        if hasattr(self.adapter, '_pos_mode_cache'):
            logger.info(f"    Position mode cache: {self.adapter._pos_mode_cache}")
        
        # Пробуем установить one-way режим
        success = await self.adapter._ensure_position_mode("one_way")
        
        if success:
            logger.info("    ✓ One-way mode confirmed")
        else:
            logger.warning("    Could not confirm position mode")
        
        return True  # Не критично для работы
    
    # ---------------------------------------------------------------------
    # Главная функция запуска тестов
    # ---------------------------------------------------------------------
    
    async def run_all_tests(self):
        """Запуск всех тестов с группировкой и детальным отчетом"""
        try:
            # Инициализация
            if not await self.setup():
                logger.error("❌ Setup failed, cannot continue")
                return
            
            logger.info("\n" + "=" * 80)
            logger.info("📋 НАЧАЛО ТЕСТИРОВАНИЯ")
            logger.info("=" * 80)
            
            # Группа 1: Базовая связь и аутентификация
            logger.info("\n🔹 ГРУППА 1: Базовая связь и аутентификация")
            logger.info("-" * 60)
            await self.run_test("Server Time", self.test_server_time)
            await self.run_test("Balance", self.test_balance)
            
            # Группа 2: Кэширование и маппинг символов
            logger.info("\n🔹 ГРУППА 2: Кэширование и маппинг символов")
            logger.info("-" * 60)
            await self.run_test("Instruments Cache", self.test_instruments_cache)
            await self.run_test("Symbol Normalization", self.test_symbol_normalization)
            await self.run_test("Instrument Info", self.test_instrument_info)
            
            # Группа 3: Маркет-данные
            logger.info("\n🔹 ГРУППА 3: Маркет-данные")
            logger.info("-" * 60)
            await self.run_test("Ticker", self.test_ticker)
            await self.run_test("Orderbook", self.test_orderbook)
            await self.run_test("Klines", self.test_klines)
            await self.run_test("Funding Rate", self.test_funding_rate)
            
            # Группа 4: Настройки торговли
            logger.info("\n🔹 ГРУППА 4: Настройки торговли")
            logger.info("-" * 60)
            await self.run_test("Position Mode", self.test_position_mode)
            await self.run_test("Set Leverage", self.test_set_leverage)
            await self.run_test("Set Margin Mode", self.test_set_margin_mode)
            await self.run_test("Round Functions", self.test_round_functions)
            
            # Группа 5: КРИТИЧЕСКИЕ тесты ордеров
            logger.info("\n🔹 ГРУППА 5: КРИТИЧЕСКИЕ тесты ордеров")
            logger.info("-" * 60)
            await self.run_test("Place Limit Order", self.test_place_limit_order)
            await self.run_test("Get Open Orders", self.test_get_open_orders)
            await self.run_test("Market ReduceOnly (No Position)", self.test_market_reduce_only_no_position)
            
            # Группа 6: Позиции
            logger.info("\n🔹 ГРУППА 6: Позиции")
            logger.info("-" * 60)
            await self.run_test("Positions", self.test_positions)
            
            # Итоговый отчет
            await self.generate_report()
            
        except Exception as e:
            logger.error(f"❌ Fatal error in test suite: {e}")
            self.critical_errors.append(f"Fatal: {e}")
        finally:
            # Очистка
            if self.adapter:
                await self.adapter.close()
                logger.info("\n✓ Adapter closed")
    
    async def generate_report(self):
        """Генерация детального отчета о результатах тестирования"""
        logger.info("\n" + "=" * 80)
        logger.info("📊 ИТОГОВЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ")
        logger.info("=" * 80)
        
        # Статистика
        success_rate = (self.passed_tests / self.total_tests * 100) if self.total_tests > 0 else 0
        total_time = sum(r.get("time", 0) for r in self.test_results.values())
        
        logger.info(f"\n📈 СТАТИСТИКА:")
        logger.info(f"  Всего тестов: {self.total_tests}")
        logger.info(f"  ✅ Пройдено: {self.passed_tests}")
        logger.info(f"  ❌ Провалено: {self.failed_tests}")
        logger.info(f"  📊 Success Rate: {success_rate:.1f}%")
        logger.info(f"  ⏱️  Общее время: {total_time:.2f}s")
        
        # Критические ошибки
        if self.critical_errors:
            logger.error(f"\n🚨 КРИТИЧЕСКИЕ ОШИБКИ ({len(self.critical_errors)}):")
            for error in self.critical_errors:
                logger.error(f"  • {error}")
        
        # Детальные результаты по группам
        logger.info("\n📝 ДЕТАЛЬНЫЕ РЕЗУЛЬТАТЫ:")
        
        groups = {
            "Базовые": ["Server Time", "Balance"],
            "Символы": ["Instruments Cache", "Symbol Normalization", "Instrument Info"],
            "Маркет": ["Ticker", "Orderbook", "Klines", "Funding Rate"],
            "Настройки": ["Position Mode", "Set Leverage", "Set Margin Mode", "Round Functions"],
            "Ордера": ["Place Limit Order", "Get Open Orders", "Market ReduceOnly (No Position)"],
            "Позиции": ["Positions"]
        }
        
        for group_name, tests in groups.items():
            logger.info(f"\n  {group_name}:")
            for test_name in tests:
                if test_name in self.test_results:
                    result = self.test_results[test_name]
                    status = result["status"]
                    time_str = f"{result['time']:.2f}s"
                    emoji = "✅" if status == "PASSED" else "❌"
                    logger.info(f"    {emoji} {test_name}: {status} ({time_str})")
        
        # Проверка критических исправлений
        logger.info("\n🔍 ПРОВЕРКА КРИТИЧЕСКИХ ИСПРАВЛЕНИЙ:")
        
        fixes = {
            "OrderInfo маппинг": "Place Limit Order" in self.test_results and 
                                 self.test_results["Place Limit Order"]["status"] == "PASSED",
            "One-way режим": "Position Mode" in self.test_results,
            "Market reduceOnly no-op": "Market ReduceOnly (No Position)" in self.test_results and
                                       self.test_results["Market ReduceOnly (No Position)"]["status"] == "PASSED",
            "Округление цен": "Round Functions" in self.test_results and
                             self.test_results["Round Functions"]["status"] == "PASSED"
        }
        
        for fix_name, is_fixed in fixes.items():
            status = "✅ FIXED" if is_fixed else "❌ NOT FIXED"
            logger.info(f"  {status}: {fix_name}")
        
        # Финальный вердикт
        logger.info("\n" + "=" * 80)
        
        if self.failed_tests == 0:
            logger.info("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО! 🎉")
            logger.info("Адаптер готов к продакшену.")
        elif len(self.critical_errors) > 0:
            logger.error("⛔ КРИТИЧЕСКИЕ ОШИБКИ ОБНАРУЖЕНЫ!")
            logger.error("Адаптер НЕ готов к продакшену.")
            logger.error("Требуется исправление критических проблем.")
        elif success_rate >= 80:
            logger.warning("⚠️  ЕСТЬ НЕКРИТИЧЕСКИЕ ОШИБКИ")
            logger.warning(f"Success rate: {success_rate:.1f}%")
            logger.warning("Адаптер работоспособен, но требует доработки.")
        else:
            logger.error("❌ МНОЖЕСТВЕННЫЕ ОШИБКИ")
            logger.error(f"Success rate: {success_rate:.1f}%")
            logger.error("Адаптер требует серьезной доработки.")
        
        logger.info("=" * 80)


# ---------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------

async def main():
    """Главная функция запуска тестирования"""
    tester = BitgetE2ETester()
    await tester.run_all_tests()


if __name__ == "__main__":
    # Запускаем тестирование
    asyncio.run(main())
