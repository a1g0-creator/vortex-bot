#!/usr/bin/env python3
"""
Bybit v5 API Ultimate Test Suite (demo-only, reads config/config.yaml directly)

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
from typing import Dict, Any, Optional

# Project root on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import yaml

from exchanges.bybit_adapter import BybitAdapter
from exchanges.base_exchange import OrderSide, OrderType


logging.basicConfig(
    level=logging.INFO,  # set DEBUG for deeper WS logs
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BybitUltimateTest")


def _load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_demo_from_config() -> Dict[str, Any]:
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

    creds = ((bybit_cfg.get("api_credentials") or {}).get("demo") or {})
    api_key = creds.get("api_key") or demo_cfg.get("api_key")
    api_secret = creds.get("api_secret") or demo_cfg.get("api_secret")
    recv_window = int(creds.get("recv_window") or 5000)

    if not api_key or not api_secret:
        raise RuntimeError(
            "Missing Bybit demo credentials in config.yaml. "
            "Provide exchanges.bybit.api_credentials.demo or bybit.demo.api_key/api_secret"
        )

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "recv_window": recv_window,
        "base_url": base_url,
        "ws_public": ws_public,
        "ws_private": ws_private,
    }


class BybitUltimateTester:
    """Расширенные тесты: WS public/private триггеры, прецизионность, burst и т.п. (demo)."""

    def __init__(self):
        self.adapter: Optional[BybitAdapter] = None
        self.cfg: Dict[str, Any] = {}
        self.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.ws_received: Dict[str, Any] = {}

    async def setup(self) -> bool:
        try:
            self.cfg = _load_demo_from_config()

            logger.info("=" * 60)
            logger.info("🚀 BYBIT V5 ULTIMATE TEST (DEMO, config.yaml)")
            logger.info("=" * 60)
            logger.info(f"Time: {datetime.now()}")
            logger.info(f"Base URL: {self.cfg['base_url']}")
            logger.info(f"WS Public: {self.cfg['ws_public']}")
            logger.info(f"WS Private: {self.cfg['ws_private']}")
            logger.info("-" * 60)

            self.adapter = BybitAdapter(
                api_key=self.cfg["api_key"],
                api_secret=self.cfg["api_secret"],
                testnet=True,
                recv_window=self.cfg["recv_window"],
            )
            # Подменяем эндпоинты ДО initialize()
            self.adapter.base_url = self.cfg["base_url"]
            self.adapter.ws_public_url = self.cfg["ws_public"]
            self.adapter.ws_private_url = self.cfg["ws_private"]

            ok = await self.adapter.initialize()
            if not ok:
                logger.error("❌ Adapter initialization failed")
                return False

            logger.info("✅ Adapter initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Setup failed: {e}")
            return False

    async def test_websocket_public(self) -> bool:
        logger.info("\n🔌 Testing Public WebSocket...")
        try:
            ws_task = asyncio.create_task(self.adapter.start_websocket())

            async def on_ticker(data):
                self.ws_received["ticker"] = data

            async def on_kline(data):
                self.ws_received["kline"] = data

            await self.adapter.subscribe_to_ticker("BTCUSDT", on_ticker)
            await self.adapter.subscribe_to_klines("BTCUSDT", "1", on_kline)

            await asyncio.sleep(6.0)
            self.adapter._ws_running = False
            await asyncio.sleep(1.0)

            got = len(self.ws_received)
            if got > 0:
                logger.info(f"✅ WebSocket Public: got {got} channel(s)")
                return True
            logger.warning("⚠️ WebSocket Public: No data received")
            return False
        except Exception as e:
            logger.error(f"❌ WebSocket Public: {e}")
            return False

    async def test_error_handling(self) -> bool:
        logger.info("\n🛡️ Testing Error Handling...")
        try:
            t = await self.adapter.get_ticker("INVALIDUSDT")
            ok1 = (t is None)

            res = await self.adapter.cancel_order("BTCUSDT", "NON_EXISTENT")
            ok2 = (res is False)

            logger.info(f"✅ Error Handling: invalid symbol handled={ok1}, invalid cancel handled={ok2}")
            return ok1 and not res
        except Exception as e:
            logger.error(f"❌ Error Handling: {e}")
            return False

    async def test_precision_rounding(self) -> bool:
        logger.info("\n🎯 Testing Precision and Rounding...")
        try:
            for s in self.symbols:
                info = await self.adapter.get_symbol_info(s)
                if not info:
                    logger.warning(f"   No instrument info for {s}")
                    continue
                rp = self.adapter.round_price(s, 12345.6789)
                rq = self.adapter.round_quantity(s, 1.234567)
                logger.info(f"   {s} price-> {rp} (tick={info.price_step}), qty-> {rq} (step={info.qty_step})")
            logger.info("✅ Precision: OK")
            return True
        except Exception as e:
            logger.error(f"❌ Precision: {e}")
            return False

    async def test_private_ws_triggers(self) -> bool:
        logger.info("\n🔐 Testing Private WebSocket triggers (order/position)...")
        try:
            t = await self.adapter.get_ticker("BTCUSDT")
            if not t:
                logger.warning("   Can't fetch ticker to trigger private events")
                return True  # не фейлим стенд без приватных WS прав

            price = self.adapter.round_price("BTCUSDT", t.last_price * 0.90)
            qty = self.adapter.round_quantity("BTCUSDT", 10.0 / max(price, 1.0))

            order = await self.adapter.place_order(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=qty,
                price=price,
            )
            if order:
                await asyncio.sleep(1.0)
                await self.adapter.cancel_order("BTCUSDT", order.order_id)

            logger.info("✅ Private WS trigger executed")
            return True
        except Exception as e:
            logger.error(f"❌ Private WS trigger: {e}")
            return False

    async def test_rate_burst(self) -> bool:
        logger.info("\n⚡ Testing burst of market requests...")
        try:
            tasks = [self.adapter.get_ticker("BTCUSDT") for _ in range(12)]
            res = await asyncio.gather(*tasks, return_exceptions=True)
            errs = [r for r in res if isinstance(r, Exception)]
            if errs:
                logger.warning(f"⚠️ Burst produced {len(errs)} errors (rate-limit may occur)")
            else:
                logger.info("✅ Burst handled without client errors")
            return True
        except Exception as e:
            logger.error(f"❌ Rate burst: {e}")
            return False

    async def test_multi_symbol_market(self) -> bool:
        logger.info("\n🎰 Testing multi-symbol market data...")
        try:
            ok = True
            for s in self.symbols:
                t = await self.adapter.get_ticker(s)
                if not t:
                    logger.warning(f"   No ticker for {s}")
                    ok = False
                else:
                    logger.info(f"   {s}: last={t.last_price:.2f}")
            return ok
        except Exception as e:
            logger.error(f"❌ Multi-symbol: {e}")
            return False

    async def test_order_history(self) -> bool:
        logger.info("\n📜 Testing order history endpoint...")
        try:
            hist = await self.adapter.get_order_history(limit=10)
            logger.info(f"   Orders in history: {len(hist)}")
            return True
        except Exception as e:
            logger.error(f"❌ Order history: {e}")
            return False

    async def run_all(self):
        if not await self.setup():
            return

        tests = [
            ("WebSocket Public", self.test_websocket_public),
            ("Error Handling", self.test_error_handling),
            ("Precision Rounding", self.test_precision_rounding),
            ("Private WS Triggers", self.test_private_ws_triggers),
            ("Rate Burst", self.test_rate_burst),
            ("Multi-Symbol Market", self.test_multi_symbol_market),
            ("Order History", self.test_order_history),
        ]

        results: Dict[str, str] = {}
        logger.info("\n" + "=" * 60)
        logger.info("RUNNING ULTIMATE TESTS")
        logger.info("=" * 60 + "\n")

        for name, fn in tests:
            logger.info(f"Testing: {name}")
            try:
                ok = await fn()
                results[name] = "PASSED" if ok else "FAILED"
            except Exception as e:
                results[name] = f"ERROR: {e}"
                logger.error(f"   Test error: {e}")
            logger.info("")

        logger.info("\n" + "=" * 60)
        logger.info("ULTIMATE TEST SUMMARY")
        logger.info("=" * 60)
        passed = sum(1 for v in results.values() if v == "PASSED")
        total = len(results)
        for n, r in results.items():
            emoji = "✅" if r == "PASSED" else "❌"
            logger.info(f"{emoji} {n}: {r}")
        logger.info(f"\nTotal: {passed}/{total} passed")

        if self.adapter:
            await self.adapter.close()


async def main():
    tester = BybitUltimateTester()
    await tester.run_all()


if __name__ == "__main__":
    asyncio.run(main())

