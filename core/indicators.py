"""
Индикаторы для торговых стратегий
Реализация Vortex Bands из оригинального кода
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class VortexBandsResult:
    """Результат расчета Vortex Bands"""
    basis: pd.Series
    upper: pd.Series
    lower: pd.Series
    bands_distance: pd.Series
    bands_distance_percent: pd.Series
    crossing_up: pd.Series
    crossing_down: pd.Series


class TechnicalIndicators:
    """
    Класс для расчета технических индикаторов
    Содержит только Vortex Bands и ATR из оригинального кода
    """
    
    def __init__(self):
        self.logger = logging.getLogger("TechnicalIndicators")
    
    @staticmethod
    def _ema(src: pd.Series, alpha: float) -> pd.Series:
        """
        Экспоненциальное скользящее среднее (точный перенос из Pine Script)
        
        Args:
            src: Исходный ряд данных
            alpha: Коэффициент сглаживания
            
        Returns:
            EMA ряд
        """
        result = src.copy()
        
        for i in range(1, len(src)):
            if pd.isna(result.iloc[i-1]):
                result.iloc[i] = src.iloc[i]
            else:
                result.iloc[i] = alpha * src.iloc[i] + (1 - alpha) * result.iloc[i-1]
        
        return result
    
    @staticmethod
    def _mnma(src: pd.Series, length: int) -> pd.Series:
        """
        Modified New Moving Average (точный перенос из Pine Script)
        
        Args:
            src: Исходный ряд данных
            length: Период расчета
            
        Returns:
            MNMA ряд
        """
        alpha = 2 / (length + 1)
        ema1 = TechnicalIndicators._ema(src, alpha)
        ema2 = TechnicalIndicators._ema(ema1, alpha)
        
        return ((2 - alpha) * ema1 - ema2) / (1 - alpha)
    
    def calculate_vortex_bands(self, 
                             df: pd.DataFrame, 
                             length: int = 20, 
                             multiplier: float = 2.0) -> VortexBandsResult:
        """
        Расчет индикатора Vortex Bands с исправлениями из Pine Script
        Точный перенос из оригинального кода calculate_indicators()
        
        Args:
            df: DataFrame с OHLCV данными
            length: Период расчета
            multiplier: Мультипликатор отклонения
            
        Returns:
            VortexBandsResult с рассчитанными значениями
        """
        try:
            # Проверяем входные данные
            required_columns = ['high', 'low', 'close']
            if not all(col in df.columns for col in required_columns):
                raise ValueError(f"DataFrame должен содержать колонки: {required_columns}")
            
            if len(df) < length + 10:
                raise ValueError(f"Недостаточно данных для расчета. Нужно минимум {length + 10} баров")
            
            # Создаем копию DataFrame
            data = df.copy()
            
            # HLC3 источник (как в Pine Script и оригинальном коде)
            data['hlc3'] = (data['high'] + data['low'] + data['close']) / 3
            
            # Расчет Vortex Bands с индивидуальными параметрами (из оригинального кода)
            vb_basis = self._mnma(data['hlc3'], length)
            
            # Отклонение БЕЗ abs() для возможности пересечений (как в Pine Script)
            dev_visual = multiplier * self._mnma(data['hlc3'] - vb_basis, length)
            vb_upper = vb_basis + dev_visual
            vb_lower = vb_basis - dev_visual
            
            # Расчет расстояния между полосами (%)
            bands_distance = abs(vb_upper - vb_lower)
            bands_distance_percent = (bands_distance / 
                                   ((vb_upper + vb_lower) / 2)) * 100
            
            # Определение пересечений полос (из оригинального кода)
            crossing_up = ((vb_upper > vb_lower) & 
                          (vb_upper.shift(1) <= vb_lower.shift(1)))
            crossing_down = ((vb_upper < vb_lower) & 
                            (vb_upper.shift(1) >= vb_lower.shift(1)))
            
            self.logger.debug(f"Vortex Bands рассчитан: length={length}, mult={multiplier}")
            
            return VortexBandsResult(
                basis=vb_basis,
                upper=vb_upper,
                lower=vb_lower,
                bands_distance=bands_distance,
                bands_distance_percent=bands_distance_percent,
                crossing_up=crossing_up,
                crossing_down=crossing_down
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка расчета Vortex Bands: {e}")
            raise
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Расчет Average True Range (ATR) - из оригинального кода
        
        Args:
            df: DataFrame с OHLC данными
            period: Период расчета
            
        Returns:
            ATR ряд
        """
        try:
            # Точный перенос из оригинального кода
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            atr = true_range.rolling(window=period).mean()
            
            self.logger.debug(f"ATR рассчитан с периодом {period}")
            return atr
            
        except Exception as e:
            self.logger.error(f"Ошибка расчета ATR: {e}")
            raise


