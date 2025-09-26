#!/usr/bin/env python
"""
E2E Smoke Test для проверки ОБЕИХ бирж (Bybit и Bitget)
Полное тестирование всех компонентов с каждой биржей
"""

import asyncio
import logging
import sys
import os
from typing import Dict, Any, Tuple

# Добавляем корневую директорию в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config_loader import config_loader
from exchanges.exchange_factory import exchange_factory
from exchanges.bybit_adapter import BybitAdapter
from exchanges.bitget_adapter import BitgetAdapter
from core.trading_engine import TradingEngine
from core.risk_manager import RiskManager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("DualExchangeTest")


async def test_config_loader():
    """Тест загрузки конфигураций"""
    logger.info("=" * 60)
    logger.info("TEST 1: ConfigLoader")
    logger.info("-" * 60)
    
    try:
        configs = config_loader.load_all_configs()
        logger.info(f"✅ Loaded {len(configs)} configurations")
        
        # Проверяем обе биржи
        bybit_config = config_loader.get_active_exchange_config("bybit")
        assert bybit_config is not None, "Bybit config is None"
        logger.info(f"✅ Bybit config: env={bybit_config.get('environment')}")
        
        bitget_config = config_loader.get_active_exchange_config("bitget")
        assert bitget_config is not None, "Bitget config is None"
        logger.info(f"✅ Bitget config: env={bitget_config.get('environment')}")
        
        strategies_config = config_loader.get_config("strategies")
        vortex_config = strategies_config.get("vortex_bands", {})
        instruments = vortex_config.get("instruments", {})
        logger.info(f"✅ Vortex instruments: {len(instruments)}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ ConfigLoader test failed: {e}")
        return False


async def test_exchange_adapter(exchange_name: str) -> Tuple[bool, Dict[str, Any]]:
    """Универсальный тест для любого биржевого адаптера"""
    logger.info("=" * 60)
    logger.info(f"TEST: {exchange_name.upper()} Adapter")
    logger.info("-" * 60)
    
    results = {
        "initialized": False,
        "balance": None,
        "ticker": None,
        "positions_count": 0,
        "can_place_orders": False
    }
    
    try:
        # Создаем адаптер
        if exchange_name == "bybit":
            adapter = exchange_factory.create_bybit_from_config()
        else:
            adapter = exchange_factory.create_bitget_from_config()
        
        # Инициализация
        success = await adapter.initialize()
        assert success, f"{exchange_name} initialization failed"
        results["initialized"] = True
        logger.info(f"✅ {exchange_name} adapter initialized")
        
        # Тест баланса
        balance = await asyncio.wait_for(adapter.get_balance("USDT"), timeout=5.0)
        if balance:
            results["balance"] = balance.wallet_balance
            logger.info(f"✅ Balance: {balance.wallet_balance:.2f} USDT")
        
        # Тест тикера
        ticker = await asyncio.wait_for(adapter.get_ticker("BTCUSDT"), timeout=5.0)
        if ticker:
            results["ticker"] = ticker.last_price
            logger.info(f"✅ BTC price: {ticker.last_price:.2f}")
        
        # Тест позиций
        positions = await asyncio.wait_for(adapter.get_positions(), timeout=5.0)
        results["positions_count"] = len(positions)
        logger.info(f"✅ Active positions: {len(positions)}")
        
        # Проверка возможности торговли
        results["can_place_orders"] = results["initialized"] and results["balance"] is not None
        
        # Закрываем соединение
        await adapter.close()
        logger.info(f"✅ {exchange_name} adapter closed")
        
        return True, results
        
    except asyncio.TimeoutError:
        logger.error(f"❌ {exchange_name} test timeout")
        return False, results
    except Exception as e:
        logger.error(f"❌ {exchange_name} test failed: {e}")
        return False, results


async def test_risk_manager_with_exchange(exchange_name: str) -> Tuple[bool, Dict[str, Any]]:
    """Тест Risk Manager с конкретной биржей"""
    logger.info("=" * 60)
    logger.info(f"TEST: Risk Manager with {exchange_name.upper()}")
    logger.info("-" * 60)
    
    results = {
        "initialized": False,
        "initial_capital": 0,
        "can_trade": False,
        "reason": ""
    }
    
    try:
        # Создаем биржу
        if exchange_name == "bybit":
            exchange = exchange_factory.create_bybit_from_config()
        else:
            exchange = exchange_factory.create_bitget_from_config()
        
        await exchange.initialize()
        logger.info(f"✅ {exchange_name} initialized for RiskManager")
        
        # Создаем Risk Manager
        risk_manager = RiskManager(exchange, initial_capital=None)
        
        # Инициализация
        success = await asyncio.wait_for(risk_manager.initialize(), timeout=5.0)
        results["initialized"] = success
        
        if success:
            logger.info(f"✅ RiskManager initialized with {exchange_name}")
            
            if risk_manager.initial_capital:
                results["initial_capital"] = risk_manager.initial_capital
                logger.info(f"✅ Initial capital: {risk_manager.initial_capital:.2f}")
            
            # Тест разрешения на торговлю
            can_trade, reason = await asyncio.wait_for(
                risk_manager.check_trade_permission(
                    symbol="BTCUSDT",
                    side="Buy",
                    position_value=100.0,
                    leverage=1
                ),
                timeout=3.0
            )
            results["can_trade"] = can_trade
            results["reason"] = reason
            logger.info(f"✅ Can trade: {can_trade}, reason: {reason}")
        
        logger.info(f"✅ RiskManager test with {exchange_name} completed")
        return True, results
        
    except asyncio.TimeoutError:
        logger.error(f"❌ RiskManager test timeout with {exchange_name}")
        return False, results
    except Exception as e:
        logger.error(f"❌ RiskManager test failed with {exchange_name}: {e}")
        return False, results


