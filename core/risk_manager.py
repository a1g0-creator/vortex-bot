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
        """Загружает конфигурацию из YAML файла. Fail-fast."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"risk.yaml not found at {self.config_path}")
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("risk.yaml must be a top-level mapping")
        return data

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Получает значение из конфигурации по 'dotted' пути.
        При ошибке возвращает default или кидает исключение.
        """
        keys = key_path.split('.')
        value = self._config
        try:
            for k in keys:
                value = value[k]
            return value
        except Exception:
            self.logger.error(f"Config key not found: {key_path}")
            if default is not None:
                return default
            raise

    def set(self, key_path: str, value: Any) -> bool:
        """
        Устанавливает значение по 'dotted' пути, если он существует.
        """
        keys = key_path.split('.')
        cur = self._config
        try:
            for k in keys[:-1]:
                cur = cur[k]  # KeyError если путь неверный
            if keys[-1] not in cur:
                raise KeyError(f"Unknown config key: {key_path}")
            cur[keys[-1]] = value
            self.logger.info(f"Config '{key_path}' = {value}")
            self.save()
            return True
        except Exception as e:
            self.logger.error(f"Set failed for '{key_path}': {e}")
            return False

    def save(self):
        """Сохраняет конфигурацию атомарно (через временный файл)."""
        if not self.get("persist_runtime_updates", True):
            self.logger.info("Persist disabled; skipping save()")
            return
        tmp_path = self.config_path.with_suffix(".tmp")
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(self._config, f, default_flow_style=False, indent=2, allow_unicode=True, sort_keys=False)
            import os
            os.replace(tmp_path, self.config_path)
            self.logger.info(f"Config saved to {self.config_path}")
        except Exception as e:
            self.logger.error(f"Atomic save failed: {e}")
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def reload(self):
        """Перезагружает конфигурацию из файла."""
        self.logger.info("Перезагрузка конфигурации из файла...")
        self._config = self._load()

    def snapshot(self) -> Dict[str, Any]:
        """Возвращает копию текущей конфигурации."""
        import copy
        return copy.deepcopy(self._config)

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

    async def update_after_trade(
        self,
        pnl: Optional[float] = None,
        *,
        realized_pnl: Optional[float] = None,
        is_open: bool = False,
        is_close: bool = True
    ):
        """
        Совместимый метод: поддерживает старые вызовы (realized_pnl/is_open/is_close)
        и новый — с единственным параметром pnl.
        """
        # Нормализуем вход
        if pnl is None:
            pnl = realized_pnl if realized_pnl is not None else 0.0

        async with self.lock:
            if not self.initialized:
                self.logger.warning("Попытка обновить метрики на неинициализированном RiskManager.")
                return

            if is_open:
                self.metrics.daily_trades_count += 1
                self.metrics.weekly_trades_count += 1

            self.metrics.daily_pnl += float(pnl)
            self.metrics.weekly_pnl += float(pnl)

            self.metrics.daily_current_balance += float(pnl)
            self.metrics.weekly_current_balance += float(pnl)
            self.metrics.daily_high_water_mark = max(self.metrics.daily_high_water_mark, self.metrics.daily_current_balance)
            self.metrics.weekly_high_water_mark = max(self.metrics.weekly_high_water_mark, self.metrics.weekly_current_balance)

            try:
                positions = await self.exchange.get_positions()
                self.metrics.current_positions = len([p for p in positions if getattr(p, "size", 0) or getattr(p, "qty", 0)])
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

        # 3a. Минимальный размер позиции (USDT)
        min_pos = self.config.get("position.min_position_size")
        if position_value < float(min_pos):
            return False, f"Размер позиции ниже минимума: {position_value:.2f} < {min_pos:.2f}"

        # 3b. Максимальный размер позиции (% от капитала)
        max_pos_pct = self.config.get("position.max_position_size_pct")
        if current_balance > 0:
            pos_pct = (position_value / current_balance) * 100
            if pos_pct > float(max_pos_pct):
                return False, f"Размер позиции превышает лимит: {pos_pct:.2f}% / {max_pos_pct}%"

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
        reset_dow_val = self.config.get("weekly.reset_dow_utc", "MONDAY")
        dow_map = {"MONDAY":0,"TUESDAY":1,"WEDNESDAY":2,"THURSDAY":3,"FRIDAY":4,"SATURDAY":5,"SUNDAY":6}

        if isinstance(reset_dow_val, str):
            reset_dow_val = reset_dow_val.strip().upper()
            if reset_dow_val not in dow_map:
                self.logger.error(f"weekly.reset_dow_utc invalid: {reset_dow_val}")
                return
            reset_dow = dow_map[reset_dow_val]
        else:
            try:
                reset_dow = int(reset_dow_val)
                if not (0 <= reset_dow <= 6):
                    raise ValueError
            except Exception:
                self.logger.error(f"weekly.reset_dow_utc must be MONDAY..SUNDAY or 0..6, got: {reset_dow_val}")
                return

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

            # Возобновляем торговлю, если она была остановлена
            self.metrics.trading_allowed = True
            self.metrics.halt_reason = ""

            log_msg = "сброшены вручную" if manual else "сброшены автоматически"
            self.logger.info(f"✅ Дневные счетчики {log_msg}. Новый баланс: {current_balance:.2f}. Торговля разрешена.")

    async def reset_weekly_counters(self, manual: bool = False):
        """Сбрасывает недельные метрики."""
        async with self.lock:
            current_balance = self.metrics.weekly_current_balance
            self.metrics.weekly_start_balance = current_balance
            self.metrics.weekly_high_water_mark = current_balance
            self.metrics.weekly_pnl = 0.0
            # Недельное количество сделок обычно не сбрасывают, но можно добавить при необходимости
            self.metrics.last_weekly_reset = datetime.now(timezone.utc)

            # Возобновляем торговлю, если она была остановлена
            self.metrics.trading_allowed = True
            self.metrics.halt_reason = ""

            log_msg = "сброшены вручную" if manual else "сброшены автоматически"
            self.logger.info(f"✅ Недельные счетчики {log_msg}. Новый баланс: {current_balance:.2f}. Торговля разрешена.")

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
                "config": self.config.snapshot(), # Используем публичный метод
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
        return True

    async def disable(self):
        """Выключает риск-менеджмент."""
        await self.set_risk_parameter("enabled", False)
        self.logger.info("Риск-менеджмент ВЫКЛЮЧЕН.")
        return True

    async def reset_counters(self, manual: bool = True) -> bool:
        """Сбрасывает все счетчики (дневные и недельные)."""
        self.logger.info("Сброс всех счетчиков...")
        await self.reset_daily_counters(manual=manual)
        await self.reset_weekly_counters(manual=manual)
        return True

    async def get_status(self) -> Dict[str, Any]:
        """Возвращает краткий статус для команды /risk."""
        async with self.lock:
            return {
                "enabled": self.config.get("enabled", False),
                "daily": {
                    "used_trades": self.metrics.daily_trades_count,
                    "max_trades": self.config.get("daily.max_trades"),
                    "realized_loss": self.metrics.daily_pnl,
                    "max_abs_loss": self.config.get("daily.max_abs_loss"),
                },
                "weekly": {
                    "realized_loss": self.metrics.weekly_pnl,
                    "max_abs_loss": self.config.get("weekly.max_abs_loss"),
                }
            }

    async def show_limits(self) -> Dict[str, Any]:
        """Возвращает основные группы лимитов для /risk_show."""
        async with self.lock:
            # Используем публичный метод snapshot для получения копии конфига
            return self.config.snapshot()

    async def set_limit(self, scope: str, key: str, value: Any) -> bool:
        """Безопасно устанавливает параметр, делегируя в set_risk_parameter."""
        path = f"{scope}.{key}"
        # set_risk_parameter уже содержит логику валидации и сохранения
        success, _ = await self.set_risk_parameter(path, value)
        return success

# --- Синглтон для глобального доступа ---

_rm_singleton: Optional["RiskManager"] = None

def get_risk_manager() -> "RiskManager":
    """Backward-compatible accessor expected by trading_engine.py."""
    if _rm_singleton is None:
        raise RuntimeError("RiskManager singleton is not set. Call set_risk_manager(...) at boot.")
    return _rm_singleton

def set_risk_manager(rm: "RiskManager") -> None:
    """Backward-compatible setter; always override."""
    global _rm_singleton
    _rm_singleton = rm

# Оставь совместимость с твоими именами (алиасы):
get_global_risk_manager = get_risk_manager
set_global_risk_manager = set_risk_manager