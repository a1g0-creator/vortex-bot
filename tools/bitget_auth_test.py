#!/usr/bin/env python3
"""
Детальный тест авторизации Bitget API
Проверяет все компоненты подписи и заголовков
"""

import os
import time
import hmac
import hashlib
import base64
import json
import requests
from datetime import datetime

# Цвета для вывода
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


def log_info(msg):
    print(f"{BLUE}[INFO]{RESET} {msg}")

def log_success(msg):
    print(f"{GREEN}[SUCCESS]{RESET} {msg}")

def log_error(msg):
    print(f"{RED}[ERROR]{RESET} {msg}")

def log_warning(msg):
    print(f"{YELLOW}[WARNING]{RESET} {msg}")


class BitgetAuthTester:
    def __init__(self):
        self.api_key = os.getenv("BITGET_TESTNET_API_KEY", "")
        self.api_secret = os.getenv("BITGET_TESTNET_API_SECRET", "")
        self.api_passphrase = os.getenv("BITGET_TESTNET_API_PASSPHRASE", "")
        self.base_url = "https://api.bitget.com"
        
    def test_credentials(self):
        """Проверка наличия учетных данных"""
        log_info("=" * 60)
        log_info("1. ПРОВЕРКА УЧЕТНЫХ ДАННЫХ")
        
        if not self.api_key:
            log_error("API_KEY не установлен")
            return False
        else:
            # Показываем часть ключа для проверки
            log_success(f"API_KEY: {self.api_key[:10]}...{self.api_key[-4:]}")
            
        if not self.api_secret:
            log_error("API_SECRET не установлен")
            return False
        else:
            log_success(f"API_SECRET: ***...{self.api_secret[-4:]}")
            
        if not self.api_passphrase:
            log_error("API_PASSPHRASE не установлен")
            return False
        else:
            log_success(f"API_PASSPHRASE: ***")
            
        return True
        
    def test_public_api(self):
        """Тест публичного API (без авторизации)"""
        log_info("=" * 60)
        log_info("2. ТЕСТ ПУБЛИЧНОГО API")
        
        url = f"{self.base_url}/api/v2/public/time"
        log_info(f"URL: {url}")
        
        try:
            response = requests.get(url, timeout=10)
            log_info(f"Status Code: {response.status_code}")
            
            data = response.json()
            log_info(f"Response: {json.dumps(data, indent=2)}")
            
            if data.get("code") == "00000":
                # Правильная обработка времени
                server_time_str = data.get("data", {}).get("serverTime", "0")
                server_time = int(server_time_str)  # Преобразуем строку в int
                dt = datetime.fromtimestamp(server_time / 1000)
                log_success(f"Сервер доступен. Время: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                return True
            else:
                log_error(f"Ошибка API: {data}")
                return False
        except requests.exceptions.Timeout:
            log_error("Таймаут подключения к серверу")
            return False
        except requests.exceptions.ConnectionError:
            log_error("Не удалось подключиться к серверу")
            return False
        except Exception as e:
            log_error(f"Ошибка: {e}")
            import traceback
            log_error(f"Traceback: {traceback.format_exc()}")
            return False
            
    def generate_signature(self, timestamp, method, path, query="", body=""):
        """Генерация подписи"""
        # Формируем строку для подписи
        prehash = timestamp + method.upper() + path
        if query:
            prehash += f"?{query}"
        if body:
            prehash += body
            
        log_info(f"Prehash string: {prehash[:100]}...")  # Показываем первые 100 символов
        
        # Генерируем подпись
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                prehash.encode("utf-8"),
                hashlib.sha256
            ).digest()
        ).decode("utf-8")
        
        log_info(f"Signature: {signature[:20]}...")
        return signature
        
    def test_auth_demo_account(self):
        """Тест авторизации для DEMO аккаунта"""
        log_info("=" * 60)
        log_info("3. ТЕСТ АВТОРИЗАЦИИ (DEMO)")
        
        timestamp = str(int(time.time() * 1000))
        method = "GET"
        path = "/api/v2/mix/account/accounts"
        query = "productType=susdt-futures"  # Для demo
        
        log_info(f"Timestamp: {timestamp}")
        log_info(f"Method: {method}")
        log_info(f"Path: {path}")
        log_info(f"Query: {query}")
        
        signature = self.generate_signature(timestamp, method, path, query)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
            "paptrading": "1"  # ВАЖНО для demo!
        }
        
        log_info("Headers:")
        for key, value in headers.items():
            if key in ["ACCESS-SIGN", "ACCESS-PASSPHRASE"]:
                log_info(f"  {key}: ***")
            else:
                log_info(f"  {key}: {value}")
        
        url = f"{self.base_url}{path}?{query}"
        log_info(f"Full URL: {url}")
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            log_info(f"Response status: {response.status_code}")
            
            data = response.json()
            log_info(f"Response: {json.dumps(data, indent=2)}")
            
            if data.get("code") == "00000":
                log_success("✅ АВТОРИЗАЦИЯ УСПЕШНА!")
                
                accounts = data.get("data", [])
                if accounts:
                    log_info("Балансы DEMO счета:")
                    for acc in accounts:
                        coin = acc.get("marginCoin", "")
                        available = acc.get("available", "0")
                        equity = acc.get("equity", "0")
                        log_info(f"  {coin}: available={available}, equity={equity}")
                else:
                    log_warning("Нет средств на DEMO счете")
                    
                return True
            else:
                log_error(f"❌ ОШИБКА АВТОРИЗАЦИИ: {data}")
                
                error_code = str(data.get("code", ""))
                error_msg = data.get("msg", "")
                
                if error_code == "40037":
                    log_error("Проблема: API ключ не найден")
                    log_info("Возможные причины:")
                    log_info("1. API ключ создан для РЕАЛЬНОГО аккаунта, а не для DEMO")
                    log_info("2. Неправильно скопирован API ключ")
                    log_info("3. API ключ был удален или деактивирован")
                    
                elif error_code == "40006":
                    log_error("Проблема: Неверная подпись")
                    log_info("Возможные причины:")
                    log_info("1. Неправильный SECRET KEY")
                    log_info("2. Ошибка в алгоритме генерации подписи")
                    
                elif error_code == "40007":
                    log_error("Проблема: Неверная PASSPHRASE")
                    log_info("Passphrase чувствительна к регистру!")
                    log_info("Проверьте BITGET_TESTNET_API_PASSPHRASE")
                    
                elif error_code == "40014":
                    log_error("Проблема: Неверный timestamp")
                    log_info("Время вашей системы может быть рассинхронизировано")
                    
                return False
                
        except Exception as e:
            log_error(f"Ошибка запроса: {e}")
            import traceback
            log_error(f"Traceback: {traceback.format_exc()}")
            return False
            
    def test_auth_real_account(self):
        """Тест авторизации для РЕАЛЬНОГО аккаунта (без paptrading)"""
        log_info("=" * 60)
        log_info("4. ТЕСТ АВТОРИЗАЦИИ (REAL ACCOUNT)")
        
        timestamp = str(int(time.time() * 1000))
        method = "GET"
        path = "/api/v2/mix/account/accounts"
        query = "productType=USDT-FUTURES"  # Для реального аккаунта
        
        signature = self.generate_signature(timestamp, method, path, query)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
            "locale": "en-US"
            # БЕЗ paptrading для реального аккаунта
        }
        
        url = f"{self.base_url}{path}?{query}"
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get("code") == "00000":
                log_success("✅ Это ключи от РЕАЛЬНОГО аккаунта!")
                log_warning("⚠️ Для DEMO трейдинга нужно создать отдельные API ключи")
                
                accounts = data.get("data", [])
                if accounts:
                    log_info("Балансы РЕАЛЬНОГО счета:")
                    for acc in accounts:
                        coin = acc.get("marginCoin", "")
                        available = acc.get("available", "0")
                        equity = acc.get("equity", "0")
                        log_info(f"  {coin}: available={available}, equity={equity}")
                        
                return True
            else:
                log_info(f"Не реальный аккаунт: {data.get('msg')}")
                return False
                
        except Exception as e:
            log_error(f"Ошибка: {e}")
            return False
            
    def test_spot_balance(self):
        """Тест получения спотового баланса"""
        log_info("=" * 60)
        log_info("5. ТЕСТ СПОТОВОГО БАЛАНСА")
        
        timestamp = str(int(time.time() * 1000))
        method = "GET"
        path = "/api/v2/spot/account/assets"
        query = ""  # Нет параметров
        
        signature = self.generate_signature(timestamp, method, path, query)
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
            "locale": "en-US"
        }
        
        url = f"{self.base_url}{path}"
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get("code") == "00000":
                log_success("✅ Спотовый баланс получен")
                assets = data.get("data", [])
                for asset in assets[:5]:  # Показываем первые 5
                    coin = asset.get("coin", "")
                    available = asset.get("available", "0")
                    if float(available) > 0:
                        log_info(f"  {coin}: {available}")
                return True
            else:
                log_info(f"Не удалось получить спотовый баланс: {data.get('msg')}")
                return False
                
        except Exception as e:
            log_error(f"Ошибка: {e}")
            return False
            
    def run_all_tests(self):
        """Запуск всех тестов"""
        print(f"\n{BLUE}{'=' * 60}{RESET}")
        print(f"{BLUE}ДИАГНОСТИКА АВТОРИЗАЦИИ BITGET API{RESET}")
        print(f"{BLUE}{'=' * 60}{RESET}\n")
        
        # Тест 1: Проверка credentials
        if not self.test_credentials():
            log_error("\nУстановите переменные окружения:")
            log_info("export BITGET_TESTNET_API_KEY='ваш_ключ'")
            log_info("export BITGET_TESTNET_API_SECRET='ваш_секрет'")
            log_info("export BITGET_TESTNET_API_PASSPHRASE='ваша_фраза'")
            return
            
        # Тест 2: Публичный API
        if not self.test_public_api():
            log_error("Проблема с подключением к серверу Bitget")
            log_info("Проверьте интернет-соединение")
            return
            
        # Тест 3: Demo авторизация
        demo_success = self.test_auth_demo_account()
        
        # Тест 4: Real авторизация
        real_success = False
        if not demo_success:
            log_info("\nПроверяем, может это ключи от реального аккаунта...")
            real_success = self.test_auth_real_account()
            
        # Тест 5: Спотовый баланс (опционально)
        if demo_success or real_success:
            self.test_spot_balance()
        
        # Итоги
        print(f"\n{BLUE}{'=' * 60}{RESET}")
        print(f"{BLUE}РЕЗУЛЬТАТЫ:{RESET}")
        print(f"{BLUE}{'=' * 60}{RESET}")
        
        if demo_success:
            log_success("✅ DEMO АВТОРИЗАЦИЯ РАБОТАЕТ!")
            log_success("Ваши API ключи настроены правильно для DEMO трейдинга")
            log_info("Можно использовать BitgetAdapter с testnet=True")
            
        elif real_success:
            log_warning("⚠️ У вас API ключи от РЕАЛЬНОГО аккаунта!")
            log_info("Для безопасного тестирования рекомендуется создать DEMO ключи:")
            log_info("1. Зайдите на https://www.bitget.com/ru/account/newapi")
            log_info("2. Создайте новый API ключ")
            log_info("3. В разделе 'Applicable Accounts' выберите 'Simulated Trading'")
            log_info("4. Сохраните новые ключи для DEMO трейдинга")
            
        else:
            log_error("❌ АВТОРИЗАЦИЯ НЕ РАБОТАЕТ")
            log_info("\nВОЗМОЖНЫЕ ПРИЧИНЫ:")
            log_info("1. Неправильная PASSPHRASE (чувствительна к регистру)")
            log_info("2. API ключи были созданы неправильно")
            log_info("3. API ключи были деактивированы")
            log_info("\nПОПРОБУЙТЕ:")
            log_info("1. Перепроверьте PASSPHRASE")
            log_info("2. Создайте новые API ключи на Bitget")
            log_info("3. Убедитесь что выбрали правильный тип аккаунта при создании")


if __name__ == "__main__":
    tester = BitgetAuthTester()
    tester.run_all_tests()
