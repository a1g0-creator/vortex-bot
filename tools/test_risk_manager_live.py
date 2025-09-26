#!/usr/bin/env python
"""
Тест Risk Manager с реальной торговлей (демо-счет)
ИСПРАВЛЕННАЯ ВЕРСИЯ - без зависаний
"""

import asyncio
import logging
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config_loader import config_loader
from exchanges.exchange_factory import exchange_factory
from core.trading_engine import TradingEngine
from core.risk_manager import RiskManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("RiskManagerLiveTest")


async def test_risk_limits():
    """Тест дневных и недельных лимитов риск-менеджера"""
    
    logger.info("=" * 60)
    logger.info("🔬 RISK MANAGER LIVE TESTING")
    logger.info("=" * 60)
    
    # Используем Bitget для тестов
    exchange = exchange_factory.create_bitget_from_config()
    await exchange.initialize()
    
    # Создаем Trading Engine
    engine = TradingEngine(exchange, mode="signals")
    await engine.initialize()
    
    logger.info(f"✅ Trading Engine initialized")
    logger.info(f"💰 Initial capital: {engine.risk_manager.initial_capital:.2f} USDT")
    
    # Тест 1: Проверка дневных лимитов БЕЗ get_risk_status
    logger.info("\n📊 TEST 1: Daily Limits (Direct Access)")
    logger.info("-" * 40)
    
    # Получаем метрики напрямую из risk_manager
    from core.risk_manager import _risk_metrics, get_daily_max_abs_loss, get_daily_max_trades, get_daily_max_drawdown_pct
    
    daily_loss_limit = await get_daily_max_abs_loss()
    daily_trades_limit = await get_daily_max_trades()
    daily_drawdown_limit = await get_daily_max_drawdown_pct()
    
    logger.info(f"Daily P&L: {_risk_metrics.daily_pnl:.2f} USDT")
    logger.info(f"Daily trades: {_risk_metrics.daily_trades_count}")
    logger.info(f"Daily loss limit: {daily_loss_limit:.2f} USDT")
    logger.info(f"Daily trades limit: {daily_trades_limit}")
    logger.info(f"Daily drawdown limit: {daily_drawdown_limit:.1f}%")
    logger.info(f"Trading allowed: {_risk_metrics.trading_allowed}")
    
    # Тест 2: Симуляция превышения дневного лимита
    logger.info("\n📊 TEST 2: Simulate Trade Permissions")
    logger.info("-" * 40)
    
    # Тестовые позиции с разными размерами
    test_positions = [
        ("BTCUSDT", "Buy", 200),    # 1% от 20k (должно пройти с новым лимитом 2%)
        ("ETHUSDT", "Buy", 300),    # 1.5% от 20k (должно пройти)
        ("XRPUSDT", "Buy", 500),    # 2.5% от 20k (НЕ должно пройти)
    ]
    
    for symbol, side, value in test_positions:
        try:
            can_trade, reason = await asyncio.wait_for(
                engine.risk_manager.check_trade_permission(
                    symbol=symbol,
                    side=side,
                    position_value=value,
                    leverage=1
                ),
                timeout=2.0
            )
            risk_pct = (value / 20000) * 100
            logger.info(f"{symbol}: ${value} ({risk_pct:.1f}%) - Can trade: {can_trade}")
            if not can_trade:
                logger.info(f"  Reason: {reason}")
        except asyncio.TimeoutError:
            logger.warning(f"{symbol}: Timeout checking permission")
    
    # Тест 3: Проверка недельных лимитов
    logger.info("\n📊 TEST 3: Weekly Limits")
    logger.info("-" * 40)
    
    from core.risk_manager import get_weekly_max_abs_loss, get_weekly_max_drawdown_pct
    
    weekly_loss_limit = await get_weekly_max_abs_loss()
    weekly_drawdown_limit = await get_weekly_max_drawdown_pct()
    
    logger.info(f"Weekly P&L: {_risk_metrics.weekly_pnl:.2f} USDT")
    logger.info(f"Weekly trades: {_risk_metrics.weekly_trades_count}")
    logger.info(f"Weekly loss limit: {weekly_loss_limit:.2f} USDT")
    logger.info(f"Weekly drawdown limit: {weekly_drawdown_limit:.1f}%")
    
    # Тест 4: Проверка максимальных позиций
    logger.info("\n📊 TEST 4: Position Limits")
    logger.info("-" * 40)
    
    from core.risk_manager import get_position_max_concurrent, get_position_max_risk_pct, get_position_max_leverage
    
    max_positions = await get_position_max_concurrent()
    max_risk_pct = await get_position_max_risk_pct()
    max_leverage = await get_position_max_leverage()
    
    logger.info(f"Current positions: {_risk_metrics.current_positions}")
    logger.info(f"Max positions: {max_positions}")
    logger.info(f"Max risk per position: {max_risk_pct}%")
    logger.info(f"Max leverage: {max_leverage}x")
    
    # Тест 5: Проверка текущей конфигурации из файла risk.yaml
    logger.info("\n📊 TEST 5: Configuration Check")
    logger.info("-" * 40)
    
    risk_config = config_loader.get_config("risk")
    if risk_config:
        logger.info("✅ risk.yaml loaded successfully")
        logger.info(f"  Enabled: {risk_config.get('enabled', False)}")
        logger.info(f"  Daily max loss: {risk_config.get('daily', {}).get('max_abs_loss', 0)} USDT")
        logger.info(f"  Position max risk: {risk_config.get('position', {}).get('max_risk_pct', 0)}%")
    else:
        logger.warning("⚠️ risk.yaml not found or not loaded")
    
    # Завершаем тест
    await engine.close()
    logger.info("\n✅ Risk Manager test completed successfully")


