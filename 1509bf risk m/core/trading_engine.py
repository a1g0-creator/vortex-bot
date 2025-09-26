"""
Основной торговый движок
Перенос логики из SimpleTradingBot с модульной архитектурой
"""

import asyncio
import time
import logging
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime

from exchanges.base_exchange import BaseExchange, OrderSide, OrderType
from config.config_loader import config_loader, get_vortex_config
from .indicators import VortexBandsAnalyzer, technical_indicators


class TradingEngine:
    """
    Основной торговый движок - перенос из SimpleTradingBot
    """
    
    def __init__(self, exchange: BaseExchange, mode: str = "signals"):
        self.exchange = exchange
        self.mode = mode  # "auto" или "signals"
        self.positions = {}  # {symbol: position_data}
        self.signal_alerts = {}  # {symbol: {entry_min, entry_max, reported_entry_zone}}
        
        # Временные метки для контроля интервалов
        self.last_scan_time = 0
        self.last_position_update = 0
        self.last_balance_update = 0
        
        # Начальный капитал
        self.initial_capital = 10000.0  # Будет обновлен при инициализации
        self.start_time = time.time()
        
        self.logger = logging.getLogger("TradingEngine")
    
    async def initialize(self) -> bool:
        """
        Инициализация торгового движка
        
        Returns:
            True если успешно инициализирован
        """
        try:
            # Инициализируем биржу
            if not await self.exchange.initialize():
                self.logger.error("Не удалось инициализировать биржу")
                return False
            
            # Получаем начальный баланс
            balance = await self.exchange.get_balance()
            if balance:
                self.initial_capital = balance.wallet_balance
                self.logger.info(f"💰 Начальный капитал: {self.initial_capital:.2f} USDT")
            
            # Синхронизируем позиции с биржей
            await self._sync_positions()
            
            self.logger.info(f"✅ Торговый движок инициализирован в режиме: {self.mode}")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка инициализации торгового движка: {e}")
            return False
    
    def _calculate_position_size_4h(self, symbol: str, current_price: float) -> float:
        """
        Расчет размера позиции для 4H стратегии (из оригинального кода)
        """
        try:
            # Получаем настройки управления рисками
            strategies_config = config_loader.get_config("strategies")
            risk_config = strategies_config.get("risk_management", {})
            position_sizing = risk_config.get("position_sizing", {})
            
            # Получаем текущий баланс
            # В данном случае используем кэшированное значение для производительности
            current_balance = self.initial_capital  # Упрощение для MVP
            
            if current_balance <= 0:
                self.logger.error(f"{symbol} - Нулевой баланс")
                return 0.0
            
            # Метод расчета размера
            method = position_sizing.get("method", "fixed_percent")
            
            if method == "fixed_amount":
                position_value_usdt = position_sizing.get("fixed_amount", 100.0)
            elif method == "fixed_percent":
                position_risk_percent = position_sizing.get("fixed_percent", 5.0) / 100
                position_value_usdt = current_balance * position_risk_percent
            else:
                # Дефолтный метод - 5% от баланса
                position_value_usdt = current_balance * 0.05
            
            # Конвертируем в количество монет
            quantity = position_value_usdt / current_price
            
            # Проверяем лимиты
            min_size = position_sizing.get("min_position_size", 10.0) / current_price
            max_size = position_sizing.get("max_position_size", 10000.0) / current_price
            
            quantity = max(min_size, min(quantity, max_size))
            
            self.logger.debug(
                f"{symbol} - Расчет размера: баланс={current_balance:.2f}, "
                f"размер={position_value_usdt:.2f} USDT, quantity={quantity:.8f}"
            )
            
            return quantity
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка расчета размера позиции: {e}")
            return 0.0
    
    def _get_total_positions(self) -> int:
        """Подсчет общего количества позиций"""
        return len(self.positions)
    
    async def _scan_opportunities(self):
        """
        Сканирование торговых возможностей (из оригинального кода)
        """
        try:
            current_positions = self._get_total_positions()
            
            # Получаем список торгуемых символов из конфигурации
            symbols = await self._get_vortex_trading_symbols()
            
            if not symbols:
                self.logger.warning("Нет доступных символов для торговли")
                return
            
            self.logger.info(f"Сканируем {len(symbols)} Vortex Bands инструментов: {symbols}")
            
            signals_checked = 0
            signals_found = 0
            
            for symbol in symbols:
                try:
                    # Получаем 4H данные
                    klines = await self.exchange.get_klines(symbol, "240", limit=200)
                    if not klines or len(klines) < 50:
                        self.logger.debug(f"{symbol} - Недостаточно данных 4H")
                        continue
                    
                    # Преобразуем в DataFrame
                    df = self._klines_to_dataframe(klines)
                    
                    signals_checked += 1
                    signal = await self._check_signal(symbol, df)
                    
                    if signal:
                        signals_found += 1
                        config = signal.get('config_used', {})
                        self.logger.info(
                            f"{symbol} - VORTEX BANDS 4H СИГНАЛ "
                            f"(L{config.get('length', 'N/A')}): {signal['reason']}"
                        )
                        
                        if self.mode == "auto":
                            success = await self._open_position(symbol, signal)
                            if success:
                                self.logger.info(f"{symbol} - Позиция успешно открыта/развернута")
                        else:  # режим сигналов
                            await self._send_signal_alert(symbol, signal)
                
                except Exception as e:
                    self.logger.error(f"{symbol} - Ошибка Vortex Bands 4H анализа: {e}")
            
            self.logger.info(
                f"Vortex Bands 4H сканирование завершено: "
                f"проверено {signals_checked}, найдено {signals_found} сигналов, "
                f"позиций: {current_positions}"
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка Vortex Bands 4H сканирования: {e}")
    
    async def _get_vortex_trading_symbols(self) -> List[str]:
        """
        Получение списка символов для торговли из конфигурации
        """
        try:
            strategies_config = config_loader.get_config("strategies")
            vortex_config = strategies_config.get("vortex_bands", {})
            instruments = vortex_config.get("instruments", {})
            
            # Получаем только включенные инструменты
            enabled_symbols = []
            for symbol, config in instruments.items():
                if config.get("enabled", True):
                    enabled_symbols.append(symbol)
            
            if not enabled_symbols:
                # Дефолтные символы если конфигурация пуста
                enabled_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
                self.logger.warning("Используем дефолтные символы")
            
            self.logger.info(f"Активные Vortex символы: {enabled_symbols}")
            return enabled_symbols
            
        except Exception as e:
            self.logger.error(f"Ошибка получения торговых символов: {e}")
            return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
    
    def _klines_to_dataframe(self, klines: List) -> pd.DataFrame:
        """
        Преобразование свечей в DataFrame
        """
        try:
            data = []
            for kline in klines:
                data.append({
                    'timestamp': pd.to_datetime(kline.timestamp, unit='ms'),
                    'open': kline.open,
                    'high': kline.high,
                    'low': kline.low,
                    'close': kline.close,
                    'volume': kline.volume
                })
            
            df = pd.DataFrame(data)
            df = df.sort_values('timestamp').reset_index(drop=True)
            
            # Убираем последнюю неполную свечу
            df = df.iloc[:-1]
            
            return df
            
        except Exception as e:
            self.logger.error(f"Ошибка преобразования свечей в DataFrame: {e}")
            return pd.DataFrame()
    
    async def _check_signal(self, symbol: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Проверка сигналов Vortex Bands (перенос из оригинального кода)
        """
        try:
            # Получаем конфигурацию для символа
            config = get_vortex_config(symbol)
            
            # Создаем анализатор с конфигурацией
            analyzer = VortexBandsAnalyzer(config)
            
            # Анализируем сигналы
            signal = analyzer.analyze_signals(df, symbol)
            
            return signal
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка проверки сигнала: {e}")
            return None
    
    async def _open_position(self, symbol: str, signal: Dict[str, Any]) -> bool:
        """
        Открытие позиции (перенос из оригинального кода)
        """
        try:
            # Получаем текущую цену
            ticker = await self.exchange.get_ticker(symbol)
            if not ticker:
                self.logger.error(f"{symbol} - Не удалось получить тикер")
                return False
            
            current_price = ticker.last_price
            
            # Рассчитываем размер позиции
            quantity = self._calculate_position_size_4h(symbol, current_price)
            if quantity <= 0:
                self.logger.error(f"{symbol} - Некорректный размер позиции: {quantity}")
                return False
            
            # Округляем количество
            quantity_str = await self._round_quantity(symbol, quantity)
            if quantity_str == "0":
                self.logger.error(f"{symbol} - Количество после округления равно 0")
                return False
            
            config = signal.get('config_used', {})
            
            # Проверяем есть ли уже позиция по этому символу
            if symbol in self.positions:
                existing_position = self.positions[symbol]
                existing_side = existing_position['side']
                new_side = signal["signal"]
                
                # Если сигнал в том же направлении - игнорируем
                if existing_side == new_side:
                    self.logger.debug(
                        f"{symbol} - Уже есть позиция в том же направлении: {existing_side}"
                    )
                    return False
                
                # РЕВЕРС ПОЗИЦИИ
                self.logger.info(
                    f"{symbol} - РЕВЕРС (L{config.get('length', 'N/A')}): "
                    f"{existing_side} -> {new_side}"
                )
                
                # Сначала закрываем существующую позицию
                await self._close_position_for_reversal(symbol)
            
            # Размещаем ордер на открытие новой позиции
            side = OrderSide.BUY if signal["signal"] == "Buy" else OrderSide.SELL
            
            order_info = await self.exchange.place_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                quantity=float(quantity_str)
            )
            
            if order_info:
                # Сохраняем в систему позиций
                self.positions[symbol] = {
                    'side': signal["signal"],
                    'size': float(quantity_str),
                    'entry': current_price,
                    'open_time': time.time(),
                    'strategy': 'vortex_4h',
                    'reversal_strategy': True,
                    'vortex_config': config
                }
                
                # Отправляем уведомление
                await self._send_position_notification(symbol, signal, quantity_str, current_price, config)
                
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка открытия позиции Vortex 4H: {e}")
            return False
    
    async def _round_quantity(self, symbol: str, quantity: float) -> str:
        """
        Округление количества согласно правилам биржи
        """
        try:
            # Получаем информацию об инструменте
            instrument = await self.exchange.get_instrument_info(symbol)
            if not instrument:
                self.logger.error(f"{symbol} - Не удалось получить информацию об инструменте")
                return "0"
            
            # Используем метод биржевого адаптера
            return self.exchange.round_quantity(symbol, quantity)
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка округления количества: {e}")
            return "0"
    
    async def _close_position_for_reversal(self, symbol: str):
        """
        Закрытие позиции для реверса (перенос из оригинального кода)
        """
        try:
            if symbol not in self.positions:
                return
            
            position = self.positions[symbol]
            side = position['side']
            size = position['size']
            
            # Определяем сторону закрытия
            close_side = OrderSide.SELL if side == "Buy" else OrderSide.BUY
            size_str = await self._round_quantity(symbol, size)
            
            if size_str == "0":
                self.logger.error(f"{symbol} - Не удалось рассчитать размер для закрытия")
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
                # Удаляем позицию из отслеживания
                del self.positions[symbol]
                self.logger.info(f"{symbol} - Позиция закрыта для реверса")
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка закрытия позиции для реверса: {e}")
    
    async def _update_all_positions(self):
        """
        Обновление всех позиций (перенос из оригинального кода)
        """
        try:
            if not self.positions:
                return
            
            # Синхронизируем с биржей
            await self._sync_positions()
            
            for symbol in list(self.positions.keys()):
                try:
                    ticker = await self.exchange.get_ticker(symbol)
                    if not ticker:
                        continue
                    
                    await self._update_single_position(symbol, ticker.last_price)
                    
                except Exception as e:
                    self.logger.error(f"{symbol} - Ошибка обновления: {e}")
                    
        except Exception as e:
            self.logger.error(f"Ошибка обновления позиций: {e}")
    
    async def _update_single_position(self, symbol: str, current_price: float):
        """
        Управление позицией для Vortex Bands - только мониторинг (перенос из оригинального кода)
        НЕТ автоматических SL/TP - только реверс по сигналам
        """
        try:
            if symbol not in self.positions:
                return
            
            position = self.positions[symbol]
            entry_price = position['entry']
            side = position['side']
            
            # Рассчитываем P&L только для мониторинга
            if side == "Buy":
                profit_pct = ((current_price - entry_price) / entry_price) * 100
            else:
                profit_pct = ((entry_price - current_price) / entry_price) * 100
            
            # ЭКСТРЕННЫЙ стоп-лосс только при критических потерях (-20%)
            # Это защита от форс-мажоров, не часть стратегии
            if profit_pct < -20.0:
                await self._close_position(symbol, f"EMERGENCY Stop Loss: {profit_pct:.2f}%")
                return
            
            # Логируем состояние позиции каждые 10% изменения
            if abs(profit_pct) > 0 and int(abs(profit_pct)) % 10 == 0:
                self.logger.info(
                    f"{symbol} - Позиция {side}: P&L {profit_pct:+.1f}% "
                    f"(entry: {entry_price:.6f}, current: {current_price:.6f})"
                )
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка обновления позиции Vortex: {e}")
    
    async def _close_position(self, symbol: str, reason: str):
        """
        Полное закрытие позиции (перенос из оригинального кода)
        """
        try:
            if symbol not in self.positions:
                return
            
            position = self.positions[symbol]
            side = position['side']
            size = position['size']
            
            # Определяем сторону закрытия
            close_side = OrderSide.SELL if side == "Buy" else OrderSide.BUY
            size_str = await self._round_quantity(symbol, size)
            
            if size_str == "0":
                self.logger.error(f"{symbol} - Не удалось рассчитать размер для закрытия")
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
                # Удаляем позицию из отслеживания
                del self.positions[symbol]
                
                # Рассчитываем P&L
                ticker = await self.exchange.get_ticker(symbol)
                profit_pct = 0
                if ticker and position['entry']:
                    current_price = ticker.last_price
                    if side == "Buy":
                        profit_pct = ((current_price - position['entry']) / position['entry']) * 100
                    else:
                        profit_pct = ((position['entry'] - current_price) / position['entry']) * 100
                
                await self._send_close_notification(symbol, reason, profit_pct)
                
                self.logger.info(f"{symbol} - Позиция закрыта: {reason}, P&L: {profit_pct:+.1f}%")
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка закрытия позиции: {e}")
    
    async def _sync_positions(self):
        """
        Синхронизация позиций с биржей (перенос из оригинального кода)
        """
        try:
            # Получаем позиции с биржи
            exchange_positions = await self.exchange.get_positions()
            
            # Создаем словарь позиций с биржи
            exchange_pos_dict = {}
            for pos in exchange_positions:
                if pos.size > 0:
                    exchange_pos_dict[pos.symbol] = pos
            
            # Удаляем позиции, которых нет на бирже
            for symbol in list(self.positions.keys()):
                if symbol not in exchange_pos_dict:
                    self.logger.info(f"🔄 {symbol} - Позиция закрыта на бирже")
                    del self.positions[symbol]
            
            # Добавляем позиции с биржи, которых нет локально
            for symbol, pos in exchange_pos_dict.items():
                if symbol not in self.positions:
                    self.logger.info(f"🔄 {symbol} - Найдена позиция на бирже")
                    
                    self.positions[symbol] = {
                        'side': pos.side,
                        'size': pos.size,
                        'entry': pos.entry_price,
                        'open_time': time.time(),
                        'strategy': 'vortex_4h',
                        'reversal_strategy': True
                    }
                    
        except Exception as e:
            self.logger.error(f"Ошибка синхронизации позиций: {e}")
    
    async def _send_signal_alert(self, symbol: str, signal: Dict[str, Any]) -> bool:
        """
        Отправка сигнального уведомления (перенос из оригинального кода)
        """
        try:
            current_price = signal["entry"]
            side = signal["signal"]
            reason = signal["reason"]
            config = signal.get('config_used', {})
            
            # Здесь будет интеграция с Telegram или другими уведомлениями
            self.logger.info(
                f"📢 {symbol} - Сигнал {side}: цена {current_price:.6f}, "
                f"конфиг L{config.get('length', 'N/A')}, причина: {reason}"
            )
            
            # Сохраняем информацию о сигнале
            self.signal_alerts[symbol] = {
                'side': side,
                'entry': current_price,
                'reason': reason,
                'config': config,
                'timestamp': time.time(),
                'strategy_type': 'reversal'
            }
            
            return True
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка отправки сигнала: {e}")
            return False
    
    async def _send_position_notification(self, symbol: str, signal: Dict, quantity_str: str, 
                                        current_price: float, config: Dict):
        """Отправка уведомления об открытии позиции"""
        try:
            message = (
                f"VORTEX BANDS 4H {signal['signal'].upper()} {symbol}\n"
                f"Количество: {quantity_str}\n"
                f"Цена входа: {current_price:.6f}\n"
                f"Параметры: L={config.get('length', 'N/A')}, M={config.get('multiplier', 'N/A')}\n"
                f"Стратегия: РЕВЕРСИВНАЯ (без SL/TP)\n"
                f"Позиций: {len(self.positions)}/10\n"
                f"Причина: {signal['reason']}"
            )
            
            self.logger.info(f"📈 Позиция открыта: {message}")
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки уведомления о позиции: {e}")
    
    async def _send_close_notification(self, symbol: str, reason: str, profit_pct: float):
        """Отправка уведомления о закрытии позиции"""
        try:
            message = (
                f"❌ {symbol} ЗАКРЫТ\n"
                f"Причина: {reason}\n"
                f"P&L: {profit_pct:+.1f}%\n"
                f"Позиций осталось: {len(self.positions)}/10"
            )
            
            self.logger.info(f"📉 Позиция закрыта: {message}")
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки уведомления о закрытии: {e}")
    
    async def run(self):
        """
        Основной цикл торгового движка (перенос из оригинального кода)
        """
        try:
            self.logger.info(f"🚀 Запуск торгового движка в режиме: {self.mode}")
            
            # Инициализируем позиции при запуске
            await self._sync_positions()
            
            while True:
                try:
                    current_time = time.time()
                    
                    # 1. Обновляем позиции каждые 2 минуты
                    if current_time - self.last_position_update > 120:
                        await self._update_all_positions()
                        self.last_position_update = current_time
                    
                    # 2. Ищем новые возможности каждые 5 минут
                    if current_time - self.last_scan_time > 300:
                        await self._scan_opportunities()
                        self.last_scan_time = current_time
                    
                    # 3. Обновляем баланс каждые 5 минут
                    if current_time - self.last_balance_update > 300:
                        balance = await self.exchange.get_balance()
                        if balance:
                            self.logger.info(f"🔄 Баланс: {balance.wallet_balance:.2f} USDT")
                        self.last_balance_update = current_time
                    
                    await asyncio.sleep(10)  # Основной цикл каждые 10 сек
                    
                except Exception as e:
                    self.logger.error(f"Ошибка в основном цикле: {e}")
                    await asyncio.sleep(30)
                    
        except Exception as e:
            self.logger.error(f"Критическая ошибка торгового движка: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """
        Получение статуса торгового движка
        """
        try:
            uptime = time.time() - self.start_time
            
            return {
                "mode": self.mode,
                "positions_count": len(self.positions),
                "signals_count": len(self.signal_alerts),
                "uptime_seconds": uptime,
                "exchange": str(self.exchange),
                "positions": list(self.positions.keys()),
                "last_scan_time": self.last_scan_time,
                "last_position_update": self.last_position_update
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка получения статуса: {e}")
            return {}
    
    async def close(self):
        """
        Закрытие торгового движка
        """
        try:
            self.logger.info("Закрытие торгового движка...")
            await self.exchange.close()
            
        except Exception as e:
            self.logger.error(f"Ошибка закрытия торгового движка: {e}")