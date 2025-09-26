#!/usr/bin/env python3
"""
Bybit v5 API Final Test Suite (demo-only, reads config/config.yaml directly)

Читает:
  exchanges.bybit.demo.{enabled, base_url, ws_public, ws_private}
  exchanges.bybit.api_credentials.demo.{api_key, api_secret, recv_window?}
  (fallback: exchanges.bybit.demo.api_key/api_secret)

Никаких переменных окружения и никакого config_loader.
"""

import os
import sys
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any

# Project root on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import yaml  # PyYAML must be available in venv

from exchanges.bybit_adapter import BybitAdapter
from exchanges.base_exchange import OrderSide, OrderType


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BybitFinalTest")


def _load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_demo_from_config() -> Dict[str, Any]:
    """Читает demo-конфиг и креды Bybit из config/config.yaml без валидации."""
    cfg_path = os.path.join(ROOT, "config", "config.yaml")
    root = _load_yaml(cfg_path)

    exchanges_cfg = (root.get("exchanges") or {})
    bybit_cfg = (exchanges_cfg.get("bybit") or {})
    if not bybit_cfg.get("enabled", True):
        raise RuntimeError("Bybit disabled in config.yaml: exchanges.bybit.enabled = false")

    demo_cfg = (bybit_cfg.get("demo") or {})
    if not demo_cfg.get("enabled", False):
        raise RuntimeError("Demo not enabled in config.yaml: exchanges.bybit.demo.enabled = false")

    base_url = demo_cfg.get("base_url") or "https://api-demo.bybit.com"
    ws_public = demo_cfg.get("ws_public") or "wss://stream-demo.bybit.com/v5/public/linear"
    ws_private = demo_cfg.get("ws_private") or "wss://stream-demo.bybit.com/v5/private"

    # creds primary: api_credentials.demo
    creds = ((bybit_cfg.get("api_credentials") or {}).get("demo") or {})
    api_key = creds.get("api_key") or demo_cfg.get("api_key")
    api_secret = creds.get("api_secret") or demo_cfg.get("api_secret")
    recv_window = int(creds.get("recv_window") or 5000)

    if not api_key or not api_secret:
        raise RuntimeError(
            "Missing Bybit demo credentials in config.yaml. "
            "Provide exchanges.bybit.api_credentials.demo.api_key/api_secret "
            "or exchanges.bybit.demo.api_key/api_secret"
        )

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "recv_window": recv_window,
        "base_url": base_url,
        "ws_public": ws_public,
        "ws_private": ws_private,
    }


