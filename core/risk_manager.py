"""
Система управления рисками для торгового бота
Полная доработка с дневными/недельными лимитами и интеграцией в Telegram
"""
from __future__ import annotations
import asyncio
import time
import logging
import yaml
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from collections import defaultdict, deque
from pathlib import Path

try:
    from exchanges.base_exchange import BaseExchange  # путь как в твоём проекте
except Exception:
    # fallback если иной layout; но у тебя именно так
    from base_exchange import BaseExchange

from config.config_loader import config_loader


@dataclass
class RiskMetrics:
    """Текущие метрики рисков"""
    # Дневные метрики
    daily_pnl: float = 0.0
    daily_trades_count: int = 0
    daily_start_balance: float = 0.0
    daily_current_balance: float = 0.0
    daily_drawdown_pct: float = 0.0
    
    # Недельные метрики
    weekly_pnl: float = 0.0
    weekly_trades_count: int = 0
    weekly_start_balance: float = 0.0
    weekly_current_balance: float = 0.0
    weekly_drawdown_pct: float = 0.0
    
    # Позиции
    current_positions: int = 0
    max_leverage_used: float = 1.0
    
    # Состояние торговли
    trading_allowed: bool = True
    halt_reason: str = ""
    last_trade_time: float = 0.0
    
    # Время последних ресетов
    last_daily_reset: float = 0.0
    last_weekly_reset: float = 0.0


# Глобальные переменные для модульного состояния (потокобезопасно через lock)
_lock = asyncio.Lock()
_risk_config = {}
_risk_metrics = RiskMetrics()
_initialized = False

# Геттеры/сеттеры для конфигурации рисков (потокобезопасно)
async def get_risk_enabled() -> bool:
    """Получить статус включения риск-менеджмента"""
    async with _lock:
        return _risk_config.get("enabled", True)

async def set_risk_enabled(enabled: bool) -> bool:
    """Установить статус включения риск-менеджмента"""
    async with _lock:
        try:
            _risk_config["enabled"] = bool(enabled)
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Risk management {'enabled' if enabled else 'disabled'}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set risk enabled: {e}")
            return False

async def get_daily_max_abs_loss() -> float:
    """Получить дневной лимит убытка"""
    async with _lock:
        return _risk_config.get("daily", {}).get("max_abs_loss", 300.0)

