# exchanges/__init__.py
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
    MarginMode,
)

from .bybit_adapter import BybitAdapter
from .bitget_adapter import BitgetAdapter

from .exchange_factory import (
    ExchangeFactory,
    ExchangeType,
    exchange_factory,
    # удобные хелперы
    create_bybit_adapter,
    create_bitget_adapter,
    test_all_exchanges,
)

__all__ = [
    # Базовые типы
    "BaseExchange",
    "Position",
    "Balance",
    "OrderInfo",
    "OrderResult",
    "Ticker",
    "Kline",
    "InstrumentInfo",
    "OrderType",
    "OrderSide",
    "OrderStatus",
    "MarginMode",

    # Адаптеры
    "BybitAdapter",
    "BitgetAdapter",

    # Фабрика и утилиты
    "ExchangeFactory",
    "ExchangeType",
    "exchange_factory",
    "create_bybit_adapter",
    "create_bitget_adapter",
    "test_all_exchanges",
]

__version__ = "2.1.0"
