"""
Модуль интеграции с криптовалютными биржами
Поддерживает Bybit и Bitget через единый интерфейс
"""

from .base_exchange import (
    BaseExchange,
    Position,
    Balance, 
    OrderInfo,
    OrderResult,
    Ticker,
    Kline,
    InstrumentInfo,
    OrderType,
    OrderSide,
    OrderStatus,
    MarginMode
)

from .bybit_adapter import BybitAdapter

# Bitget будет добавлен позже
# from .bitget_adapter import BitgetAdapter

from .exchange_factory import (
    ExchangeFactory,
    ExchangeType,
    exchange_factory,
    create_bybit_adapter
)

__all__ = [
    # Базовые классы и типы
    'BaseExchange',
    'Position',
    'Balance',
    'OrderInfo', 
    'OrderResult',
    'Ticker',
    'Kline',
    'InstrumentInfo',
    'OrderType',
    'OrderSide',
    'OrderStatus',
    'MarginMode',
    
    # Адаптеры бирж
    'BybitAdapter',
    # 'BitgetAdapter',
    
    # Фабрика и утилиты
    'ExchangeFactory',
    'ExchangeType',
    'exchange_factory',
    'create_bybit_adapter'
]

__version__ = "2.1.0"