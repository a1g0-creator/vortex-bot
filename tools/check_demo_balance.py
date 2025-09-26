#!/usr/bin/env python3
"""
Проверка различных методов получения баланса demo счета Bitget
"""

import os
import time
import hmac
import hashlib
import base64
import json
import requests

# Цвета
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


class BitgetBalanceChecker:
    def __init__(self):
        self.api_key = os.getenv("BITGET_TESTNET_API_KEY", "")
        self.api_secret = os.getenv("BITGET_TESTNET_API_SECRET", "")
        self.api_passphrase = os.getenv("BITGET_TESTNET_API_PASSPHRASE", "")
        self.base_url = "https://api.bitget.com"
        
    def generate_signature(self, timestamp, method, path, query="", body=""):
        """Генерация подписи"""
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
        """Универсальный запрос"""
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
            
    def test_futures_balance(self):
        """Тест различных методов получения баланса фьючерсов"""
        print(f"\n{BLUE}═══════════════════════════════════════════════{RESET}")
        print(f"{BLUE}ТЕСТ БАЛАНСОВ ФЬЮЧЕРСНОГО СЧЕТА{RESET}")
        print(f"{BLUE}═══════════════════════════════════════════════{RESET}\n")
        
        tests = [
            {
                "name": "1. accounts с susdt-futures",
                "path": "/api/v2/mix/account/accounts",
                "query": "productType=susdt-futures"
            },
            {
                "name": "2. accounts с SUSDT-FUTURES",
                "path": "/api/v2/mix/account/accounts", 
                "query": "productType=SUSDT-FUTURES"
            },
            {
                "name": "3. account (единичный) с BTCUSDT",
                "path": "/api/v2/mix/account/account",
                "query": "symbol=BTCUSDT&productType=susdt-futures"
            },
            {
                "name": "4. account с marginCoin=SUSDT",
                "path": "/api/v2/mix/account/account",
                "query": "symbol=BTCUSDT&productType=susdt-futures&marginCoin=SUSDT"
            },
            {
                "name": "5. account с marginCoin=USDT",
                "path": "/api/v2/mix/account/account",
                "query": "symbol=BTCUSDT&productType=susdt-futures&marginCoin=USDT"
            },
            {
                "name": "6. assets (активы)",
                "path": "/api/v2/mix/account/assets",
                "query": "productType=susdt-futures"
            }
        ]
        
        for test in tests:
            print(f"{YELLOW}Тест: {test['name']}{RESET}")
            print(f"Path: {test['path']}")
            print(f"Query: {test['query']}")
            
            result = self.make_request("GET", test["path"], test["query"])
            
            if result.get("code") == "00000":
                data = result.get("data")
                if data:
                    print(f"{GREEN}✅ Успех!{RESET}")
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                coin = item.get("marginCoin", item.get("coin", "?"))
                                available = item.get("available", item.get("availableBalance", "0"))
                                equity = item.get("equity", item.get("totalBalance", "0"))
                                print(f"  {coin}: available={available}, equity={equity}")
                    elif isinstance(data, dict):
                        coin = data.get("marginCoin", data.get("coin", "?"))
                        available = data.get("available", data.get("availableBalance", "0"))
                        equity = data.get("equity", data.get("totalBalance", "0"))
                        print(f"  {coin}: available={available}, equity={equity}")
                else:
                    print(f"{YELLOW}⚠️ Пустой ответ (data пустая){RESET}")
            else:
                print(f"{RED}❌ Ошибка: {result.get('code')} - {result.get('msg')}{RESET}")
            
            print("-" * 50)
            
    def test_unified_balance(self):
        """Тест единого счета"""
        print(f"\n{BLUE}═══════════════════════════════════════════════{RESET}")
        print(f"{BLUE}ТЕСТ ЕДИНОГО СЧЕТА{RESET}")
        print(f"{BLUE}═══════════════════════════════════════════════{RESET}\n")
        
        tests = [
            {
                "name": "1. all-account-balance",
                "path": "/api/v2/account/all-account-balance",
                "query": ""
            },
            {
                "name": "2. unified balance",
                "path": "/api/v2/account/unified-balance",
                "query": ""
            },
            {
                "name": "3. asset overview",
                "path": "/api/v2/account/asset-overview",
                "query": ""
            }
        ]
        
        for test in tests:
            print(f"{YELLOW}Тест: {test['name']}{RESET}")
            
            # Пробуем с paptrading
            print("С paptrading=1:")
            result = self.make_request("GET", test["path"], test["query"], use_paptrading=True)
            self.print_result(result)
            
            # Пробуем без paptrading
            print("Без paptrading:")
            result = self.make_request("GET", test["path"], test["query"], use_paptrading=False)
            self.print_result(result)
            
            print("-" * 50)
            
    def test_spot_balance(self):
        """Тест спотового счета"""
        print(f"\n{BLUE}═══════════════════════════════════════════════{RESET}")
        print(f"{BLUE}ТЕСТ СПОТОВОГО СЧЕТА{RESET}")
        print(f"{BLUE}═══════════════════════════════════════════════{RESET}\n")
        
        # Спот обычно не поддерживает demo, но проверим
        result = self.make_request("GET", "/api/v2/spot/account/assets", "", use_paptrading=False)
        
        if result.get("code") == "00000":
            print(f"{GREEN}✅ Спотовый счет доступен{RESET}")
            assets = result.get("data", [])
            for asset in assets[:5]:
                if float(asset.get("available", 0)) > 0:
                    print(f"  {asset['coin']}: {asset['available']}")
        else:
            print(f"{RED}Спот недоступен: {result.get('msg')}{RESET}")
            
    def print_result(self, result):
        """Вывод результата"""
        if result.get("code") == "00000":
            data = result.get("data")
            if data:
                print(f"{GREEN}  ✅ Успех: {json.dumps(data, indent=2)[:200]}...{RESET}")
            else:
                print(f"{YELLOW}  ⚠️ Пустые данные{RESET}")
        else:
            print(f"{RED}  ❌ {result.get('code')}: {result.get('msg')}{RESET}")
            
    def test_funding_account(self):
        """Тест funding аккаунта"""
        print(f"\n{BLUE}═══════════════════════════════════════════════{RESET}")
        print(f"{BLUE}ТЕСТ FUNDING СЧЕТА{RESET}")
        print(f"{BLUE}═══════════════════════════════════════════════{RESET}\n")
        
        result = self.make_request("GET", "/api/v2/account/funding-assets", "", use_paptrading=True)
        self.print_result(result)
        
    def run_all_tests(self):
        """Запуск всех тестов"""
        print(f"\n{BLUE}{'=' * 60}{RESET}")
        print(f"{BLUE}ПРОВЕРКА ВСЕХ МЕТОДОВ ПОЛУЧЕНИЯ БАЛАНСА BITGET{RESET}")
        print(f"{BLUE}{'=' * 60}{RESET}")
        
        # Фьючерсы
        self.test_futures_balance()
        
        # Единый счет
        self.test_unified_balance()
        
        # Спот
        self.test_spot_balance()
        
        # Funding
        self.test_funding_account()
        
        print(f"\n{GREEN}РЕКОМЕНДАЦИИ:{RESET}")
        print("1. Используйте метод, который возвращает данные")
        print("2. Для demo фьючерсов обычно нужен productType=susdt-futures")
        print("3. Убедитесь что на demo счете есть средства")
        print("4. Проверьте правильный marginCoin (USDT или SUSDT)")


if __name__ == "__main__":
    checker = BitgetBalanceChecker()
    checker.run_all_tests()
