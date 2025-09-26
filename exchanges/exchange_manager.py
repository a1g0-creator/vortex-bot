"""
Мультибиржевый менеджер для управления несколькими биржами
Реализует арбитражную торговлю и автоматический выбор биржи
Версия с поддержкой demo/mainnet окружений
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from collections import defaultdict

from exchanges.base_exchange import BaseExchange, Balance, Position, Ticker
from exchanges.exchange_factory import exchange_factory
from config.config_loader import config_loader


@dataclass
class ExchangeStatus:
    """Статус биржи"""
    name: str
    environment: str
    connected: bool
    last_ping: float
    error_count: int
    avg_response_time: float
    total_balance: float
    available_balance: float
    active_positions: int


class ExchangeManager:
    """
    Мультибиржевый менеджер - центральное управление несколькими биржами
    Функциональность:
    - Управление подключениями к биржам
    - Автоматический выбор оптимальной биржи
    - Арбитражная торговля
    - Балансировка и перераспределение средств
    """
    
    def __init__(self):
        self.logger = logging.getLogger("ExchangeManager")
        
        # Подключенные биржи
        self.exchanges: Dict[str, BaseExchange] = {}
        self.primary_exchange: Optional[str] = None
        
        # Статусы бирж
        self.exchange_statuses: Dict[str, ExchangeStatus] = {}
        
        # Арбитражная торговля
        self.arbitrage_enabled = False
        self.min_spread_percent = 0.5  # Минимальный спред для арбитража
        self.max_position_size = 1000.0  # Максимальный размер арбитражной позиции
        
        # Мониторинг
        self.last_health_check = 0
        self.health_check_interval = 60  # Проверка каждую минуту
        
        # Конфигурация
        self._load_config()
    
    def _load_config(self):
        """Загрузка конфигурации мультибиржевого управления"""
        try:
            exchanges_config = config_loader.get_config("exchanges")
            
            # Приоритеты бирж
            priority_config = exchanges_config.get("exchanges", {}).get("priority", {})
            self.primary_exchange = priority_config.get("primary", "bybit")
            
            # Включенные биржи
            self.enabled_exchanges = exchanges_config.get("exchanges", {}).get("enabled", [])
            
            self.logger.info(f"Enabled exchanges: {self.enabled_exchanges}")
            self.logger.info(f"Primary exchange: {self.primary_exchange}")
            
        except Exception as e:
            self.logger.error(f"Error loading config: {e}")
            self.enabled_exchanges = ["bybit"]
            self.primary_exchange = "bybit"
    
    async def initialize(self) -> bool:
        """
        Инициализация всех включенных бирж
        
        Returns:
            True если хотя бы одна биржа инициализирована
        """
        try:
            self.logger.info("Initializing Exchange Manager...")
            
            success_count = 0
            
            for exchange_name in self.enabled_exchanges:
                if await self._initialize_exchange(exchange_name):
                    success_count += 1
            
            if success_count == 0:
                self.logger.error("Failed to initialize any exchange")
                return False
            
            self.logger.info(f"✅ Exchange Manager initialized: {success_count}/{len(self.enabled_exchanges)} exchanges connected")
            
            # Запускаем мониторинг
            asyncio.create_task(self._monitor_exchanges())
            
            return True
            
        except Exception as e:
            self.logger.error(f"Exchange Manager initialization failed: {e}")
            return False
    
    async def _initialize_exchange(self, exchange_name: str) -> bool:
        """
        Инициализация конкретной биржи
        
        Args:
            exchange_name: Название биржи
            
        Returns:
            True если успешно инициализирована
        """
        try:
            self.logger.info(f"Initializing {exchange_name}...")
            
            # Проверяем что биржа включена в конфигурации
            exchange_config = config_loader.get_config("exchanges").get(exchange_name, {})
            if not exchange_config.get("enabled", False):
                self.logger.warning(f"{exchange_name} is disabled in configuration")
                return False
            
            # Создаем адаптер из конфигурации
            adapter = exchange_factory.create_exchange_from_config(exchange_name)
            
            if not adapter:
                self.logger.error(f"Failed to create adapter for {exchange_name}")
                return False
            
            # Инициализируем адаптер
            if not await adapter.initialize():
                self.logger.error(f"Failed to initialize {exchange_name}")
                return False
            
            # Сохраняем адаптер
            self.exchanges[exchange_name] = adapter
            
            # Создаем статус
            environment = config_loader.get_active_environment(exchange_name)
            self.exchange_statuses[exchange_name] = ExchangeStatus(
                name=exchange_name,
                environment=environment or "unknown",
                connected=True,
                last_ping=time.time(),
                error_count=0,
                avg_response_time=0,
                total_balance=0,
                available_balance=0,
                active_positions=0
            )
            
            # Обновляем баланс
            await self._update_exchange_balance(exchange_name)
            
            self.logger.info(f"✅ {exchange_name} ({environment}) initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error initializing {exchange_name}: {e}")
            return False
    
    async def _update_exchange_balance(self, exchange_name: str):
        """Обновление баланса биржи"""
        try:
            exchange = self.exchanges.get(exchange_name)
            if not exchange:
                return
            
            balance = await exchange.get_balance()
            if balance:
                status = self.exchange_statuses[exchange_name]
                status.total_balance = balance.wallet_balance
                status.available_balance = balance.available_balance
            
        except Exception as e:
            self.logger.error(f"Error updating balance for {exchange_name}: {e}")
    
    async def _monitor_exchanges(self):
        """Мониторинг состояния бирж"""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)
                
                for exchange_name, exchange in self.exchanges.items():
                    try:
                        # Проверяем подключение
                        start_time = time.time()
                        server_time = await exchange.get_server_time()
                        response_time = time.time() - start_time
                        
                        status = self.exchange_statuses[exchange_name]
                        
                        if server_time:
                            status.connected = True
                            status.last_ping = time.time()
                            status.avg_response_time = (status.avg_response_time * 0.9 + response_time * 0.1)
                            status.error_count = 0
                        else:
                            status.connected = False
                            status.error_count += 1
                        
                        # Обновляем баланс
                        await self._update_exchange_balance(exchange_name)
                        
                        # Обновляем количество позиций
                        positions = await exchange.get_positions()
                        status.active_positions = len(positions)
                        
                    except Exception as e:
                        self.logger.error(f"Error monitoring {exchange_name}: {e}")
                        status = self.exchange_statuses[exchange_name]
                        status.connected = False
                        status.error_count += 1
                
            except Exception as e:
                self.logger.error(f"Monitor error: {e}")
    
    async def get_primary_exchange(self) -> Optional[BaseExchange]:
        """
        Получение основной биржи для торговли
        
        Returns:
            Экземпляр основной биржи или None
        """
        # Сначала пытаемся вернуть заданную primary биржу
        if self.primary_exchange in self.exchanges:
            exchange = self.exchanges[self.primary_exchange]
            status = self.exchange_statuses[self.primary_exchange]
            
            if status.connected:
                return exchange
        
        # Если primary недоступна, ищем любую доступную
        for exchange_name, exchange in self.exchanges.items():
            status = self.exchange_statuses[exchange_name]
            if status.connected:
                self.logger.warning(f"Primary exchange unavailable, using {exchange_name}")
                return exchange
        
        self.logger.error("No connected exchanges available")
        return None
    
    async def get_exchange_statuses(self) -> Dict[str, ExchangeStatus]:
        """
        Получение статусов всех бирж
        
        Returns:
            Словарь со статусами бирж
        """
        return self.exchange_statuses.copy()
    
    async def close(self):
        """Закрытие всех подключений"""
        self.logger.info("Closing Exchange Manager...")
        
        for exchange_name, exchange in self.exchanges.items():
            try:
                await exchange.close()
                self.logger.info(f"Closed {exchange_name}")
            except Exception as e:
                self.logger.error(f"Error closing {exchange_name}: {e}")
        
        self.exchanges.clear()
        self.exchange_statuses.clear()


# Глобальный экземпляр менеджера
exchange_manager = ExchangeManager()