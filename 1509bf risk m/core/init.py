"""
Основные компоненты торгового движка Vortex Trading Bot
"""

from .indicators import VortexBands
from .trading_engine import TradingEngine
from .position_manager import PositionManager
from .risk_manager import RiskManager

# Будет добавлено позже при создании exchange_manager
# from .exchange_manager import ExchangeManager

__all__ = [
    'VortexBands',
    'TradingEngine', 
    'PositionManager',
    'RiskManager',
    # 'ExchangeManager'
]

__version__ = "2.1.0"