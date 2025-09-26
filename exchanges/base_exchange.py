"""
Абстрактный базовый класс для всех криптобирж
Определяет единый интерфейс для работы с любыми биржами
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import asyncio


class OrderType(Enum):
    """Типы ордеров"""
    MARKET = "Market"
    LIMIT = "Limit"
    STOP_MARKET = "StopMarket"
    STOP_LIMIT = "StopLimit"


class OrderSide(Enum):
    """Стороны ордеров"""
    BUY = "Buy"
    SELL = "Sell"


class OrderStatus(Enum):
    """Статусы ордеров"""
    NEW = "New"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"


class MarginMode(Enum):
    """Режимы маржи"""
    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


@dataclass
class Position:
    """Унифицированная модель позиции"""
    symbol: str
    side: str  # "Buy" или "Sell"
    size: float
    entry_price: float
    mark_price: float
    pnl: float
    pnl_percentage: float
    leverage: int
    margin_mode: str
    liquidation_price: float
    unrealized_pnl: float
    created_time: int
    updated_time: int


@dataclass
class Balance:
    """Унифицированная модель баланса"""
    coin: str
    wallet_balance: float
    available_balance: float
    used_balance: float
    unrealized_pnl: float


@dataclass
class OrderInfo:
    """Унифицированная модель ордера"""
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float
    status: str
    filled_quantity: float
    remaining_quantity: float
    created_time: int
    updated_time: int


@dataclass
class Ticker:
    """Унифицированная модель тикера"""
    symbol: str
    last_price: float
    bid_price: float
    ask_price: float
    volume_24h: float
    turnover_24h: float
    price_change_24h: float
    price_change_24h_percent: float
    high_24h: float
    low_24h: float


@dataclass
class Kline:
    """Унифицированная модель свечи"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


@dataclass
class InstrumentInfo:
    """Информация о торговом инструменте"""
    symbol: str
    base_coin: str
    quote_coin: str
    status: str
    min_order_qty: float
    max_order_qty: float
    qty_step: float
    price_precision: int
    qty_precision: int
    min_price: float
    max_price: float
    price_step: float
    leverage_filter: Dict[str, Any]
    lot_size_filter: Dict[str, Any]


