"""
Пакет конфигурации Vortex Trading Bot
Централизованное управление настройками системы
"""

from .config_loader import (
    ConfigLoader,
    ConfigValidationError,
    config_loader,
    get_app_config,
    get_bybit_credentials,
    get_telegram_credentials,
    get_database_config,
    reload_configs
)

__all__ = [
    'ConfigLoader',
    'ConfigValidationError', 
    'config_loader',
    'get_app_config',
    'get_bybit_credentials',
    'get_telegram_credentials',
    'get_database_config',
    'reload_configs'
]

__version__ = "2.1.0"