"""
Фабрика для создания экземпляров биржевых адаптеров
Централизованное управление подключениями к различным биржам
С добавленной поддержкой BitgetAdapter
"""

import logging
from typing import Dict, Optional, Type, Any
from enum import Enum

from .base_exchange import BaseExchange
from .bybit_adapter import BybitAdapter
from .bitget_adapter import BitgetAdapter  # Импортируем BitgetAdapter


class ExchangeType(Enum):
    """Поддерживаемые типы бирж"""
    BYBIT = "bybit"
    BITGET = "bitget"


class ExchangeFactory:
    """
    Фабрика для создания и управления биржевыми адаптерами
    Паттерн Factory + Registry для гибкого управления биржами
    """
    
    # Реестр доступных адаптеров
    _adapters: Dict[ExchangeType, Type[BaseExchange]] = {
        ExchangeType.BYBIT: BybitAdapter,
        ExchangeType.BITGET: BitgetAdapter,  # Регистрируем BitgetAdapter
    }
    
    # Кэш созданных экземпляров
    _instances: Dict[str, BaseExchange] = {}
    
    def __init__(self):
        self.logger = logging.getLogger("ExchangeFactory")
    
    @classmethod
    def register_adapter(cls, exchange_type: ExchangeType, adapter_class: Type[BaseExchange]):
        """
        Регистрация нового адаптера биржи
        
        Args:
            exchange_type: Тип биржи
            adapter_class: Класс адаптера
        """
        cls._adapters[exchange_type] = adapter_class
        logging.getLogger("ExchangeFactory").info(
            f"Registered adapter {adapter_class.__name__} for {exchange_type.value}"
        )
    
    @classmethod
    def get_supported_exchanges(cls) -> list[str]:
        """
        Получить список поддерживаемых бирж
        
        Returns:
            Список названий бирж
        """
        return [exchange.value for exchange in cls._adapters.keys()]
    
    def create_exchange(self, 
                       exchange_type: ExchangeType,
                       api_key: str,
                       api_secret: str,
                       testnet: bool = True,
                       **kwargs) -> Optional[BaseExchange]:
        """
        Создание экземпляра биржевого адаптера
        
        Args:
            exchange_type: Тип биржи
            api_key: API ключ
            api_secret: API секрет
            testnet: Использовать тестовую сеть
            **kwargs: Дополнительные параметры для адаптера
            
        Returns:
            Экземпляр адаптера или None при ошибке
        """
        adapter_class = self._adapters.get(exchange_type)
        
        if not adapter_class:
            self.logger.error(f"Unsupported exchange type: {exchange_type.value}")
            return None
        
        try:
            # Создаем уникальный ключ для кэширования
            cache_key = f"{exchange_type.value}_{api_key[:8]}_{testnet}"
            
            # Проверяем кэш
            if cache_key in self._instances:
                self.logger.info(f"Using cached {exchange_type.value} adapter")
                return self._instances[cache_key]
            
            # Создаем новый экземпляр
            self.logger.info(f"Creating new {exchange_type.value} adapter (testnet={testnet})")
            
            # Передаем все параметры в конструктор
            adapter = adapter_class(
                api_key=api_key,
                api_secret=api_secret,
                testnet=testnet,
                **kwargs
            )
            
            # Кэшируем
            self._instances[cache_key] = adapter
            
            return adapter
            
        except Exception as e:
            self.logger.error(f"Error creating {exchange_type.value} adapter: {e}")
            return None
    
    def create_bybit(self, 
                    api_key: str, 
                    api_secret: str, 
                    testnet: bool = True,
                    recv_window: int = 10000) -> Optional[BybitAdapter]:
        """
        Удобный метод для создания Bybit адаптера
        
        Args:
            api_key: API ключ Bybit
            api_secret: API секрет Bybit
            testnet: Использовать demo.bybit.com
            recv_window: Окно получения для API
            
        Returns:
            Экземпляр BybitAdapter или None
        """
        return self.create_exchange(
            ExchangeType.BYBIT,
            api_key,
            api_secret,
            testnet,
            recv_window=recv_window
        )
    
    def create_bitget(self, 
                     api_key: str, 
                     api_secret: str, 
                     passphrase: str,
                     testnet: bool = True,
                     recv_window: int = 10000) -> Optional[BitgetAdapter]:
        """
        Удобный метод для создания Bitget адаптера
        
        Args:
            api_key: API ключ Bitget
            api_secret: API секрет Bitget
            passphrase: Passphrase для Bitget
            testnet: Использовать тестовую сеть (demo режим)
            recv_window: Окно получения для API
            
        Returns:
            Экземпляр BitgetAdapter или None
        """
        return self.create_exchange(
            ExchangeType.BITGET,
            api_key,
            api_secret,
            testnet,
            passphrase=passphrase,  # Передаем passphrase как именованный параметр
            recv_window=recv_window
        )
    
    async def initialize_exchange(self, exchange: BaseExchange) -> bool:
        """
        Инициализация биржевого адаптера
        
        Args:
            exchange: Экземпляр адаптера
            
        Returns:
            True если инициализация успешна
        """
        try:
            self.logger.info(f"Initializing {exchange.exchange_name} adapter")
            
            # Вызываем метод initialize адаптера
            if hasattr(exchange, 'initialize'):
                result = await exchange.initialize()
                if result:
                    self.logger.info(f"{exchange.exchange_name} adapter initialized successfully")
                else:
                    self.logger.error(f"Failed to initialize {exchange.exchange_name} adapter")
                return result
            else:
                self.logger.warning(f"{exchange.exchange_name} adapter doesn't have initialize method")
                return True
                
        except Exception as e:
            self.logger.error(f"Error initializing {exchange.exchange_name}: {e}")
            return False
    
    async def test_connection(self, exchange: BaseExchange) -> Dict[str, Any]:
        """
        Тестирование подключения к бирже
        
        Args:
            exchange: Экземпляр адаптера
            
        Returns:
            Результаты тестирования
        """
        results = {
            "exchange": exchange.exchange_name,
            "testnet": exchange.testnet,
            "status": "unknown",
            "tests": {}
        }
        
        try:
            # Тест времени сервера
            try:
                server_time = await exchange.get_server_time()
                results["tests"]["server_time"] = {
                    "passed": server_time > 0,
                    "value": server_time
                }
            except Exception as e:
                results["tests"]["server_time"] = {
                    "passed": False,
                    "error": str(e)
                }
            
            # Тест баланса
            try:
                balance = await exchange.get_balance()
                results["tests"]["balance"] = {
                    "passed": balance is not None,
                    "value": balance.__dict__ if balance else None
                }
            except Exception as e:
                results["tests"]["balance"] = {
                    "passed": False,
                    "error": str(e)
                }
            
            # Тест получения инструментов
            try:
                instruments = await exchange.get_all_instruments()
                results["tests"]["instruments"] = {
                    "passed": len(instruments) > 0,
                    "count": len(instruments)
                }
            except Exception as e:
                results["tests"]["instruments"] = {
                    "passed": False,
                    "error": str(e)
                }
            
            # Определяем общий статус
            all_passed = all(
                test.get("passed", False) 
                for test in results["tests"].values()
            )
            results["status"] = "connected" if all_passed else "partial"
            
        except Exception as e:
            results["status"] = "error"
            results["error"] = str(e)
        
        return results
    
    def get_instance(self, exchange_type: ExchangeType, testnet: bool = True) -> Optional[BaseExchange]:
        """
        Получить существующий экземпляр адаптера из кэша
        
        Args:
            exchange_type: Тип биржи
            testnet: Тестовая сеть
            
        Returns:
            Экземпляр адаптера или None
        """
        for key, instance in self._instances.items():
            if (instance.exchange_name == exchange_type.value.lower() and 
                instance.testnet == testnet):
                return instance
        
        return None
    
    def list_active_connections(self) -> Dict[str, Dict[str, Any]]:
        """
        Список активных подключений
        
        Returns:
            Словарь с информацией о подключениях
        """
        connections = {}
        
        for key, instance in self._instances.items():
            connections[key] = {
                "exchange": instance.exchange_name,
                "testnet": instance.testnet,
                "class": instance.__class__.__name__,
                "url": instance.base_url
            }
        
        return connections
    
    async def close_all(self):
        """Закрыть все активные подключения"""
        for instance in self._instances.values():
            if hasattr(instance, 'close'):
                try:
                    await instance.close()
                    self.logger.info(f"Closed {instance.exchange_name} connection")
                except Exception as e:
                    self.logger.error(f"Error closing {instance.exchange_name}: {e}")
        
        self._instances.clear()