async def set_daily_max_abs_loss(value: float) -> bool:
    """Установить дневной лимит убытка"""
    async with _lock:
        try:
            if not isinstance(value, (int, float)) or value <= 0:
                return False
            
            if "daily" not in _risk_config:
                _risk_config["daily"] = {}
            _risk_config["daily"]["max_abs_loss"] = float(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Daily max absolute loss set to: {value}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set daily max abs loss: {e}")
            return False

async def get_daily_max_drawdown_pct() -> float:
    """Получить дневной лимит просадки в %"""
    async with _lock:
        return _risk_config.get("daily", {}).get("max_drawdown_pct", 8.0)

async def set_daily_max_drawdown_pct(value: float) -> bool:
    """Установить дневной лимит просадки в %"""
    async with _lock:
        try:
            if not isinstance(value, (int, float)) or value <= 0 or value > 100:
                return False
            
            if "daily" not in _risk_config:
                _risk_config["daily"] = {}
            _risk_config["daily"]["max_drawdown_pct"] = float(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Daily max drawdown set to: {value}%")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set daily max drawdown: {e}")
            return False

async def get_daily_max_trades() -> int:
    """Получить дневной лимит сделок"""
    async with _lock:
        return _risk_config.get("daily", {}).get("max_trades", 50)

async def set_daily_max_trades(value: int) -> bool:
    """Установить дневной лимит сделок"""
    async with _lock:
        try:
            if not isinstance(value, int) or value <= 0:
                return False
            
            if "daily" not in _risk_config:
                _risk_config["daily"] = {}
            _risk_config["daily"]["max_trades"] = int(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Daily max trades set to: {value}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set daily max trades: {e}")
            return False

async def get_daily_reset_time_utc() -> str:
    """Получить время ресета дневных счетчиков"""
    async with _lock:
        return _risk_config.get("daily", {}).get("reset_time_utc", "00:00")

async def set_daily_reset_time_utc(time_str: str) -> bool:
    """Установить время ресета дневных счетчиков (HH:MM)"""
    async with _lock:
        try:
            # Валидация формата времени
            time_parts = time_str.split(":")
            if len(time_parts) != 2:
                return False
            
            hour, minute = int(time_parts[0]), int(time_parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return False
            
            if "daily" not in _risk_config:
                _risk_config["daily"] = {}
            _risk_config["daily"]["reset_time_utc"] = time_str
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Daily reset time set to: {time_str} UTC")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set daily reset time: {e}")
            return False

async def get_weekly_max_abs_loss() -> float:
    """Получить недельный лимит убытка"""
    async with _lock:
        return _risk_config.get("weekly", {}).get("max_abs_loss", 1000.0)

async def set_weekly_max_abs_loss(value: float) -> bool:
    """Установить недельный лимит убытка"""
    async with _lock:
        try:
            if not isinstance(value, (int, float)) or value <= 0:
                return False
            
            if "weekly" not in _risk_config:
                _risk_config["weekly"] = {}
            _risk_config["weekly"]["max_abs_loss"] = float(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Weekly max absolute loss set to: {value}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set weekly max abs loss: {e}")
            return False

async def get_weekly_max_drawdown_pct() -> float:
    """Получить недельный лимит просадки в %"""
    async with _lock:
        return _risk_config.get("weekly", {}).get("max_drawdown_pct", 20.0)

async def set_weekly_max_drawdown_pct(value: float) -> bool:
    """Установить недельный лимит просадки в %"""
    async with _lock:
        try:
            if not isinstance(value, (int, float)) or value <= 0 or value > 100:
                return False
            
            if "weekly" not in _risk_config:
                _risk_config["weekly"] = {}
            _risk_config["weekly"]["max_drawdown_pct"] = float(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Weekly max drawdown set to: {value}%")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set weekly max drawdown: {e}")
            return False

async def get_weekly_reset_dow_utc() -> str:
    """Получить день недели ресета недельных счетчиков"""
    async with _lock:
        return _risk_config.get("weekly", {}).get("reset_dow_utc", "MONDAY")

async def set_weekly_reset_dow_utc(dow: str) -> bool:
    """Установить день недели ресета недельных счетчиков"""
    async with _lock:
        try:
            valid_days = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
            if dow.upper() not in valid_days:
                return False
            
            if "weekly" not in _risk_config:
                _risk_config["weekly"] = {}
            _risk_config["weekly"]["reset_dow_utc"] = dow.upper()
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Weekly reset day set to: {dow.upper()}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set weekly reset day: {e}")
            return False

async def get_position_max_risk_pct() -> float:
    """Получить максимальный риск на позицию в %"""
    async with _lock:
        return _risk_config.get("position", {}).get("max_risk_pct", 1.0)

async def set_position_max_risk_pct(value: float) -> bool:
    """Установить максимальный риск на позицию в %"""
    async with _lock:
        try:
            if not isinstance(value, (int, float)) or value <= 0 or value > 100:
                return False
            
            if "position" not in _risk_config:
                _risk_config["position"] = {}
            _risk_config["position"]["max_risk_pct"] = float(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Position max risk set to: {value}%")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set position max risk: {e}")
            return False

async def get_position_max_leverage() -> int:
    """Получить максимальное плечо"""
    async with _lock:
        return _risk_config.get("position", {}).get("max_leverage", 10)

async def set_position_max_leverage(value: int) -> bool:
    """Установить максимальное плечо"""
    async with _lock:
        try:
            if not isinstance(value, int) or value <= 0 or value > 125:
                return False
            
            if "position" not in _risk_config:
                _risk_config["position"] = {}
            _risk_config["position"]["max_leverage"] = int(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Max leverage set to: {value}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set max leverage: {e}")
            return False

async def get_position_max_concurrent() -> int:
    """Получить максимальное количество позиций"""
    async with _lock:
        return _risk_config.get("position", {}).get("max_concurrent_positions", 3)

async def set_position_max_concurrent(value: int) -> bool:
    """Установить максимальное количество позиций"""
    async with _lock:
        try:
            if not isinstance(value, int) or value <= 0:
                return False
            
            if "position" not in _risk_config:
                _risk_config["position"] = {}
            _risk_config["position"]["max_concurrent_positions"] = int(value)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Max concurrent positions set to: {value}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set max concurrent positions: {e}")
            return False

async def get_circuit_breaker_hard_stop() -> bool:
    """Получить статус жесткой остановки"""
    async with _lock:
        return _risk_config.get("circuit_breaker", {}).get("hard_stop", True)

async def set_circuit_breaker_hard_stop(enabled: bool) -> bool:
    """Установить статус жесткой остановки"""
    async with _lock:
        try:
            if "circuit_breaker" not in _risk_config:
                _risk_config["circuit_breaker"] = {}
            _risk_config["circuit_breaker"]["hard_stop"] = bool(enabled)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Circuit breaker hard stop {'enabled' if enabled else 'disabled'}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set circuit breaker hard stop: {e}")
            return False

async def get_circuit_breaker_cool_down() -> int:
    """Получить время остывания в минутах"""
    async with _lock:
        return _risk_config.get("circuit_breaker", {}).get("cool_down_minutes", 120)

async def set_circuit_breaker_cool_down(minutes: int) -> bool:
    """Установить время остывания в минутах"""
    async with _lock:
        try:
            if not isinstance(minutes, int) or minutes < 0:
                return False
            
            if "circuit_breaker" not in _risk_config:
                _risk_config["circuit_breaker"] = {}
            _risk_config["circuit_breaker"]["cool_down_minutes"] = int(minutes)
            
            await _save_config_if_allowed()
            logger = logging.getLogger("RiskManager")
            logger.info(f"Circuit breaker cool down set to: {minutes} minutes")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set circuit breaker cool down: {e}")
            return False

async def get_persist_runtime_updates() -> bool:
    """Получить статус сохранения runtime обновлений"""
    async with _lock:
        return _risk_config.get("persist_runtime_updates", True)

async def set_persist_runtime_updates(enabled: bool) -> bool:
    """Установить статус сохранения runtime обновлений"""
    async with _lock:
        try:
            _risk_config["persist_runtime_updates"] = bool(enabled)
            
            logger = logging.getLogger("RiskManager")
            logger.info(f"Persist runtime updates {'enabled' if enabled else 'disabled'}")
            return True
        except Exception as e:
            logger = logging.getLogger("RiskManager")
            logger.error(f"Failed to set persist runtime updates: {e}")
            return False

async def _save_config_if_allowed():
    """Сохранить конфигурацию в файл, если разрешено"""
    if not _risk_config.get("persist_runtime_updates", True):
        return
    
    try:
        config_file_path = Path("config/config.yaml")
        
        if not config_file_path.exists():
            logger = logging.getLogger("RiskManager")
            logger.warning("Config file not found, runtime updates not saved")
            return
        
        # Загружаем текущий конфиг файл
        with open(config_file_path, 'r', encoding='utf-8') as f:
            full_config = yaml.safe_load(f) or {}
        
        # Обновляем секцию risk
        full_config["risk"] = _risk_config.copy()
        
        # Сохраняем обратно
        with open(config_file_path, 'w', encoding='utf-8') as f:
            yaml.dump(full_config, f, default_flow_style=False, indent=2, allow_unicode=True)
        
        logger = logging.getLogger("RiskManager")
        logger.info("Risk configuration saved to file")
        
    except Exception as e:
        logger = logging.getLogger("RiskManager")
        logger.error(f"Failed to save config: {e}")


class RiskManager:
    """
    Менеджер рисков - система контроля дневных/недельных лимитов
    Интегрируется с TradingEngine и Telegram
    """
    
    def __init__(self, exchange: BaseExchange, initial_capital: Optional[float] = None, **kwargs):
        """
        :param exchange: адаптер биржи
        :param initial_capital: начальный капитал из strategies.yaml (может быть None)
        :param kwargs: будущие необязательные параметры; игнорируются безопасно
        """
        self.initialized: bool = False
        self.exchange = exchange
        self.logger = logging.getLogger("RiskManager")

        self.initial_capital = float(initial_capital) if initial_capital is not None else None

        # История сделок/метрик для расчётов (пример)
        self.trade_history = deque(maxlen=1000)

        self.logger.info(
            f"RiskManager initialized (initial_capital={self.initial_capital if self.initial_capital is not None else 'auto'})"
        )

    def is_initialized(self) -> bool:
        """
        ПАТЧ: Метод проверки инициализации - единый источник истины
        Проверяет как инстанс, так и глобальную переменную для обратной совместимости
    
        Returns:
            True если инициализирован
        """
        global _initialized
        # Проверяем ОБА источника для обратной совместимости
        return bool(self.initialized) or _initialized

    async def initialize(self) -> bool:
        """
        Если нужно — подтягиваем баланс/лимиты от биржи и калибруем дневные лимиты.
    
        ПАТЧ: Устанавливаем флаг инициализации как на инстансе, так и глобально
        для полной совместимости
        """
        global _initialized, _risk_metrics  # Добавляем глобальные переменные
    
        try:
            # Загружаем конфигурацию рисков
            await self._load_risk_config()
    
            # Если капитал не задан, возьмём кошелёк биржи
            if self.initial_capital is None:
                try:
                    balance = await self.exchange.get_balance("USDT")
                    if balance and hasattr(balance, 'wallet_balance'):
                        self.initial_capital = float(balance.wallet_balance)
                        self.logger.info(f"Initial capital calibrated from wallet: {self.initial_capital}")
                except Exception as e:
                    self.logger.warning(f"Failed to calibrate initial capital from wallet: {e}")
                    # НЕ устанавливаем фейковое значение!
                    # Если не можем получить баланс - это ошибка
                    return False
    
            # Устанавливаем флаг инициализации ТОЛЬКО если есть капитал
            if self.initial_capital is not None and self.initial_capital > 0:
                # ПАТЧ: Устанавливаем признак инициализации на инстансе
                self.initialized = True
                # И синхронизируем с глобальной переменной для обратной совместимости
                _initialized = True
        
                # Инициализируем метрики
                _risk_metrics.daily_start_balance = self.initial_capital
                _risk_metrics.daily_current_balance = self.initial_capital
                _risk_metrics.weekly_start_balance = self.initial_capital
                _risk_metrics.weekly_current_balance = self.initial_capital
            
                # ПАТЧ: Регистрируем синглтон если есть функция set_risk_manager
                try:
                    set_risk_manager(self)
                except NameError:
                    pass  # Функция может быть не определена в некоторых контекстах
        
                self.logger.info(f"✅ RiskManager fully initialized with capital: {self.initial_capital}")
                return True
            else:
                self.logger.error("Cannot initialize RiskManager without initial capital")
                # ПАТЧ: Синхронизируем оба флага при неудачной инициализации
                self.initialized = False
                _initialized = False
                return False
    
        except Exception as e:
            self.logger.error(f"RiskManager initialize() error: {e}")
            # ПАТЧ: Синхронизируем оба флага при исключении
            self.initialized = False
            _initialized = False
            return False
    
    async def _load_risk_config(self):
        """Загрузка конфигурации рисков"""
        global _risk_config
        
        try:
            main_config = config_loader.get_config("config")
            risk_section = main_config.get("risk", {})
            
            # Дефолтные значения, если секции нет
            if not risk_section:
                _risk_config = {
                    "enabled": True,
                    "currency": "USDT",
                    "daily": {
                        "max_abs_loss": 300.0,
                        "max_drawdown_pct": 8.0,
                        "max_trades": 50,
                        "reset_time_utc": "00:00"
                    },
                    "weekly": {
                        "max_abs_loss": 1000.0,
                        "max_drawdown_pct": 20.0,
                        "reset_dow_utc": "MONDAY"
                    },
                    "position": {
                        "max_risk_pct": 1.0,
                        "max_leverage": 10,
                        "max_concurrent_positions": 3
                    },
                    "circuit_breaker": {
                        "hard_stop": True,
                        "cool_down_minutes": 120
                    },
                    "persist_runtime_updates": True
                }
                self.logger.warning("Risk section not found in config, using defaults")
            else:
                _risk_config = risk_section.copy()
                
            self.logger.info("Risk configuration loaded successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to load risk config: {e}")
            # Используем дефолтные значения при ошибке
            _risk_config = {
                "enabled": True,
                "currency": "USDT",
                "daily": {"max_abs_loss": 300.0, "max_drawdown_pct": 8.0, "max_trades": 50, "reset_time_utc": "00:00"},
                "weekly": {"max_abs_loss": 1000.0, "max_drawdown_pct": 20.0, "reset_dow_utc": "MONDAY"},
                "position": {"max_risk_pct": 1.0, "max_leverage": 10, "max_concurrent_positions": 3},
                "circuit_breaker": {"hard_stop": True, "cool_down_minutes": 120},
                "persist_runtime_updates": True
            }
    
    async def _initialize_metrics(self):
        """Инициализация метрик рисков"""
        global _risk_metrics
        
        try:
            # Получаем текущий баланс
            balance = await self.exchange.get_balance()
            current_balance = balance.wallet_balance if balance else 0.0
            
            now = time.time()
            
            # Инициализируем метрики, если они пустые
            if _risk_metrics.daily_start_balance == 0.0:
                _risk_metrics.daily_start_balance = current_balance
                _risk_metrics.last_daily_reset = now
                
            if _risk_metrics.weekly_start_balance == 0.0:
                _risk_metrics.weekly_start_balance = current_balance
                _risk_metrics.last_weekly_reset = now
                
            _risk_metrics.daily_current_balance = current_balance
            _risk_metrics.weekly_current_balance = current_balance
            
            # Пересчитываем P&L
            _risk_metrics.daily_pnl = current_balance - _risk_metrics.daily_start_balance
            _risk_metrics.weekly_pnl = current_balance - _risk_metrics.weekly_start_balance
            
            # Пересчитываем просадки
            if _risk_metrics.daily_start_balance > 0:
                _risk_metrics.daily_drawdown_pct = max(0, 
                    ((_risk_metrics.daily_start_balance - current_balance) / _risk_metrics.daily_start_balance) * 100)
                    
            if _risk_metrics.weekly_start_balance > 0:
                _risk_metrics.weekly_drawdown_pct = max(0, 
                    ((_risk_metrics.weekly_start_balance - current_balance) / _risk_metrics.weekly_start_balance) * 100)
            
            # Обновляем количество позиций
            positions = await self.exchange.get_positions()
            _risk_metrics.current_positions = len([p for p in positions if p.size > 0])
            
            self.logger.info(
                f"Risk metrics initialized: daily_pnl={_risk_metrics.daily_pnl:.2f}, "
                f"weekly_pnl={_risk_metrics.weekly_pnl:.2f}, positions={_risk_metrics.current_positions}"
            )
            
        except Exception as e:
            self.logger.error(f"Failed to initialize metrics: {e}")
    
    async def _check_and_perform_resets(self):
        """Проверка и выполнение ресетов по времени"""
        global _risk_metrics
        
        try:
            now = datetime.now(timezone.utc)
            
            # Проверяем дневной ресет
            await self._check_daily_reset(now)
            
            # Проверяем недельный ресет
            await self._check_weekly_reset(now)
            
        except Exception as e:
            self.logger.error(f"Failed to check resets: {e}")
    
    async def _check_daily_reset(self, now: datetime):
        """Проверка и выполнение дневного ресета"""
        global _risk_metrics
        
        try:
            reset_time_str = await get_daily_reset_time_utc()
            reset_hour, reset_minute = map(int, reset_time_str.split(":"))
            
            # Время ресета сегодня
            reset_time_today = now.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
            
            # Последний ресет
            last_reset = datetime.fromtimestamp(_risk_metrics.last_daily_reset, tz=timezone.utc)
            
            # Если текущее время больше времени ресета сегодня и последний ресет был вчера или раньше
            if now >= reset_time_today and last_reset.date() < now.date():
                await self._perform_daily_reset()
                
        except Exception as e:
            self.logger.error(f"Failed to check daily reset: {e}")
    
    async def _check_weekly_reset(self, now: datetime):
        """Проверка и выполнение недельного ресета"""
        global _risk_metrics
        
        try:
            reset_dow_str = await get_weekly_reset_dow_utc()
            reset_dow = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"].index(reset_dow_str)
            
            # Последний ресет
            last_reset = datetime.fromtimestamp(_risk_metrics.last_weekly_reset, tz=timezone.utc)
            
            # Если сегодня нужный день недели и прошло больше недели с последнего ресета
            if now.weekday() == reset_dow and (now - last_reset).days >= 7:
                # Дополнительно проверяем, что это время после 00:00
                if now.hour >= 0:
                    await self._perform_weekly_reset()
                    
        except Exception as e:
            self.logger.error(f"Failed to check weekly reset: {e}")
    
    async def _perform_daily_reset(self):
        """Выполнение дневного ресета"""
        global _risk_metrics
        
        try:
            # Получаем текущий баланс
            balance = await self.exchange.get_balance()
            current_balance = balance.wallet_balance if balance else 0.0
            
            # Ресетим дневные метрики
            _risk_metrics.daily_start_balance = current_balance
            _risk_metrics.daily_current_balance = current_balance
            _risk_metrics.daily_pnl = 0.0
            _risk_metrics.daily_trades_count = 0
            _risk_metrics.daily_drawdown_pct = 0.0
            _risk_metrics.last_daily_reset = time.time()
            
            # Если торговля была остановлена из-за дневных лимитов, возобновляем её
            if not _risk_metrics.trading_allowed and "daily" in _risk_metrics.halt_reason.lower():
                _risk_metrics.trading_allowed = True
                _risk_metrics.halt_reason = ""
            
            self.logger.info(f"✅ Daily reset performed: start_balance={current_balance:.2f}")
            
        except Exception as e:
            self.logger.error(f"Failed to perform daily reset: {e}")
    
    async def _perform_weekly_reset(self):
        """Выполнение недельного ресета"""
        global _risk_metrics
        
        try:
            # Получаем текущий баланс
            balance = await self.exchange.get_balance()
            current_balance = balance.wallet_balance if balance else 0.0
            
            # Ресетим недельные метрики
            _risk_metrics.weekly_start_balance = current_balance
            _risk_metrics.weekly_current_balance = current_balance
            _risk_metrics.weekly_pnl = 0.0
            _risk_metrics.weekly_trades_count = 0
            _risk_metrics.weekly_drawdown_pct = 0.0
            _risk_metrics.last_weekly_reset = time.time()
            
            # Если торговля была остановлена из-за недельных лимитов, возобновляем её
            if not _risk_metrics.trading_allowed and "weekly" in _risk_metrics.halt_reason.lower():
                _risk_metrics.trading_allowed = True
                _risk_metrics.halt_reason = ""
            
            self.logger.info(f"✅ Weekly reset performed: start_balance={current_balance:.2f}")
            
        except Exception as e:
            self.logger.error(f"Failed to perform weekly reset: {e}")
    
    async def check_trade_permission(self, symbol: str, side: str, 
                                       position_value: float, leverage: int = 1) -> Tuple[bool, str]:
        """
        Проверка разрешения на открытие сделки (pre-trade check)
    
        Args:
            symbol: Торговый символ
            side: Направление сделки (Buy/Sell)
            position_value: Стоимость позиции в USDT
            leverage: Плечо
        
        Returns:
            Tuple (разрешено, причина отказа)
        """
        global _risk_metrics
    
        try:
            # ПАТЧ: Используем метод is_initialized вместо прямой проверки глобальной переменной
            if not self.is_initialized():
                return False, "Risk manager not initialized"
        
            # Проверяем включение риск-менеджмента
            if not await get_risk_enabled():
                return True, "Risk management disabled"
        
            # Проверяем ресеты
            await self._check_and_perform_resets()
        
            # Проверяем общее состояние торговли
            if not _risk_metrics.trading_allowed:
                return False, f"Trading halted: {_risk_metrics.halt_reason}"
        
            # Проверяем дневные лимиты
            permission, reason = await self._check_daily_limits()
            if not permission:
                return False, reason
        
            # Проверяем недельные лимиты
            permission, reason = await self._check_weekly_limits()
            if not permission:
                return False, reason
        
            # Проверяем лимиты позиций
            permission, reason = await self._check_position_limits(position_value, leverage)
            if not permission:
                return False, reason
        
            return True, "OK"
        
        except Exception as e:
            self.logger.error(f"Error checking trade permission: {e}")
            return False, f"Risk check error: {e}"
    
    async def _check_daily_limits(self) -> Tuple[bool, str]:
        """Проверка дневных лимитов"""
        global _risk_metrics
        
        try:
            # Проверяем абсолютный убыток
            max_abs_loss = await get_daily_max_abs_loss()
            if abs(_risk_metrics.daily_pnl) >= max_abs_loss and _risk_metrics.daily_pnl < 0:
                await self._halt_trading(f"Daily absolute loss limit exceeded: {abs(_risk_metrics.daily_pnl):.2f} >= {max_abs_loss}")
                return False, f"Daily loss limit exceeded: {abs(_risk_metrics.daily_pnl):.2f}/{max_abs_loss}"
            
            # Проверяем просадку
            max_drawdown = await get_daily_max_drawdown_pct()
            if _risk_metrics.daily_drawdown_pct >= max_drawdown:
                await self._halt_trading(f"Daily drawdown limit exceeded: {_risk_metrics.daily_drawdown_pct:.2f}% >= {max_drawdown}%")
                return False, f"Daily drawdown limit exceeded: {_risk_metrics.daily_drawdown_pct:.2f}%/{max_drawdown}%"
            
            # Проверяем количество сделок
            max_trades = await get_daily_max_trades()
            if _risk_metrics.daily_trades_count >= max_trades:
                await self._halt_trading(f"Daily trades limit exceeded: {_risk_metrics.daily_trades_count} >= {max_trades}")
                return False, f"Daily trades limit exceeded: {_risk_metrics.daily_trades_count}/{max_trades}"
            
            return True, "Daily limits OK"
            
        except Exception as e:
            self.logger.error(f"Error checking daily limits: {e}")
            return False, f"Daily limits check error: {e}"
    
    async def _check_weekly_limits(self) -> Tuple[bool, str]:
        """Проверка недельных лимитов"""
        global _risk_metrics
        
        try:
            # Проверяем абсолютный убыток
            max_abs_loss = await get_weekly_max_abs_loss()
            if abs(_risk_metrics.weekly_pnl) >= max_abs_loss and _risk_metrics.weekly_pnl < 0:
                await self._halt_trading(f"Weekly absolute loss limit exceeded: {abs(_risk_metrics.weekly_pnl):.2f} >= {max_abs_loss}")
                return False, f"Weekly loss limit exceeded: {abs(_risk_metrics.weekly_pnl):.2f}/{max_abs_loss}"
            
            # Проверяем просадку
            max_drawdown = await get_weekly_max_drawdown_pct()
            if _risk_metrics.weekly_drawdown_pct >= max_drawdown:
                await self._halt_trading(f"Weekly drawdown limit exceeded: {_risk_metrics.weekly_drawdown_pct:.2f}% >= {max_drawdown}%")
                return False, f"Weekly drawdown limit exceeded: {_risk_metrics.weekly_drawdown_pct:.2f}%/{max_drawdown}%"
            
            return True, "Weekly limits OK"
            
        except Exception as e:
            self.logger.error(f"Error checking weekly limits: {e}")
            return False, f"Weekly limits check error: {e}"
    
    async def _check_position_limits(self, position_value: float, leverage: int) -> Tuple[bool, str]:
        """Проверка лимитов позиций"""
        global _risk_metrics
        
        try:
            # Проверяем максимальное количество позиций
            max_positions = await get_position_max_concurrent()
            if _risk_metrics.current_positions >= max_positions:
                return False, f"Max concurrent positions exceeded: {_risk_metrics.current_positions}/{max_positions}"
            
            # Проверяем плечо
            max_leverage = await get_position_max_leverage()
            if leverage > max_leverage:
                return False, f"Leverage too high: {leverage}x > {max_leverage}x"
            
            # Проверяем риск на позицию
            max_risk_pct = await get_position_max_risk_pct()
            balance = await self.exchange.get_balance()
            current_balance = balance.wallet_balance if balance else 0.0
            
            if current_balance > 0:
                position_risk_pct = (position_value / current_balance) * 100
                if position_risk_pct > max_risk_pct:
                    return False, f"Position risk too high: {position_risk_pct:.2f}% > {max_risk_pct}%"
            
            return True, "Position limits OK"
            
        except Exception as e:
            self.logger.error(f"Error checking position limits: {e}")
            return False, f"Position limits check error: {e}"
    
    async def _halt_trading(self, reason: str):
        """Остановка торговли (circuit breaker)"""
        global _risk_metrics
        
        try:
            hard_stop = await get_circuit_breaker_hard_stop()
            
            if hard_stop:
                _risk_metrics.trading_allowed = False
                _risk_metrics.halt_reason = reason
                
                self.logger.error(f"🛑 TRADING HALTED: {reason}")
            else:
                cool_down = await get_circuit_breaker_cool_down()
                self.logger.warning(f"⚠️ Risk limit exceeded (soft stop): {reason}")
                self.logger.info(f"Cool down period: {cool_down} minutes")
                
        except Exception as e:
            self.logger.error(f"Error halting trading: {e}")
    
    async def update_after_trade(self, symbol: str, side: str, quantity: float, 
                               price: float, pnl: float):
        """
        Обновление метрик после исполнения сделки (post-trade update)
        
        Args:
            symbol: Торговый символ
            side: Направление сделки
            quantity: Количество
            price: Цена исполнения
            pnl: Реализованный P&L в USDT
        """
        global _risk_metrics
        
        try:
            async with _lock:
                # Обновляем счетчики сделок
                _risk_metrics.daily_trades_count += 1
                _risk_metrics.weekly_trades_count += 1
                
                # Обновляем P&L
                _risk_metrics.daily_pnl += pnl
                _risk_metrics.weekly_pnl += pnl
                
                # Обновляем текущий баланс
                balance = await self.exchange.get_balance()
                current_balance = balance.wallet_balance if balance else 0.0
                
                _risk_metrics.daily_current_balance = current_balance
                _risk_metrics.weekly_current_balance = current_balance
                
                # Пересчитываем просадки
                if _risk_metrics.daily_start_balance > 0:
                    _risk_metrics.daily_drawdown_pct = max(0, 
                        ((_risk_metrics.daily_start_balance - current_balance) / _risk_metrics.daily_start_balance) * 100)
                        
                if _risk_metrics.weekly_start_balance > 0:
                    _risk_metrics.weekly_drawdown_pct = max(0, 
                        ((_risk_metrics.weekly_start_balance - current_balance) / _risk_metrics.weekly_start_balance) * 100)
                
                # Обновляем количество позиций
                positions = await self.exchange.get_positions()
                _risk_metrics.current_positions = len([p for p in positions if p.size > 0])
                
                # Сохраняем информацию о сделке
                _risk_metrics.last_trade_time = time.time()
                
                self.logger.info(
                    f"Trade metrics updated: {symbol} {side} {quantity} @ {price}, "
                    f"PnL: {pnl:+.2f}, Daily: {_risk_metrics.daily_pnl:+.2f}, "
                    f"Weekly: {_risk_metrics.weekly_pnl:+.2f}"
                )
                
        except Exception as e:
            self.logger.error(f"Error updating trade metrics: {e}")
    
    async def get_risk_status(self) -> Dict[str, Any]:
        """
        Получение текущего статуса рисков для Telegram и веб-интерфейса
        
        Returns:
            Словарь с метриками рисков
        """
        global _risk_metrics
        
        try:
            async with _lock:
                # Проверяем ресеты
                await self._check_and_perform_resets()
                
                status = {
                    "enabled": await get_risk_enabled(),
                    "currency": _risk_config.get("currency", "USDT"),
                    "trading_allowed": _risk_metrics.trading_allowed,
                    "halt_reason": _risk_metrics.halt_reason,
                    
                    # Дневные метрики
                    "daily": {
                        "pnl": _risk_metrics.daily_pnl,
                        "trades_count": _risk_metrics.daily_trades_count,
                        "drawdown_pct": _risk_metrics.daily_drawdown_pct,
                        "start_balance": _risk_metrics.daily_start_balance,
                        "current_balance": _risk_metrics.daily_current_balance,
                        
                        # Лимиты
                        "max_abs_loss": await get_daily_max_abs_loss(),
                        "max_drawdown_pct": await get_daily_max_drawdown_pct(),
                        "max_trades": await get_daily_max_trades(),
                        "reset_time_utc": await get_daily_reset_time_utc(),
                        
                        # Использование лимитов в %
                        "loss_usage_pct": min(100, (abs(_risk_metrics.daily_pnl) / await get_daily_max_abs_loss()) * 100) if _risk_metrics.daily_pnl < 0 else 0,
                        "drawdown_usage_pct": min(100, (_risk_metrics.daily_drawdown_pct / await get_daily_max_drawdown_pct()) * 100),
                        "trades_usage_pct": min(100, (_risk_metrics.daily_trades_count / await get_daily_max_trades()) * 100)
                    },
                    
                    # Недельные метрики
                    "weekly": {
                        "pnl": _risk_metrics.weekly_pnl,
                        "trades_count": _risk_metrics.weekly_trades_count,
                        "drawdown_pct": _risk_metrics.weekly_drawdown_pct,
                        "start_balance": _risk_metrics.weekly_start_balance,
                        "current_balance": _risk_metrics.weekly_current_balance,
                        
                        # Лимиты
                        "max_abs_loss": await get_weekly_max_abs_loss(),
                        "max_drawdown_pct": await get_weekly_max_drawdown_pct(),
                        "reset_dow_utc": await get_weekly_reset_dow_utc(),
                        
                        # Использование лимитов в %
                        "loss_usage_pct": min(100, (abs(_risk_metrics.weekly_pnl) / await get_weekly_max_abs_loss()) * 100) if _risk_metrics.weekly_pnl < 0 else 0,
                        "drawdown_usage_pct": min(100, (_risk_metrics.weekly_drawdown_pct / await get_weekly_max_drawdown_pct()) * 100)
                    },
                    
                    # Позиции
                    "positions": {
                        "current_count": _risk_metrics.current_positions,
                        "max_concurrent": await get_position_max_concurrent(),
                        "max_risk_pct": await get_position_max_risk_pct(),
                        "max_leverage": await get_position_max_leverage(),
                        "current_leverage": _risk_metrics.max_leverage_used,
                        
                        # Использование лимитов в %
                        "count_usage_pct": min(100, (_risk_metrics.current_positions / await get_position_max_concurrent()) * 100)
                    },
                    
                    # Circuit Breaker
                    "circuit_breaker": {
                        "hard_stop": await get_circuit_breaker_hard_stop(),
                        "cool_down_minutes": await get_circuit_breaker_cool_down()
                    },
                    
                    # Временные метки
                    "timestamps": {
                        "last_daily_reset": _risk_metrics.last_daily_reset,
                        "last_weekly_reset": _risk_metrics.last_weekly_reset,
                        "last_trade": _risk_metrics.last_trade_time
                    }
                }
                
                return status
                
        except Exception as e:
            self.logger.error(f"Error getting risk status: {e}")
            return {"error": str(e)}
    
    async def reset_daily_counters(self) -> bool:
        """Ручной сброс дневных счетчиков"""
        try:
            await self._perform_daily_reset()
            self.logger.info("✅ Daily counters manually reset")
            return True
        except Exception as e:
            self.logger.error(f"Failed to reset daily counters: {e}")
            return False
    
    async def reset_weekly_counters(self) -> bool:
        """Ручной сброс недельных счетчиков"""
        try:
            await self._perform_weekly_reset()
            self.logger.info("✅ Weekly counters manually reset")
            return True
        except Exception as e:
            self.logger.error(f"Failed to reset weekly counters: {e}")
            return False
    
    async def set_risk_parameter(self, path: str, value: Any) -> Tuple[bool, str]:
        """
        Установка параметра риска через dotted path (для Telegram)
        
        Args:
            path: Путь к параметру (например, "daily.max_abs_loss")
            value: Новое значение
            
        Returns:
            Tuple (успешно, сообщение)
        """
        try:
            # Маппинг путей к функциям
            setters = {
                "enabled": set_risk_enabled,
                "daily.max_abs_loss": set_daily_max_abs_loss,
                "daily.max_drawdown_pct": set_daily_max_drawdown_pct,
                "daily.max_trades": set_daily_max_trades,
                "daily.reset_time_utc": set_daily_reset_time_utc,
                "weekly.max_abs_loss": set_weekly_max_abs_loss,
                "weekly.max_drawdown_pct": set_weekly_max_drawdown_pct,
                "weekly.reset_dow_utc": set_weekly_reset_dow_utc,
                "position.max_risk_pct": set_position_max_risk_pct,
                "position.max_leverage": set_position_max_leverage,
                "position.max_concurrent_positions": set_position_max_concurrent,
                "circuit_breaker.hard_stop": set_circuit_breaker_hard_stop,
                "circuit_breaker.cool_down_minutes": set_circuit_breaker_cool_down,
                "persist_runtime_updates": set_persist_runtime_updates
            }
            
            if path not in setters:
                return False, f"Unknown parameter path: {path}"
            
            # Преобразование типов
            if path in ["daily.max_abs_loss", "daily.max_drawdown_pct", "weekly.max_abs_loss", 
                       "weekly.max_drawdown_pct", "position.max_risk_pct"]:
                value = float(value)
            elif path in ["daily.max_trades", "position.max_leverage", "position.max_concurrent_positions", 
                         "circuit_breaker.cool_down_minutes"]:
                value = int(value)
            elif path in ["enabled", "circuit_breaker.hard_stop", "persist_runtime_updates"]:
                value = str(value).lower() in ["true", "1", "yes", "on"]
            
            # Вызываем соответствующий setter
            success = await setters[path](value)
            
            if success:
                return True, f"Parameter {path} set to {value}"
            else:
                return False, f"Failed to set {path} to {value} (validation failed)"
                
        except Exception as e:
            return False, f"Error setting parameter {path}: {e}"


# Глобальный экземпляр для использования в других модулях
_global_risk_manager = None

def get_risk_manager() -> Optional[RiskManager]:
    """Получить глобальный экземпляр риск-менеджера"""
    return _global_risk_manager

def set_risk_manager(risk_manager: RiskManager):
    """Установить глобальный экземпляр риск-менеджера"""
    global _global_risk_manager
    _global_risk_manager = risk_manager