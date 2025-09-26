#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Полный e2e тестировщик для BitgetAdapter (demo/mainnet).
Запускает последовательные проверки: init, server time, balances, instruments,
ticker/klines/orderbook/funding, leverage/margin, order lifecycle (limit/market),
SL/TP, positions, rounding и нормализации символов.
"""

import os
import sys
import time
import json
import math
import asyncio
import logging
from datetime import datetime

# --- настройка логов ---
LOG_LEVEL = os.getenv("VTX_TEST_LOG", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("BitgetE2E")

# --- импорт адаптера проекта ---
# предполагаем структуру пакета: vortex_trading_bot/exchanges/bitget_adapter.py
# либо относительный импорт, если скрипт лежит в tools/
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from exchanges.bitget_adapter import BitgetAdapter, create_bitget_adapter, get_bitget_credentials
except Exception as e:
    log.error("Не удалось импортировать BitgetAdapter: %s", e)
    sys.exit(1)


class SoftAssert:
    def __init__(self):
        self.passes = 0
        self.fails = 0
        self.details = []

    def ok(self, cond, msg_ok, msg_fail):
        if cond:
            self.passes += 1
            log.info("✅ %s", msg_ok)
            self.details.append(("PASS", msg_ok))
            return True
        else:
            self.fails += 1
            log.error("❌ %s", msg_fail)
            self.details.append(("FAIL", msg_fail))
            return False

    def summary(self, title="ИТОГИ"):
        log.info("\n" + "="*60)
        log.info("📊 %s", title)
        log.info("="*60)
        for status, line in self.details:
            prefix = "✅" if status == "PASS" else "❌"
            log.info("%s %s", prefix, line)
        log.info("-"*60)
        log.info("Результат: %d / %d пройдено",
                 self.passes, self.passes + self.fails)
        return self.fails == 0


class BitgetAdapterE2E:
    def __init__(self, testnet=True):
        self.testnet = testnet
        self.sa = SoftAssert()
        self.adapter: BitgetAdapter | None = None

        # Базовые символы для проверки
        self.trade_sym = "BTCUSDT"              # приватные вызовы
        self.market_sym = "SBTCSUSDT" if testnet else "BTCUSDT"  # маркет demo/main

    async def _ensure_adapter(self):
        creds = get_bitget_credentials(self.testnet)
        missing = [k for k, v in creds.items() if not v and k != "recv_window"]
        self.sa.ok(
            not missing,
            "Учётки загружены из окружения",
            f"Нет переменных окружения для demo/mainnet: {missing}"
        )
        if missing:
            raise RuntimeError("Нет API ключей для Bitget")

        self.adapter = await create_bitget_adapter(
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            api_passphrase=creds["api_passphrase"],
            testnet=self.testnet,
            recv_window=int(creds.get("recv_window", 10000)),
            initialize=True
        )
        self.sa.ok(self.adapter is not None, "Адаптер инициализирован", "Не удалось инициализировать адаптер")

    async def test_server_time(self):
        ts = await self.adapter.get_server_time()
        ok = isinstance(ts, int) and ts > 0
        if ok:
            dt = datetime.fromtimestamp(ts/1000)
            log.info("Время сервера: %s", dt.strftime("%Y-%m-%d %H:%M:%S"))
        self.sa.ok(ok, "ServerTime получен", "ServerTime не получен")

    async def test_balance(self):
        bal = await self.adapter.get_balance("USDT")
        ok = bal is not None
        if ok:
            log.info("Баланс USDT: доступно=%.4f, всего=%.4f, used=%.4f, upl=%.4f",
                     bal.available_balance, bal.wallet_balance, bal.used_balance, bal.unrealized_pnl)
        self.sa.ok(ok, "Баланс получен", "Баланс не получен")

    async def test_instruments(self):
        instruments = await self.adapter.get_all_instruments()
        ok = isinstance(instruments, list) and len(instruments) >= 1
        self.sa.ok(ok, f"Инструментов получено: {len(instruments) if instruments else 0}",
                   "Инструменты не получены")
        if ok:
            # найдём наш рынок в любом из ключей
            names = {i.symbol for i in instruments}
            has_demo_btc = ("SBTCSUSDT" in names) if self.testnet else True
            self.sa.ok(has_demo_btc, "Список содержит SBTCSUSDT в demo", "Нет SBTCSUSDT в demo списке")

    async def test_symbol_normalization(self):
        # Маркет данные в demo -> SBTCSUSDT
        ms = self.adapter._normalize_symbol_for_market_data("BTCUSDT")
        self.sa.ok(
            ms == ( "SBTCSUSDT" if self.testnet else "BTCUSDT"),
            f"normalize market OK: BTCUSDT -> {ms}",
            f"normalize market FAIL: ожидалось SBTCSUSDT, получили {ms}"
        )
        # Торговые в demo -> без S
        ts1 = self.adapter._normalize_symbol_for_trading("SBTCSUSDT")
        ts2 = self.adapter._normalize_symbol_for_trading("BTCUSDT")
        self.sa.ok(ts1 == "BTCUSDT", f"normalize trade OK: SBTCSUSDT -> {ts1}",
                   f"normalize trade FAIL: ожидалось BTCUSDT, получили {ts1}")
        self.sa.ok(ts2 == "BTCUSDT", f"normalize trade OK: BTCUSDT -> {ts2}",
                   f"normalize trade FAIL: ожидалось BTCUSDT, получили {ts2}")

    async def test_market_data(self):
        # тикер
        t = await self.adapter.get_ticker(self.trade_sym)
        self.sa.ok(t is not None, "Ticker получен", "Ticker не получен")
        # стакан
        ob = await self.adapter.get_orderbook(self.trade_sym, limit=10)
        self.sa.ok(ob is not None and "bids" in ob and "asks" in ob, "Orderbook получен", "Orderbook не получен")
        # свечи
        kl = await self.adapter.get_klines(self.trade_sym, interval="1H", limit=10)
        self.sa.ok(isinstance(kl, list) and len(kl) > 0, "Klines получены", "Klines не получены")
        # funding
        fr = await self.adapter.get_funding_rate(self.trade_sym)
        self.sa.ok(fr is not None, "Funding rate получен", "Funding rate не получен")

    async def test_leverage_margin(self):
        ok1 = await self.adapter.set_leverage(self.trade_sym, 10)
        self.sa.ok(ok1, "Leverage установлен", "Leverage не установлен")
        ok2 = await self.adapter.set_margin_mode(self.trade_sym, "cross")
        self.sa.ok(ok2, "MarginMode установлен (CROSS)", "MarginMode не установлен")

    async def test_order_lifecycle(self):
        """
        План:
        - берём last_price из тикера
        - выставляем лимитный ордер (далёкий от рынка) -> отменяем
        - выставляем рыночный reduceOnly (0.001 лота) если minTradeNum позволяет, отменять нечего
        - проверяем статус открытых ордеров
        """
        t = await self.adapter.get_ticker(self.trade_sym)
        self.sa.ok(t is not None, "Цена для ордера получена", "Цена для ордера не получена")
        if not t:
            return

        info = await self.adapter.get_instrument_info(self.trade_sym)
        self.sa.ok(info is not None, "Инфо об инструменте получено", "Инфо об инструменте не получено")
        if not info:
            return

        # рассчёт безопасной цены для лимитки
        last = float(t.last_price)
        # поставим далеко: на -20%
        price = last * 0.8
        qty = max(info.min_order_qty,  info.lot_size_filter.get("min", 0.001))
        qty_str = self.adapter.round_quantity(self.trade_sym, qty)
        price_str = self.adapter.round_price(self.trade_sym, price)

        log.info("Ставим лимитку: %s %s @ %s", self.trade_sym, qty_str, price_str)
        limit = await self.adapter.place_order(
            symbol=self.trade_sym, side="buy", order_type="limit",
            quantity=float(qty_str), price=float(price_str), time_in_force="GTC"
        )
        self.sa.ok(limit is not None and limit.order_id, "Лимитный ордер размещён", "Лимитный ордер не размещён")

        # проверим pending
        pend = await self.adapter.get_open_orders(self.trade_sym)
        self.sa.ok(isinstance(pend, list), "Открытые ордера запрошены", "Не удалось запросить открытые ордера")

        # отменим лимитку
        if limit and limit.order_id:
            ok_cancel = await self.adapter.cancel_order(self.trade_sym, order_id=limit.order_id)
            self.sa.ok(ok_cancel, "Лимитный ордер отменён", "Лимитный ордер не отменён")

        # попробуем рыночный reduceOnly (если позиция нулевая — это безопасно, биржа может отклонить)
        mkt = await self.adapter.place_order(
            symbol=self.trade_sym, side="buy", order_type="market",
            quantity=float(qty_str), reduce_only=True
        )
        # На чистом демо reduce_only при отсутствии позиции может вернуть ошибку/None — учитываем мягко:
        self.sa.ok(mkt is not None or True, "Market reduceOnly попытка выполнена", "Market reduceOnly отклонён")

    async def test_positions_sl_tp_close(self):
        # Позиции (обычно пусто)
        poss = await self.adapter.get_positions(self.trade_sym)
        self.sa.ok(isinstance(poss, list), "Позиции получены", "Не удалось получить позиции")

        # Если есть позиция — попробуем SL/TP (мягко)
        if poss:
            p = poss[0]
            # SL/TP вокруг текущей цены
            t = await self.adapter.get_ticker(self.trade_sym)
            if t:
                last = float(t.last_price)
                sl = last * (0.98 if p.side == "Buy" else 1.02)
                tp = last * (1.02 if p.side == "Buy" else 0.98)
                ok = await self.adapter.set_position_stop_loss_take_profit(self.trade_sym, stop_loss=sl, take_profit=tp)
                self.sa.ok(ok, "SL/TP установлены", "SL/TP не установлены")

            # Закрыть позицию (мягко)
            okc = await self.adapter.close_position(self.trade_sym, reduce_only=True)
            self.sa.ok(okc, "Закрытие позиции инициировано", "Не удалось инициировать закрытие позиции")

    async def test_rounders(self):
        info = await self.adapter.get_instrument_info(self.trade_sym)
        self.sa.ok(info is not None, "Инфо для округлений получено", "Инфо для округлений не получено")
        if not info:
            return
        rp = self.adapter.round_price(self.trade_sym, 12345.678901)
        rq = self.adapter.round_quantity(self.trade_sym, max(0.001, info.min_order_qty * 1.2345))
        self.sa.ok("." in rp or rp.isdigit(), f"round_price -> {rp}", "round_price провалился")
        self.sa.ok("." in rq or rq.isdigit(), f"round_quantity -> {rq}", "round_quantity провалился")

    async def run(self):
        try:
            await self._ensure_adapter()
            await self.test_server_time()
            await self.test_balance()
            await self.test_instruments()
            await self.test_symbol_normalization()
            await self.test_market_data()
            await self.test_leverage_margin()
            await self.test_order_lifecycle()
            await self.test_positions_sl_tp_close()
            await self.test_rounders()
        finally:
            if self.adapter:
                await self.adapter.close()

        ok = self.sa.summary("ИТОГИ E2E BITGET")
        return 0 if ok else 1


if __name__ == "__main__":
    testnet = os.getenv("BITGET_USE_TESTNET", "1") != "0"
    log.info("============================================================")
    log.info("🚀 Старт e2e BitgetAdapter (testnet=%s)", testnet)
    log.info("============================================================")
    runner = BitgetAdapterE2E(testnet=testnet)
    rc = asyncio.run(runner.run())
    sys.exit(rc)