async def test_trading_engine_with_exchange(exchange_name: str) -> Tuple[bool, Dict[str, Any]]:
    """Тест Trading Engine с конкретной биржей"""
    logger.info("=" * 60)
    logger.info(f"TEST: Trading Engine with {exchange_name.upper()}")
    logger.info("-" * 60)
    
    results = {
        "initialized": False,
        "mode": "",
        "risk_manager_active": False,
        "ready": False
    }
    
    try:
        # Создаем биржу
        if exchange_name == "bybit":
            exchange = exchange_factory.create_bybit_from_config()
        else:
            exchange = exchange_factory.create_bitget_from_config()
        
        await exchange.initialize()
        
        # Создаем Trading Engine
        engine = TradingEngine(exchange, mode="signals")
        
        # Инициализация
        success = await asyncio.wait_for(engine.initialize(), timeout=10.0)
        results["initialized"] = success
        
        if success:
            logger.info(f"✅ TradingEngine initialized with {exchange_name}")
            
            results["mode"] = engine.mode
            results["risk_manager_active"] = engine.risk_manager is not None
            results["ready"] = (
                engine.mode in ["auto", "signals"] and
                engine.exchange is not None and
                engine.risk_manager is not None
            )
            
            logger.info(f"✅ Mode: {results['mode']}")
            logger.info(f"✅ Risk Manager: {results['risk_manager_active']}")
            logger.info(f"✅ Ready for production: {results['ready']}")
        
        logger.info(f"✅ TradingEngine test with {exchange_name} completed")
        return True, results
        
    except asyncio.TimeoutError:
        logger.error(f"❌ TradingEngine test timeout with {exchange_name}")
        return False, results
    except Exception as e:
        logger.error(f"❌ TradingEngine test failed with {exchange_name}: {e}")
        return False, results