class VortexBandsAnalyzer:
    """
    Анализатор сигналов Vortex Bands - перенос логики из _check_signal()
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("VortexBandsAnalyzer")
        self.indicators = TechnicalIndicators()
    
    def analyze_signals(self, df: pd.DataFrame, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Анализ сигналов Vortex Bands - точный перенос из _check_signal()
        
        Args:
            df: DataFrame с OHLCV данными (4H таймфрейм)
            symbol: Торговый символ
            
        Returns:
            Словарь с сигналом или None
        """
        try:
            # Получаем параметры для символа
            length = self.config.get('length', 20)
            multiplier = self.config.get('multiplier', 2.0)
            alert_distance_2 = self.config.get('alert_distance_2', 2.5)
            alert_distance_1 = self.config.get('alert_distance_1', 1.5)
            min_bars_required = self.config.get('min_bars_required', 20)
            
            # Используем 4H данные (как в оригинальном коде)
            if len(df) < max(length + 5, min_bars_required):
                return None
            
            # Рассчитываем Vortex Bands
            vb_result = self.indicators.calculate_vortex_bands(
                df, length=length, multiplier=multiplier
            )
            
            # Анализируем после последней завершенной свечи (как в оригинале)
            current_idx = len(df) - 2
            if current_idx < min_bars_required:
                return None
            
            # Получаем текущие значения (точный перенос из _check_signal)
            close = df['close'].iloc[current_idx]
            vb_upper = vb_result.upper.iloc[current_idx]
            vb_lower = vb_result.lower.iloc[current_idx]
            vb_basis = vb_result.basis.iloc[current_idx]
            bands_distance_percent = vb_result.bands_distance_percent.iloc[current_idx]
            bands_distance_prev = vb_result.bands_distance_percent.iloc[current_idx-1]
            crossing_up = vb_result.crossing_up.iloc[current_idx]
            crossing_down = vb_result.crossing_down.iloc[current_idx]
            
            # Проверяем валидность данных
            if any(pd.isna([close, vb_upper, vb_lower, bands_distance_percent])):
                return None
            
            # Определение направления движения полос (из оригинального кода)
            bands_approaching = bands_distance_percent < bands_distance_prev
            
            # Условие готовности к входу (из оригинального кода)
            approaching_condition = (bands_approaching or 
                                   bands_distance_percent <= (alert_distance_1 * 1.3))
            
            # Направление momentum (из оригинального кода)
            bullish_momentum = close > vb_basis
            bearish_momentum = close < vb_basis
            
            # Alert 1: срабатывает при достижении alert_distance_1
            alert_1_triggered = (bands_distance_percent <= alert_distance_1 and 
                               approaching_condition)
            
            # Дополнительное условие: пересечения полос
            crossing_entry_condition = ((crossing_up or crossing_down) and 
                                      bands_distance_percent > alert_distance_1)
            
            # Основные сигналы входа
            long_alert_1_condition = ((alert_1_triggered or crossing_entry_condition) and 
                                    bullish_momentum)
            short_alert_1_condition = ((alert_1_triggered or crossing_entry_condition) and 
                                     bearish_momentum)
            
            # Запасные сигналы при фактическом пересечении
            backup_long_signal = crossing_up and bullish_momentum
            backup_short_signal = crossing_down and bearish_momentum
            
            # Комбинированные сигналы входа
            final_long_condition = long_alert_1_condition or backup_long_signal
            final_short_condition = short_alert_1_condition or backup_short_signal
            
            # === LONG SIGNAL === (точный перенос из оригинального кода)
            if final_long_condition:
                return {
                    "signal": "Buy",
                    "entry": close,
                    "reason": f"VB 4H Long L{length}: bands {bands_distance_percent:.2f}% (trigger:{alert_distance_1}%), crossing_up: {crossing_up}, backup: {backup_long_signal}",
                    "signal_type": "reversal",
                    "config_used": self.config
                }
            
            # === SHORT SIGNAL === (точный перенос из оригинального кода)
            if final_short_condition:
                return {
                    "signal": "Sell", 
                    "entry": close,
                    "reason": f"VB 4H Short L{length}: bands {bands_distance_percent:.2f}% (trigger:{alert_distance_1}%), crossing_down: {crossing_down}, backup: {backup_short_signal}",
                    "signal_type": "reversal",
                    "config_used": self.config
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"{symbol} - Ошибка Vortex Bands анализа: {e}")
            return None


# Глобальный экземпляр индикаторов
technical_indicators = TechnicalIndicators()