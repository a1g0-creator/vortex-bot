#!/usr/bin/env python3
"""
Диагностический скрипт для проверки подключения к Bitget
Исправленная версия с правильными эндпоинтами
"""

import asyncio
import sys
import os
import logging
from pathlib import Path
from typing import Optional

# Добавляем корневую директорию в Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

async def main():
    """Основная функция диагностики"""
    
    print("🔍 ДИАГНОСТИКА ПОДКЛЮЧЕНИЯ К BITGET")
    print("=" * 50)
    
    # ========================================
    # ШАГ 1: Проверка файлов конфигурации
    # ========================================
    print("📂 ШАГ 1: Проверка файлов конфигурации")
    print("-" * 30)
    
    required_files = [
        "config/exchanges.yaml",
        "exchanges/bitget_adapter.py", 
        "exchanges/exchange_factory.py",
        "exchanges/base_exchange.py"
    ]
    
    missing_files = []
    for file_path in required_files:
        if os.path.exists(file_path):
            print(f"✅ {file_path} - найден")
        else:
            print(f"❌ {file_path} - НЕ НАЙДЕН")
            missing_files.append(file_path)
    
    if missing_files:
        print(f"❌ Отсутствуют критические файлы: {missing_files}")
        return False
    
    # ========================================
    # ШАГ 2: Загрузка конфигурации
    # ========================================
    print("\n⚙️ ШАГ 2: Загрузка конфигурации")
    print("-" * 30)
    
    try:
        from config.config_loader import config_loader
        exchanges_config = config_loader.get_config("exchanges")
        print("✅ Конфигурация загружена")
        
        # Проверяем включенные биржи
        enabled_exchanges = exchanges_config.get("exchanges", {}).get("enabled", [])
        print(f"📊 Включенные биржи: {enabled_exchanges}")
        
        if "bitget" not in enabled_exchanges:
            print("❌ ПРОБЛЕМА: Bitget не включен в списке бирж!")
            print("🔧 РЕШЕНИЕ: Добавьте 'bitget' в exchanges.enabled в exchanges.yaml")
            return False
        else:
            print("✅ Bitget включен в конфигурации")
            
    except Exception as e:
        print(f"❌ Ошибка загрузки конфигурации: {e}")
        return False
    
    # ========================================
    # ШАГ 3: Проверка настроек Bitget
    # ========================================
    print("\n🏪 ШАГ 3: Проверка настроек Bitget")
    print("-" * 30)
    
    try:
        bitget_config = exchanges_config.get("bitget", {})
        if not bitget_config:
            print("❌ ПРОБЛЕМА: Секция 'bitget' не найдена в exchanges.yaml!")
            return False
        
        print("✅ Секция bitget найдена")
        print(f"📊 Bitget enabled: {bitget_config.get('enabled', False)}")
        
        # Проверяем testnet/mainnet настройки
        testnet_config = bitget_config.get("testnet", {})
        mainnet_config = bitget_config.get("mainnet", {})
        
        testnet_enabled = testnet_config.get("enabled", False)
        mainnet_enabled = mainnet_config.get("enabled", False)
        
        print(f"📊 Testnet enabled: {testnet_enabled}")
        print(f"📊 Mainnet enabled: {mainnet_enabled}")
        
        if not testnet_enabled and not mainnet_enabled:
            print("❌ ПРОБЛЕМА: Ни testnet, ни mainnet не включены!")
            print("🔧 РЕШЕНИЕ: Включите testnet: enabled: true для demo ключей")
            return False
        
        # Определяем какую сеть использовать
        if testnet_enabled:
            print("✅ Используем TESTNET для demo ключей")
            network = "testnet"
            network_config = testnet_config
        else:
            print("✅ Используем MAINNET")
            network = "mainnet"
            network_config = mainnet_config
            
    except Exception as e:
        print(f"❌ Ошибка проверки настроек: {e}")
        return False
    
    # ========================================
    # ШАГ 4: Проверка API ключей
    # ========================================
    print(f"\n🔑 ШАГ 4: Проверка API ключей ({network})")
    print("-" * 30)
    
    try:
        api_credentials = bitget_config.get("api_credentials", {}).get(network, {})
        
        api_key = api_credentials.get("api_key", "")
        api_secret = api_credentials.get("api_secret", "")
        api_passphrase = api_credentials.get("passphrase", "")
        
        # Маскируем ключи для безопасности
        masked_key = api_key[:10] + "..." if len(api_key) > 10 else "НЕ УСТАНОВЛЕН"
        masked_secret = api_secret[:10] + "..." if len(api_secret) > 10 else "НЕ УСТАНОВЛЕН"
        masked_passphrase = api_passphrase[:6] + "..." if len(api_passphrase) > 6 else "НЕ УСТАНОВЛЕН"
        
        print(f"🔍 API Key: {masked_key} ({len(api_key)} символов)")
        print(f"🔍 API Secret: {masked_secret} ({len(api_secret)} символов)")
        print(f"🔍 Passphrase: {masked_passphrase} ({len(api_passphrase)} символов)")
        
        if not all([api_key, api_secret, api_passphrase]):
            print("❌ ПРОБЛЕМА: Отсутствуют обязательные API ключи!")
            print("🔧 РЕШЕНИЕ: Добавьте все три ключа в exchanges.yaml:")
            print("   api_credentials:")
            print("     testnet:")
            print("       api_key: 'bg_ваш_demo_key'")
            print("       api_secret: 'ваш_demo_secret'")
            print("       passphrase: 'ваш_demo_passphrase'")
            return False
        
        print("✅ Все API ключи присутствуют")
        
    except Exception as e:
        print(f"❌ Ошибка проверки ключей: {e}")
        return False
    
    # ========================================
    # ШАГ 5: Проверка импорта адаптера
    # ========================================
    print("\n📦 ШАГ 5: Проверка импорта адаптера")
    print("-" * 30)
    
    try:
        from exchanges.bitget_adapter import BitgetAdapter
        print("✅ BitgetAdapter импортирован успешно")
        
        from exchanges.exchange_factory import ExchangeFactory, ExchangeType
        print("✅ ExchangeFactory импортирован успешно")
        
        # Проверяем что Bitget зарегистрирован в фабрике
        factory = ExchangeFactory()
        supported = factory.get_supported_exchanges()
        print(f"📊 Поддерживаемые биржи: {supported}")
        
        if "bitget" not in supported:
            print("❌ ПРОБЛЕМА: Bitget не зарегистрирован в ExchangeFactory!")
            print("🔧 РЕШЕНИЕ: Проверьте что в exchange_factory.py есть:")
            print("   from .bitget_adapter import BitgetAdapter")
            print("   ExchangeType.BITGET: BitgetAdapter в _adapters")
            return False
        else:
            print("✅ Bitget зарегистрирован в фабрике")
            
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        print("🔧 РЕШЕНИЕ: Проверьте синтаксис в bitget_adapter.py")
        return False
    except Exception as e:
        print(f"❌ Ошибка импорта: {e}")
        return False
    
    # ========================================
    # ШАГ 6: Создание Bitget адаптера
    # ========================================
    print("\n🏗️ ШАГ 6: Создание Bitget адаптера")
    print("-" * 30)
    
    try:
        adapter = BitgetAdapter(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            testnet=testnet_enabled,
            recv_window=10000
        )
        print("✅ Bitget адаптер создан успешно")
        print(f"📊 Base URL: {adapter.base_url}")
        print(f"📊 WebSocket URL: {getattr(adapter, 'ws_url', adapter.WEBSOCKET_URL)}")
        print(f"📊 Testnet mode: {adapter.testnet}")
        
    except Exception as e:
        print(f"❌ Ошибка создания адаптера: {e}")
        return False
    
    # ========================================
    # ШАГ 7: Инициализация адаптера
    # ========================================
    print("\n🚀 ШАГ 7: Инициализация адаптера")
    print("-" * 30)
    
    try:
        init_result = await adapter.initialize()
        if init_result:
            print("✅ Адаптер инициализирован успешно")
        else:
            print("❌ Ошибка инициализации адаптера")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        import traceback
        print("📋 Детали ошибки:")
        traceback.print_exc()
        return False
    
    # ========================================
    # ШАГ 8: Тестирование подключения
    # ========================================
    print("\n🧪 ШАГ 8: Тестирование подключения")
    print("-" * 30)
    
    try:
        # Тест 1: Получение времени сервера
        print("🕒 Тест 1: Получение времени сервера...")
        server_time = await adapter._get_server_time()
        if server_time:
            print(f"✅ Время сервера получено: {server_time}")
        else:
            print("❌ Не удалось получить время сервера")
            
        # Тест 2: Получение баланса (исправленная версия)
        print("💰 Тест 2: Получение баланса...")
        balance = await adapter.get_balance()
        if balance:
            print(f"✅ Баланс получен: {balance.wallet_balance} USDT")
            print(f"   Доступно: {balance.available_balance} USDT")
            print(f"   Использовано: {balance.used_balance} USDT")
            print(f"   Нереализованный P&L: {balance.unrealized_pnl} USDT")
        else:
            print("❌ Не удалось получить баланс")
            
        # Тест 3: Получение позиций
        print("📊 Тест 3: Получение позиций...")
        positions = await adapter.get_positions()
        print(f"✅ Получено {len(positions)} позиций")
        if positions:
            for pos in positions[:3]:  # Показываем первые 3 позиции
                print(f"   {pos.symbol}: {pos.side} {pos.size} @ {pos.entry_price}")
                
        # Тест 4: Получение информации об инструментах
        print("📋 Тест 4: Получение информации об инструментах...")
        instruments = await adapter.get_instruments()
        print(f"✅ Получено {len(instruments)} инструментов")
        if instruments:
            print(f"📋 Пример: {instruments[0].get('symbol', 'N/A')}")
            
        # Тест 5: Получение тикера BTCUSDT
        print("📈 Тест 5: Получение тикера BTCUSDT...")
        ticker = await adapter.get_ticker("BTCUSDT")
        if ticker:
            print(f"✅ Тикер получен: {ticker.symbol} = ${ticker.last_price:.2f}")
        else:
            print("❌ Не удалось получить тикер")
            
    except Exception as e:
        print(f"❌ Ошибка тестирования: {e}")
        import traceback
        print("📋 Детали ошибки:")
        traceback.print_exc()
    
    # ========================================
    # Закрытие соединения
    # ========================================
    try:
        await adapter.close()
        print("🔄 Соединение закрыто")
    except Exception as e:
        print(f"⚠️ Предупреждение при закрытии: {e}")
    
    # ========================================
    # ИТОГОВЫЙ СТАТУС
    # ========================================
    print("\n🎯 ИТОГОВЫЙ СТАТУС")
    print("=" * 30)
    print("✅ Диагностика завершена!")
    print("📊 Если все тесты прошли успешно, подключение к Bitget работает")
    print("❌ Если есть ошибки, следуйте предложенным решениям выше")
    
    return True

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️ Диагностика прервана пользователем")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)
