"""
Менеджер позиций для торгового бота
Управление открытием, закрытием и мониторингом позиций
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

from exchanges.base_exchange import BaseExchange, OrderSide, OrderType
from config.config_loader import config_loader


@dataclass
class PositionData:
    """Данные о позиции"""
    symbol: str
    side: str  # "Buy" или "Sell"
    size: float
    entry_price: float
    current_price: float
    pnl: float
    pnl_percentage: float
    open_time: float
    strategy: str
    config: Dict[str, Any]
    order_id: Optional[str] = None
    
    @property
    def duration_minutes(self) -> float:
        """Время в позиции в минутах"""
        return (time.time() - self.open_time) / 60


class PositionManager:
    """
    Менеджер позиций - извлечен из TradingEngine
    Централизованное управление всеми позициями
    """
    
    def __init__(self, exchange: BaseExchange):
        self.exchange = exchange
        self.positions: Dict[str, PositionData] = {}
        self.logger = logging.getLogger("PositionManager")
        
        # Настройки из конфигурации
        self._load_config()
    
    def _load_config(self):
        """Загрузка настроек управления позициями"""
        try:
            strategies_config = config_loader.get_config("strategies")
            risk_config = strategies_config.get("risk_management", {})
            
            # Настройки управления рисками
            self.max_positions = risk_config.get("global", {}).get("max_positions", 10)
            self.daily_loss_limit = risk_config.get("global", {}).get("daily_loss_limit", 500.0)
            self.max_drawdown_percent = risk_config.get("global", {}).get("max_drawdown_percent", 20.0)
            
            # Настройки размера позиций
            position_sizing = risk_config.get("position_sizing", {})
            self.sizing_method = position_sizing.get("method", "fixed_percent")
            self.fixed_amount = position_sizing.get("fixed_amount", 100.0)
            self.fixed_percent = position_sizing.get("fixed_percent", 5.0)
            self.min_position_size = position_sizing.get("min_position_size", 10.0)
            self.max_position_size = position_sizing.get("max_position_size", 10000.0)
            
            # Защита от переторговли
            overtrading = risk_config.get("overtrading_protection", {})
            self.max_trades_per_day = overtrading.get("max_trades_per_day", 20)
            self.max_trades_per_hour = overtrading.get("max_trades_per_hour", 5)
            self.min_trade_interval = overtrading.get("min_trade_interval", 30)
            
            self.logger.info("Конфигурация позиций загружена")
            
        except Exception as e:
            self.logger.error(f"Ошибка загрузки конфигурации: {e}")
            # Дефолтные значения
            self.max_positions = 10
            self.sizing_method = "fixed_percent"
            self.fixed_percent = 5.0
    
    async def calculate_position_size(self, symbol: str, current_price: float, 
                                    strategy: str = "vortex_4h") -> float:
        """
        Расчет размера позиции с учетом настроек риск-менеджмента
        
        Args:
            symbol: Торговый символ
            current_price: Текущая цена
            strategy: Название стратегии
            
        Returns:
            Размер позиции в базовой валюте
        """
        try:
            # Получаем текущий баланс
            balance = await self.exchange.get_balance()
            if not balance or balance.wallet_balance <= 0:
                self.logger.error(f"{symbol} - Нулевой баланс")
                return 0.0
            
            current_balance = balance.wallet_balance
            
            # Рассчитываем размер в зависимости от метода
            if self.sizing_method == "fixed_amount":
                position_value_usdt = self.fixed_amount
            elif self.sizing_method == "fixed_percent":
                position_value_usdt = current_balance * (self.fixed_percent / 100)
            elif self.sizing_method == "adaptive":
                # TODO: Реализовать адаптивное управление
                position_value_usdt = current_balance * 0.05
            else:
                # Дефолт - 5% от баланса
                position_value_usdt = current_balance * 0.05
            
            # Применяем лимиты
            position_value_usdt = max(self.min_position_size, 
                                    min(position_value_usdt, self.max_position_size))
            
            # Конвертируем в количество монет
            quantity = position_value_usdt / current_price
            
            # Дополнительные проверки для конкретного символа
            instrument = await self.exchange.get_instrument_info(symbol)
            if instrument:
                min_qty = instrument.min_order_qty
                max_qty = instrument.max_order_qty
                
                if quantity < min_qty:
                    self.logger.warning(f"{symbol} - Количество {quantity} меньше минимума {min_qty}")
                    return min_qty * 1.1  # Буфер 10%
                
                if quantity > max_qty * 0.5:  # Не более 50% от максимума
                    self.logger.warning(f"{symbol} - Количество {quantity} слишком большое")
                    quantity = max_qty * 0.1
            
            self.logger.debug(
                f"{symbol} - Расчет размера: баланс={current_balance:.2f}, "
                f"метод={self.sizing_method}, размер={position_value_usdt:.2f} USDT, "
                f"quantity={quantity:.8f}"
            )
            
            return quantity
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка расчета размера позиции: {e}")
            return 0.0
    
    async def open_position(self, symbol: str, side: str, signal: Dict[str, Any]) -> bool:
        """
        Открытие новой позиции или реверс существующей
        
        Args:
            symbol: Торговый символ
            side: Направление ("Buy" или "Sell")
            signal: Данные сигнала
            
        Returns:
            True если позиция успешно открыта
        """
        try:
            # Проверяем лимиты
            if not await self._check_position_limits(symbol):
                return False
            
            # Получаем текущую цену
            ticker = await self.exchange.get_ticker(symbol)
            if not ticker:
                self.logger.error(f"{symbol} - Не удалось получить тикер")
                return False
            
            current_price = ticker.last_price
            
            # Проверяем существующую позицию
            if symbol in self.positions:
                existing_position = self.positions[symbol]
                if existing_position.side == side:
                    self.logger.debug(f"{symbol} - Уже есть позиция в том же направлении")
                    return False
                
                # Реверс позиции
                self.logger.info(f"{symbol} - РЕВЕРС: {existing_position.side} -> {side}")
                await self._close_position_for_reversal(symbol)
            
            # Рассчитываем размер позиции
            quantity = await self.calculate_position_size(symbol, current_price)
            if quantity <= 0:
                self.logger.error(f"{symbol} - Некорректный размер позиции")
                return False
            
            # Округляем количество
            quantity_str = self.exchange.round_quantity(symbol, quantity)
            if quantity_str == "0":
                self.logger.error(f"{symbol} - Количество после округления равно 0")
                return False
            
            # Размещаем ордер
            order_side = OrderSide.BUY if side == "Buy" else OrderSide.SELL
            order_info = await self.exchange.place_order(
                symbol=symbol,
                side=order_side,
                order_type=OrderType.MARKET,
                quantity=float(quantity_str)
            )
            
            if order_info:
                # Создаем запись о позиции
                position_data = PositionData(
                    symbol=symbol,
                    side=side,
                    size=float(quantity_str),
                    entry_price=current_price,
                    current_price=current_price,
                    pnl=0.0,
                    pnl_percentage=0.0,
                    open_time=time.time(),
                    strategy=signal.get("signal_type", "vortex_4h"),
                    config=signal.get("config_used", {}),
                    order_id=order_info.order_id
                )
                
                self.positions[symbol] = position_data
                
                self.logger.info(
                    f"✅ {symbol} позиция открыта: {side} {quantity_str} @ {current_price:.6f}"
                )
                
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка открытия позиции: {e}")
            return False
    
    async def close_position(self, symbol: str, reason: str = "Manual close") -> bool:
        """
        Закрытие позиции
        
        Args:
            symbol: Торговый символ
            reason: Причина закрытия
            
        Returns:
            True если позиция успешно закрыта
        """
        try:
            if symbol not in self.positions:
                self.logger.warning(f"{symbol} - Позиция не найдена для закрытия")
                return False
            
            position = self.positions[symbol]
            
            # Определяем противоположную сторону
            close_side = OrderSide.SELL if position.side == "Buy" else OrderSide.BUY
            
            # Округляем размер
            size_str = self.exchange.round_quantity(symbol, position.size)
            if size_str == "0":
                self.logger.error(f"{symbol} - Не удалось рассчитать размер для закрытия")
                return False
            
            # Размещаем ордер на закрытие
            order_info = await self.exchange.place_order(
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=float(size_str),
                reduce_only=True
            )
            
            if order_info:
                # Рассчитываем финальный P&L
                final_pnl = await self._calculate_final_pnl(symbol)
                
                self.logger.info(
                    f"❌ {symbol} позиция закрыта: {reason}, "
                    f"P&L: {final_pnl:.2f}%, время: {position.duration_minutes:.1f}м"
                )
                
                # Удаляем позицию
                del self.positions[symbol]
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка закрытия позиции: {e}")
            return False
    
    async def update_positions(self):
        """
        Обновление всех позиций - синхронизация с биржей и расчет P&L
        """
        try:
            if not self.positions:
                return
            
            # Синхронизируем с биржей
            await self._sync_with_exchange()
            
            # Обновляем каждую позицию
            for symbol in list(self.positions.keys()):
                await self._update_single_position(symbol)
                
        except Exception as e:
            self.logger.error(f"Ошибка обновления позиций: {e}")
    
    async def _update_single_position(self, symbol: str):
        """
        Обновление отдельной позиции
        """
        try:
            if symbol not in self.positions:
                return
            
            position = self.positions[symbol]
            
            # Получаем текущую цену
            ticker = await self.exchange.get_ticker(symbol)
            if not ticker:
                return
            
            current_price = ticker.last_price
            position.current_price = current_price
            
            # Рассчитываем P&L
            if position.side == "Buy":
                pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
            else:
                pnl_pct = ((position.entry_price - current_price) / position.entry_price) * 100
            
            position.pnl_percentage = pnl_pct
            position.pnl = (position.size * position.entry_price) * (pnl_pct / 100)
            
            # Проверяем экстренные условия
            await self._check_emergency_conditions(symbol, position)
            
            # Логируем значительные изменения
            if abs(pnl_pct) > 0 and int(abs(pnl_pct)) % 5 == 0:
                self.logger.info(
                    f"{symbol} - {position.side}: P&L {pnl_pct:+.1f}% "
                    f"(entry: {position.entry_price:.6f}, current: {current_price:.6f})"
                )
                
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка обновления позиции: {e}")
    
    async def _check_emergency_conditions(self, symbol: str, position: PositionData):
        """
        Проверка экстренных условий для принудительного закрытия
        """
        try:
            # Экстренный стоп-лосс при критических потерях
            if position.pnl_percentage < -20.0:
                self.logger.warning(
                    f"{symbol} - ЭКСТРЕННОЕ ЗАКРЫТИЕ: потери {position.pnl_percentage:.2f}%"
                )
                await self.close_position(symbol, f"Emergency Stop Loss: {position.pnl_percentage:.2f}%")
                return
            
            # Проверка максимального времени в позиции (если настроено)
            max_time = self._get_max_position_time(position.strategy)
            if max_time > 0 and position.duration_minutes > max_time:
                self.logger.info(
                    f"{symbol} - Закрытие по времени: {position.duration_minutes:.1f}м > {max_time}м"
                )
                await self.close_position(symbol, f"Max time exceeded: {position.duration_minutes:.1f}m")
                
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка проверки экстренных условий: {e}")
    
    def _get_max_position_time(self, strategy: str) -> int:
        """
        Получение максимального времени в позиции для стратегии
        
        Returns:
            Максимальное время в минутах (0 = без ограничений)
        """
        try:
            strategies_config = config_loader.get_config("strategies")
            vortex_config = strategies_config.get("vortex_bands", {})
            position_mgmt = vortex_config.get("trading_logic", {}).get("position_management", {})
            
            return position_mgmt.get("max_position_time", 0)
            
        except Exception:
            return 0  # Без ограничений по умолчанию
    
    async def _sync_with_exchange(self):
        """
        Синхронизация локальных позиций с позициями на бирже
        """
        try:
            # Получаем позиции с биржи
            exchange_positions = await self.exchange.get_positions()
            
            # Создаем словарь позиций с биржи
            exchange_pos_dict = {}
            for pos in exchange_positions:
                if pos.size > 0:
                    exchange_pos_dict[pos.symbol] = pos
            
            # Удаляем локальные позиции, которых нет на бирже
            for symbol in list(self.positions.keys()):
                if symbol not in exchange_pos_dict:
                    self.logger.info(f"🔄 {symbol} - Позиция закрыта на бирже")
                    del self.positions[symbol]
            
            # Добавляем позиции с биржи, которых нет локально
            for symbol, exchange_pos in exchange_pos_dict.items():
                if symbol not in self.positions:
                    self.logger.info(f"🔄 {symbol} - Найдена позиция на бирже")
                    
                    position_data = PositionData(
                        symbol=symbol,
                        side=exchange_pos.side,
                        size=exchange_pos.size,
                        entry_price=exchange_pos.entry_price,
                        current_price=exchange_pos.mark_price,
                        pnl=exchange_pos.pnl,
                        pnl_percentage=exchange_pos.pnl_percentage,
                        open_time=time.time(),  # Приблизительное время
                        strategy="unknown",
                        config={}
                    )
                    
                    self.positions[symbol] = position_data
                    
        except Exception as e:
            self.logger.error(f"Ошибка синхронизации с биржей: {e}")
    
    async def _close_position_for_reversal(self, symbol: str):
        """
        Закрытие позиции для реверса (без уведомлений P&L)
        """
        try:
            if symbol not in self.positions:
                return
            
            position = self.positions[symbol]
            close_side = OrderSide.SELL if position.side == "Buy" else OrderSide.BUY
            size_str = self.exchange.round_quantity(symbol, position.size)
            
            if size_str == "0":
                self.logger.error(f"{symbol} - Не удалось рассчитать размер для реверса")
                return
            
            # Размещаем ордер на закрытие
            order_info = await self.exchange.place_order(
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=float(size_str),
                reduce_only=True
            )
            
            if order_info:
                del self.positions[symbol]
                self.logger.info(f"{symbol} - Позиция закрыта для реверса")
                
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка закрытия для реверса: {e}")
    
    async def _calculate_final_pnl(self, symbol: str) -> float:
        """
        Расчет финального P&L при закрытии позиции
        """
        try:
            if symbol not in self.positions:
                return 0.0
            
            position = self.positions[symbol]
            ticker = await self.exchange.get_ticker(symbol)
            
            if not ticker:
                return position.pnl_percentage
            
            current_price = ticker.last_price
            
            if position.side == "Buy":
                pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
            else:
                pnl_pct = ((position.entry_price - current_price) / position.entry_price) * 100
            
            return pnl_pct
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка расчета финального P&L: {e}")
            return 0.0
    
    async def _check_position_limits(self, symbol: str) -> bool:
        """
        Проверка лимитов перед открытием позиции
        """
        try:
            # Проверяем максимальное количество позиций
            if len(self.positions) >= self.max_positions:
                self.logger.warning(f"Достигнут лимит позиций: {len(self.positions)}/{self.max_positions}")
                return False
            
            # TODO: Добавить проверки на дневные лимиты потерь
            # TODO: Добавить проверки на частоту торговли
            
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки лимитов: {e}")
            return False
    
    def get_positions_summary(self) -> Dict[str, Any]:
        """
        Получение сводки по всем позициям
        """
        try:
            if not self.positions:
                return {
                    "total_positions": 0,
                    "total_pnl": 0.0,
                    "total_pnl_percent": 0.0,
                    "positions": []
                }
            
            total_pnl = sum(pos.pnl for pos in self.positions.values())
            total_value = sum(pos.size * pos.entry_price for pos in self.positions.values())
            total_pnl_percent = (total_pnl / total_value * 100) if total_value > 0 else 0.0
            
            positions_list = []
            for symbol, pos in self.positions.items():
                positions_list.append({
                    "symbol": symbol,
                    "side": pos.side,
                    "size": pos.size,
                    "entry_price": pos.entry_price,
                    "current_price": pos.current_price,
                    "pnl_percentage": pos.pnl_percentage,
                    "duration_minutes": pos.duration_minutes,
                    "strategy": pos.strategy
                })
            
            return {
                "total_positions": len(self.positions),
                "total_pnl": total_pnl,
                "total_pnl_percent": total_pnl_percent,
                "positions": positions_list
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка получения сводки позиций: {e}")
            return {
                "total_positions": 0,
                "total_pnl": 0.0,
                "total_pnl_percent": 0.0,
                "positions": []
            }