"""
Менеджер рисков для торгового бота
Контроль лимитов, защита депозита, адаптивное управление
"""

import time
import logging
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict, deque

from exchanges.base_exchange import BaseExchange
from config.config_loader import config_loader


@dataclass
class TradeRecord:
    """Запись о сделке для анализа"""
    symbol: str
    side: str
    size: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_percentage: float
    timestamp: float
    duration_minutes: float
    strategy: str


@dataclass
class RiskMetrics:
    """Метрики рисков"""
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    monthly_pnl: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    risk_score: float = 0.0  # 0-100, где 100 = максимальный риск


@dataclass
class RiskLimits:
    """Лимиты рисков"""
    max_drawdown_percent: float = 20.0
    daily_loss_limit: float = 500.0
    weekly_loss_limit: float = 2000.0
    monthly_loss_limit: float = 5000.0
    max_trades_per_day: int = 20
    max_trades_per_hour: int = 5
    min_trade_interval: int = 30  # минуты
    max_position_value: float = 10000.0
    max_correlation: float = 0.7


class RiskManager:
    """
    Менеджер рисков - центральная система контроля рисков
    Интегрируется с TradingEngine и PositionManager
    """
    
    def __init__(self, exchange: BaseExchange, initial_capital: float = 10000.0):
        self.exchange = exchange
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        
        self.logger = logging.getLogger("RiskManager")
        
        # История сделок для анализа
        self.trade_history: deque = deque(maxlen=1000)  # Последние 1000 сделок
        self.daily_trades: deque = deque(maxlen=100)    # Сделки за день
        self.hourly_trades: deque = deque(maxlen=24)    # Сделки за час
        
        # Временные метки
        self.last_trade_time = 0
        self.session_start_time = time.time()
        self.last_balance_update = 0
        
        # Текущие метрики
        self.metrics = RiskMetrics()
        self.limits = RiskLimits()
        
        # Адаптивное управление
        self.adaptive_enabled = False
        self.adaptive_multiplier = 1.0  # Текущий мультипликатор размера позиций
        
        # Экстренные блокировки
        self.trading_halted = False
        self.halt_reason = ""
        self.halt_timestamp = 0
        
        # Загружаем конфигурацию
        self._load_config()
    
    def _load_config(self):
        """Загрузка настроек управления рисками из конфигурации"""
        try:
            strategies_config = config_loader.get_config("strategies")
            risk_config = strategies_config.get("risk_management", {})
            
            # Глобальные лимиты
            global_config = risk_config.get("global", {})
            self.limits.max_drawdown_percent = global_config.get("max_drawdown_percent", 20.0)
            self.limits.daily_loss_limit = global_config.get("daily_loss_limit", 500.0)
            self.limits.weekly_loss_limit = global_config.get("weekly_loss_limit", 2000.0)
            self.limits.monthly_loss_limit = global_config.get("monthly_loss_limit", 5000.0)
            
            # Защита от переторговли
            overtrading = risk_config.get("overtrading_protection", {})
            self.limits.max_trades_per_day = overtrading.get("max_trades_per_day", 20)
            self.limits.max_trades_per_hour = overtrading.get("max_trades_per_hour", 5)
            self.limits.min_trade_interval = overtrading.get("min_trade_interval", 30)
            
            # Адаптивное управление
            adaptive_config = risk_config.get("adaptive_sizing", {})
            self.adaptive_enabled = adaptive_config.get("enabled", False)
            
            # Корреляционные лимиты
            correlation_config = risk_config.get("correlation_limits", {})
            self.limits.max_correlation = correlation_config.get("max_correlation", 0.7)
            self.max_drawdown_percent = risk_config.get("global", {}).get("max_drawdown_percent", 20.0)
            
            self.logger.info("✅ Конфигурация риск-менеджера загружена")
            
        except Exception as e:
            self.logger.error(f"Ошибка загрузки конфигурации рисков: {e}")
    
    async def initialize(self) -> bool:
        """
        Инициализация менеджера рисков
        
        Returns:
            True если успешно инициализирован
        """
        try:
            # Получаем текущий баланс
            balance = await self.exchange.get_balance()
            if balance:
                self.current_capital = balance.wallet_balance
                
                # Если баланс изменился, пересчитываем просадку
                if self.current_capital != self.initial_capital:
                    drawdown = ((self.initial_capital - self.current_capital) / self.initial_capital) * 100
                    self.metrics.current_drawdown = max(0, drawdown)
                    self.metrics.max_drawdown = max(self.metrics.max_drawdown, self.metrics.current_drawdown)
            
            # Запускаем периодическое обновление метрик
            asyncio.create_task(self._periodic_metrics_update())
            
            self.logger.info(
                f"✅ Риск-менеджер инициализирован: капитал={self.current_capital:.2f} USDT, "
                f"просадка={self.metrics.current_drawdown:.2f}%"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации риск-менеджера: {e}")
            return False
    
    async def check_trade_permission(self, symbol: str, side: str, 
                                   position_value: float, strategy: str = "") -> Tuple[bool, str]:
        """
        Проверка разрешения на открытие сделки
        
        Args:
            symbol: Торговый символ
            side: Направление сделки
            position_value: Стоимость позиции в USDT
            strategy: Название стратегии
            
        Returns:
            Tuple (разрешено, причина отказа)
        """
        try:
            # Проверка экстренной остановки
            if self.trading_halted:
                return False, f"Торговля приостановлена: {self.halt_reason}"
            
            # Проверка лимитов потерь
            permission, reason = await self._check_loss_limits()
            if not permission:
                return False, reason
            
            # Проверка лимитов переторговли
            permission, reason = self._check_overtrading_limits()
            if not permission:
                return False, reason
            
            # Проверка размера позиции
            permission, reason = self._check_position_size_limits(position_value)
            if not permission:
                return False, reason
            
            # Проверка корреляций (если включено)
            permission, reason = await self._check_correlation_limits(symbol)
            if not permission:
                return False, reason
            
            # Проверка максимальной просадки
            if self.metrics.current_drawdown >= self.limits.max_drawdown_percent:
                await self._emergency_halt("Превышена максимальная просадка")
                return False, f"Превышена максимальная просадка: {self.metrics.current_drawdown:.2f}%"
            
            return True, "OK"
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки разрешения на торговлю: {e}")
            return False, f"Ошибка проверки: {e}"
    
    async def _check_loss_limits(self) -> Tuple[bool, str]:
        """Проверка лимитов потерь"""
        try:
            # Обновляем текущие P&L
            await self._update_pnl_metrics()
            
            # Дневные потери
            if abs(self.metrics.daily_pnl) >= self.limits.daily_loss_limit:
                await self._emergency_halt(f"Превышен дневной лимит потерь: {self.metrics.daily_pnl:.2f} USDT")
                return False, f"Дневной лимит потерь: {abs(self.metrics.daily_pnl):.2f}/{self.limits.daily_loss_limit:.2f} USDT"
            
            # Недельные потери
            if abs(self.metrics.weekly_pnl) >= self.limits.weekly_loss_limit:
                await self._emergency_halt(f"Превышен недельный лимит потерь: {self.metrics.weekly_pnl:.2f} USDT")
                return False, f"Недельный лимит потерь: {abs(self.metrics.weekly_pnl):.2f}/{self.limits.weekly_loss_limit:.2f} USDT"
            
            # Месячные потери
            if abs(self.metrics.monthly_pnl) >= self.limits.monthly_loss_limit:
                await self._emergency_halt(f"Превышен месячный лимит потерь: {self.metrics.monthly_pnl:.2f} USDT")
                return False, f"Месячный лимит потерь: {abs(self.metrics.monthly_pnl):.2f}/{self.limits.monthly_loss_limit:.2f} USDT"
            
            return True, "OK"
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки лимитов потерь: {e}")
            return False, f"Ошибка проверки лимитов: {e}"
    
    def _check_overtrading_limits(self) -> Tuple[bool, str]:
        """Проверка лимитов переторговли"""
        try:
            current_time = time.time()
            
            # Проверка минимального интервала между сделками
            if self.last_trade_time > 0:
                time_since_last = (current_time - self.last_trade_time) / 60
                if time_since_last < self.limits.min_trade_interval:
                    return False, f"Слишком рано для новой сделки: {time_since_last:.1f}м < {self.limits.min_trade_interval}м"
            
            # Очищаем старые записи
            self._cleanup_old_trades()
            
            # Проверка дневных сделок
            if len(self.daily_trades) >= self.limits.max_trades_per_day:
                return False, f"Превышен дневной лимит сделок: {len(self.daily_trades)}/{self.limits.max_trades_per_day}"
            
            # Проверка часовых сделок
            hour_ago = current_time - 3600
            recent_trades = [t for t in self.hourly_trades if t > hour_ago]
            if len(recent_trades) >= self.limits.max_trades_per_hour:
                return False, f"Превышен часовой лимит сделок: {len(recent_trades)}/{self.limits.max_trades_per_hour}"
            
            return True, "OK"
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки переторговли: {e}")
            return False, f"Ошибка проверки переторговли: {e}"
    
    def _check_position_size_limits(self, position_value: float) -> Tuple[bool, str]:
        """Проверка лимитов размера позиции"""
        try:
            if position_value > self.limits.max_position_value:
                return False, f"Превышен максимальный размер позиции: {position_value:.2f} > {self.limits.max_position_value:.2f} USDT"
            
            # Проверка на долю от капитала
            max_position_percent = 20.0  # Максимум 20% капитала в одной позиции
            max_allowed = self.current_capital * (max_position_percent / 100)
            
            if position_value > max_allowed:
                return False, f"Позиция слишком большая для текущего капитала: {position_value:.2f} > {max_allowed:.2f} USDT"
            
            return True, "OK"
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки размера позиции: {e}")
            return False, f"Ошибка проверки размера: {e}"
    
    async def _check_correlation_limits(self, symbol: str) -> Tuple[bool, str]:
        """Проверка корреляционных лимитов"""
        try:
            # TODO: Реализовать корреляционный анализ между позициями
            # Пока что простая проверка - не более 50% экспозиции в одном секторе
            
            # Получаем текущие позиции
            positions = await self.exchange.get_positions()
            
            # Анализируем секторную концентрацию
            sector_exposure = defaultdict(float)
            total_exposure = 0.0
            
            for pos in positions:
                if pos.size > 0:
                    exposure = pos.size * pos.mark_price
                    total_exposure += exposure
                    
                    # Простая классификация по базовой валюте
                    base_coin = pos.symbol.replace('USDT', '').replace('USD', '')
                    sector_exposure[base_coin] += exposure
            
            # Проверяем концентрацию
            if total_exposure > 0:
                for sector, exposure in sector_exposure.items():
                    concentration = (exposure / total_exposure) * 100
                    if concentration > 50.0:  # Более 50% в одном секторе
                        return False, f"Слишком высокая концентрация в {sector}: {concentration:.1f}%"
            
            return True, "OK"
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки корреляций: {e}")
            return True, "OK"  # При ошибке разрешаем торговлю
    
    async def check_risks(self) -> Dict[str, Any]:
        """Проверка рисков"""
        try:
            status = self.get_risk_status()
            alerts = []
        
            # Проверяем просадку
            if status.get("current_drawdown_percent", 0) > self.max_drawdown_percent:
                alerts.append({
                    "type": "danger",
                    "message": f"Превышена максимальная просадка: {status['current_drawdown_percent']:.2f}%"
                })
        
            return {"status": status, "alerts": alerts}
        
        except Exception as e:
            self.logger.error(f"Ошибка проверки рисков: {e}")
            return {"status": {}, "alerts": []}
    
    def calculate_adaptive_position_size(self, base_size: float, symbol: str) -> float:
        """
        Адаптивный расчет размера позиции на основе недавних результатов
        
        Args:
            base_size: Базовый размер позиции
            symbol: Торговый символ
            
        Returns:
            Скорректированный размер позиции
        """
        try:
            if not self.adaptive_enabled:
                return base_size
            
            # Анализируем последние сделки для адаптации
            recent_trades = self._get_recent_trades(lookback_trades=20)
            
            if len(recent_trades) < 5:
                return base_size  # Недостаточно данных для адаптации
            
            # Рассчитываем винрейт и среднюю прибыльность
            winning_trades = [t for t in recent_trades if t.pnl > 0]
            win_rate = len(winning_trades) / len(recent_trades)
            
            avg_win = sum(t.pnl_percentage for t in winning_trades) / len(winning_trades) if winning_trades else 0
            losing_trades = [t for t in recent_trades if t.pnl <= 0]
            avg_loss = sum(t.pnl_percentage for t in losing_trades) / len(losing_trades) if losing_trades else 0
            
            # Корректировка мультипликатора
            if win_rate >= 0.6 and avg_win > abs(avg_loss):
                # Хорошие результаты - увеличиваем размер
                self.adaptive_multiplier = min(self.adaptive_multiplier * 1.05, 2.0)
            elif win_rate <= 0.4 or avg_win < abs(avg_loss):
                # Плохие результаты - уменьшаем размер
                self.adaptive_multiplier = max(self.adaptive_multiplier * 0.95, 0.5)
            
            adapted_size = base_size * self.adaptive_multiplier
            
            self.logger.debug(
                f"{symbol} - Адаптивный размер: винрейт={win_rate:.2f}, "
                f"мультипликатор={self.adaptive_multiplier:.3f}, "
                f"размер={base_size:.2f} -> {adapted_size:.2f}"
            )
            
            return adapted_size
            
        except Exception as e:
            self.logger.error(f"Ошибка адаптивного расчета: {e}")
            return base_size
    
    def record_trade(self, symbol: str, side: str, size: float, entry_price: float,
                    exit_price: float, pnl: float, duration_minutes: float, strategy: str = ""):
        """
        Запись сделки в историю для анализа
        
        Args:
            symbol: Торговый символ
            side: Направление сделки
            size: Размер позиции
            entry_price: Цена входа
            exit_price: Цена выхода
            pnl: P&L в USDT
            duration_minutes: Длительность сделки в минутах
            strategy: Название стратегии
        """
        try:
            pnl_percentage = ((exit_price - entry_price) / entry_price) * 100
            if side == "Sell":
                pnl_percentage = -pnl_percentage
            
            trade_record = TradeRecord(
                symbol=symbol,
                side=side,
                size=size,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                pnl_percentage=pnl_percentage,
                timestamp=time.time(),
                duration_minutes=duration_minutes,
                strategy=strategy
            )
            
            # Добавляем в историю
            self.trade_history.append(trade_record)
            
            # Обновляем временные метки
            current_time = time.time()
            self.last_trade_time = current_time
            self.daily_trades.append(current_time)
            self.hourly_trades.append(current_time)
            
            # Обновляем метрики
            self._update_trade_metrics()
            
            self.logger.info(
                f"📝 Сделка записана: {symbol} {side} P&L={pnl:.2f} USDT ({pnl_percentage:+.2f}%)"
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка записи сделки: {e}")
    
    def _update_trade_metrics(self):
        """Обновление торговых метрик"""
        try:
            if not self.trade_history:
                return
            
            # Общая статистика
            self.metrics.total_trades = len(self.trade_history)
            
            # Винрейт
            winning_trades = [t for t in self.trade_history if t.pnl > 0]
            self.metrics.win_rate = len(winning_trades) / len(self.trade_history) * 100
            
            # Профит фактор
            total_wins = sum(t.pnl for t in winning_trades)
            losing_trades = [t for t in self.trade_history if t.pnl <= 0]
            total_losses = abs(sum(t.pnl for t in losing_trades))
            
            if total_losses > 0:
                self.metrics.profit_factor = total_wins / total_losses
            else:
                self.metrics.profit_factor = float('inf') if total_wins > 0 else 0
            
            # Риск-скор (0-100)
            self.metrics.risk_score = self._calculate_risk_score()
            
        except Exception as e:
            self.logger.error(f"Ошибка обновления метрик: {e}")
    
    def _calculate_risk_score(self) -> float:
        """
        Расчет общего риск-скора (0-100)
        100 = максимальный риск
        """
        try:
            risk_score = 0.0
            
            # Компонент просадки (0-40 баллов)
            drawdown_score = min(self.metrics.current_drawdown / self.limits.max_drawdown_percent * 40, 40)
            risk_score += drawdown_score
            
            # Компонент частоты торговли (0-20 баллов)
            current_hour_trades = len([t for t in self.hourly_trades if t > time.time() - 3600])
            frequency_score = min(current_hour_trades / self.limits.max_trades_per_hour * 20, 20)
            risk_score += frequency_score
            
            # Компонент винрейта (0-20 баллов, инвертированный)
            if self.metrics.total_trades >= 10:
                winrate_score = max(0, (50 - self.metrics.win_rate) / 50 * 20)
                risk_score += winrate_score
            
            # Компонент волатильности (0-20 баллов)
            recent_trades = self._get_recent_trades(10)
            if recent_trades:
                pnl_std = self._calculate_pnl_std(recent_trades)
                volatility_score = min(pnl_std / 10 * 20, 20)  # 10% std = max score
                risk_score += volatility_score
            
            return min(risk_score, 100.0)
            
        except Exception as e:
            self.logger.error(f"Ошибка расчета риск-скора: {e}")
            return 50.0  # Средний риск при ошибке
    
    def _calculate_pnl_std(self, trades: List[TradeRecord]) -> float:
        """Расчет стандартного отклонения P&L"""
        try:
            if len(trades) < 2:
                return 0.0
            
            pnl_values = [t.pnl_percentage for t in trades]
            mean_pnl = sum(pnl_values) / len(pnl_values)
            variance = sum((x - mean_pnl) ** 2 for x in pnl_values) / len(pnl_values)
            
            return variance ** 0.5
            
        except Exception:
            return 0.0
    
    async def _update_pnl_metrics(self):
        """Обновление метрик P&L по временным периодам"""
        try:
            current_time = time.time()
            
            # Временные границы
            day_ago = current_time - 86400  # 24 часа
            week_ago = current_time - 604800  # 7 дней
            month_ago = current_time - 2592000  # 30 дней
            
            # Дневной P&L
            daily_trades = [t for t in self.trade_history if t.timestamp >= day_ago]
            self.metrics.daily_pnl = sum(t.pnl for t in daily_trades)
            
            # Недельный P&L
            weekly_trades = [t for t in self.trade_history if t.timestamp >= week_ago]
            self.metrics.weekly_pnl = sum(t.pnl for t in weekly_trades)
            
            # Месячный P&L
            monthly_trades = [t for t in self.trade_history if t.timestamp >= month_ago]
            self.metrics.monthly_pnl = sum(t.pnl for t in monthly_trades)
            
            # Обновляем баланс и просадку
            balance = await self.exchange.get_balance()
            if balance:
                self.current_capital = balance.wallet_balance
                
                # Рассчитываем текущую просадку от пика
                peak_capital = max(self.initial_capital, self.current_capital)
                if peak_capital > self.initial_capital:
                    # Новый пик - сбрасываем отсчет просадки
                    self.initial_capital = peak_capital
                
                current_drawdown = ((peak_capital - self.current_capital) / peak_capital) * 100
                self.metrics.current_drawdown = max(0, current_drawdown)
                self.metrics.max_drawdown = max(self.metrics.max_drawdown, self.metrics.current_drawdown)
            
        except Exception as e:
            self.logger.error(f"Ошибка обновления P&L метрик: {e}")
    
    def _get_recent_trades(self, lookback_trades: int = 20) -> List[TradeRecord]:
        """Получение последних сделок"""
        try:
            if len(self.trade_history) <= lookback_trades:
                return list(self.trade_history)
            else:
                return list(self.trade_history)[-lookback_trades:]
        except Exception:
            return []
    
    def _cleanup_old_trades(self):
        """Очистка старых записей о сделках"""
        try:
            current_time = time.time()
            
            # Очищаем дневные сделки (старше 24 часов)
            day_ago = current_time - 86400
            self.daily_trades = deque([t for t in self.daily_trades if t >= day_ago], maxlen=100)
            
            # Очищаем часовые сделки (старше 24 часов)
            self.hourly_trades = deque([t for t in self.hourly_trades if t >= day_ago], maxlen=24)
            
        except Exception as e:
            self.logger.error(f"Ошибка очистки старых записей: {e}")
    
    async def _emergency_halt(self, reason: str):
        """Экстренная остановка торговли"""
        try:
            self.trading_halted = True
            self.halt_reason = reason
            self.halt_timestamp = time.time()
            
            self.logger.critical(f"🚨 ЭКСТРЕННАЯ ОСТАНОВКА ТОРГОВЛИ: {reason}")
            
            # TODO: Интеграция с системой уведомлений
            # await send_emergency_notification(reason)
            
        except Exception as e:
            self.logger.error(f"Ошибка экстренной остановки: {e}")
    
    def resume_trading(self, admin_override: bool = False) -> bool:
        """
        Возобновление торговли после остановки
        
        Args:
            admin_override: Принудительное возобновление администратором
            
        Returns:
            True если торговля возобновлена
        """
        try:
            if not self.trading_halted:
                return True
            
            if admin_override:
                self.trading_halted = False
                self.halt_reason = ""
                self.halt_timestamp = 0
                self.logger.info("✅ Торговля возобновлена администратором")
                return True
            
            # Автоматическое возобновление после остывания
            if time.time() - self.halt_timestamp > 3600:  # 1 час остывания
                # Проверяем, исправлены ли проблемы
                if self.metrics.current_drawdown < self.limits.max_drawdown_percent:
                    self.trading_halted = False
                    self.halt_reason = ""
                    self.halt_timestamp = 0
                    self.logger.info("✅ Торговля возобновлена автоматически")
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Ошибка возобновления торговли: {e}")
            return False
    
    async def _periodic_metrics_update(self):
        """Периодическое обновление метрик"""
        try:
            while True:
                await self._update_pnl_metrics()
                self._update_trade_metrics()
                self._cleanup_old_trades()
                
                # Логируем метрики каждые 15 минут
                if int(time.time()) % 900 == 0:  # Каждые 15 минут
                    self._log_risk_summary()
                
                await asyncio.sleep(60)  # Обновляем каждую минуту
                
        except Exception as e:
            self.logger.error(f"Ошибка периодического обновления метрик: {e}")
    
    def _log_risk_summary(self):
        """Логирование сводки по рискам"""
        try:
            self.logger.info(
                f"📊 РИСК-СВОДКА: Просадка={self.metrics.current_drawdown:.2f}%, "
                f"Дневной P&L={self.metrics.daily_pnl:+.2f}, "
                f"Винрейт={self.metrics.win_rate:.1f}%, "
                f"Риск-скор={self.metrics.risk_score:.1f}/100, "
                f"Сделок сегодня={len(self.daily_trades)}"
            )
        except Exception as e:
            self.logger.error(f"Ошибка логирования сводки: {e}")
    
    def get_risk_status(self) -> Dict[str, Any]:
        """
        Получение текущего статуса рисков
        
        Returns:
            Словарь с подробным статусом
        """
        try:
            return {
                "trading_allowed": not self.trading_halted,
                "halt_reason": self.halt_reason,
                "metrics": {
                    "current_drawdown": self.metrics.current_drawdown,
                    "max_drawdown": self.metrics.max_drawdown,
                    "daily_pnl": self.metrics.daily_pnl,
                    "weekly_pnl": self.metrics.weekly_pnl,
                    "monthly_pnl": self.metrics.monthly_pnl,
                    "win_rate": self.metrics.win_rate,
                    "profit_factor": self.metrics.profit_factor,
                    "total_trades": self.metrics.total_trades,
                    "risk_score": self.metrics.risk_score
                },
                "limits": {
                    "max_drawdown_percent": self.limits.max_drawdown_percent,
                    "daily_loss_limit": self.limits.daily_loss_limit,
                    "max_trades_per_day": self.limits.max_trades_per_day,
                    "trades_today": len(self.daily_trades)
                },
                "adaptive": {
                    "enabled": self.adaptive_enabled,
                    "multiplier": self.adaptive_multiplier
                },
                "capital": {
                    "initial": self.initial_capital,
                    "current": self.current_capital,
                    "total_return_percent": ((self.current_capital - self.initial_capital) / self.initial_capital) * 100
                }
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка получения статуса рисков: {e}")
            return {
                "trading_allowed": False,
                "error": str(e)
            }
    
    def export_trade_history(self, format: str = "dict") -> Any:
        """
        Экспорт истории сделок
        
        Args:
            format: Формат экспорта (dict, csv, json)
            
        Returns:
            Данные в запрошенном формате
        """
        try:
            if format == "dict":
                return [
                    {
                        "symbol": trade.symbol,
                        "side": trade.side,
                        "size": trade.size,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "pnl": trade.pnl,
                        "pnl_percentage": trade.pnl_percentage,
                        "timestamp": trade.timestamp,
                        "duration_minutes": trade.duration_minutes,
                        "strategy": trade.strategy
                    }
                    for trade in self.trade_history
                ]
            
            # TODO: Реализовать экспорт в CSV и JSON
            
            return list(self.trade_history)
            
        except Exception as e:
            self.logger.error(f"Ошибка экспорта истории: {e}")
            return []
