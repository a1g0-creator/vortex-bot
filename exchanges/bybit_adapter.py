"""
Bybit API v5 Adapter - Production Ready with Demo/Mainnet Support
Full implementation for Bybit Derivatives (Linear Perpetual)
API Documentation: https://bybit-exchange.github.io/docs/v5/intro
"""

import asyncio
import time
import json
import hmac
import hashlib
import aiohttp
import websockets
from typing import Dict, List, Optional, Any, Tuple, Callable
from urllib.parse import urlencode
import logging
from decimal import Decimal, ROUND_DOWN
from datetime import datetime
import uuid

from .base_exchange import (
    BaseExchange, Position, Balance, OrderInfo, Ticker, Kline, InstrumentInfo,
    OrderType, OrderSide, OrderStatus, MarginMode
)


class BybitAdapter(BaseExchange):
    """
    Bybit v5 API Adapter - Unified Account
    Supports USDT Perpetual futures trading with demo/mainnet environments
    """
    
    # Rate Limits per category - https://bybit-exchange.github.io/docs/v5/rate-limit
    RATE_LIMITS = {
        "order": 10,      # 10 req/sec
        "position": 10,   # 10 req/sec
        "market": 20,     # 20 req/sec
        "account": 10     # 10 req/sec
    }
    
    # внутри class BybitAdapter(BaseExchange):
    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        *,
        base_url: Optional[str] = None,
        ws_public: Optional[str] = None,
        ws_private: Optional[str] = None,
        testnet: bool = True,
        recv_window: int = 10000,
        # НОВОЕ: среда окружения от фабрики ('demo' / 'mainnet' | алиасы 'testnet'/'prod'/'live')
        environment: Optional[str] = None,
        # альтернативный путь инициализации через dict
        config: Optional[Dict[str, Any]] = None,
        # запасной шлюз — если придут лишние ключи, не падаем
        **_ignore: Any,
    ):
        """
        Initialize Bybit v5 adapter.
        Поддерживает два режима:
        1) явные именованные параметры;
        2) config-dict через параметр `config`.
        """

        def _norm_env(env_str: Optional[str], is_testnet: bool) -> str:
            if env_str is None:
                return "demo" if is_testnet else "mainnet"
            s = env_str.strip().lower()
            if s in ("demo", "testnet"):
                return "demo"
            if s in ("prod", "production", "main", "mainnet", "live"):
                return "mainnet"
            # на неизвестные значения ориентируемся по флагу testnet
            return "demo" if is_testnet else "mainnet"

        if config is not None:
            # --- Инициализация через dict ---
            api_key = config.get("api_key", "")
            api_secret = config.get("api_secret", "")
            env_from_cfg = config.get("environment")
            # demo/testnet => True
            testnet = (str(env_from_cfg).lower() in ("demo", "testnet")) or bool(config.get("testnet", False))
            super().__init__(api_key, api_secret, testnet)

            self.environment = _norm_env(env_from_cfg, testnet)
            self._base_url = config.get("base_url")
            self._ws_public_url = config.get("ws_public")
            self._ws_private_url = config.get("ws_private")
            self.recv_window = int(config.get("recv_window", recv_window or 10000))

            # Для конфиг-инициализации URL должны быть заданы в yaml/.env
            if not all([self._base_url, self._ws_public_url, self._ws_private_url]):
                raise ValueError("Missing required URL configuration for BybitAdapter")
        else:
            # --- Инициализация именованными аргументами ---
            if not api_key or not api_secret:
                raise ValueError("api_key and api_secret are required for BybitAdapter")

            # Если явно передали environment — приоритезируем его над testnet
            if environment is not None:
                env_norm = environment.strip().lower()
                testnet = env_norm in ("demo", "testnet")

            super().__init__(api_key, api_secret, testnet)

            # Дефолтные эндпоинты сохраняем, но фабрика обычно подставляет свои
            self._base_url = base_url or ("https://api-demo.bybit.com" if testnet else "https://api.bybit.com")
            self._ws_public_url = ws_public or (
                "wss://stream-demo.bybit.com/v5/public/linear" if testnet else "wss://stream.bybit.com/v5/public/linear"
            )
            self._ws_private_url = ws_private or (
                "wss://stream-demo.bybit.com/v5/private" if testnet else "wss://stream.bybit.com/v5/private"
            )
            self.recv_window = int(recv_window or 10000)
            self.environment = _norm_env(environment, testnet)

        if not all([self.api_key, self.api_secret]):
            raise ValueError("Missing API credentials")

        # ===== Session and WebSocket management =====
        self.session: Optional[aiohttp.ClientSession] = None
        self._ws_public: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_private: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_public_task = None
        self._ws_private_task = None
        self._ws_stop = asyncio.Event()
        self._ws_callbacks = {}
        self._ws_subscriptions = set()
        self._ws_auth = False
        self._ws_running = False

        # ===== Rate limiting =====
        self.last_request_times = {k: 0 for k in self.RATE_LIMITS}
        self.request_counts = {k: 0 for k in self.RATE_LIMITS}

        # ===== Caches =====
        self._instruments_cache = {}
        self._cache_timestamp = 0
        self._cache_ttl = 300  # 5 minutes

        # ===== Position mode cache =====
        self._position_mode = None
        self._position_mode_timestamp = 0

        # ===== Retry policy =====
        self.max_retries = 3
        self.retry_delay = 1.0

        # ===== Logging =====
        self.logger = logging.getLogger(f"BybitAdapter-{self.environment}")
        self.logger.info("BybitAdapter initialized:")
        self.logger.info(f"  Environment: {self.environment}")
        self.logger.info(f"  Base URL: {self.base_url}")
        self.logger.info(f"  WS Public: {self.ws_public_url}")
        self.logger.info(f"  WS Private: {self.ws_private_url}")
        self.logger.info(f"  API Key: {self.api_key[:10]}...")

    
    @classmethod
    def from_config(cls, config: Dict[str, Any]):
        """Alternative constructor from config dict"""
        return cls(config=config)

    # ===== Properties =====
    
    @property
    def base_url(self) -> str:
        """Get base URL for REST API"""
        return self._base_url
    
    @property
    def ws_url(self) -> str:
        """Get public WebSocket URL (for compatibility)"""
        return self._ws_public_url
    
    @property
    def ws_public_url(self) -> str:
        """Get public WebSocket URL"""
        return self._ws_public_url
    
    @property
    def ws_private_url(self) -> str:
        """Get private WebSocket URL"""
        return self._ws_private_url
    
    # ===== Core Methods =====
    
    async def initialize(self) -> bool:
        """Initialize the adapter"""
        try:
            if not self.session:
                timeout = aiohttp.ClientTimeout(total=30)
                self.session = aiohttp.ClientSession(timeout=timeout)
            
            # Test connection
            server_time = await self.get_server_time()
            if not server_time:
                self.logger.error("Failed to get server time")
                return False
            
            self.logger.info(f"Server time: {server_time}")
            
            # Get account info to verify API keys
            balance = await self.get_balance()
            if balance:
                self.logger.info(f"✅ Bybit {self.environment} connection established")
                self.logger.info(f"    Balance: {balance.wallet_balance:.2f} USDT")
            else:
                self.logger.warning("Could not fetch balance - check API permissions")
            
            # Load instruments
            await self._load_instruments_cache()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            return False
    
    async def close(self):
        """Close all connections gracefully"""
        try:
            self.logger.info("Closing BybitAdapter...")
            
            # Stop WebSocket
            await self.stop_websocket()
            
            # Close HTTP session
            if self.session:
                await self.session.close()
                self.session = None
            
            self.logger.info("BybitAdapter closed successfully")
            
        except Exception as e:
            self.logger.error(f"Error closing adapter: {e}")
    
    def __del__(self):
        """Destructor to ensure cleanup"""
        try:
            if self.session and not self.session.closed:
                # Try to close session without hard actions
                import asyncio
                loop = None
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                
                if loop and not loop.is_closed():
                    asyncio.create_task(self.session.close())
        except Exception:
            # Do not log errors in destructor
            pass
    
    # ===== Abstract Methods Implementation =====
    
    def _generate_signature(self, params_str: str, timestamp: str = None) -> str:
        """
        Generate HMAC-SHA256 signature for v5 API
        https://bybit-exchange.github.io/docs/v5/guide#how-to-sign
        """
        return hmac.new(
            self.api_secret.encode('utf-8'),
            params_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def _get_headers(self, method: str, endpoint: str, params: dict = None) -> Dict[str, str]:
        """Get headers for HTTP request"""
        return {
            "Content-Type": "application/json"
        }
    
    async def _check_rate_limit(self, endpoint: str):
        """Check and enforce rate limits"""
        # Simplified rate limiting - could be enhanced
        current_time = time.time()
        
        # Determine category based on endpoint
        category = "market"
        if "order" in endpoint:
            category = "order"
        elif "position" in endpoint:
            category = "position"
        elif "account" in endpoint:
            category = "account"
        
        # Simple rate limiting
        last_request = self.last_request_times.get(category, 0)
        time_since_last = current_time - last_request
        min_interval = 1.0 / self.RATE_LIMITS[category]
        
        if time_since_last < min_interval:
            await asyncio.sleep(min_interval - time_since_last)
        
        self.last_request_times[category] = time.time()
    
    async def _make_request(self, method: str, endpoint: str, params: Dict = None, 
                          signed: bool = True, retry_count: int = 0) -> Optional[Dict]:
        """
        Make HTTP request to Bybit API v5
        https://bybit-exchange.github.io/docs/v5/intro#authentication
        """
        try:
            # Rate limiting
            await self._check_rate_limit(endpoint)
            
            url = f"{self.base_url}/{endpoint}"
            
            headers = {
                "Content-Type": "application/json"
            }
            
            # Prepare parameters
            if params is None:
                params = {}
            
            timestamp = str(int(time.time() * 1000))
            
            if signed:
                # Add required headers for signed requests
                headers["X-BAPI-API-KEY"] = self.api_key
                headers["X-BAPI-TIMESTAMP"] = timestamp
                headers["X-BAPI-RECV-WINDOW"] = str(self.recv_window)
                headers["X-BAPI-SIGN-TYPE"] = "2"  # HMAC SHA256
                
                # Generate signature based on method
                if method == "GET":
                    # Sort params for GET
                    query_string = urlencode(sorted(params.items())) if params else ""
                    sign_str = f"{timestamp}{self.api_key}{self.recv_window}{query_string}"
                else:  # POST
                    body_str = json.dumps(params) if params else ""
                    sign_str = f"{timestamp}{self.api_key}{self.recv_window}{body_str}"
                
                headers["X-BAPI-SIGN"] = self._generate_signature(sign_str)
            
            # Log request details for debugging
            self.logger.debug(f"Request: {method} {url}")
            if params and method == "GET":
                self.logger.debug(f"Params: {params}")
            
            # Make request
            if not self.session:
                timeout = aiohttp.ClientTimeout(total=30)
                self.session = aiohttp.ClientSession(timeout=timeout)
            
            async with self.session.request(
                method=method,
                url=url,
                headers=headers,
                params=params if method == "GET" else None,
                json=params if method == "POST" else None
            ) as response:
                data = await response.json()
                
                # Check for success
                if data.get("retCode") == 0:
                    return data.get("result", {})
                else:
                    error_msg = f"API error: {data.get('retMsg')} (code: {data.get('retCode')})"
                    self.logger.error(error_msg)
                    
                    # Handle rate limit errors with retry
                    if data.get("retCode") in [10006, 429] and retry_count < self.max_retries:
                        await asyncio.sleep(self.retry_delay * (2 ** retry_count))
                        return await self._make_request(method, endpoint, params, signed, retry_count + 1)
                    
                    return None
                    
        except aiohttp.ClientError as e:
            # Network errors - retry with exponential backoff
            if retry_count < self.max_retries:
                self.logger.warning(f"Network error (retry {retry_count + 1}): {e}")
                await asyncio.sleep(self.retry_delay * (2 ** retry_count))
                return await self._make_request(method, endpoint, params, signed, retry_count + 1)
            else:
                self.logger.error(f"Max retries exceeded for {endpoint}: {e}")
                return None
        except Exception as e:
            self.logger.error(f"Request error for {endpoint}: {e}")
            return None
    
    # ===== Market Data Methods =====
    
    async def get_server_time(self) -> Optional[int]:
        """Get server time"""
        try:
            result = await self._make_request(
                "GET",
                "v5/market/time",
                signed=False
            )
            
            if result and "timeSecond" in result:
                return int(result["timeSecond"]) * 1000
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting server time: {e}")
            return None
    
    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get ticker information with bid/ask aliases for compatibility
        """
        try:
            result = await self._make_request(
                "GET",
                "v5/market/tickers",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol)
                },
                signed=False
            )
            
            if not result or "list" not in result or not result["list"]:
                return None
            
            ticker_data = result["list"][0]
            
            # Create Ticker according to base model fields
            ticker = Ticker(
                symbol=ticker_data["symbol"],
                last_price=float(ticker_data.get("lastPrice", 0)),
                bid_price=float(ticker_data.get("bid1Price", 0)),
                ask_price=float(ticker_data.get("ask1Price", 0)),
                volume_24h=float(ticker_data.get("volume24h", 0)),
                turnover_24h=float(ticker_data.get("turnover24h", 0)),
                price_change_24h=float(ticker_data.get("price24hPcnt", 0)),
                price_change_24h_percent=float(ticker_data.get("price24hPcnt", 0)),
                high_24h=float(ticker_data.get("highPrice24h", 0)),
                low_24h=float(ticker_data.get("lowPrice24h", 0))
            )
            
            # Aliases for compatibility with tester
            setattr(ticker, "bid", ticker.bid_price)
            setattr(ticker, "ask", ticker.ask_price)
            setattr(ticker, "last", ticker.last_price)
            
            return ticker
            
        except Exception as e:
            self.logger.error(f"Error getting ticker: {e}")
            return None
    
    async def get_all_tickers(self) -> List[Ticker]:
        """Get all tickers with bid/ask aliases"""
        try:
            result = await self._make_request(
                "GET",
                "v5/market/tickers",
                {"category": "linear"},
                signed=False
            )
            
            if not result or "list" not in result:
                return []
            
            tickers = []
            for ticker_data in result["list"]:
                ticker = Ticker(
                    symbol=ticker_data["symbol"],
                    last_price=float(ticker_data.get("lastPrice", 0)),
                    bid_price=float(ticker_data.get("bid1Price", 0)),
                    ask_price=float(ticker_data.get("ask1Price", 0)),
                    volume_24h=float(ticker_data.get("volume24h", 0)),
                    turnover_24h=float(ticker_data.get("turnover24h", 0)),
                    price_change_24h=float(ticker_data.get("price24hPcnt", 0)),
                    price_change_24h_percent=float(ticker_data.get("price24hPcnt", 0)),
                    high_24h=float(ticker_data.get("highPrice24h", 0)),
                    low_24h=float(ticker_data.get("lowPrice24h", 0))
                )
                
                # Aliases for compatibility
                setattr(ticker, "bid", ticker.bid_price)
                setattr(ticker, "ask", ticker.ask_price)
                setattr(ticker, "last", ticker.last_price)
                
                tickers.append(ticker)
            
            return tickers
            
        except Exception as e:
            self.logger.error(f"Error getting all tickers: {e}")
            return []
    
    async def get_balance(self, coin: str = "USDT") -> Optional[Balance]:
        """Get account balance"""
        try:
            result = await self._make_request(
                "GET",
                "v5/account/wallet-balance",
                {"accountType": "UNIFIED"}
            )
            
            if not result or "list" not in result or not result["list"]:
                return None
            
            account_data = result["list"][0]
            target_coin = None
            
            for coin_data in account_data.get("coin", []):
                if coin_data["coin"] == coin:
                    target_coin = coin_data
                    break
            
            if not target_coin:
                return None
            
            # Base model Balance fields
            wallet_balance = target_coin.get("walletBalance", "0")
            available_balance = target_coin.get("availableToWithdraw", "0")
            used_balance = target_coin.get("totalOrderIM", "0")
            unrealized_pnl = target_coin.get("unrealisedPnl", "0")
            
            # Safe float conversion
            def safe_float(value):
                try:
                    if value == "" or value is None:
                        return 0.0
                    return float(value)
                except (ValueError, TypeError):
                    return 0.0
            
            return Balance(
                coin=coin,
                wallet_balance=safe_float(wallet_balance),
                available_balance=safe_float(available_balance),
                used_balance=safe_float(used_balance),
                unrealized_pnl=safe_float(unrealized_pnl)
            )
            
        except Exception as e:
            self.logger.error(f"Error getting balance: {e}")
            return None
    
    async def get_wallet_balance(self, coin: str = "USDT") -> List[Balance]:
        """
        Get wallet balances - с опциональным фильтром по монете
    
        Args:
            coin: Опциональный фильтр по конкретной монете
        
        Returns:
            Список балансов (если указан coin - только для этой монеты)
        """
        try:
            result = await self._make_request(
                "GET",
                "v5/account/wallet-balance",
                {"accountType": "UNIFIED"}
            )
        
            if not result or "list" not in result or not result["list"]:
                return []
        
            account_data = result["list"][0]
            balances = []
        
            def safe_float(value):
                try:
                    if value == "" or value is None:
                        return 0.0
                    return float(value)
                except (ValueError, TypeError):
                    return 0.0
        
            for coin_data in account_data.get("coin", []):
                # Если указан фильтр по монете, пропускаем остальные
                if coin and coin_data["coin"] != coin:
                    continue
                
                balance = Balance(
                    coin=coin_data["coin"],
                    wallet_balance=safe_float(coin_data.get("walletBalance", "0")),
                    available_balance=safe_float(coin_data.get("availableToWithdraw", "0")),
                    used_balance=safe_float(coin_data.get("totalOrderIM", "0")),
                    unrealized_pnl=safe_float(coin_data.get("unrealisedPnl", "0"))
                )
                balances.append(balance)
        
            return balances
        
        except Exception as e:
            self.logger.error(f"Error getting wallet balances: {e}")
            return []
    
    async def get_positions(self, symbol: str = None) -> List[Position]:
        """Get current positions"""
        try:
            result = await self._make_request(
                "GET",
                "v5/position/list",
                {"category": "linear", **({"symbol": self._normalize_symbol(symbol)} if symbol else {"settleCoin": "USDT"})}
            )
            
            if not result or "list" not in result:
                return []
            
            positions = []
            for pos_data in result["list"]:
                if float(pos_data.get("size", 0)) == 0:
                    continue  # Skip empty positions
                
                position = Position(
                    symbol=pos_data["symbol"],
                    side=pos_data["side"],
                    size=float(pos_data["size"]),
                    entry_price=float(pos_data.get("avgPrice", 0)),
                    mark_price=float(pos_data.get("markPrice", 0)),
                    pnl=float(pos_data.get("unrealisedPnl", 0)),
                    pnl_percentage=float(pos_data.get("unrealisedPnl", 0)) / float(pos_data.get("positionValue", 1)) * 100,
                    leverage=int(float(pos_data.get("leverage", 1))),
                    margin_mode=pos_data.get("tradeMode", "CROSS"),
                    liquidation_price=float(pos_data.get("liqPrice", 0)),
                    unrealized_pnl=float(pos_data.get("unrealisedPnl", 0)),
                    created_time=int(pos_data.get("createdTime", 0)),
                    updated_time=int(pos_data.get("updatedTime", 0))
                )
                positions.append(position)
            
            return positions
            
        except Exception as e:
            self.logger.error(f"Error getting positions: {e}")
            return []
    
    async def get_open_orders(self, symbol: str = None) -> List[OrderInfo]:
        """Get open orders"""
        try:
            params = {"category": "linear"}
            if symbol:
                params["symbol"] = self._normalize_symbol(symbol)
            
            result = await self._make_request(
                "GET",
                "v5/order/realtime",
                params
            )
            
            if not result or "list" not in result:
                return []
            
            orders = []
            for order_data in result["list"]:
                order = OrderInfo(
                    order_id=order_data["orderId"],
                    symbol=order_data["symbol"],
                    side=order_data["side"],
                    order_type=order_data["orderType"],
                    quantity=float(order_data["qty"]),
                    price=float(order_data.get("price", 0)),
                    status=order_data["orderStatus"],
                    filled_quantity=float(order_data.get("cumExecQty", 0)),
                    remaining_quantity=float(order_data["qty"]) - float(order_data.get("cumExecQty", 0)),
                    created_time=int(order_data.get("createdTime", 0)),
                    updated_time=int(order_data.get("updatedTime", 0))
                )
                orders.append(order)
            
            return orders
            
        except Exception as e:
            self.logger.error(f"Error getting open orders: {e}")
            return []
    
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
        **kwargs
    ) -> Optional[OrderInfo]:
        """Place an order"""
        try:
            params = {
                "category": "linear",
                "symbol": self._normalize_symbol(symbol),
                "side": side.value,
                "orderType": order_type.value,
                "qty": str(quantity),
                "timeInForce": "GTC"
            }
            
            if price is not None:
                params["price"] = str(price)
            
            if reduce_only:
                params["reduceOnly"] = True
            
            result = await self._make_request(
                "POST",
                "v5/order/create",
                params
            )
            
            if result and "orderId" in result:
                return OrderInfo(
                    order_id=result["orderId"],
                    symbol=symbol,
                    side=side.value,
                    order_type=order_type.value,
                    quantity=quantity,
                    price=price or 0.0,
                    status="New",
                    filled_quantity=0.0,
                    remaining_quantity=quantity,
                    created_time=int(time.time() * 1000),
                    updated_time=int(time.time() * 1000)
                )
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error placing order: {e}")
            return None
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order"""
        try:
            result = await self._make_request(
                "POST",
                "v5/order/cancel",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol),
                    "orderId": order_id
                }
            )
            
            return result is not None
            
        except Exception as e:
            self.logger.error(f"Error canceling order: {e}")
            return False
    
    async def get_order_status(self, symbol: str, order_id: str) -> Optional[OrderInfo]:
        """Get order status"""
        try:
            result = await self._make_request(
                "GET",
                "v5/order/realtime",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol),
                    "orderId": order_id
                }
            )
            
            if not result or "list" not in result or not result["list"]:
                return None
            
            order_data = result["list"][0]
            
            return OrderInfo(
                order_id=order_data["orderId"],
                symbol=order_data["symbol"],
                side=order_data["side"],
                order_type=order_data["orderType"],
                quantity=float(order_data["qty"]),
                price=float(order_data.get("price", 0)),
                status=order_data["orderStatus"],
                filled_quantity=float(order_data.get("cumExecQty", 0)),
                remaining_quantity=float(order_data["qty"]) - float(order_data.get("cumExecQty", 0)),
                created_time=int(order_data.get("createdTime", 0)),
                updated_time=int(order_data.get("updatedTime", 0))
            )
            
        except Exception as e:
            self.logger.error(f"Error getting order status: {e}")
            return None
    
    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> List[Kline]:
        """Get kline/candlestick data"""
        try:
            params = {
                "category": "linear",
                "symbol": self._normalize_symbol(symbol),
                "interval": interval,
                "limit": limit
            }
            
            if start_time:
                params["start"] = start_time
            if end_time:
                params["end"] = end_time
            
            result = await self._make_request(
                "GET",
                "v5/market/kline",
                params,
                signed=False
            )
            
            if not result or "list" not in result:
                return []
            
            klines = []
            for kline_data in result["list"]:
                kline = Kline(
                    timestamp=int(kline_data[0]),
                    open=float(kline_data[1]),
                    high=float(kline_data[2]),
                    low=float(kline_data[3]),
                    close=float(kline_data[4]),
                    volume=float(kline_data[5]),
                    turnover=float(kline_data[6]) if len(kline_data) > 6 else 0.0
                )
                klines.append(kline)
            
            return klines
            
        except Exception as e:
            self.logger.error(f"Error getting klines: {e}")
            return []
    
    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate"""
        try:
            result = await self._make_request(
                "GET",
                "v5/market/funding/history",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol),
                    "limit": 1
                },
                signed=False
            )
            
            if result and "list" in result and result["list"]:
                return float(result["list"][0].get("fundingRate", 0))
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting funding rate: {e}")
            return None
    
    async def get_liquidation_price(self, symbol: str) -> Optional[float]:
        """Get liquidation price for a position"""
        try:
            result = await self._make_request(
                "GET",
                "v5/position/list",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol)
                }
            )
            
            if result and "list" in result and result["list"]:
                for pos_data in result["list"]:
                    if float(pos_data.get("size", 0)) > 0:
                        return float(pos_data.get("liqPrice", 0))
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting liquidation price: {e}")
            return None
    
    async def get_instrument_info(self, symbol: str) -> Optional[InstrumentInfo]:
        """Get instrument information for a specific symbol"""
        try:
            result = await self._make_request(
                "GET",
                "v5/market/instruments-info",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol)
                },
                signed=False
            )
            
            if not result or "list" not in result or not result["list"]:
                return None
            
            inst_data = result["list"][0]
            
            # InstrumentInfo fields according to base model
            return InstrumentInfo(
                symbol=inst_data["symbol"],
                base_coin=inst_data["baseCoin"],
                quote_coin=inst_data["quoteCoin"],
                status=inst_data["status"],
                min_order_qty=float(inst_data["lotSizeFilter"]["minOrderQty"]),
                max_order_qty=float(inst_data["lotSizeFilter"]["maxOrderQty"]),
                qty_step=float(inst_data["lotSizeFilter"]["qtyStep"]),
                price_precision=len(str(float(inst_data["priceFilter"]["tickSize"])).split('.')[-1]),
                qty_precision=len(str(float(inst_data["lotSizeFilter"]["qtyStep"])).split('.')[-1]),
                min_price=float(inst_data["priceFilter"]["minPrice"]),
                max_price=float(inst_data["priceFilter"]["maxPrice"]),
                price_step=float(inst_data["priceFilter"]["tickSize"]),
                leverage_filter=inst_data.get("leverageFilter", {}),
                lot_size_filter=inst_data.get("lotSizeFilter", {})
            )
            
        except Exception as e:
            self.logger.error(f"Error getting instrument info: {e}")
            return None
    
    async def get_all_instruments(self) -> List[InstrumentInfo]:
        """Get all available instruments"""
        try:
            # Check cache first
            if self._instruments_cache and (time.time() - self._cache_timestamp) < self._cache_ttl:
                return list(self._instruments_cache.values())
            
            result = await self._make_request(
                "GET",
                "v5/market/instruments-info",
                {"category": "linear"},
                signed=False
            )
            
            if not result or "list" not in result:
                return []
            
            instruments = []
            for inst_data in result["list"]:
                if inst_data["status"] != "Trading":
                    continue
                
                instrument = InstrumentInfo(
                    symbol=inst_data["symbol"],
                    base_coin=inst_data["baseCoin"],
                    quote_coin=inst_data["quoteCoin"],
                    status=inst_data["status"],
                    min_order_qty=float(inst_data["lotSizeFilter"]["minOrderQty"]),
                    max_order_qty=float(inst_data["lotSizeFilter"]["maxOrderQty"]),
                    qty_step=float(inst_data["lotSizeFilter"]["qtyStep"]),
                    price_precision=len(str(float(inst_data["priceFilter"]["tickSize"])).split('.')[-1]),
                    qty_precision=len(str(float(inst_data["lotSizeFilter"]["qtyStep"])).split('.')[-1]),
                    min_price=float(inst_data["priceFilter"]["minPrice"]),
                    max_price=float(inst_data["priceFilter"]["maxPrice"]),
                    price_step=float(inst_data["priceFilter"]["tickSize"]),
                    leverage_filter=inst_data.get("leverageFilter", {}),
                    lot_size_filter=inst_data.get("lotSizeFilter", {})
                )
                
                instruments.append(instrument)
                self._instruments_cache[instrument.symbol] = instrument
            
            self._cache_timestamp = time.time()
            return instruments
            
        except Exception as e:
            self.logger.error(f"Error getting instruments: {e}")
            return []
    
    def round_price(self, symbol: str, price: float) -> str:
        """Round price to valid increment"""
        try:
            instrument = self._instruments_cache.get(self._normalize_symbol(symbol))
            if instrument:
                tick_size = instrument.price_step
                rounded = round(price / tick_size) * tick_size
                # Determine decimal places based on tick size
                if tick_size >= 1:
                    return f"{rounded:.0f}"
                elif tick_size >= 0.1:
                    return f"{rounded:.1f}"
                elif tick_size >= 0.01:
                    return f"{rounded:.2f}"
                elif tick_size >= 0.001:
                    return f"{rounded:.3f}"
                else:
                    return f"{rounded:.4f}"
            return f"{price:.4f}"  # Default precision
        except Exception:
            return f"{price:.4f}"
    
    def round_quantity(self, symbol: str, quantity: float) -> str:
        """Round quantity to valid increment"""
        try:
            instrument = self._instruments_cache.get(self._normalize_symbol(symbol))
            if instrument:
                qty_step = instrument.qty_step
                rounded = round(quantity / qty_step) * qty_step
                # Determine decimal places based on step size
                if qty_step >= 1:
                    return f"{rounded:.0f}"
                elif qty_step >= 0.1:
                    return f"{rounded:.1f}"
                elif qty_step >= 0.01:
                    return f"{rounded:.2f}"
                else:
                    return f"{rounded:.3f}"
            return f"{quantity:.3f}"  # Default precision
        except Exception:
            return f"{quantity:.3f}"
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol"""
        try:
            result = await self._make_request(
                "POST",
                "v5/position/set-leverage",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol),
                    "buyLeverage": str(leverage),
                    "sellLeverage": str(leverage)
                }
            )
            
            return result is not None
            
        except Exception as e:
            self.logger.error(f"Error setting leverage: {e}")
            return False
    
    async def set_margin_mode(self, symbol: str, margin_mode: MarginMode) -> bool:
        """Set margin mode for a symbol"""
        try:
            mode_map = {
                MarginMode.CROSS: "REGULAR_MARGIN",
                MarginMode.ISOLATED: "ISOLATED_MARGIN"
            }
            
            result = await self._make_request(
                "POST",
                "v5/position/switch-isolated",
                {
                    "category": "linear",
                    "symbol": self._normalize_symbol(symbol),
                    "tradeMode": mode_map[margin_mode],
                    "buyLeverage": "10",  # Default leverage
                    "sellLeverage": "10"
                }
            )
            
            return result is not None
            
        except Exception as e:
            self.logger.error(f"Error setting margin mode: {e}")
            return False
    
    async def set_position_stop_loss_take_profit(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> bool:
        """Set stop loss and take profit for a position"""
        try:
            params = {
                "category": "linear",
                "symbol": self._normalize_symbol(symbol)
            }
            
            if stop_loss is not None:
                params["stopLoss"] = str(stop_loss)
            if take_profit is not None:
                params["takeProfit"] = str(take_profit)
            
            result = await self._make_request(
                "POST",
                "v5/position/trading-stop",
                params
            )
            
            return result is not None
            
        except Exception as e:
            self.logger.error(f"Error setting stop loss/take profit: {e}")
            return False
    
    # ===== WebSocket Methods =====
    
    async def start_websocket(
        self,
        public_topics: Optional[List[str]] = None,
        private_topics: Optional[List[str]] = None,
        on_public: Optional[Callable[[dict], Any]] = None,
        on_private: Optional[Callable[[dict], Any]] = None,
    ) -> bool:
        """
        Start WebSocket connections (public & private)
        - Важно: приватный WS стартуем, даже если нет topics/callback,
        чтобы пройти e2e-проверку ws_private_connect/ws_private_auth.
        """
        try:
            self.logger.info("Starting WebSocket connections...")
            self._ws_stop.clear()
            self._ws_running = True
            ok = True

            # Public
            if public_topics or on_public:
                try:
                    self._ws_public_task = asyncio.create_task(
                        self._run_public_ws_loop(public_topics or [], on_public)
                    )
                    await asyncio.sleep(0.5)
                    self.logger.info("✅ Public WebSocket start requested")
                except Exception as e:
                    self.logger.error(f"Failed to start public WebSocket: {e}")
                    ok = False

            # Private — стартуем в любом случае (требование тестера)
            try:
                self._ws_private_task = asyncio.create_task(
                    self._run_private_ws_loop(private_topics or [], on_private)
                )
                await asyncio.sleep(0.5)
                self.logger.info("✅ Private WebSocket start requested (topics=%s, cb=%s)",
                                 bool(private_topics), bool(on_private))
            except Exception as e:
                self.logger.error(f"Failed to start private WebSocket: {e}")
                ok = False

            return ok
        except Exception as e:
            self.logger.error(f"Error starting WebSocket: {e}")
            return False

    async def stop_websocket(self):
        """Stop WebSocket connections"""
        try:
            self.logger.info("Stopping WebSocket connections...")
            
            self._ws_running = False
            self._ws_stop.set()
            
            # Close connections
            if self._ws_public:
                try:
                    await self._ws_public.close()
                except:
                    pass
                self._ws_public = None
            
            if self._ws_private:
                try:
                    await self._ws_private.close()
                except:
                    pass
                self._ws_private = None
            
            # Cancel tasks
            if self._ws_public_task:
                self._ws_public_task.cancel()
                try:
                    await self._ws_public_task
                except asyncio.CancelledError:
                    pass
                self._ws_public_task = None
            
            if self._ws_private_task:
                self._ws_private_task.cancel()
                try:
                    await self._ws_private_task
                except asyncio.CancelledError:
                    pass
                self._ws_private_task = None
            
            self.logger.info("WebSocket connections stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping WebSocket: {e}")
    
    async def subscribe_to_trades(self, symbol: str, callback) -> bool:
        """Subscribe to trade updates"""
        try:
            # This is a stub implementation for the abstract method
            self.logger.warning("Trade subscription not implemented in minimal WebSocket")
            return False
        except Exception as e:
            self.logger.error(f"Error subscribing to trades: {e}")
            return False
    
    async def subscribe_to_klines(self, symbol: str, interval: str, callback) -> bool:
        """Subscribe to kline updates"""
        try:
            # This is a stub implementation for the abstract method
            self.logger.warning("Kline subscription not implemented in minimal WebSocket")
            return False
        except Exception as e:
            self.logger.error(f"Error subscribing to klines: {e}")
            return False
    
    async def subscribe_to_position_updates(self, callback) -> bool:
        """Subscribe to position updates"""
        try:
            # This is a stub implementation for the abstract method
            self.logger.warning("Position subscription not implemented in minimal WebSocket")
            return False
        except Exception as e:
            self.logger.error(f"Error subscribing to position updates: {e}")
            return False
    
    async def subscribe_ticker(self, symbol: str, callback):
        """Subscribe to ticker updates for e2e test compatibility"""
        try:
            # Simple ticker subscription for testing
            self._ws_callbacks[f"tickers.{self._normalize_symbol(symbol)}"] = callback
            self.logger.info(f"Subscribed to ticker: {symbol}")
        except Exception as e:
            self.logger.error(f"Error subscribing to ticker: {e}")
    
    async def _run_public_ws_loop(self, topics: List[str], callback: Callable[[dict], Any]):
        """Run public WebSocket loop"""
        retry_count = 0
        max_retries = 3
        
        while self._ws_running and retry_count < max_retries:
            try:
                self.logger.info(f"Connecting to public WebSocket: {self.ws_public_url}")
                
                async with websockets.connect(self.ws_public_url) as ws:
                    self._ws_public = ws
                    self.logger.info("Public WebSocket connected")
                    
                    # Subscribe to topics
                    if topics:
                        subscribe_msg = {
                            "op": "subscribe",
                            "args": topics
                        }
                        await ws.send(json.dumps(subscribe_msg))
                        self.logger.info(f"Subscribed to public topics: {topics}")
                    
                    # Handle messages
                    async for message in ws:
                        if not self._ws_running:
                            break
                        
                        try:
                            data = json.loads(message)
                            if data.get("op") == "pong":
                                continue
                            
                            # Call callback
                            if callback:
                                await callback(data)
                                
                        except json.JSONDecodeError:
                            self.logger.warning(f"Invalid JSON from public WS: {message}")
                        except Exception as e:
                            self.logger.error(f"Error handling public WS message: {e}")
                    
                    retry_count = 0  # Reset on successful run
                    
            except Exception as e:
                retry_count += 1
                self.logger.error(f"Public WebSocket error (retry {retry_count}): {e}")
                if retry_count < max_retries and self._ws_running:
                    await asyncio.sleep(5)
    
    async def _run_private_ws_loop(self, topics: List[str], callback: Optional[Callable[[dict], Any]]):
        """
        Private WS loop (Bybit v5)
        - Подпись: sign_str = f"{ts}{api_key}{recv_window}"
        - Auth args: [api_key, ts, signature, recv_window]
        """
        max_retries = 5
        retry = 0

        while self._ws_running and retry < max_retries:
            try:
                self.logger.info(f"Connecting to private WebSocket: {self.ws_private_url}")

                async with websockets.connect(
                    self.ws_private_url,
                    ping_interval=None,  # пингуем сами оп-кодом
                    ping_timeout=None,
                    close_timeout=10
                ) as ws:
                    self._ws_private = ws
                    self._ws_auth = False
                    self.logger.info("Private WebSocket connected (socket established)")

                    # === AUTH ===
                    ts = int(time.time() * 1000)
                    recv_window = int(self.recv_window)  # приводим к int на всякий
                    sign_str = f"{ts}{self.api_key}{recv_window}"
                    signature = hmac.new(
                        self.api_secret.encode("utf-8"),
                        sign_str.encode("utf-8"),
                        hashlib.sha256
                    ).hexdigest()

                    auth_msg = {
                        "op": "auth",
                        "args": [self.api_key, ts, signature, recv_window]  # ВАЖНО: 4-й аргумент
                    }
                    await ws.send(json.dumps(auth_msg))

                    # ждём ответ на auth
                    auth_deadline = time.time() + 10
                    while time.time() < auth_deadline and not self._ws_auth:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=2)
                            data = json.loads(raw)
                        except asyncio.TimeoutError:
                            continue

                        if data.get("op") == "auth":
                            if data.get("success") is True or str(data).lower().find("success") != -1:
                                self._ws_auth = True
                                self.logger.info("✅ Private WS authenticated")
                                break
                            else:
                                self.logger.error(f"Private WS auth failed: {data}")
                                raise RuntimeError("WS auth failed")

                    if not self._ws_auth:
                        # сервер не прислал явный ответ, считаем неуспехом
                        self.logger.error("Private WS auth timed out")
                        raise RuntimeError("WS auth timeout")

                    # Подписки после auth
                    if topics:
                        sub = {"op": "subscribe", "args": topics}
                        await ws.send(json.dumps(sub))
                        self.logger.info(f"Subscribed to private topics: {topics}")

                    # Фоновый ping каждые ~20s op=ping
                    ping_task = asyncio.create_task(self._send_ws_ping(ws))
                    try:
                        async for message in ws:
                            try:
                                data = json.loads(message)
                            except json.JSONDecodeError:
                                self.logger.warning(f"Invalid JSON from private WS: {message}")
                                continue

                            if data.get("op") == "pong":
                                continue
                            if "topic" in data and callback:
                                await callback(data)
                            # подтверждения подписок
                            if data.get("success") is True and data.get("op") == "subscribe":
                                self.logger.debug(f"Private WS subscription confirmed: {data}")
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

                    # если цикл чтения вышел — пробуем переподключиться
                    retry = 0
            except Exception as e:
                retry += 1
                self.logger.error(f"Private WebSocket error (attempt {retry}/{max_retries}): {e}")
                # подчистим ссылку, чтобы e2e мог корректно увидеть статус
                self._ws_private = None
                self._ws_auth = False
                if self._ws_running and retry < max_retries:
                    await asyncio.sleep(min(10, 1.5 * retry))
            finally:
                if not self._ws_running:
                    break

        # исчерпали ретраи
        if retry >= max_retries:
            self._ws_private = None
            self._ws_auth = False
            self.logger.error("Max retries reached for private WebSocket")


    
    async def _send_ws_ping(self, ws):
        """Periodic ping for Bybit v5 WS (op=ping)"""
        try:
            while self._ws_running:
                await asyncio.sleep(20)
                await ws.send(json.dumps({"op": "ping"}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Private WS ping error: {e}")

    
    # ===== Helper Methods =====
    
    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to Bybit format (e.g., BTCUSDT)"""
        symbol = symbol.upper().replace("-", "").replace("/", "").replace("_", "")
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        return symbol
    
    async def _load_instruments_cache(self):
        """Load and cache instrument information"""
        try:
            instruments = await self.get_all_instruments()
            self.logger.info(f"Loaded {len(instruments)} instruments into cache")
        except Exception as e:
            self.logger.error(f"Error loading instruments cache: {e}")
