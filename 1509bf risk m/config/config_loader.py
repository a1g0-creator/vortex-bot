"""
Загрузчик конфигураций из YAML файлов
Централизованное управление настройками системы
Версия с поддержкой demo/mainnet окружений без testnet
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
import json


@dataclass
class ConfigValidationError(Exception):
    """Ошибка валидации конфигурации"""
    message: str
    config_file: str
    field: str = None


class ConfigLoader:
    """
    Загрузчик и валидатор конфигураций из YAML файлов
    Поддерживает горячую перезагрузку и валидацию настроек
    Работает с demo/mainnet окружениями
    """
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.logger = logging.getLogger("ConfigLoader")
        
        # Кэш загруженных конфигураций
        self._configs = {}
        self._file_timestamps = {}
        
        # Активные окружения для бирж
        self._active_environments = {}
        
        # Обязательные файлы конфигурации
        self.required_files = {
            "config": "config.yaml",
            "exchanges": "exchanges.yaml", 
            "strategies": "strategies.yaml"
        }
        
        # Дополнительные файлы (опциональные)
        self.optional_files = {
            "telegram": "telegram.yaml",
            "database": "database.yaml",
            "monitoring": "monitoring.yaml"
        }
        
        self._validate_config_directory()
    
    def _validate_config_directory(self):
        """Проверка существования директории конфигураций"""
        if not self.config_dir.exists():
            self.logger.warning(f"Config directory {self.config_dir} doesn't exist, creating...")
            self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # Проверяем наличие обязательных файлов
        missing_files = []
        for name, filename in self.required_files.items():
            filepath = self.config_dir / filename
            if not filepath.exists():
                missing_files.append(filename)
        
        if missing_files:
            self.logger.warning(f"Missing required config files: {missing_files}")
    
    def load_config(self, config_name: str) -> Dict[str, Any]:
        """
        Загрузка конфигурации из YAML файла
        
        Args:
            config_name: Имя конфигурации (без расширения)
            
        Returns:
            Словарь с конфигурацией
        """
        # Получаем имя файла
        if config_name in self.required_files:
            filename = self.required_files[config_name]
        elif config_name in self.optional_files:
            filename = self.optional_files[config_name]
        else:
            filename = f"{config_name}.yaml"
        
        filepath = self.config_dir / filename
        
        if not filepath.exists():
            self.logger.warning(f"Config file {filepath} not found")
            return {}
        
        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file) or {}
                
            self._configs[config_name] = config
            self._file_timestamps[config_name] = filepath.stat().st_mtime
            
            self.logger.debug(f"Loaded config: {config_name}")
            return config
            
        except Exception as e:
            self.logger.error(f"Error loading config {config_name}: {e}")
            raise ConfigValidationError(
                f"Failed to load configuration: {e}",
                filename
            )
    
    def load_all_configs(self) -> Dict[str, Dict[str, Any]]:
        """
        Загрузка всех конфигураций
        
        Returns:
            Словарь со всеми конфигурациями
        """
        self.logger.info("Loading all configurations...")
        
        configs = {}
        
        # Загружаем обязательные конфигурации
        for name in self.required_files:
            try:
                configs[name] = self.load_config(name)
            except Exception as e:
                self.logger.error(f"Failed to load required config {name}: {e}")
                raise
        
        # Загружаем опциональные конфигурации
        for name in self.optional_files:
            try:
                config = self.load_config(name)
                if config:
                    configs[name] = config
            except Exception as e:
                self.logger.warning(f"Failed to load optional config {name}: {e}")
        
        # Валидация загруженных конфигураций
        self._validate_all_configs(configs)
        
        # Определяем активные окружения для бирж
        self._determine_active_environments(configs.get("exchanges", {}))
        
        self.logger.info(f"✅ Loaded {len(configs)} configurations successfully")
        return configs
    
    def _determine_active_environments(self, exchanges_config: Dict[str, Any]):
        """
        Определение активных окружений для каждой биржи
        
        Args:
            exchanges_config: Конфигурация бирж
        """
        self._active_environments = {}
        
        # Проверяем Bybit
        bybit_config = exchanges_config.get("bybit", {})
        if bybit_config.get("enabled", False):
            demo_enabled = bybit_config.get("demo", {}).get("enabled", False)
            mainnet_enabled = bybit_config.get("mainnet", {}).get("enabled", False)
            
            if demo_enabled and mainnet_enabled:
                raise ConfigValidationError(
                    "Both demo and mainnet cannot be enabled simultaneously for Bybit",
                    "exchanges.yaml",
                    "bybit.demo/mainnet"
                )
            elif demo_enabled:
                self._active_environments["bybit"] = "demo"
                self.logger.info("Bybit active environment: demo")
            elif mainnet_enabled:
                self._active_environments["bybit"] = "mainnet"
                self.logger.info("Bybit active environment: mainnet")
            else:
                raise ConfigValidationError(
                    "No active environment for Bybit (either demo or mainnet must be enabled)",
                    "exchanges.yaml",
                    "bybit.demo/mainnet"
                )
        
        # Проверяем Bitget (если включен)
        bitget_config = exchanges_config.get("bitget", {})
        if bitget_config.get("enabled", False):
            demo_enabled = bitget_config.get("demo", {}).get("enabled", False)
            mainnet_enabled = bitget_config.get("mainnet", {}).get("enabled", False)
            
            if demo_enabled and mainnet_enabled:
                raise ConfigValidationError(
                    "Both demo and mainnet cannot be enabled simultaneously for Bitget",
                    "exchanges.yaml",
                    "bitget.demo/mainnet"
                )
            elif demo_enabled:
                self._active_environments["bitget"] = "demo"
                self.logger.info("Bitget active environment: demo")
            elif mainnet_enabled:
                self._active_environments["bitget"] = "mainnet"
                self.logger.info("Bitget active environment: mainnet")
    
    def _validate_all_configs(self, configs: Dict[str, Dict[str, Any]]):
        """
        Валидация всех загруженных конфигураций
        
        Args:
            configs: Словарь конфигураций для валидации
        """
        try:
            # Валидация основной конфигурации
            self._validate_main_config(configs.get("config", {}))
            
            # Валидация конфигурации бирж
            self._validate_exchanges_config(configs.get("exchanges", {}))
            
            # Валидация конфигурации стратегий
            self._validate_strategies_config(configs.get("strategies", {}))
            
            self.logger.info("✅ All configs validated successfully")
            
        except Exception as e:
            self.logger.error(f"Config validation failed: {e}")
            raise
    
    def _validate_main_config(self, config: Dict[str, Any]):
        """Валидация основной конфигурации"""
        required_sections = ["app", "logging", "database"]
        
        for section in required_sections:
            if section not in config:
                raise ConfigValidationError(
                    f"Missing required section: {section}",
                    "config.yaml",
                    section
                )
        
        # Проверяем настройки приложения
        app_config = config.get("app", {})
        if not app_config.get("name"):
            raise ConfigValidationError(
                "App name is required",
                "config.yaml", 
                "app.name"
            )
        
        # Проверяем настройки логирования
        logging_config = config.get("logging", {})
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if logging_config.get("level") not in valid_levels:
            raise ConfigValidationError(
                f"Invalid logging level. Must be one of: {valid_levels}",
                "config.yaml",
                "logging.level"
            )
    
    def _validate_exchanges_config(self, config: Dict[str, Any]):
        """Валидация конфигурации бирж с поддержкой demo/mainnet"""
        if not config.get("exchanges", {}).get("enabled"):
            raise ConfigValidationError(
                "At least one exchange must be enabled",
                "exchanges.yaml",
                "exchanges.enabled"
            )
        
        # Проверяем конфигурацию Bybit
        bybit_config = config.get("bybit", {})
        if bybit_config.get("enabled", False):
            # Определяем активное окружение
            demo_enabled = bybit_config.get("demo", {}).get("enabled", False)
            mainnet_enabled = bybit_config.get("mainnet", {}).get("enabled", False)
            
            # Проверяем что хотя бы одно окружение включено
            if not demo_enabled and not mainnet_enabled:
                raise ConfigValidationError(
                    "No active environment for Bybit (either demo or mainnet must be enabled)",
                    "exchanges.yaml",
                    "bybit.demo/mainnet"
                )
            
            # Проверяем что не включены оба одновременно
            if demo_enabled and mainnet_enabled:
                raise ConfigValidationError(
                    "Both demo and mainnet are enabled for Bybit - choose one",
                    "exchanges.yaml",
                    "bybit.demo/mainnet"
                )
            
            # Определяем какое окружение активно
            active_env = "demo" if demo_enabled else "mainnet"
            
            # Проверяем наличие API ключей для активного окружения
            api_creds = bybit_config.get("api_credentials", {}).get(active_env, {})
            if not api_creds.get("api_key") or not api_creds.get("api_secret"):
                raise ConfigValidationError(
                    f"Bybit {active_env} API credentials are required",
                    "exchanges.yaml",
                    f"bybit.api_credentials.{active_env}"
                )
            
            # Проверяем настройки окружения
            env_config = bybit_config.get(active_env, {})
            required_fields = ["base_url", "ws_public", "ws_private"]
            for field in required_fields:
                if not env_config.get(field):
                    raise ConfigValidationError(
                        f"Missing required field {field} for Bybit {active_env}",
                        "exchanges.yaml",
                        f"bybit.{active_env}.{field}"
                    )
    
    def _validate_strategies_config(self, config: Dict[str, Any]):
        """Валидация конфигурации стратегий"""
        active_strategies = config.get("active_strategies", {})
        
        if not active_strategies.get("primary"):
            raise ConfigValidationError(
                "Primary strategy must be specified",
                "strategies.yaml",
                "active_strategies.primary"
            )
        
        # Проверяем конфигурацию Vortex Bands
        vortex_config = config.get("vortex_bands", {})
        if active_strategies.get("primary") == "vortex_bands":
            if not vortex_config.get("instruments"):
                raise ConfigValidationError(
                    "Vortex Bands instruments configuration is required",
                    "strategies.yaml",
                    "vortex_bands.instruments"
                )
            
            # Проверяем параметры инструментов
            for symbol, params in vortex_config.get("instruments", {}).items():
                required_params = ["length", "multiplier", "alert_distance_1", "alert_distance_2"]
                for param in required_params:
                    if param not in params:
                        raise ConfigValidationError(
                            f"Missing required parameter {param} for {symbol}",
                            "strategies.yaml",
                            f"vortex_bands.instruments.{symbol}.{param}"
                        )
    
    def get_config(self, config_name: str) -> Dict[str, Any]:
        """
        Получение конфигурации по имени
        
        Args:
            config_name: Имя конфигурации
            
        Returns:
            Словарь с конфигурацией
        """
        if config_name not in self._configs:
            self.load_config(config_name)
        
        return self._configs.get(config_name, {})
    
    def get_config_value(self, config_name: str, key_path: str, default: Any = None) -> Any:
        """
        Получение значения из конфигурации по пути
        
        Args:
            config_name: Имя конфигурации
            key_path: Путь к значению (через точку)
            default: Значение по умолчанию
            
        Returns:
            Значение или default
        """
        config = self.get_config(config_name)
        
        keys = key_path.split('.')
        value = config
        
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        
        return value if value is not None else default
    
    def reload_config(self, config_name: Optional[str] = None) -> bool:
        """
        Перезагрузка конфигурации
        
        Args:
            config_name: Имя конфигурации или None для всех
            
        Returns:
            True если успешно перезагружено
        """
        try:
            if config_name:
                self.load_config(config_name)
                self.logger.info(f"Reloaded config: {config_name}")
            else:
                self.load_all_configs()
                self.logger.info("Reloaded all configs")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to reload config: {e}")
            return False
    
    def get_vortex_instrument_config(self, symbol: str) -> Dict[str, Any]:
        """
        Получение конфигурации Vortex Bands для конкретного инструмента
        
        Args:
            symbol: Торговый символ
            
        Returns:
            Конфигурация инструмента или дефолтные параметры
        """
        strategies_config = self.get_config("strategies")
        vortex_config = strategies_config.get("vortex_bands", {})
        
        # Ищем конфигурацию для символа
        instruments = vortex_config.get("instruments", {})
        if symbol in instruments:
            return instruments[symbol]
        
        # Возвращаем дефолтные параметры
        default_params = vortex_config.get("default_parameters", {
            "length": 20,
            "multiplier": 2.0,
            "alert_distance_2": 2.5,
            "alert_distance_1": 1.5,
            "flag_reset_distance": 2.5,
            "min_bars_required": 20
        })
        
        self.logger.warning(f"Using default Vortex parameters for {symbol}")
        return default_params
    
    def get_active_exchange_config(self, exchange: str) -> Dict[str, Any]:
        """
        Получение полной конфигурации активного окружения биржи
        
        Args:
            exchange: Название биржи (bybit, bitget)
            
        Returns:
            Словарь с конфигурацией активного окружения
        """
        exchanges_config = self.get_config("exchanges")
        exchange_config = exchanges_config.get(exchange, {})
        
        if not exchange_config.get("enabled", False):
            self.logger.warning(f"Exchange {exchange} is not enabled")
            return {}
        
        # Получаем активное окружение
        active_env = self._active_environments.get(exchange)
        if not active_env:
            self.logger.warning(f"No active environment found for {exchange}")
            return {}
        
        # Собираем полную конфигурацию активного окружения
        env_config = exchange_config.get(active_env, {})
        api_creds = exchange_config.get("api_credentials", {}).get(active_env, {})
        
        # Объединяем все в единую структуру
        result = {
            "exchange": exchange,
            "environment": active_env,
            "base_url": env_config.get("base_url"),
            "ws_public": env_config.get("ws_public"),
            "ws_private": env_config.get("ws_private"),
            "api_key": api_creds.get("api_key"),
            "api_secret": api_creds.get("api_secret"),
            "recv_window": api_creds.get("recv_window", 5000)
        }
        
        # Добавляем дополнительные параметры из конфига
        if "trading" in exchange_config:
            result["trading"] = exchange_config["trading"]
        
        return result
    
    def get_exchange_credentials(self, exchange: str, environment: Optional[str] = None) -> Dict[str, str]:
        """
        Получение API ключей для биржи с учетом активного окружения
        
        Args:
            exchange: Название биржи (bybit, bitget)
            environment: Окружение (demo/mainnet) или None для автоопределения
            
        Returns:
            Словарь с API ключами
        """
        exchanges_config = self.get_config("exchanges")
        exchange_config = exchanges_config.get(exchange, {})
        
        # Если окружение не указано, используем активное
        if environment is None:
            environment = self._active_environments.get(exchange)
            if not environment:
                self.logger.warning(f"No active environment found for {exchange}")
                return {}
        
        credentials = exchange_config.get("api_credentials", {}).get(environment, {})
        
        if not credentials.get("api_key") or not credentials.get("api_secret"):
            self.logger.warning(f"Missing API credentials for {exchange} {environment}")
        
        return credentials
    
    def get_active_environment(self, exchange: str) -> Optional[str]:
        """
        Получение активного окружения для биржи
        
        Args:
            exchange: Название биржи
            
        Returns:
            Название активного окружения или None
        """
        return self._active_environments.get(exchange)
    
    def is_demo_mode(self, exchange: str) -> bool:
        """
        Проверка работы в демо-режиме
        
        Args:
            exchange: Название биржи
            
        Returns:
            True если демо режим
        """
        env = self.get_active_environment(exchange)
        return env == "demo"


# Глобальный экземпляр загрузчика конфигураций
config_loader = ConfigLoader()


# Удобные функции для быстрого доступа к конфигурациям
def get_app_config() -> Dict[str, Any]:
    """Получение конфигурации приложения"""
    return config_loader.get_config_value("config", "app", {})


def get_database_config() -> Dict[str, Any]:
    """Получение конфигурации базы данных"""
    return config_loader.get_config_value("config", "database", {})


def get_logging_config() -> Dict[str, Any]:
    """Получение конфигурации логирования"""
    return config_loader.get_config_value("config", "logging", {})


def get_bybit_credentials(testnet: bool = True) -> Dict[str, str]:
    """
    Получение Bybit API ключей (для обратной совместимости)
    
    Args:
        testnet: True для demo, False для mainnet
        
    Returns:
        Словарь с API ключами
    """
    environment = "demo" if testnet else "mainnet"
    return config_loader.get_exchange_credentials("bybit", environment)


def get_bybit_active_config() -> Dict[str, Any]:
    """Получение полной конфигурации активного окружения Bybit"""
    return config_loader.get_active_exchange_config("bybit")


def get_vortex_config(symbol: str) -> Dict[str, Any]:
    """Получение конфигурации Vortex Bands для символа"""
    return config_loader.get_vortex_instrument_config(symbol)


def reload_all_configs() -> bool:
    """Перезагрузка всех конфигураций"""
    return config_loader.reload_config()


# Инициализация при импорте модуля
try:
    config_loader.load_all_configs()
    logging.getLogger("ConfigLoader").info("🎉 Configuration system initialized successfully")
except Exception as e:
    logging.getLogger("ConfigLoader").error(f"❌ Failed to initialize configuration system: {e}")
    raise