class BybitFinalTester:
    """Базовый набор тестов REST + простая торговая операция (demo)."""

    def __init__(self):
        self.adapter: Optional[BybitAdapter] = None
        self.symbol = "BTCUSDT"
        self.passed = 0
        self.failed = 0
        self.results: Dict[str, str] = {}
        self.cfg: Dict[str, Any] = {}

    async def setup(self) -> bool:
        try:
            self.cfg = _load_demo_from_config()

            logger.info("=" * 60)
            logger.info("🚀 BYBIT V5 FINAL TEST (DEMO, config.yaml)")
            logger.info("=" * 60)
            logger.info(f"Time: {datetime.now()}")
            logger.info(f"Base URL: {self.cfg['base_url']}")
            logger.info(f"WS Public: {self.cfg['ws_public']}")
            logger.info(f"WS Private: {self.cfg['ws_private']}")
            logger.info(f"Symbol: {self.symbol}")
            logger.info("-" * 60)

            # Создаём адаптер (testnet=True для demo), подменяем эндпоинты ДО initialize()
            self.adapter = BybitAdapter(
                api_key=self.cfg["api_key"],
                api_secret=self.cfg["api_secret"],
                testnet=True,
                recv_window=self.cfg["recv_window"],
            )
            self.adapter.base_url = self.cfg["base_url"]
            self.adapter.ws_public_url = self.cfg["ws_public"]
            self.adapter.ws_private_url = self.cfg["ws_private"]

            ok = await self.adapter.initialize()
            if not ok:
                logger.error("❌ Adapter initialization failed")
                return False

            logger.info("✅ Adapter initialized successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Setup failed: {e}")
            return False

    async def test_server_time(self) -> bool:
        try:
            ts = await self.adapter.get_server_time()
            if ts:
                logger.info(f"✅ Server Time: {datetime.fromtimestamp(ts/1000)}")
                return True
            logger.error("❌ Server Time: Failed")
            return False
        except Exception as e:
            logger.error(f"❌ Server Time: {e}")
            return False

    async def test_balance(self) -> bool:
        try:
            bal = await self.adapter.get_balance()
            if bal:
                logger.info("✅ Balance:")
                logger.info(f"   Wallet:     {bal.wallet_balance:.4f} USDT")
                logger.info(f"   Available:  {bal.available_balance:.4f} USDT")
                logger.info(f"   Unrealized: {bal.unrealized_pnl:.4f} USDT")
                return True
            logger.error("❌ Balance: Failed to fetch")
            return False
        except Exception as e:
            logger.error(f"❌ Balance: {e}")
            return False

    async def test_instruments(self) -> bool:
        try:
            instruments = await self.adapter.get_instruments()
            if instruments:
                logger.info(f"✅ Instruments: {len(instruments)}")
                for inst in instruments[:3]:
                    logger.info(f"   - {inst.symbol}: tick={inst.price_step}, step={inst.qty_step}")
                return True
            logger.error("❌ Instruments: None found")
            return False
        except Exception as e:
            logger.error(f"❌ Instruments: {e}")
            return False

    async def test_ticker(self) -> bool:
        try:
            t = await self.adapter.get_ticker(self.symbol)
            if t:
                logger.info(f"✅ Ticker {self.symbol}: last={t.last_price:.2f} bid={t.bid_price:.2f} ask={t.ask_price:.2f}")
                return True
            logger.error("❌ Ticker: Failed")
            return False
        except Exception as e:
            logger.error(f"❌ Ticker: {e}")
            return False

    async def test_klines(self) -> bool:
        try:
            ks = await self.adapter.get_klines(self.symbol, "60", limit=10)
            if ks:
                last = ks[-1]
                logger.info(f"✅ Klines: {len(ks)} candles; latest O={last.open:.2f} H={last.high:.2f} L={last.low:.2f} C={last.close:.2f}")
                return True
            logger.error("❌ Klines: Failed")
            return False
        except Exception as e:
            logger.error(f"❌ Klines: {e}")
            return False

    async def test_orderbook(self) -> bool:
        try:
            ob = await self.adapter.get_orderbook(self.symbol, limit=5)
            if ob and ob.get("bids") is not None and ob.get("asks") is not None:
                logger.info(f"✅ Orderbook: bids={len(ob['bids'])} asks={len(ob['asks'])}")
                return True
            logger.error("❌ Orderbook: Failed")
            return False
        except Exception as e:
            logger.error(f"❌ Orderbook: {e}")
            return False

    async def test_place_cancel_order(self) -> bool:
        try:
            t = await self.adapter.get_ticker(self.symbol)
            if not t:
                logger.error("❌ Order Test: cannot get ticker")
                return False

            # Ставим лимит далек от рынка, чтобы не исполнился
            price = self.adapter.round_price(self.symbol, t.last_price * 0.90)
            qty = self.adapter.round_quantity(self.symbol, 10.0 / max(price, 1.0))

            logger.info(f"   Placing limit Buy {qty} @ {price}")
            order = await self.adapter.place_order(
                symbol=self.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=qty,
                price=price,
                time_in_force="GTC",
            )
            if not order:
                logger.error("❌ Order Test: place failed")
                return False

            await asyncio.sleep(1.0)
            ok = await self.adapter.cancel_order(self.symbol, order.order_id)
            if ok:
                logger.info("✅ Order Test: placed & cancelled")
                return True
            logger.error("❌ Order Test: cancel failed")
            return False
        except Exception as e:
            logger.error(f"❌ Order Test: {e}")
            return False

    async def test_leverage(self) -> bool:
        try:
            ok = await self.adapter.set_leverage(self.symbol, 5)
            if ok:
                logger.info("✅ Leverage set to 5x")
                return True
            logger.error("❌ Leverage: failed")
            return False
        except Exception as e:
            logger.error(f"❌ Leverage: {e}")
            return False

    async def test_margin_mode(self) -> bool:
        try:
            ok = await self.adapter.set_margin_mode(self.symbol, "CROSS")
            if ok:
                logger.info("✅ Margin Mode set to CROSS")
                return True
            logger.error("❌ Margin Mode: failed")
            return False
        except Exception as e:
            logger.error(f"❌ Margin Mode: {e}")
            return False

    async def test_positions(self) -> bool:
        try:
            positions = await self.adapter.get_positions()
            logger.info(f"✅ Positions: {len(positions)} active")
            return True
        except Exception as e:
            logger.error(f"❌ Positions: {e}")
            return False

    async def test_open_orders(self) -> bool:
        try:
            orders = await self.adapter.get_open_orders()
            logger.info(f"✅ Open Orders: {len(orders)}")
            return True
        except Exception as e:
            logger.error(f"❌ Open Orders: {e}")
            return False

    async def run_all(self):
        if not await self.setup():
            return

        tests = [
            ("Server Time", self.test_server_time),
            ("Balance", self.test_balance),
            ("Instruments", self.test_instruments),
            ("Ticker", self.test_ticker),
            ("Klines", self.test_klines),
            ("Orderbook", self.test_orderbook),
            ("Place/Cancel Order", self.test_place_cancel_order),
            ("Leverage", self.test_leverage),
            ("Margin Mode", self.test_margin_mode),
            ("Positions", self.test_positions),
            ("Open Orders", self.test_open_orders),
        ]

        logger.info("\n" + "=" * 60)
        logger.info("RUNNING TESTS")
        logger.info("=" * 60 + "\n")

        for name, fn in tests:
            logger.info(f"Testing: {name}")
            try:
                ok = await fn()
                self.results[name] = "PASSED" if ok else "FAILED"
                self.passed += 1 if ok else 0
                self.failed += 0 if ok else 1
            except Exception as e:
                self.results[name] = f"ERROR: {e}"
                self.failed += 1
                logger.error(f"   Test error: {e}")
            logger.info("")

        total = self.passed + self.failed
        rate = (self.passed / total * 100.0) if total else 0.0
        logger.info("=" * 60)
        logger.info("TEST SUMMARY")
        logger.info("=" * 60)
        logger.info(f"✅ Passed: {self.passed}/{total}")
        logger.info(f"❌ Failed: {self.failed}/{total}")
        logger.info(f"📊 Success Rate: {rate:.1f}%")
        for n, r in self.results.items():
            emoji = "✅" if r == "PASSED" else "❌"
            logger.info(f"  {emoji} {n}: {r}")

        if self.adapter:
            await self.adapter.close()


async def main():
    tester = BybitFinalTester()
    await tester.run_all()


if __name__ == "__main__":
    asyncio.run(main())

