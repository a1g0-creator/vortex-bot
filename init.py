"""
Vortex Trading Bot v2.1
Модульный торговый бот для криптовалютных фьючерсов

Архитектура:
- config/: Система конфигурации YAML
- core/: Торговый движок и индикаторы  
- exchanges/: Адаптеры бирж (Bybit, Bitget)
- telegram/: Telegram бот управления
- logs/: Файлы логирования

Основные возможности:
- Торговля по стратегии Vortex Bands
- Мультибиржевая поддержка (Bybit + Bitget)
- Web-интерфейс управления
- Telegram уведомления
- Система управления рисками
"""

from main import VortexTradingBot

# Версия системы
__version__ = "2.1.0"
__author__ = "Vortex Trading Team"
__description__ = "Advanced Multi-Exchange Cryptocurrency Futures Trading Bot"

# Основные компоненты для импорта
__all__ = [
    'VortexTradingBot'
]

# Информация о проекте
PROJECT_INFO = {
    "name": "Vortex Trading Bot",
    "version": __version__,
    "description": __description__,
    "author": __author__,
    "supported_exchanges": ["Bybit", "Bitget"],
    "supported_strategies": ["Vortex Bands"],
    "features": [
        "Multi-exchange support",
        "Real-time WebSocket data",
        "Risk management system", 
        "Telegram notifications",
        "Web interface",
        "PostgreSQL data storage",
        "Docker containerization"
    ]
}