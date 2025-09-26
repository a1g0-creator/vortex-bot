"""
Система управления рисками для торгового бота.
Эта версия полностью переработана для исключения глобального состояния и жестко
закодированных значений. Управление конфигурацией осуществляется через класс
RiskConfig, который работает напрямую с файлом risk.yaml.
"""
from __future__ import annotations
import asyncio
import logging
import yaml
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

# Абстракция для type hinting, чтобы избежать циклического импорта
try:
    from exchanges.base_exchange import BaseExchange
except (ImportError, ModuleNotFoundError):
    # Fallback для сценариев, где структура проекта может отличаться
    class BaseExchange:
        async def get_balance(self, currency: str = "USDT") -> Any: ...
        async def get_positions(self) -> List[Any]: ...

# --- Слой конфигурации ---

class RiskConfig:
    """
    Адаптер для работы с конфигурационным файлом risk.yaml.
    Обеспечивает чтение, запись и доступ к параметрам риска,
    инкапсулируя всю логику работы с файлом.
    """
    _DEFAULT_CONFIG = {
        "enabled": True,
        "currency": "USDT",
        "persist_runtime_updates": True,
        "daily": {
            "max_abs_loss": 500.0,
            "max_drawdown_pct": 5.0,
            "max_trades": 20,
            "reset_time_utc": "00:00",
        },
        "weekly": {
            "max_abs_loss": 1500.0,
            "max_drawdown_pct": 10.0,
            "reset_dow_utc": "MONDAY",
        },
        "position": {
            "max_risk_pct": 2.0,
            "max_leverage": 10,
            "max_concurrent_positions": 5,
            "max_position_size_pct": 10.0,
            "min_position_size": 10.0,
        },
        "circuit_breaker": {
            "hard_stop": True,
            "cool_down_minutes": 120,
            "triggers": {
                "consecutive_losses": 5,
                "rapid_loss_pct": 3.0,
                "rapid_loss_minutes": 60,
                "critical_drawdown_pct": 15.0,
            },
        },
    }

    def __init__(self, config_path: str = "config/risk.yaml"):
        self.config_path = Path(config_path)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._config = self._load()

    def _load(self) -> Dict[str, Any]:
        """Загружает конфигурацию из YAML файла, используя дефолты при отсутствии."""
        if not self.config_path.exists():
            self.logger.warning(f"Конфигурационный файл не найден: {self.config_path}. Используются значения по умолчанию.")
            return self._DEFAULT_CONFIG.copy()

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded_config = yaml.safe_load(f)
                if not loaded_config:
                    self.logger.warning(f"Файл конфигурации пуст: {self.config_path}. Используются значения по умолчанию.")
                    return self._DEFAULT_CONFIG.copy()
                # Здесь можно добавить логику слияния с дефолтами, если нужно
                return loaded_config
        except Exception as e:
            self.logger.error(f"Ошибка загрузки конфигурации из {self.config_path}: {e}. Используются значения по умолчанию.")
            return self._DEFAULT_CONFIG.copy()

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Получает значение из конфигурации по 'dotted' пути (e.g., 'daily.max_abs_loss').
        """
        keys = key_path.split('.')
        value = self._config
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            # Попробуем найти в дефолтной конфигурации
            default_value = self._DEFAULT_CONFIG
            try:
                for key in keys:
                    default_value = default_value[key]
                return default_value
            except (KeyError, TypeError):
                self.logger.warning(f"Параметр '{key_path}' не найден ни в risk.yaml, ни в дефолтной конфигурации.")
                return default

    def set(self, key_path: str, value: Any) -> bool:
        """
        Устанавливает значение по 'dotted' пути и сохраняет конфигурацию, если разрешено.
        """
        keys = key_path.split('.')
        config_part = self._config
        try:
            for key in keys[:-1]:
                if key not in config_part:
                    config_part[key] = {}
                config_part = config_part[key]

            config_part[keys[-1]] = value
            self.logger.info(f"Параметр '{key_path}' обновлен на значение: {value}")
            self.save()
            return True
        except Exception as e:
            self.logger.error(f"Не удалось установить параметр '{key_path}': {e}")
            return False

    def save(self):
        """Сохраняет текущую конфигурацию в файл risk.yaml, если разрешено."""
        if not self.get("persist_runtime_updates", True):
            self.logger.info("Сохранение изменений в файл отключено в конфигурации.")
            return

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self._config, f, default_flow_style=False, indent=2, allow_unicode=True, sort_keys=False)
            self.logger.info(f"Конфигурация успешно сохранена в {self.config_path}")
        except Exception as e:
            self.logger.error(f"Ошибка сохранения конфигурации в {self.config_path}: {e}")

    def reload(self):
        """Перезагружает конфигурацию из файла."""
        self.logger.info("Перезагрузка конфигурации из файла...")
        self._config = self._load()

# --- Структуры данных ---

@dataclass
class RiskMetrics:
    """Хранит текущие метрики рисков в реальном времени."""
    daily_pnl: float = 0.0
    daily_trades_count: int = 0
    daily_start_balance: float = 0.0
    daily_current_balance: float = 0.0
    daily_high_water_mark: float = 0.0 # Для расчета просадки

    weekly_pnl: float = 0.0
    weekly_trades_count: int = 0
    weekly_start_balance: float = 0.0
    weekly_current_balance: float = 0.0
    weekly_high_water_mark: float = 0.0 # Для расчета просадки

    current_positions: int = 0
    trading_allowed: bool = True
    halt_reason: str = ""
    
    last_trade_time: Optional[datetime] = None
    last_daily_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_weekly_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

# --- Основной класс ---

class RiskManager:
    """
    Менеджер рисков, управляющий торговыми лимитами на основе конфигурации из risk.yaml.
    Работает как stateful-объект, инкапсулируя всю логику и состояние.
    """
    def __init__(self, exchange: BaseExchange, initial_capital: Optional[float] = None, **kwargs):
        self.exchange = exchange
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config = RiskConfig()

        self.initial_capital = float(initial_capital) if initial_capital is not None else None
        self.metrics = RiskMetrics()
        self.lock = asyncio.Lock()
        self.initialized = False

        self.logger.info(f"RiskManager создан (капитал: {self.initial_capital or 'не задан'})")

    async def initialize(self) -> bool:
        """
        Инициализирует менеджер: получает начальный капитал (если не задан),
        устанавливает начальные метрики и флаг готовности к работе.
        """
        async with self.lock:
            if self.initialized:
                return True
            
            self.config.reload() # Загружаем последнюю версию конфига при старте

            if self.initial_capital is None:
                try:
                    balance_info = await self.exchange.get_balance(self.config.get("currency", "USDT"))
                    # Предполагаем, что API биржи возвращает объект с атрибутом wallet_balance
                    self.initial_capital = float(balance_info.wallet_balance)
                    self.logger.info(f"Начальный капитал успешно получен с биржи: {self.initial_capital}")
                except Exception as e:
                    self.logger.error(f"Не удалось получить начальный капитал с биржи: {e}. Инициализация не удалась.")
                    return False

            if self.initial_capital is not None and self.initial_capital > 0:
                self.metrics.daily_start_balance = self.initial_capital
                self.metrics.daily_current_balance = self.initial_capital
                self.metrics.daily_high_water_mark = self.initial_capital

                self.metrics.weekly_start_balance = self.initial_capital
                self.metrics.weekly_current_balance = self.initial_capital
                self.metrics.weekly_high_water_mark = self.initial_capital

                self.initialized = True
                self.logger.info(f"✅ RiskManager успешно инициализирован с капиталом: {self.initial_capital}")

                # Совместимость с синглтон-паттерном
                set_global_risk_manager(self)
                return True
            
            self.logger.error("Инициализация RiskManager не удалась: отсутствует или нулевой начальный капитал.")
            return False

    async def check_trade_permission(self, position_value: float, leverage: int = 1) -> Tuple[bool, str]:
        """
        Главный метод проверки: можно ли открывать сделку.
        Агрегирует все проверки лимитов.
        """
        async with self.lock:
            if not self.initialized:
                return False, "RiskManager не инициализирован"
            
            if not self.config.get("enabled", True):
                return True, "Риск-менеджмент отключен"

            await self._check_and_perform_resets()

            if not self.metrics.trading_allowed:
                return False, f"Торговля остановлена: {self.metrics.halt_reason}"

            checks = [
                self._check_daily_limits,
                self._check_weekly_limits,
                self._check_position_limits,
            ]
            
            # Передаем параметры только в те проверки, где они нужны
            check_params = {
                self._check_position_limits: (position_value, leverage)
            }

            for check_func in checks:
                params = check_params.get(check_func, ())
                allowed, reason = await check_func(*params)
                if not allowed:
                    await self._halt_trading(reason)
                    return False, reason

            return True, "OK"

    async def update_after_trade(self, pnl: float):
        """Обновляет метрики после закрытия сделки."""
        async with self.lock:
            if not self.initialized:
                self.logger.warning("Попытка обновить метрики на неинициализированном RiskManager.")
                return

            self.metrics.daily_trades_count += 1
            self.metrics.weekly_trades_count += 1
            self.metrics.daily_pnl += pnl
            self.metrics.weekly_pnl += pnl
            
            # Обновляем текущий баланс и высшую отметку
            self.metrics.daily_current_balance += pnl
            self.metrics.weekly_current_balance += pnl
            self.metrics.daily_high_water_mark = max(self.metrics.daily_high_water_mark, self.metrics.daily_current_balance)
            self.metrics.weekly_high_water_mark = max(self.metrics.weekly_high_water_mark, self.metrics.weekly_current_balance)

            # Обновляем количество открытых позиций
            try:
                positions = await self.exchange.get_positions()
                self.metrics.current_positions = len([p for p in positions if p.size > 0])
            except Exception as e:
                self.logger.error(f"Не удалось обновить количество позиций: {e}")

            self.metrics.last_trade_time = datetime.now(timezone.utc)
            self.logger.info(f"Метрики обновлены: PnL={pnl:+.2f}, Daily PnL={self.metrics.daily_pnl:+.2f}")

    # --- Приватные методы проверок и управления ---

    async def _check_daily_limits(self) -> Tuple[bool, str]:
        """Проверяет дневные лимиты: убыток, просадка, количество сделок."""
        # 1. Абсолютный убыток
        max_loss = self.config.get("daily.max_abs_loss")
        if self.metrics.daily_pnl < 0 and abs(self.metrics.daily_pnl) >= max_loss:
            return False, f"Дневной лимит убытка превышен: {self.metrics.daily_pnl:.2f} / -{max_loss}"

        # 2. Просадка
        drawdown = (self.metrics.daily_high_water_mark - self.metrics.daily_current_balance)
        if drawdown > 0 and self.metrics.daily_high_water_mark > 0:
            drawdown_pct = (drawdown / self.metrics.daily_high_water_mark) * 100
            max_drawdown_pct = self.config.get("daily.max_drawdown_pct")
            if drawdown_pct >= max_drawdown_pct:
                return False, f"Дневная просадка превышена: {drawdown_pct:.2f}% / {max_drawdown_pct}%"

        # 3. Количество сделок
        max_trades = self.config.get("daily.max_trades")
        if self.metrics.daily_trades_count >= max_trades:
            return False, f"Дневной лимит сделок превышен: {self.metrics.daily_trades_count} / {max_trades}"

        return True, "Daily limits OK"

    async def _check_weekly_limits(self) -> Tuple[bool, str]:
        """Проверяет недельные лимиты: убыток, просадка."""
        # 1. Абсолютный убыток
        max_loss = self.config.get("weekly.max_abs_loss")
        if self.metrics.weekly_pnl < 0 and abs(self.metrics.weekly_pnl) >= max_loss:
            return False, f"Недельный лимит убытка превышен: {self.metrics.weekly_pnl:.2f} / -{max_loss}"

        # 2. Просадка
        drawdown = (self.metrics.weekly_high_water_mark - self.metrics.weekly_current_balance)
        if drawdown > 0 and self.metrics.weekly_high_water_mark > 0:
            drawdown_pct = (drawdown / self.metrics.weekly_high_water_mark) * 100
            max_drawdown_pct = self.config.get("weekly.max_drawdown_pct")
            if drawdown_pct >= max_drawdown_pct:
                return False, f"Недельная просадка превышена: {drawdown_pct:.2f}% / {max_drawdown_pct}%"

        return True, "Weekly limits OK"

    async def _check_position_limits(self, position_value: float, leverage: int) -> Tuple[bool, str]:
        """Проверяет лимиты для новой позиции: количество, плечо, риск."""
        # 1. Количество одновременных позиций
        max_concurrent = self.config.get("position.max_concurrent_positions")
        if self.metrics.current_positions >= max_concurrent:
            return False, f"Лимит одновременных позиций: {self.metrics.current_positions} / {max_concurrent}"

        # 2. Максимальное плечо
        max_leverage = self.config.get("position.max_leverage")
        if leverage > max_leverage:
            return False, f"Запрошенное плечо слишком высокое: {leverage}x / {max_leverage}x"

        # 3. Риск на позицию
        max_risk_pct = self.config.get("position.max_risk_pct")
        current_balance = self.metrics.daily_current_balance
        if current_balance > 0:
            # Риск считается как % от текущего баланса
            risk_pct = (position_value / current_balance) * 100
            if risk_pct > max_risk_pct:
                return False, f"Риск на позицию превышен: {risk_pct:.2f}% / {max_risk_pct}%"

        return True, "Position limits OK"
    
    async def _halt_trading(self, reason: str):
        """Останавливает торговлю (Circuit Breaker)."""
        if self.config.get("circuit_breaker.hard_stop", True):
            self.metrics.trading_allowed = False
            self.metrics.halt_reason = reason
            self.logger.critical(f"🛑 ТОРГОВЛЯ ОСТАНОВЛЕНА. Причина: {reason}")
        else:
            self.logger.warning(f"⚠️ Событие риска (soft-stop): {reason}. Торговля не остановлена.")

    # --- Сброс счетчиков ---

    async def _check_and_perform_resets(self):
        """Проверяет, не пора ли сбросить дневные или недельные счетчики."""
        now = datetime.now(timezone.utc)
        
        # Дневной сброс
        reset_time_str = self.config.get("daily.reset_time_utc", "00:00")
        h, m = map(int, reset_time_str.split(':'))
        reset_time_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= reset_time_today and self.metrics.last_daily_reset.date() < now.date():
            await self.reset_daily_counters()

        # Недельный сброс
        reset_dow_str = self.config.get("weekly.reset_dow_utc", "MONDAY").upper()
        days = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
        if reset_dow_str in days:
            reset_dow = days.index(reset_dow_str)
            if now.weekday() == reset_dow and self.metrics.last_weekly_reset.date() < now.date():
                 await self.reset_weekly_counters()

    async def reset_daily_counters(self, manual: bool = False):
        """Сбрасывает дневные метрики."""
        async with self.lock:
            current_balance = self.metrics.daily_current_balance
            self.metrics.daily_start_balance = current_balance
            self.metrics.daily_high_water_mark = current_balance
            self.metrics.daily_pnl = 0.0
            self.metrics.daily_trades_count = 0
            self.metrics.last_daily_reset = datetime.now(timezone.utc)

            # Возобновляем торговлю, если она была остановлена по дневным лимитам
            if not self.metrics.trading_allowed and "дневной" in self.metrics.halt_reason.lower():
                self.metrics.trading_allowed = True
                self.metrics.halt_reason = ""
                self.logger.info("Торговля возобновлена после дневного сброса.")

            log_msg = "сброшены вручную" if manual else "сброшены автоматически"
            self.logger.info(f"✅ Дневные счетчики {log_msg}. Новый баланс: {current_balance:.2f}")

    async def reset_weekly_counters(self, manual: bool = False):
        """Сбрасывает недельные метрики."""
        async with self.lock:
            current_balance = self.metrics.weekly_current_balance
            self.metrics.weekly_start_balance = current_balance
            self.metrics.weekly_high_water_mark = current_balance
            self.metrics.weekly_pnl = 0.0
            # Недельное количество сделок обычно не сбрасывают, но можно добавить при необходимости
            self.metrics.last_weekly_reset = datetime.now(timezone.utc)

            if not self.metrics.trading_allowed and "недельный" in self.metrics.halt_reason.lower():
                self.metrics.trading_allowed = True
                self.metrics.halt_reason = ""
                self.logger.info("Торговля возобновлена после недельного сброса.")

            log_msg = "сброшены вручную" if manual else "сброшены автоматически"
            self.logger.info(f"✅ Недельные счетчики {log_msg}. Новый баланс: {current_balance:.2f}")

    # --- Публичные методы для управления и получения статуса ---

    async def get_risk_status(self) -> Dict[str, Any]:
        """Возвращает полный статус риск-менеджера для API или Telegram."""
        async with self.lock:
            # Расчет текущих просадок
            daily_drawdown = (self.metrics.daily_high_water_mark - self.metrics.daily_current_balance)
            daily_drawdown_pct = (daily_drawdown / self.metrics.daily_high_water_mark * 100) if self.metrics.daily_high_water_mark > 0 else 0

            weekly_drawdown = (self.metrics.weekly_high_water_mark - self.metrics.weekly_current_balance)
            weekly_drawdown_pct = (weekly_drawdown / self.metrics.weekly_high_water_mark * 100) if self.metrics.weekly_high_water_mark > 0 else 0

            return {
                "initialized": self.initialized,
                "trading_allowed": self.metrics.trading_allowed,
                "halt_reason": self.metrics.halt_reason,
                "config": self.config._config, # Отдаем текущую конфигурацию
                "metrics": {
                    "daily": {
                        "pnl": self.metrics.daily_pnl,
                        "trades_count": self.metrics.daily_trades_count,
                        "start_balance": self.metrics.daily_start_balance,
                        "current_balance": self.metrics.daily_current_balance,
                        "high_water_mark": self.metrics.daily_high_water_mark,
                        "drawdown_pct": daily_drawdown_pct,
                    },
                    "weekly": {
                        "pnl": self.metrics.weekly_pnl,
                        "start_balance": self.metrics.weekly_start_balance,
                        "current_balance": self.metrics.weekly_current_balance,
                        "high_water_mark": self.metrics.weekly_high_water_mark,
                        "drawdown_pct": weekly_drawdown_pct,
                    },
                    "positions": {
                        "current_count": self.metrics.current_positions,
                    },
                    "timestamps": {
                        "last_daily_reset": self.metrics.last_daily_reset.isoformat(),
                        "last_weekly_reset": self.metrics.last_weekly_reset.isoformat(),
                        "last_trade": self.metrics.last_trade_time.isoformat() if self.metrics.last_trade_time else None,
                    }
                }
            }

    async def set_risk_parameter(self, path: str, value: Any) -> Tuple[bool, str]:
        """
        Устанавливает параметр риска по 'dotted' пути (например, 'daily.max_abs_loss').
        """
        # Здесь можно добавить валидацию и преобразование типов
        try:
            # Пример простого преобразования типов
            if "pct" in path or "loss" in path:
                value = float(value)
            elif "trades" in path or "count" in path or "leverage" in path or "minutes" in path:
                value = int(value)
            elif "enabled" in path or "stop" in path:
                value = str(value).lower() in ['true', '1', 'yes', 'on']
        except ValueError as e:
            return False, f"Неверный тип значения для '{path}': {e}"

        success = self.config.set(path, value)
        if success:
            return True, f"Параметр '{path}' успешно установлен в '{value}'."
        else:
            return False, f"Не удалось установить параметр '{path}'."

    async def enable(self):
        """Включает риск-менеджмент."""
        await self.set_risk_parameter("enabled", True)
        self.logger.info("Риск-менеджмент ВКЛЮЧЕН.")

    async def disable(self):
        """Выключает риск-менеджмент."""
        await self.set_risk_parameter("enabled", False)
        self.logger.info("Риск-менеджмент ВЫКЛЮЧЕН.")


# --- Синглтон для глобального доступа ---

_global_risk_manager: Optional[RiskManager] = None

def get_global_risk_manager() -> Optional[RiskManager]:
    """Возвращает глобальный экземпляр RiskManager."""
    return _global_risk_manager

def set_global_risk_manager(manager: RiskManager):
    """Устанавливает глобальный экземпляр RiskManager."""
    global _global_risk_manager
    if _global_risk_manager is None:
        _global_risk_manager = manager
        logging.getLogger(__name__).info("Глобальный экземпляр RiskManager установлен.")