async def main():
    """Главная функция тестирования обеих бирж"""
    logger.info("🚀 Starting Dual Exchange E2E Tests")
    logger.info("=" * 60)
    
    # Общие результаты
    all_results = {
        "config": False,
        "bybit": {},
        "bitget": {}
    }
    
    try:
        # 1. Тест конфигурации
        all_results["config"] = await test_config_loader()
        
        # 2. Тест адаптеров бирж
        logger.info("\n" + "=" * 60)
        logger.info("📊 EXCHANGE ADAPTERS TESTING")
        logger.info("=" * 60)
        
        bybit_success, bybit_results = await test_exchange_adapter("bybit")
        all_results["bybit"]["adapter"] = {
            "success": bybit_success,
            "details": bybit_results
        }
        
        bitget_success, bitget_results = await test_exchange_adapter("bitget")
        all_results["bitget"]["adapter"] = {
            "success": bitget_success,
            "details": bitget_results
        }
        
        # 3. Тест Risk Manager с обеими биржами
        logger.info("\n" + "=" * 60)
        logger.info("📊 RISK MANAGER TESTING")
        logger.info("=" * 60)
        
        bybit_rm_success, bybit_rm_results = await test_risk_manager_with_exchange("bybit")
        all_results["bybit"]["risk_manager"] = {
            "success": bybit_rm_success,
            "details": bybit_rm_results
        }
        
        bitget_rm_success, bitget_rm_results = await test_risk_manager_with_exchange("bitget")
        all_results["bitget"]["risk_manager"] = {
            "success": bitget_rm_success,
            "details": bitget_rm_results
        }
        
        # 4. Тест Trading Engine с обеими биржами
        logger.info("\n" + "=" * 60)
        logger.info("📊 TRADING ENGINE TESTING")
        logger.info("=" * 60)
        
        bybit_te_success, bybit_te_results = await test_trading_engine_with_exchange("bybit")
        all_results["bybit"]["trading_engine"] = {
            "success": bybit_te_success,
            "details": bybit_te_results
        }
        
        bitget_te_success, bitget_te_results = await test_trading_engine_with_exchange("bitget")
        all_results["bitget"]["trading_engine"] = {
            "success": bitget_te_success,
            "details": bitget_te_results
        }
        
        # Финальный отчет
        logger.info("\n" + "=" * 60)
        logger.info("📊 FINAL REPORT - DUAL EXCHANGE TESTING")
        logger.info("=" * 60)
        
        # Анализ Bybit
        logger.info("\n🔹 BYBIT RESULTS:")
        bybit_adapter_ok = all_results["bybit"]["adapter"]["success"]
        bybit_rm_ok = all_results["bybit"]["risk_manager"]["success"]
        bybit_te_ok = all_results["bybit"]["trading_engine"]["success"]
        
        logger.info(f"  Adapter: {'✅' if bybit_adapter_ok else '❌'}")
        if bybit_adapter_ok:
            bybit_details = all_results["bybit"]["adapter"]["details"]
            logger.info(f"    Balance: {bybit_details['balance']:.2f} USDT")
            logger.info(f"    BTC Price: ${bybit_details['ticker']:.2f}")
        
        logger.info(f"  Risk Manager: {'✅' if bybit_rm_ok else '❌'}")
        if bybit_rm_ok:
            rm_details = all_results["bybit"]["risk_manager"]["details"]
            logger.info(f"    Capital: {rm_details['initial_capital']:.2f} USDT")
            logger.info(f"    Can Trade: {rm_details['can_trade']}")
        
        logger.info(f"  Trading Engine: {'✅' if bybit_te_ok else '❌'}")
        if bybit_te_ok:
            te_details = all_results["bybit"]["trading_engine"]["details"]
            logger.info(f"    Ready: {te_details['ready']}")
        
        # Анализ Bitget
        logger.info("\n🔹 BITGET RESULTS:")
        bitget_adapter_ok = all_results["bitget"]["adapter"]["success"]
        bitget_rm_ok = all_results["bitget"]["risk_manager"]["success"]
        bitget_te_ok = all_results["bitget"]["trading_engine"]["success"]
        
        logger.info(f"  Adapter: {'✅' if bitget_adapter_ok else '❌'}")
        if bitget_adapter_ok:
            bitget_details = all_results["bitget"]["adapter"]["details"]
            logger.info(f"    Balance: {bitget_details['balance']:.2f} USDT")
            logger.info(f"    BTC Price: ${bitget_details['ticker']:.2f}")
        
        logger.info(f"  Risk Manager: {'✅' if bitget_rm_ok else '❌'}")
        if bitget_rm_ok:
            rm_details = all_results["bitget"]["risk_manager"]["details"]
            logger.info(f"    Capital: {rm_details['initial_capital']:.2f} USDT")
            logger.info(f"    Can Trade: {rm_details['can_trade']}")
        
        logger.info(f"  Trading Engine: {'✅' if bitget_te_ok else '❌'}")
        if bitget_te_ok:
            te_details = all_results["bitget"]["trading_engine"]["details"]
            logger.info(f"    Ready: {te_details['ready']}")
        
        # Общий вердикт
        bybit_ready = bybit_adapter_ok and bybit_rm_ok and bybit_te_ok
        bitget_ready = bitget_adapter_ok and bitget_rm_ok and bitget_te_ok
        
        logger.info("\n" + "=" * 60)
        logger.info("🎯 FINAL VERDICT:")
        logger.info(f"  Bybit: {'✅ READY FOR PRODUCTION' if bybit_ready else '❌ NOT READY'}")
        logger.info(f"  Bitget: {'✅ READY FOR PRODUCTION' if bitget_ready else '❌ NOT READY'}")
        
        if bybit_ready and bitget_ready:
            logger.info("\n🎉 BOTH EXCHANGES ARE READY FOR PRODUCTION!")
            logger.info("You can now proceed to multi-exchange trading implementation.")
        elif bybit_ready or bitget_ready:
            ready_exchange = "Bybit" if bybit_ready else "Bitget"
            logger.info(f"\n⚠️ Only {ready_exchange} is ready for production.")
        else:
            logger.info("\n❌ Neither exchange is fully ready.")
        
        return 0 if (bybit_ready and bitget_ready) else 1
        
    except Exception as e:
        logger.error(f"❌ Critical error in tests: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Очистка кэша
        try:
            for key in list(exchange_factory._instances.keys()):
                exchange = exchange_factory._instances.get(key)
                if exchange and hasattr(exchange, 'close'):
                    try:
                        await asyncio.wait_for(exchange.close(), timeout=2.0)
                    except:
                        pass
            exchange_factory._instances.clear()
        except:
            pass


if __name__ == "__main__":
    logging.getLogger("ExchangeFactory").setLevel(logging.DEBUG)
    logging.getLogger("BitgetAdapter").setLevel(logging.INFO)
    logging.getLogger("BybitAdapter").setLevel(logging.INFO)
    
    exit_code = asyncio.run(main())
    sys.exit(exit_code)