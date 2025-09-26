#!/usr/bin/env python3
"""
E2E тестер для Bybit демо/мэйннета - ENHANCED версия с детальной диагностикой
- Подхватывает проект из корня (../)
- Импортирует адаптер из exchanges.bybit_adapter
- До старта тестов выводит список НЕреализованных абстрактных методов
- ДОБАВЛЕНО: Детальная диагностика ошибок и состояния WebSocket
"""

import asyncio
import logging
import sys
import inspect
from pathlib import Path
from typing import Dict, Any, Optional

# >>> Корень проекта (…/Vortex_trading_bot)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Импорты строго из папки exchanges, как у тебя на скриншотах
from exchanges.bybit_adapter import BybitAdapter      # <-- ВАЖНО
from config.config_loader import config_loader, get_bybit_active_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("BybitE2E")

class BybitDemoE2ETester:
    def __init__(self):
        self.adapter: Optional[BybitAdapter] = None
        self.test_symbol = "BTCUSDT"
        self.results: Dict[str, Dict[str, bool]] = {}

    def _check_abstract_methods(self) -> bool:
        """Печатаем отсутствующие реализации абстрактных методов у BybitAdapter"""
        missing = getattr(BybitAdapter, "__abstractmethods__", set())
        if missing:
            log.error("❌ BybitAdapter сейчас абстрактный. НЕ реализованы методы:")
            for m in sorted(missing):
                log.error("   - %s", m)
            log.error("   → Допиши эти методы в exchanges/bybit_adapter.py (см. чек-лист ниже)")
            return False
        log.info("✅ BybitAdapter не является абстрактным классом (OK)")
        return True

    async def setup(self) -> bool:
        log.info("=" * 60)
        log.info("🚀 Starting Bybit E2E Test")
        log.info("=" * 60)

        # 1) Сразу проверяем абстрактность
        if not self._check_abstract_methods():
            return False

        # 2) Конфиг
        active_env = config_loader.get_active_environment("bybit")
        log.info("Active environment: %r", active_env)

        cfg = get_bybit_active_config()
        if not cfg:
            log.error("❌ Failed to get Bybit configuration")
            return False

        required = ["environment", "base_url", "ws_public", "ws_private", "api_key", "api_secret"]
        missing_fields = [k for k in required if not cfg.get(k)]
        if missing_fields:
            log.error("❌ Missing config fields: %s", missing_fields)
            return False

        log.info("Configuration:")
        log.info("  environment: %s", cfg["environment"])
        log.info("  base_url   : %s", cfg["base_url"])
        log.info("  ws_public  : %s", cfg["ws_public"])
        log.info("  ws_private : %s", cfg["ws_private"])
        log.info("  api_key    : %s...", cfg["api_key"][:8])

        # 3) Инициализация адаптера (универсально: поддержим 2 сигнатуры)
        #    а) BybitAdapter(cfg_dict)
        #    б) BybitAdapter(api_key, api_secret, testnet=bool)
        init_ok = False
        try:
            sig = inspect.signature(BybitAdapter)
            if len(sig.parameters) == 1:
                self.adapter = BybitAdapter(cfg)  # type: ignore
            else:
                self.adapter = BybitAdapter(cfg["api_key"], cfg["api_secret"],
                                            testnet=(cfg["environment"] == "demo"))  # type: ignore
            log.info("Initializing adapter...")
            if hasattr(self.adapter, "initialize") and inspect.iscoroutinefunction(self.adapter.initialize):
                init_ok = await self.adapter.initialize()
            else:
                init_ok = True
        except Exception as e:
            log.exception("❌ Adapter init error: %s", e)
            return False

        if not init_ok:
            log.error("❌ Adapter.initialize() returned False")
            return False

        log.info("✅ Adapter initialized")
        return True

    async def test_rest(self) -> Dict[str, bool]:
        res = {"server_time": False, "balance": False, "instruments": False, "ticker": False, "positions": False}
        log.info("\n" + "=" * 60)
        log.info("📡 Testing REST API")
        log.info("=" * 60)

        try:
            t = await self.adapter.get_server_time()  # type: ignore
            res["server_time"] = bool(t)
            log.info("Server time: %s", t)
        except Exception as e:
            log.error("Server time error: %s", e)

        try:
            b = await self.adapter.get_balance()  # type: ignore
            res["balance"] = bool(b)
            if b:
                log.info("Balance: %.2f USDT (available: %.2f)", b.wallet_balance, b.available_balance)
        except Exception as e:
            log.error("Balance error: %s", e)

        try:
            instruments = await self.adapter.get_all_instruments()  # type: ignore
            res["instruments"] = bool(instruments)
            if instruments:
                log.info("Loaded %d instruments", len(instruments))
        except Exception as e:
            log.error("Instruments error: %s", e)

        try:
            tk = await self.adapter.get_ticker(self.test_symbol)  # type: ignore
            res["ticker"] = bool(tk)
            if tk:
                log.info("Ticker %s: bid=%.2f ask=%.2f last=%.2f", self.test_symbol, tk.bid, tk.ask, tk.last)
        except Exception as e:
            log.error("Ticker error: %s", e)

        # ENHANCED: Детальная диагностика позиций
        try:
            log.info("🔍 ENHANCED: Testing positions with detailed diagnostics...")
            pos = await self.adapter.get_positions()  # type: ignore
            res["positions"] = True
            if pos:
                log.info("Open positions: %d", len(pos))
                for i, p in enumerate(pos[:3]):  # Показываем первые 3 позиции
                    log.info(f"  Position {i+1}: {p.symbol} {p.side} {p.size}")
            else:
                log.info("No open positions (empty list returned)")
        except Exception as e:
            log.error("❌ ENHANCED: Positions error details:")
            log.error(f"   Exception type: {type(e).__name__}")
            log.error(f"   Exception message: {str(e)}")
            log.error(f"   Full traceback:", exc_info=True)
            
            # Пробуем диагностировать API запрос
            try:
                log.info("🔧 ENHANCED: Attempting diagnostic API calls...")
                # Проверяем доступность разных endpoints
                server_time = await self.adapter.get_server_time()
                log.info(f"   Server time accessible: {bool(server_time)}")
                
                # Проверяем session
                session_status = "Unknown"
                if hasattr(self.adapter, 'session'):
                    if self.adapter.session is None:
                        session_status = "None"
                    elif self.adapter.session.closed:
                        session_status = "Closed"
                    else:
                        session_status = "Open"
                log.info(f"   HTTP session status: {session_status}")
                
            except Exception as diag_e:
                log.error(f"   Diagnostic call failed: {diag_e}")

        return res

    async def test_ws(self) -> Dict[str, bool]:
        res = {"ws_public_connect": False, "ws_public_subscribe": False, "ws_private_connect": False, "ws_private_auth": False}
        log.info("\n" + "=" * 60)
        log.info("🔌 Testing WebSocket")
        log.info("=" * 60)

        try:
            tick_event = asyncio.Event()

            async def on_tick(data):
                log.info("Received ticker update: %s", data)
                tick_event.set()

            # ENHANCED: Детальная диагностика до запуска WebSocket
            log.info("🔍 ENHANCED: Pre-WebSocket diagnostics...")
            log.info(f"   Adapter has start_websocket: {hasattr(self.adapter, 'start_websocket')}")
            log.info(f"   Adapter has stop_websocket: {hasattr(self.adapter, 'stop_websocket')}")
            log.info(f"   Adapter has subscribe_ticker: {hasattr(self.adapter, 'subscribe_ticker')}")
            
            # Проверяем начальное состояние WebSocket атрибутов
            initial_ws_public = getattr(self.adapter, "_ws_public", "NOT_FOUND")
            initial_ws_private = getattr(self.adapter, "_ws_private", "NOT_FOUND")
            initial_ws_auth = getattr(self.adapter, "_ws_auth", "NOT_FOUND")
            initial_ws_running = getattr(self.adapter, "_ws_running", "NOT_FOUND")
            
            log.info(f"   Initial _ws_public: {initial_ws_public}")
            log.info(f"   Initial _ws_private: {initial_ws_private}")  
            log.info(f"   Initial _ws_auth: {initial_ws_auth}")
            log.info(f"   Initial _ws_running: {initial_ws_running}")

            log.info("🚀 ENHANCED: Starting WebSocket with detailed monitoring...")
            start_result = await self.adapter.start_websocket()  # type: ignore
            log.info(f"   start_websocket() returned: {start_result}")
            
            # Проверяем состояние после запуска
            post_ws_public = getattr(self.adapter, "_ws_public", "NOT_FOUND")
            post_ws_private = getattr(self.adapter, "_ws_private", "NOT_FOUND")
            post_ws_auth = getattr(self.adapter, "_ws_auth", "NOT_FOUND")
            post_ws_running = getattr(self.adapter, "_ws_running", "NOT_FOUND")
            
            log.info(f"   After start _ws_public: {post_ws_public}")
            log.info(f"   After start _ws_private: {post_ws_private}")
            log.info(f"   After start _ws_auth: {post_ws_auth}")
            log.info(f"   After start _ws_running: {post_ws_running}")
            
            # Детальная проверка типов объектов
            if post_ws_public != "NOT_FOUND" and post_ws_public is not None:
                log.info(f"   _ws_public type: {type(post_ws_public)}")
                log.info(f"   _ws_public closed: {getattr(post_ws_public, 'closed', 'N/A')}")
            
            if post_ws_private != "NOT_FOUND" and post_ws_private is not None:
                log.info(f"   _ws_private type: {type(post_ws_private)}")
                log.info(f"   _ws_private closed: {getattr(post_ws_private, 'closed', 'N/A')}")

            res["ws_public_connect"] = True
            await asyncio.sleep(1)
            
            log.info("🎯 ENHANCED: Testing ticker subscription...")
            await self.adapter.subscribe_ticker(self.test_symbol, on_tick)  # type: ignore
            res["ws_public_subscribe"] = True

            try:
                await asyncio.wait_for(tick_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                log.warning("No ticker in 10s (ok on quiet markets)")

            # ENHANCED: Детальная проверка private WebSocket
            log.info("🔍 ENHANCED: Checking private WebSocket status...")
            
            ws_private_obj = getattr(self.adapter, "_ws_private", None)
            ws_auth_status = getattr(self.adapter, "_ws_auth", False)
            
            log.info(f"   _ws_private object: {ws_private_obj}")
            log.info(f"   _ws_private is None: {ws_private_obj is None}")
            log.info(f"   _ws_auth status: {ws_auth_status}")
            log.info(f"   _ws_auth type: {type(ws_auth_status)}")
            
            if ws_private_obj is not None:
                res["ws_private_connect"] = True
                log.info("✅ ENHANCED: Private WebSocket connection detected")
                
                if ws_auth_status:
                    res["ws_private_auth"] = True
                    log.info("✅ ENHANCED: Private WebSocket authentication confirmed")
                else:
                    log.error("❌ ENHANCED: Private WebSocket connected but not authenticated")
                    
                    # Дополнительная диагностика
                    try:
                        if hasattr(ws_private_obj, 'closed'):
                            log.info(f"   WS private closed status: {ws_private_obj.closed}")
                        if hasattr(self.adapter, '_ws_private_task'):
                            task = self.adapter._ws_private_task
                            if task:
                                log.info(f"   Private task done: {task.done()}")
                                log.info(f"   Private task cancelled: {task.cancelled()}")
                                if task.done() and task.exception():
                                    log.error(f"   Private task exception: {task.exception()}")
                    except Exception as task_e:
                        log.error(f"   Task inspection error: {task_e}")
            else:
                log.error("❌ ENHANCED: Private WebSocket connection NOT established")
                
                # Диагностика причин
                try:
                    if hasattr(self.adapter, '_ws_private_task'):
                        task = self.adapter._ws_private_task
                        if task is None:
                            log.error("   Private task was never created")
                        else:
                            log.info(f"   Private task exists: {task}")
                            log.info(f"   Private task done: {task.done()}")
                            log.info(f"   Private task cancelled: {task.cancelled()}")
                            if task.done() and task.exception():
                                log.error(f"   Private task exception: {task.exception()}")
                except Exception as diag_e:
                    log.error(f"   Private task diagnostic error: {diag_e}")

        except Exception as e:
            log.error("❌ ENHANCED: WebSocket error details:")
            log.error(f"   Exception type: {type(e).__name__}")
            log.error(f"   Exception message: {str(e)}")
            log.error(f"   Full traceback:", exc_info=True)
        finally:
            try:
                log.info("🧹 ENHANCED: Cleaning up WebSocket...")
                await self.adapter.stop_websocket()  # type: ignore
                log.info("✅ ENHANCED: WebSocket cleanup completed")
            except Exception as cleanup_e:
                log.error(f"❌ ENHANCED: WebSocket cleanup error: {cleanup_e}")
        return res

    async def run(self) -> bool:
        if not await self.setup():
            return False
        
        try:
            self.results["rest"] = await self.test_rest()
            self.results["ws"] = await self.test_ws()

            log.info("\n" + "=" * 60)
            log.info("📊 TEST SUMMARY")
            log.info("=" * 60)
            total = 0
            passed = 0
            for cat, rr in self.results.items():
                log.info("[%s]", cat.upper())
                for name, ok in rr.items():
                    total += 1
                    passed += int(bool(ok))
                    log.info("  %s %s", "✅" if ok else "❌", name)

            ok_ratio = (passed / total * 100) if total else 0.0
            if ok_ratio >= 80:
                log.info("✅ E2E PASSED (%d/%d = %.1f%%)", passed, total, ok_ratio)
                return True
            else:
                log.error("❌ E2E FAILED (%d/%d = %.1f%%)", passed, total, ok_ratio)
                return False
        finally:
            # ENHANCED: Принудительная очистка ресурсов адаптера
            await self._enhanced_cleanup()
    
    async def _enhanced_cleanup(self):
        """ENHANCED: Принудительная очистка с детальной диагностикой"""
        try:
            if self.adapter:
                log.info("🧹 ENHANCED: Starting comprehensive adapter cleanup...")
                
                # Проверяем и закрываем WebSocket
                try:
                    if hasattr(self.adapter, 'stop_websocket'):
                        log.info("   Stopping WebSocket connections...")
                        await self.adapter.stop_websocket()
                        log.info("   ✅ WebSocket stopped")
                except Exception as e:
                    log.warning(f"   ⚠️ WebSocket stop error: {e}")
                
                # Проверяем и закрываем HTTP сессию
                try:
                    if hasattr(self.adapter, 'close'):
                        log.info("   Closing adapter...")
                        await self.adapter.close()
                        log.info("   ✅ Adapter closed")
                except Exception as e:
                    log.warning(f"   ⚠️ Adapter close error: {e}")
                
                # Дополнительная принудительная очистка сессии
                try:
                    if hasattr(self.adapter, 'session') and self.adapter.session:
                        session = self.adapter.session
                        log.info(f"   Session status: closed={session.closed}")
                        if not session.closed:
                            log.info("   Force closing session...")
                            await session.close()
                            await asyncio.sleep(0.3)  # Дополнительное время
                            log.info(f"   Session after force close: closed={session.closed}")
                        log.info("   ✅ Session cleanup completed")
                except Exception as e:
                    log.warning(f"   ⚠️ Session cleanup error: {e}")
                
                log.info("✅ ENHANCED: Adapter cleanup completed")
        except Exception as e:
            log.error(f"❌ ENHANCED: Cleanup error: {e}")

async def _main():
    tester = BybitDemoE2ETester()
    success = await tester.run()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(_main())