class BaseExchange(ABC):
    """
    Абстрактный базовый класс для всех криптобирж
    Определяет единый интерфейс для работы с любыми биржами
    """
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.exchange_name = self.__class__.__name__.replace('Adapter', '').lower()
        self._session = None
        self._ws_connection = None
        
    @property
    @abstractmethod
    def base_url(self) -> str:
        """Базовый URL REST API"""
        pass
    
    @property
    @abstractmethod
    def ws_url(self) -> str:
        """URL WebSocket API"""
        pass
    
    # =====================================
    # МЕТОДЫ АУТЕНТИФИКАЦИИ
    # =====================================
    
    @abstractmethod
    def _generate_signature(self, params: str, timestamp: str) -> str:
        """Генерация подписи для аутентификации"""
        pass
    
    @abstractmethod
    def _get_headers(self, method: str, endpoint: str, params: dict = None) -> Dict[str, str]:
        """Получение заголовков для HTTP запроса"""
        pass
    
    # =====================================
    # МЕТОДЫ РАБОТЫ С БАЛАНСОМ
    # =====================================
    
    @abstractmethod
    async def get_balance(self, coin: str = "USDT") -> Optional[Balance]:
        """
        Получить баланс аккаунта
        
        Args:
            coin: Монета для получения баланса (по умолчанию USDT)
            
        Returns:
            Balance объект или None при ошибке
        """
        pass
    
    @abstractmethod
    async def get_wallet_balance(self) -> List[Balance]:
        """
        Получить все балансы кошелька
        
        Returns:
            Список всех балансов
        """
        pass
    
    # =====================================
    # МЕТОДЫ РАБОТЫ С ОРДЕРАМИ
    # =====================================
    
    @abstractmethod
    async def place_order(self, 
                         symbol: str,
                         side: OrderSide,
                         order_type: OrderType,
                         quantity: float,
                         price: Optional[float] = None,
                         stop_price: Optional[float] = None,
                         time_in_force: str = "GTC",
                         reduce_only: bool = False) -> Optional[OrderInfo]:
        """
        Размещение ордера
        
        Args:
            symbol: Торговая пара
            side: Сторона ордера (Buy/Sell)
            order_type: Тип ордера
            quantity: Количество
            price: Цена (для лимитных ордеров)
            stop_price: Стоп цена (для стоп ордеров)
            time_in_force: Время действия ордера
            reduce_only: Только для закрытия позиции
            
        Returns:
            OrderInfo объект или None при ошибке
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        Отмена ордера
        
        Args:
            symbol: Торговая пара
            order_id: ID ордера
            
        Returns:
            True если успешно отменен
        """
        pass
    
    @abstractmethod
    async def get_order_status(self, symbol: str, order_id: str) -> Optional[OrderInfo]:
        """
        Получение статуса ордера
        
        Args:
            symbol: Торговая пара
            order_id: ID ордера
            
        Returns:
            OrderInfo объект или None
        """
        pass
    
    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderInfo]:
        """
        Получение открытых ордеров
        
        Args:
            symbol: Торговая пара (опционально)
            
        Returns:
            Список открытых ордеров
        """
        pass
    
    # =====================================
    # МЕТОДЫ РАБОТЫ С ПОЗИЦИЯМИ
    # =====================================
    
    @abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Получение позиций
        
        Args:
            symbol: Торговая пара (опционально)
            
        Returns:
            Список позиций
        """
        pass
    
    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Установка кредитного плеча
        
        Args:
            symbol: Торговая пара
            leverage: Размер плеча (1-100)
            
        Returns:
            True если успешно установлено
        """
        pass
    
    @abstractmethod
    async def set_margin_mode(self, symbol: str, margin_mode: MarginMode) -> bool:
        """
        Установка режима маржи
        
        Args:
            symbol: Торговая пара
            margin_mode: Режим маржи (ISOLATED/CROSS)
            
        Returns:
            True если успешно установлен
        """
        pass
    
    @abstractmethod
    async def set_position_stop_loss_take_profit(self,
                                                symbol: str,
                                                stop_loss: Optional[float] = None,
                                                take_profit: Optional[float] = None) -> bool:
        """
        Установка Stop Loss и Take Profit для позиции
        
        Args:
            symbol: Торговая пара
            stop_loss: Цена стоп лосса
            take_profit: Цена тейк профита
            
        Returns:
            True если успешно установлены
        """
        pass
    
    # =====================================
    # МЕТОДЫ ПОЛУЧЕНИЯ РЫНОЧНЫХ ДАННЫХ
    # =====================================
    
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Получение тикера
        
        Args:
            symbol: Торговая пара
            
        Returns:
            Ticker объект или None
        """
        pass
    
    @abstractmethod
    async def get_all_tickers(self) -> List[Ticker]:
        """
        Получение всех тикеров
        
        Returns:
            Список всех тикеров
        """
        pass
    
    @abstractmethod
    async def get_klines(self,
                        symbol: str,
                        interval: str,
                        limit: int = 200,
                        start_time: Optional[int] = None,
                        end_time: Optional[int] = None) -> List[Kline]:
        """
        Получение исторических данных свечей
        
        Args:
            symbol: Торговая пара
            interval: Интервал (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Количество свечей
            start_time: Время начала (timestamp)
            end_time: Время окончания (timestamp)
            
        Returns:
            Список свечей
        """
        pass
    
    @abstractmethod
    async def get_instrument_info(self, symbol: str) -> Optional[InstrumentInfo]:
        """
        Получение информации о торговом инструменте
        
        Args:
            symbol: Торговая пара
            
        Returns:
            InstrumentInfo объект или None
        """
        pass
    
    @abstractmethod
    async def get_all_instruments(self) -> List[InstrumentInfo]:
        """
        Получение информации о всех торговых инструментах
        
        Returns:
            Список всех инструментов
        """
        pass
    
    # =====================================
    # УТИЛИТАРНЫЕ МЕТОДЫ
    # =====================================
    
    @abstractmethod
    async def get_server_time(self) -> int:
        """
        Получение времени сервера
        
        Returns:
            Timestamp сервера
        """
        pass
    
    @abstractmethod
    def round_quantity(self, symbol: str, quantity: float) -> str:
        """
        Округление количества согласно правилам биржи
        
        Args:
            symbol: Торговая пара
            quantity: Количество для округления
            
        Returns:
            Округленное количество как строка
        """
        pass
    
    @abstractmethod
    def round_price(self, symbol: str, price: float) -> str:
        """
        Округление цены согласно правилам биржи
        
        Args:
            symbol: Торговая пара
            price: Цена для округления
            
        Returns:
            Округленная цена как строка
        """
        pass
    
    # =====================================
    # WEBSOCKET МЕТОДЫ
    # =====================================
    
    @abstractmethod
    async def subscribe_to_trades(self, symbol: str, callback) -> bool:
        """
        Подписка на поток сделок
        
        Args:
            symbol: Торговая пара
            callback: Функция обратного вызова
            
        Returns:
            True если подписка успешна
        """
        pass
    
    @abstractmethod
    async def subscribe_to_klines(self, symbol: str, interval: str, callback) -> bool:
        """
        Подписка на поток свечей
        
        Args:
            symbol: Торговая пара
            interval: Интервал
            callback: Функция обратного вызова
            
        Returns:
            True если подписка успешна
        """
        pass
    
    @abstractmethod
    async def subscribe_to_position_updates(self, callback) -> bool:
        """
        Подписка на обновления позиций
        
        Args:
            callback: Функция обратного вызова
            
        Returns:
            True если подписка успешна
        """
        pass
    
    # =====================================
    # СПЕЦИФИЧНЫЕ ДЛЯ ФЬЮЧЕРСОВ МЕТОДЫ
    # =====================================
    
    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """
        Получение текущей ставки финансирования
        
        Args:
            symbol: Торговая пара
            
        Returns:
            Ставка финансирования или None
        """
        pass
    
    @abstractmethod
    async def get_liquidation_price(self, symbol: str) -> Optional[float]:
        """
        Получение цены ликвидации
        
        Args:
            symbol: Торговая пара
            
        Returns:
            Цена ликвидации или None
        """
        pass
    
    # =====================================
    # МЕТОДЫ ЖИЗНЕННОГО ЦИКЛА
    # =====================================
    
    async def initialize(self) -> bool:
        """
        Инициализация соединения с биржей
        
        Returns:
            True если успешно инициализирована
        """
        try:
            # Тестируем подключение
            server_time = await self.get_server_time()
            if server_time is None:
                return False
                
            # Получаем информацию об аккаунте
            balance = await self.get_balance()
            if balance is None:
                return False
                
            return True
        except Exception as e:
            print(f"Ошибка инициализации {self.exchange_name}: {e}")
            return False
    
    async def close(self):
        """Закрытие соединений"""
        if self._ws_connection:
            await self._ws_connection.close()
        if self._session:
            await self._session.close()
    
    def __str__(self) -> str:
        return f"{self.exchange_name}({'testnet' if self.testnet else 'mainnet'})"
    
    def __repr__(self) -> str:
        return self.__str__()