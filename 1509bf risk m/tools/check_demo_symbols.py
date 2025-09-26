#!/usr/bin/env python3
"""
Проверка доступных символов и их правильных названий для demo режима
"""

import os
import time
import hmac
import hashlib
import base64
import json
import requests

GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


class BitgetSymbolChecker:
    def __init__(self):
        self.api_key = os.getenv("BITGET_TESTNET_API_KEY", "")
        self.api_secret = os.getenv("BITGET_TESTNET_API_SECRET", "")
        self.api_passphrase = os.getenv("BITGET_TESTNET_API_PASSPHRASE", "")
        self.base_url = "https://api.bitget.com"
        
    def generate_signature(self, timestamp, method, path, query="", body=""):
        message = timestamp + method.upper() + path
        if query:
            message += f"?{query}"
        if body:
            message += body
        mac = hmac.new(
            bytes(self.api_secret, encoding='utf8'),
            bytes(message, encoding='utf-8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode('utf-8')
        
    def make_request(self, method, path, query="", body="", use_paptrading=True):
        timestamp = str(int(time.time() * 1000))
        signature = self.generate_signature(timestamp, method, path, query, body)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
            "locale": "en-US"
        }
        
        if use_paptrading:
            headers["paptrading"] = "1"
        
        url = f"{self.base_url}{path}"
        if query:
            url += f"?{query}"
            
        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=10)
            else:
                response = requests.post(url, headers=headers, data=body, timeout=10)
            return response.json()
        except Exception as e:
            return {"error": str(e)}
            
    def check_symbols(self):
        print(f"\n{BLUE}{'=' * 60}{RESET}")
        print(f"{BLUE}ПРОВЕРКА ДОСТУПНЫХ СИМВОЛОВ ДЛЯ DEMO{RESET}")
        print(f"{BLUE}{'=' * 60}{RESET}\n")
        
        # Получаем список контрактов
        result = self.make_request("GET", "/api/v2/mix/market/contracts", "productType=susdt-futures")
        
        if result.get("code") == "00000":
            contracts = result.get("data", [])
            print(f"{GREEN}✅ Найдено контрактов: {len(contracts)}{RESET}\n")
            
            valid_symbols = []
            
            for contract in contracts:
                symbol = contract.get("symbol", "")
                base = contract.get("baseCoin", "")
                quote = contract.get("quoteCoin", "")
                status = contract.get("symbolStatus", "")
                max_lever = contract.get("maxLever", "")
                min_trade = contract.get("minTradeNum", "")
                
                print(f"{YELLOW}Символ: {symbol}{RESET}")
                print(f"  Base: {base}, Quote: {quote}")
                print(f"  Статус: {status}")
                print(f"  Макс. плечо: {max_lever}")
                print(f"  Мин. размер: {min_trade}")
                
                if status == "normal":
                    valid_symbols.append(symbol)
                    
                    # Тестируем размещение ордера
                    print(f"  {BLUE}Тест ордера...{RESET}")
                    
                    # Получаем тикер
                    ticker_result = self.make_request(
                        "GET", 
                        "/api/v2/mix/market/ticker",
                        f"symbol={symbol}&productType=susdt-futures"
                    )
                    
                    if ticker_result.get("code") == "00000":
                        ticker_data = ticker_result.get("data", [])
                        if ticker_data:
                            ticker = ticker_data[0] if isinstance(ticker_data, list) else ticker_data
                            price = float(ticker.get("lastPr", 0))
                            
                            if price > 0:
                                # Пробуем разместить ордер
                                test_price = price * 0.8
                                order_body = json.dumps({
                                    "symbol": symbol,
                                    "productType": "susdt-futures",
                                    "marginMode": "crossed",
                                    "marginCoin": "USDT",
                                    "side": "buy",
                                    "orderType": "limit",
                                    "size": str(min_trade),
                                    "price": str(test_price),
                                    "force": "GTC",
                                    "clientOid": f"test_{int(time.time() * 1000)}"
                                })
                                
                                order_result = self.make_request(
                                    "POST",
                                    "/api/v2/mix/order/place-order",
                                    body=order_body
                                )
                                
                                if order_result.get("code") == "00000":
                                    order_id = order_result.get("data", {}).get("orderId")
                                    print(f"    {GREEN}✅ Ордер размещен: {order_id}{RESET}")
                                    
                                    # Отменяем ордер
                                    cancel_body = json.dumps({
                                        "symbol": symbol,
                                        "productType": "susdt-futures",
                                        "orderId": order_id
                                    })
                                    
                                    cancel_result = self.make_request(
                                        "POST",
                                        "/api/v2/mix/order/cancel-order",
                                        body=cancel_body
                                    )
                                    
                                    if cancel_result.get("code") == "00000":
                                        print(f"    {GREEN}✅ Ордер отменен{RESET}")
                                else:
                                    error_msg = order_result.get("msg", "Unknown error")
                                    print(f"    {RED}❌ Ошибка ордера: {error_msg}{RESET}")
                
                print("-" * 40)
            
            print(f"\n{GREEN}РАБОЧИЕ СИМВОЛЫ ДЛЯ DEMO:{RESET}")
            for symbol in valid_symbols:
                print(f"  • {symbol}")
                
            # Проверяем альтернативные названия
            print(f"\n{YELLOW}ПРОВЕРКА АЛЬТЕРНАТИВНЫХ НАЗВАНИЙ:{RESET}")
            
            test_symbols = [
                "BTCUSDT",
                "ETHUSDT",
                "XRPUSDT",
                "BTCSUSDT",
                "ETHSUSDT",
                "XRPSUSDT"
            ]
            
            for symbol in test_symbols:
                print(f"\nТест {symbol}:")
                
                # Тикер
                ticker_result = self.make_request(
                    "GET",
                    "/api/v2/mix/market/ticker",
                    f"symbol={symbol}&productType=susdt-futures"
                )
                
                if ticker_result.get("code") == "00000":
                    print(f"  {GREEN}✅ Тикер работает{RESET}")
                else:
                    print(f"  {RED}❌ Тикер: {ticker_result.get('msg')}{RESET}")
                
                # Плечо
                leverage_body = json.dumps({
                    "symbol": symbol,
                    "productType": "susdt-futures",
                    "marginCoin": "USDT",
                    "leverage": "10"
                })
                
                leverage_result = self.make_request(
                    "POST",
                    "/api/v2/mix/account/set-leverage",
                    body=leverage_body
                )
                
                if leverage_result.get("code") == "00000":
                    print(f"  {GREEN}✅ Плечо работает{RESET}")
                else:
                    print(f"  {RED}❌ Плечо: {leverage_result.get('msg')}{RESET}")
                    
        else:
            print(f"{RED}❌ Ошибка получения контрактов: {result.get('msg')}{RESET}")


if __name__ == "__main__":
    checker = BitgetSymbolChecker()
    checker.check_symbols()