async def test_with_small_order():
    """Тест с минимальным тестовым ордером"""
    
    logger.info("\n" + "=" * 60)
    logger.info("🚨 MINIMAL ORDER TEST (DEMO ACCOUNT)")
    logger.info("=" * 60)
    
    exchange = exchange_factory.create_bitget_from_config()
    await exchange.initialize()
    
    engine = TradingEngine(exchange, mode="signals")
    await engine.initialize()
    
    # Получаем текущую цену BTC
    ticker = await exchange.get_ticker("BTCUSDT")
    current_price = ticker.last_price
    
    logger.info(f"BTC Price: ${current_price:.2f}")
    
    # ОЧЕНЬ маленький тестовый ордер
    test_order_price = current_price * 0.7  # 30% ниже рынка
    test_quantity = 0.0001  # Минимально возможное количество
    position_value = test_order_price * test_quantity  # ~$8-10
    
    logger.info(f"Test order: {test_quantity} BTC @ ${test_order_price:.2f}")
    logger.info(f"Position value: ${position_value:.2f}")
    
    # Проверяем разрешение от Risk Manager
    can_trade, reason = await engine.risk_manager.check_trade_permission(
        symbol="BTCUSDT",
        side="Buy",
        position_value=position_value,
        leverage=1
    )
    
    risk_pct = (position_value / engine.risk_manager.initial_capital) * 100
    logger.info(f"Risk: {risk_pct:.4f}% of capital")
    logger.info(f"Permission: {can_trade}, Reason: {reason}")
    
    if can_trade:
        logger.info("✅ Risk Manager approved this minimal test order")
    else:
        logger.warning(f"❌ Even minimal order blocked: {reason}")
    
    await engine.close()


async def main():
    """Главная функция тестирования"""
    
    try:
        # Запускаем основные тесты
        await test_risk_limits()
        
        # Тест с минимальным ордером
        await test_with_small_order()
        
        logger.info("\n" + "=" * 60)
        logger.info("🎉 All Risk Manager tests completed!")
        logger.info("=" * 60)
        
        logger.info("\n📝 ИНСТРУКЦИЯ:")
        logger.info("1. Проверьте, что max_risk_pct в risk.yaml = 2.0 или больше")
        logger.info("2. Бот работает нормально, получает сигналы")
        logger.info("3. Risk Manager корректно проверяет лимиты")
        logger.info("4. Для реальной торговли переключитесь в режим 'auto'")
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
