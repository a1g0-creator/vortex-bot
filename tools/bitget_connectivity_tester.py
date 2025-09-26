#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bitget connectivity & balance tester (prod-grade)
- Loads API creds from .env
- Tests public /api/v2/public/time (no paptrading)
- Tests private /api/v2/mix/account/accounts and /api/v2/mix/account/account
- Tries multiple combinations for productType, symbol, marginCoin
- Adds 'paptrading: 1' only for signed requests on demo
- Proper Bitget V2 signature (HMAC-SHA256 -> base64)
"""

import os
import sys
import json
import hmac
import time
import base64
import hashlib
import asyncio
import argparse
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import urlencode

import aiohttp

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None


BITGET_REST_BASE = "https://api.bitget.com"

# Test matrices
PRODUCT_TYPES_DEMO = ["USDT-FUTURES", "susdt-futures", "SUSDT_UMCBL", "simulated-susdt-futures"]
PRODUCT_TYPES_MAIN = ["USDT-FUTURES"]
SYMBOL_VARIANTS = [
    "btcusdt",   # v2 пример из доков
    "BTCUSDT",   # ALL CAPS
    "BtcUsdt",   # запрошенный вариант: первые буквы токенов заглавные
]
MARGIN_COINS = ["usdt", "USDT", "SUSDT"]  # тестируем оба регистра + SUSDT на демо


def load_env():
    if load_dotenv is not None:
        load_dotenv(override=False)

    env = {
        "bitget_demo": {
            "key": os.getenv("BITGET_TESTNET_API_KEY", "") or "",
            "secret": os.getenv("BITGET_TESTNET_API_SECRET", "") or "",
            "passphrase": os.getenv("BITGET_TESTNET_API_PASSPHRASE", "") or "",
        },
        "bitget_main": {
            "key": os.getenv("BITGET_MAINNET_API_KEY", "") or "",
            "secret": os.getenv("BITGET_MAINNET_API_SECRET", "") or "",
            "passphrase": os.getenv("BITGET_MAINNET_API_PASSPHRASE", "") or "",
        },
    }
    return env


def build_signature(secret: str, prehash: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


def auth_headers_v2(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    method: str,
    path: str,
    query_string: str,
    body_str: str,
) -> Dict[str, str]:
    ts = str(int(time.time() * 1000))
    prehash = ts + method.upper() + path
    if query_string:
        prehash += "?" + query_string
    if body_str:
        prehash += body_str
    sign = build_signature(api_secret, prehash)
    return {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sign,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": api_passphrase,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "locale": "en-US",
    }


async def make_request(
    session: aiohttp.ClientSession,
    method: str,
    endpoint: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    signed: bool = False,
    creds: Optional[Dict[str, str]] = None,
    demo: bool = False,
) -> Tuple[int, str, Optional[Any]]:
    url = f"{BITGET_REST_BASE.rstrip('/')}{endpoint}"
    params = params or {}
    query_string = urlencode(params) if params else ""
    if query_string:
        url = f"{url}?{query_string}"

    body_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False) if data is not None else ""

    headers: Dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "locale": "en-US",
    }

    if signed:
        assert creds is not None, "signed request without creds"
        headers.update(
            auth_headers_v2(
                api_key=creds["key"],
                api_secret=creds["secret"],
                api_passphrase=creds["passphrase"],
                method=method,
                path=endpoint,
                query_string=query_string,
                body_str=body_str,
            )
        )
        # В DEMO добавляем paptrading только на приватные
        if demo:
            headers["paptrading"] = "1"

    async with session.request(
        method.upper(), url, headers=headers, data=body_str if body_str else None, timeout=30
    ) as resp:
        text = await resp.text()
        try:
            payload = json.loads(text)
        except Exception:
            return resp.status, text, None

        # Обёртка Bitget
        code = str(payload.get("code", ""))
        data_field = payload.get("data", None)
        return resp.status, code, data_field


async def test_public_time(session: aiohttp.ClientSession) -> bool:
    status, code, data = await make_request(session, "GET", "/api/v2/public/time", signed=False)
    ok = status == 200 and (code in ("00000", "success", "0")) and isinstance(data, dict) and "serverTime" in data
    print(f"[PUBLIC] /api/v2/public/time -> HTTP {status}, code={code}, data_keys={list(data.keys()) if isinstance(data, dict) else type(data)}")
    return ok


async def test_accounts_list(session: aiohttp.ClientSession, creds: Dict[str, str], demo: bool, product_types: List[str]) -> List[Tuple[str, Any]]:
    print("\n[PRIVATE] /api/v2/mix/account/accounts (list) — пробуем productType варианты")
    successes: List[Tuple[str, Any]] = []
    for pt in product_types:
        status, code, data = await make_request(
            session,
            "GET",
            "/api/v2/mix/account/accounts",
            params={"productType": pt},
            signed=True,
            creds=creds,
            demo=demo,
        )
        ok = status == 200 and (code in ("00000", "success", "0"))
        print(f"  productType={pt:<24} -> HTTP {status}, code={code}, type={type(data).__name__}")
        if ok and data:
            successes.append((pt, data))
    return successes


async def test_single_account(
    session: aiohttp.ClientSession,
    creds: Dict[str, str],
    demo: bool,
    product_types: List[str],
    symbols: List[str],
    margin_coins: List[str],
) -> List[Tuple[str, str, str, Any]]:
    print("\n[PRIVATE] /api/v2/mix/account/account (single) — matrix productType × symbol × marginCoin")
    wins: List[Tuple[str, str, str, Any]] = []
    for pt in product_types:
        for sym in symbols:
            for mc in margin_coins:
                status, code, data = await make_request(
                    session,
                    "GET",
                    "/api/v2/mix/account/account",
                    params={"productType": pt, "symbol": sym, "marginCoin": mc},
                    signed=True,
                    creds=creds,
                    demo=demo,
                )
                ok = status == 200 and (code in ("00000", "success", "0")) and data
                equity = None
                if isinstance(data, dict):
                    try:
                        equity = float(data.get("equity", 0) or 0)
                    except Exception:
                        equity = None
                print(
                    f"  pt={pt:<24} sym={sym:<10} mc={mc:<6} -> HTTP {status}, code={code}, "
                    f"equity={equity if equity is not None else 'n/a'}"
                )
                if ok:
                    wins.append((pt, sym, mc, data))
    return wins


async def main():
    parser = argparse.ArgumentParser(description="Bitget connectivity & balance tester")
    parser.add_argument("--env", choices=["demo", "main"], default="demo", help="Environment: demo or main")
    args = parser.parse_args()

    env = load_env()
    if args.env == "demo":
        creds = env["bitget_demo"]
        demo = True
        product_types = PRODUCT_TYPES_DEMO
    else:
        creds = env["bitget_main"]
        demo = False
        product_types = PRODUCT_TYPES_MAIN

    # Валидация кредов
    missing = [k for k, v in creds.items() if not v]
    if missing:
        print(f"[FATAL] Missing credentials for {args.env}: {missing}. Check your .env.")
        sys.exit(2)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers={"Accept": "application/json", "locale": "en-US"}) as session:
        print(f"=== Bitget Tester | env={args.env} | demo={demo} ===\nREST: {BITGET_REST_BASE}\n")

        # 1) PUBLIC
        pub_ok = await test_public_time(session)
        if not pub_ok:
            print("[ERROR] Public time failed. Check network / base URL.")
            # не выходим — продолжаем тесты приватных, вдруг paptrading и подпись ок

        # 2) PRIVATE list
        acc_list_results = await test_accounts_list(session, creds, demo, product_types)

        # 3) PRIVATE single (матрица)
        single_results = await test_single_account(session, creds, demo, product_types, SYMBOL_VARIANTS, MARGIN_COINS)

        print("\n=== SUMMARY ===")
        if acc_list_results:
            first_pt, data = acc_list_results[0]
            print(f"[OK] accounts(list) worked for productType={first_pt}. Items: {len(data) if isinstance(data, list) else '1'}")
        else:
            print("[WARN] accounts(list) didn't return data for any productType")

        if single_results:
            # ищем лучшую запись (с ненулевым equity)
            best = None
            for pt, sym, mc, node in single_results:
                try:
                    eq = float(node.get("equity", 0) or 0) if isinstance(node, dict) else 0.0
                except Exception:
                    eq = 0.0
                if best is None or eq > best[4]:
                    best = (pt, sym, mc, node, eq)
            pt, sym, mc, node, eq = best
            print(f"[OK] single(account) best match: productType={pt}, symbol={sym}, marginCoin={mc}, equity={eq:.4f}")
        else:
            print("[WARN] single(account) didn't succeed for any combination")

        # Успех, если хотя бы что-то приватное вернулось корректно
        if acc_list_results or single_results:
            sys.exit(0)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
