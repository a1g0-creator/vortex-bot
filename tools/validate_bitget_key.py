#!/usr/bin/env python3
"""
Проверка различных вариантов работы с Bitget API
"""

import os
import time
import hmac
import hashlib
import base64
import json
import requests

# Цвета
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


class BitgetKeyValidator:
    def __init__(self):
        self.api_key = os.getenv("BITGET_TESTNET_API_KEY", "")
        self.api_secret = os.getenv("BITGET_TESTNET_API_SECRET", "")
        self.api_passphrase = os.getenv("BITGET_TESTNET_API_PASSPHRASE", "")
        
    def log(self, level, msg):
        colors = {"INFO": BLUE, "SUCCESS": GREEN, "ERROR": RED, "WARNING": YELLOW}
        print(f"{colors.get(level, '')}[{level}]{RESET} {msg}")
        
    def generate_signature(self, timestamp, method, path, query="", body=""):
        """Генерация подписи по документации Bitget"""
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
        d = mac.digest()
        return base64.b64encode(d).decode('utf-8')
        
    def test_demo_futures(self):
        """Тест demo фьючерсов с разными вариантами"""
        self.log("INFO", "=" * 60)
        self.log("INFO", "ТЕСТ 1: Demo Futures Account")
        
        tests = [
            {
                "name": "Demo с susdt-futures",
                "path": "/api/v2/mix/account/accounts",
                "query": "productType=susdt-futures",
                "headers_extra": {"paptrading": "1"}
            },
            {
                "name": "Demo с SUSDT-FUTURES (заглавные)",
                "path": "/api/v2/mix/account/accounts",
                "query": "productType=SUSDT-FUTURES",
                "headers_extra": {"paptrading": "1"}
            },
            {
                "name": "Demo без productType",
                "path": "/api/v2/mix/account/accounts",
                "query": "",
                "headers_extra": {"paptrading": "1"}
            },
            {
                "name": "Demo account info",
                "path": "/api/v2/mix/account/account",
                "query": "symbol=BTCUSDT&productType=susdt-futures",
                "headers_extra": {"paptrading": "1"}
            }
        ]
        
        for test in tests:
            self.log("INFO", f"\nПробуем: {test['name']}")
            
            timestamp = str(int(time.time() * 1000))
            signature = self.generate_signature(timestamp, "GET", test["path"], test["query"])
            
            headers = {
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": signature,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": self.api_passphrase,
                "Content-Type": "application/json",
                "locale": "en-US"
            }
            headers.update(test.get("headers_extra", {}))
            
            url = f"https://api.bitget.com{test['path']}"
            if test["query"]:
                url += f"?{test['query']}"
                
            self.log("INFO", f"URL: {url}")
            self.log("INFO", f"paptrading: {headers.get('paptrading', 'НЕТ')}")
            
            try:
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()
                
                if data.get("code") == "00000":
                    self.log("SUCCESS", f"✅ УСПЕХ! {test['name']}")
                    if data.get("data"):
                        self.log("INFO", f"Data: {json.dumps(data['data'], indent=2)}")
                    return True
                else:
                    self.log("ERROR", f"❌ {data.get('code')}: {data.get('msg')}")
                    
            except Exception as e:
                self.log("ERROR", f"Exception: {e}")
                
        return False
        
    def test_spot_account(self):
        """Тест спотового аккаунта"""
        self.log("INFO", "=" * 60)
        self.log("INFO", "ТЕСТ 2: Spot Account")
        
        timestamp = str(int(time.time() * 1000))
        path = "/api/v2/spot/account/assets"
        
        signature = self.generate_signature(timestamp, "GET", path)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
            "locale": "en-US"
        }
        
        url = f"https://api.bitget.com{path}"
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get("code") == "00000":
                self.log("SUCCESS", "✅ Спотовый аккаунт доступен")
                assets = data.get("data", [])
                for asset in assets:
                    if float(asset.get("available", 0)) > 0:
                        self.log("INFO", f"  {asset['coin']}: {asset['available']}")
                return True
            else:
                self.log("ERROR", f"Spot: {data.get('code')}: {data.get('msg')}")
                
        except Exception as e:
            self.log("ERROR", f"Exception: {e}")
            
        return False
        
    def test_unified_account(self):
        """Тест единого счета"""
        self.log("INFO", "=" * 60)
        self.log("INFO", "ТЕСТ 3: Unified Account")
        
        timestamp = str(int(time.time() * 1000))
        path = "/api/v2/account/all-account-balance"
        
        signature = self.generate_signature(timestamp, "GET", path)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
            "locale": "en-US"
        }
        
        url = f"https://api.bitget.com{path}"
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get("code") == "00000":
                self.log("SUCCESS", "✅ Единый счет доступен")
                return True
            else:
                self.log("ERROR", f"Unified: {data.get('code')}: {data.get('msg')}")
                
        except Exception as e:
            self.log("ERROR", f"Exception: {e}")
            
        return False
        
    def test_different_base_urls(self):
        """Тест разных базовых URL"""
        self.log("INFO", "=" * 60)
        self.log("INFO", "ТЕСТ 4: Разные базовые URL")
        
        urls = [
            "https://api.bitget.com",
            "https://api-demo.bitget.com",  # Может существовать отдельный demo URL
        ]
        
        for base_url in urls:
            self.log("INFO", f"\nПробуем URL: {base_url}")
            
            timestamp = str(int(time.time() * 1000))
            path = "/api/v2/mix/account/accounts"
            query = "productType=susdt-futures"
            
            signature = self.generate_signature(timestamp, "GET", path, query)
            
            headers = {
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": signature,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": self.api_passphrase,
                "Content-Type": "application/json",
                "locale": "en-US",
                "paptrading": "1"
            }
            
            url = f"{base_url}{path}?{query}"
            
            try:
                response = requests.get(url, headers=headers, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("code") == "00000":
                        self.log("SUCCESS", f"✅ Работает с {base_url}")
                        return True
                    else:
                        self.log("ERROR", f"{data.get('msg')}")
                else:
                    self.log("ERROR", f"HTTP {response.status_code}")
                    
            except requests.exceptions.ConnectionError:
                self.log("ERROR", f"Не удалось подключиться к {base_url}")
            except Exception as e:
                self.log("ERROR", f"Exception: {e}")
                
        return False
        
    def verify_key_format(self):
        """Проверка формата ключей"""
        self.log("INFO", "=" * 60)
        self.log("INFO", "ПРОВЕРКА ФОРМАТА КЛЮЧЕЙ")
        
        self.log("INFO", f"API Key длина: {len(self.api_key)} символов")
        self.log("INFO", f"API Key: {self.api_key[:6]}...{self.api_key[-4:]}")
        
        # Проверка на пробелы и спецсимволы
        if ' ' in self.api_key or '\n' in self.api_key or '\t' in self.api_key:
            self.log("ERROR", "❌ API Key содержит пробелы или переносы строк!")
            return False
            
        if ' ' in self.api_secret or '\n' in self.api_secret or '\t' in self.api_secret:
            self.log("ERROR", "❌ API Secret содержит пробелы или переносы строк!")
            return False
            
        if ' ' in self.api_passphrase or '\n' in self.api_passphrase or '\t' in self.api_passphrase:
            self.log("ERROR", "❌ Passphrase содержит пробелы или переносы строк!")
            return False
            
        self.log("SUCCESS", "✅ Формат ключей корректный")
        return True
        
    def run_all_tests(self):
        """Запуск всех тестов"""
        print(f"\n{BLUE}{'=' * 60}{RESET}")
        print(f"{BLUE}ВАЛИДАЦИЯ КЛЮЧЕЙ BITGET{RESET}")
        print(f"{BLUE}{'=' * 60}{RESET}\n")
        
        # Проверка формата
        if not self.verify_key_format():
            self.log("ERROR", "Проверьте, нет ли лишних символов в ключах")
            return
            
        # Тесты подключения
        results = []
        
        # Demo futures
        results.append(("Demo Futures", self.test_demo_futures()))
        
        # Spot
        results.append(("Spot Account", self.test_spot_account()))
        
        # Unified
        results.append(("Unified Account", self.test_unified_account()))
        
        # Different URLs
        results.append(("Alternative URLs", self.test_different_base_urls()))
        
        # Итоги
        print(f"\n{BLUE}{'=' * 60}{RESET}")
        print(f"{BLUE}ИТОГИ:{RESET}")
        
        success_count = sum(1 for _, result in results if result)
        
        for name, result in results:
            status = f"{GREEN}✅{RESET}" if result else f"{RED}❌{RESET}"
            print(f"{status} {name}")
            
        if success_count == 0:
            print(f"\n{RED}НИ ОДИН ТЕСТ НЕ ПРОШЕЛ{RESET}")
            print(f"\n{YELLOW}РЕКОМЕНДАЦИИ:{RESET}")
            print("1. Убедитесь что API ключ точно скопирован (без пробелов)")
            print("2. Проверьте что passphrase правильная (регистр важен!)")
            print("3. Попробуйте создать новый API ключ")
            print("4. При создании ключа выберите:")
            print("   - Type: System Generated")
            print("   - Permissions: Read + Trade")
            print("   - IP whitelist: оставьте пустым для теста")
            print("5. Убедитесь что ключ активен (не на паузе)")
        else:
            print(f"\n{GREEN}Некоторые тесты прошли успешно!{RESET}")
            print("Используйте работающий метод в адаптере")


if __name__ == "__main__":
    validator = BitgetKeyValidator()
    validator.run_all_tests()