# Глобальный экземпляр фабрики
exchange_factory = ExchangeFactory()


# Удобные функции для быстрого создания адаптеров
async def create_bybit_adapter(api_key: str, 
                              api_secret: str, 
                              testnet: bool = True,
                              recv_window: int = 10000,
                              initialize: bool = True) -> Optional[BybitAdapter]:
    """
    Быстрое создание и инициализация Bybit адаптера
    
    Args:
        api_key: API ключ
        api_secret: API секрет
        testnet: Тестовая сеть
        recv_window: Окно получения
        initialize: Автоматически инициализировать
        
    Returns:
        Готовый к использованию адаптер или None
    """
    adapter = exchange_factory.create_bybit(api_key, api_secret, testnet, recv_window)
    
    if adapter and initialize:
        success = await exchange_factory.initialize_exchange(adapter)
        if not success:
            return None
    
    return adapter


async def create_bitget_adapter(api_key: str,
                               api_secret: str,
                               passphrase: str,
                               testnet: bool = True,
                               recv_window: int = 10000,
                               initialize: bool = True) -> Optional[BitgetAdapter]:
    """
    Быстрое создание и инициализация Bitget адаптера
    
    Args:
        api_key: API ключ
        api_secret: API секрет
        passphrase: Пассфраза Bitget
        testnet: Тестовая сеть (demo режим)
        recv_window: Окно получения
        initialize: Автоматически инициализировать
        
    Returns:
        Готовый к использованию адаптер или None
    """
    adapter = exchange_factory.create_bitget(api_key, api_secret, passphrase, testnet, recv_window)
    
    if adapter and initialize:
        success = await exchange_factory.initialize_exchange(adapter)
        if not success:
            return None
    
    return adapter


async def test_all_exchanges() -> Dict[str, Dict[str, Any]]:
    """
    Тестирование всех активных подключений
    
    Returns:
        Результаты тестирования всех бирж
    """
    results = {}
    
    for key, instance in exchange_factory._instances.items():
        test_result = await exchange_factory.test_connection(instance)
        results[key] = test_result
    
    return results