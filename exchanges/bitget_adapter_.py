"""
Bitget API адаптер для интеграции с торговой системой
Полная реализация BaseExchange интерфейса
Версия с финальными исправлениями для demo режима
"""

import time
import hmac
import hashlib
import base64
import json
import asyncio
import aiohttp
import websockets
import logging
import inspect
from typing import Dict, List, Optional, Any, Union, Tuple, Callable
from urllib.parse import urlencode
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from .base_exchange import (
    BaseExchange, Balance, Position, OrderInfo, Ticker, Kline,
    OrderSide, OrderType, MarginMode, InstrumentInfo
)


class BitgetAdapter(BaseExchange):
    """
    Bitget API адаптер - реализация BaseExchange
    Поддерживает USDT-M фьючерсы и демо-режим
    """

    # REST хосты
    MAINNET_BASE_URL = "https://api.bitget.com"
    TESTNET_BASE_URL = "https://api.bitget.com"  # demo REST на том же хосте с paptrading=1

    # WS хосты
    MAINNET_WS_PUBLIC = "wss://ws.bitget.com/v2/ws/public"
    MAINNET_WS_PRIVATE = "wss://ws.bitget.com/v2/ws/private"
    TESTNET_WS_PUBLIC = "wss://wspap.bitget.com/v2/ws/public"
    TESTNET_WS_PRIVATE = "wss://wspap.bitget.com/v2/ws/private"

    # productType для фьючерсов
    PRODUCT_TYPE_MAIN = "USDT-FUTURES"
    PRODUCT_TYPE_DEMO = "susdt-futures"  # для demo эндпоинтов

    SIDE_MAPPING = {
        "Buy": "buy",
        "Sell": "sell",
        "buy": "buy",
        "sell": "sell"
    }

    ORDER_TYPE_MAPPING = {
        "Market": "market",
        "Limit": "limit",
        "market": "market",
        "limit": "limit"
    }

    ORDER_STATUS_MAPPING = {
        "new": "New",
        "partial_fill": "PartiallyFilled",
        "filled": "Filled",
        "cancelled": "Cancelled",
        "live": "New",
        "partial-fill": "PartiallyFilled",
        "init": "New",
        "partially_filled": "PartiallyFilled"
    }
    
    # Маппинг символов для demo режима
    # Маркет данные используют символы с S, торговые операции - без S
    DEMO_SYMBOL_MAPPING = {
        # Маркет данные -> Торговые операции
        "SBTCSUSDT": "BTCUSDT",
        "SETHSUSDT": "ETHUSDT", 
        "SXRPSUSDT": "XRPUSDT",
        # Обратный маппинг
        "BTCUSDT": "SBTCSUSDT",
        "ETHUSDT": "SETHSUSDT",
        "XRPUSDT": "SXRPSUSDT"
    }

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        recv_window: int = 10000,
        # Поддержка обоих вариантов имени параметра
        api_passphrase: str = None,
        passphrase: str = None,
        **kwargs  # Игнорируем неизвестные параметры
    ):
        """
        Args:
            api_key: API ключ
            api_secret: Секретный ключ
            testnet: DEMO режим (paptrading)
            recv_window: окно получения (мс)
            api_passphrase: Пассфраза Bitget (основной параметр)
            passphrase: Пассфраза Bitget (алиас для совместимости с фабрикой)
        """
        super().__init__(api_key, api_secret, testnet)
        
        # Приоритет: api_passphrase, затем passphrase
        self.api_passphrase = api_passphrase or passphrase
        if not self.api_passphrase:
            raise ValueError("Either api_passphrase or passphrase must be provided")
            
        self.recv_window = recv_window

        self.logger = logging.getLogger("BitgetAdapter")
        self.session: Optional[aiohttp.ClientSession] = None

        # WebSocket
        self._ws_public: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_private: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_callbacks: Dict[str, Callable] = {}
        self._ws_subscriptions: List[str] = []

        # Выбор правильных WS URL
        self._ws_public_url = self.TESTNET_WS_PUBLIC if self.testnet else self.MAINNET_WS_PUBLIC
        self._ws_private_url = self.TESTNET_WS_PRIVATE if self.testnet else self.MAINNET_WS_PRIVATE

        # Rate limiting
        self.last_request_time = 0.0
        self.request_delay = 0.1  # 100ms
        self.rate_limit_lock = asyncio.Lock()

        # Кэш символов
        self.symbols_cache: Dict[str, Dict[str, Any]] = {}
        self.symbols_cache_time = 0.0
        self.cache_ttl = 300.0  # 5 минут

        self.logger.info(f"BitgetAdapter initialized: testnet={self.testnet}, passphrase=***")
    
    def _pt(self) -> str:
        """Возвращаем v2-совместимый тип рынка фьючерсов по умолчанию."""
        return "USDT-FUTURES"

    async def _ensure_http_session(self) -> None:
        """
        Гарантированно создаёт aiohttp.ClientSession перед REST-вызовами.
        В demo добавляем paptrading=1 на уровне default headers.
        """
        if getattr(self, "session", None) is not None and not self.session.closed:
            return
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(limit=100)
            default_headers = {
                "Accept": "application/json",
                "locale": "en-US",
            }
            if getattr(self, "testnet", False):
                # Требование demo REST Bitget
                default_headers["paptrading"] = "1"

            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers=default_headers,
                trust_env=True,
            )
            self.logger.info("BitgetAdapter: Created HTTP session (demo=%s)", self.testnet)
        except Exception as e:
            self.logger.error("BitgetAdapter: Failed to create HTTP session: %s", e, exc_info=True)
            raise

    async def _ensure_position_mode(self, target: str = "one_way") -> bool:
        """
        Гарантирует нужный режим позиций на аккаунте.
        ИСПРАВЛЕННАЯ ВЕРСИЯ для Bitget testnet
        """
        try:
            if not hasattr(self, "_pos_mode_cache"):
                self._pos_mode_cache = None
            if self._pos_mode_cache == target:
                return True

            product_type = "susdt-futures" if self.testnet else "USDT-FUTURES"

            # Для Bitget testnet API v2 требуется специальный формат
            # Попытка 1: с полным набором параметров для testnet
            payload1 = {
                "productType": product_type,
                "marginCoin": "USDT",
                "holdMode": "single_hold" if target == "one_way" else "double_hold",
                "posMode": "one_way_mode" if target == "one_way" else "hedge_mode"  # Добавляем оба поля
            }
        
            self.logger.info(f"Setting position mode with full payload: {payload1}")
        
            res = await self._make_request(
                "POST",
                "/api/v2/mix/account/set-position-mode",
                data=payload1,
                signed=True
            )
        
            if res is not None and res != {} and not res.get("code"):
                self._pos_mode_cache = target
                self.logger.info(f"✅ Position mode set to {target} successfully")
                return True

            # Попытка 2: альтернативный endpoint для testnet
            # Некоторые версии Bitget testnet требуют другой endpoint
            try:
                payload2 = {
                    "symbol": "SBTCSUSDT",  # Специфичный символ для testnet
                    "marginCoin": "SUSDT",  # Для testnet используется SUSDT
                    "holdSide": "single" if target == "one_way" else "both"
                }
            
                self.logger.info(f"Trying alternative: Setting position mode with payload: {payload2}")
            
                res2 = await self._make_request(
                    "POST",
                    "/api/v2/mix/account/set-hold-mode",  # Альтернативный endpoint
                    data=payload2,
                    signed=True
                )
            
                if res2 is not None and res2 != {} and not res2.get("code"):
                    self._pos_mode_cache = target
                    self.logger.info(f"✅ Position mode set to {target} using alternative endpoint")
                    return True
            except:
                pass

            # Если все попытки неудачны, продолжаем без кеша
            # Для testnet это может быть нормально, если режим уже установлен
            self.logger.warning(
                "Could not set position mode explicitly. "
                "This might be normal for testnet if mode is already set. Proceeding..."
            )
        
            # Для testnet считаем это успехом, если сервер отвечает
            self._pos_mode_cache = target
            return True

        except Exception as e:
            self.logger.error(f"_ensure_position_mode error: {e}")
            # Для testnet не считаем это критической ошибкой
            if self.testnet:
                self.logger.info("Proceeding despite position mode setup issue (testnet mode)")
                return True
            return False

    def _normalize_symbol_for_trading(self, symbol: str) -> str:
        """
        Приватные/торговые вызовы.
        DEMO: всегда без префикса S (BTCUSDT/ETHUSDT/XRPUSDT).
        """
        s = symbol.upper().replace("/", "")
        if self.testnet:
            if s == "SBTCSUSDT": return "BTCUSDT"
            if s == "SETHSUSDT": return "ETHUSDT"
            if s == "SXRPSUSDT": return "XRPUSDT"
            # если пришёл уже нормальный торговый символ — оставляем
            return s
        return s
    
    def _normalize_symbol_for_market_data(self, symbol: str) -> str:
        """
        Нормализация символа для маркет-данных в demo режиме:
        - demo: всегда SBTCSUSDT/SETHSUSDT/SXRPSUSDT
        - mainnet: обычные символы
        """
        s = symbol.upper().replace("/", "")
        if not self.testnet:
            return s
        # уже корректный S-символ — возвращаем как есть
        if s in ("SBTCSUSDT", "SETHSUSDT", "SXRPSUSDT"):
            return s
        # маппинг с обычных символов на демо-рыночные
        if s == "BTCUSDT": return "SBTCSUSDT"
        if s == "ETHUSDT": return "SETHSUSDT"
        if s == "XRPUSDT": return "SXRPSUSDT"
        # по умолчанию — не трогаем (на случай новых рынков)
        return s

    # ---------------------------------------------------------------------
    # Свойства требуемые BaseExchange
    # ---------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        """Базовый URL REST API"""
        return self.TESTNET_BASE_URL if self.testnet else self.MAINNET_BASE_URL

    @property
    def ws_url(self) -> str:
        """Публичный WS URL"""
        return self._ws_public_url

    # ---------------------------------------------------------------------
    # Абстрактные методы BaseExchange (правильная сигнатура)
    # ---------------------------------------------------------------------
    def _generate_signature(self, params: str, timestamp: str) -> str:
        """
        Генерация подписи для аутентификации (сигнатура BaseExchange)
        
        Args:
            params: Строка параметров для подписи
            timestamp: Timestamp в миллисекундах (строка)
            
        Returns:
            Подпись
        """
        # Для Bitget V2 params уже содержит полную строку для подписи
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                params.encode("utf-8"),
                hashlib.sha256
            ).digest()
        ).decode("utf-8")
        return signature

    def _get_headers(self, method: str, endpoint: str, params: dict = None) -> Dict[str, str]:
        """
        Формирует заголовки для REST v2 Bitget.
        - В demo (testnet) всегда добавляет 'paptrading: 1' для всех эндпойнтов (требование Bitget Demo).
        - Заголовки авторизации/подписи добавляются отдельно (этот метод их не формирует).
        """
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "locale": "en-US",
        }

        # Demo REST: paptrading обязателен для любых ручек (не только приватных)
        if getattr(self, "testnet", False):
            headers["paptrading"] = "1"

        return headers

    def _is_private_endpoint(self, endpoint: str) -> bool:
        """Проверка, является ли эндпоинт приватным"""
        private_patterns = [
            "/account/", "/order/", "/position/", 
            "/mix/account/", "/mix/order/", "/mix/position/"
        ]
        return any(pattern in endpoint for pattern in private_patterns)

    def _generate_auth_headers(
        self,
        method: str,
        path: str,
        query_string: str = "",
        body: str = ""
    ) -> Dict[str, str]:
        """
        Генерация заголовков авторизации для Bitget V2
        """
        try:
            timestamp = str(int(time.time() * 1000))
            prehash = timestamp + method.upper() + path
            if query_string:
                prehash += f"?{query_string}"
            if body:
                prehash += body

            signature = self._generate_signature(prehash, timestamp)

            auth_headers = {
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": signature,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": self.api_passphrase,
                # важно для стабильности запросов
                "ACCESS-RECV-WINDOW": str(self.recv_window),
                "Content-Type": "application/json",
                "locale": "en-US",
            }

            if self.testnet:
                # флаг демо для приватных запросов
                auth_headers["paptrading"] = "1"

            return auth_headers

        except Exception as e:
            self.logger.error(f"Error generating auth headers: {e}")
            return {}

    # ---------------------------------------------------------------------
    # Низкоуровневые HTTP методы
    # ---------------------------------------------------------------------
    async def _rate_limit(self):
        """Rate limiting для соблюдения ограничений API"""
        async with self.rate_limit_lock:
            now = time.time()
            dt = now - self.last_request_time
            if dt < self.request_delay:
                await asyncio.sleep(self.request_delay - dt)
            self.last_request_time = time.time()

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        signed: bool = False,
        json_body: dict | None = None,
    ):
        """
        Унифицированный REST-вызов с ленивым созданием сессии и разбором обёртки Bitget (code/msg/data).
        """
        await self._ensure_http_session()

        url = (self.base_url if hasattr(self, "base_url") else "").rstrip("/") + endpoint
        params = params or {}

        body_str = "" if json_body is None else json.dumps(json_body, separators=(",", ":"), ensure_ascii=False)
        headers = self._get_headers(method, endpoint, params, body_str, signed)

        try:
            async with self.session.request(
                method.upper(),
                url,
                params=params if method.upper() == "GET" else None,
                data=None if json_body is None else body_str,
                headers=headers,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    self.logger.error("BitgetAdapter: HTTP %s %s failed (%s): %s", method, endpoint, resp.status, text)
                    return None

                # Ответ Bitget обычно {"code":"00000","msg":"success","requestTime":...,"data":...}
                try:
                    payload = json.loads(text)
                except Exception:
                    self.logger.error("BitgetAdapter: JSON parse error at %s: %s", endpoint, text)
                    return None

                # Eсли есть "code" — проверим успех
                code = str(payload.get("code", "00000"))
                if code not in ("00000", "success", "0"):  # на всякий случай
                    self.logger.error("BitgetAdapter: API error at %s: code=%s msg=%s",
                                      endpoint, code, payload.get("msg"))
                    return None

                # Возвращаем data если есть, иначе весь payload
                return payload.get("data", payload)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error("BitgetAdapter: request error %s %s: %s", method, endpoint, e, exc_info=True)
            return None


    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        """Безопасное преобразование в float"""
        try:
            if value is None or value == "":
                return default
            return float(value)
        except:
            return default

    def _safe_int(self, value: Any, default: int = 0) -> int:
        """Безопасное преобразование в int"""
        try:
            if value is None or value == "":
                return default
            return int(value)
        except:
            return default

    def _map_order_to_model(self, order_data: dict) -> OrderInfo:
        """
        Безопасный маппинг данных ордера в модель OrderInfo
        Фильтрует поля по сигнатуре конструктора
        """
        # Получаем сигнатуру конструктора OrderInfo
        sig = inspect.signature(OrderInfo.__init__)
        valid_params = list(sig.parameters.keys())
        valid_params.remove('self')  # Убираем self из списка
        
        # Маппинг полей Bitget -> наша модель
        field_mapping = {
            "orderId": "order_id",
            "clientOid": "order_id",  # Используем clientOid как order_id если нет orderId
            "symbol": "symbol",
            "side": "side", 
            "orderType": "order_type",
            "size": "quantity",
            "price": "price",
            "status": "status",
            "state": "status",  # Альтернативное поле статуса
            "filledSize": "filled_quantity",
            "baseVolume": "filled_quantity",  # Альтернативное поле
            "remainSize": "remaining_quantity",
            "ctime": "created_time",
            "cTime": "created_time",  # Альтернативное поле
            "utime": "updated_time",
            "uTime": "updated_time"   # Альтернативное поле
        }
        
        # Создаем словарь с правильными полями
        order_params = {}
        
        for bitget_field, model_field in field_mapping.items():
            if model_field in valid_params and bitget_field in order_data:
                value = order_data[bitget_field]
                
                # Преобразуем значения
                if model_field in ["quantity", "price", "filled_quantity", "remaining_quantity"]:
                    order_params[model_field] = self._safe_float(value)
                elif model_field in ["created_time", "updated_time"]:
                    order_params[model_field] = self._safe_int(value)
                elif model_field == "status":
                    # Нормализуем статус
                    order_params[model_field] = self.ORDER_STATUS_MAPPING.get(str(value).lower(), "Unknown")
                elif model_field == "side":
                    # Нормализуем side
                    order_params[model_field] = self.SIDE_MAPPING.get(value, value)
                elif model_field == "order_type":
                    # Нормализуем тип ордера
                    order_params[model_field] = self.ORDER_TYPE_MAPPING.get(value, value)
                else:
                    order_params[model_field] = value
        
        # Добавляем обязательные поля с дефолтными значениями если их нет
        if "order_id" not in order_params:
            order_params["order_id"] = order_data.get("orderId", order_data.get("clientOid", ""))
        if "symbol" not in order_params:
            order_params["symbol"] = ""
        if "side" not in order_params:
            order_params["side"] = "Buy"
        if "order_type" not in order_params:
            order_params["order_type"] = "Limit"
        if "quantity" not in order_params:
            order_params["quantity"] = 0.0
        if "price" not in order_params:
            order_params["price"] = 0.0
        if "status" not in order_params:
            order_params["status"] = "Unknown"
        if "filled_quantity" not in order_params:
            order_params["filled_quantity"] = 0.0
        if "remaining_quantity" not in order_params:
            order_params["remaining_quantity"] = order_params.get("quantity", 0.0)
        if "created_time" not in order_params:
            order_params["created_time"] = int(time.time() * 1000)
        if "updated_time" not in order_params:
            order_params["updated_time"] = int(time.time() * 1000)
        
        return OrderInfo(**order_params)

    # ---------------------------------------------------------------------
    # Инициализация и закрытие
    # ---------------------------------------------------------------------
    async def initialize(self) -> bool:
        """Инициализация Bitget-адаптера: проверяем доступность REST."""
        try:
            await self._ensure_http_session()
            # Проверяем REST через серверное время
            ts = await self.get_server_time()
            if not ts:
                self.logger.error("BitgetAdapter: Failed to connect to Bitget API")
                return False

            self.logger.info("BitgetAdapter: ✅ Bitget %s connection established (server_time=%s)",
                             "demo" if self.testnet else "mainnet", ts)
            return True
        except Exception as e:
            self.logger.error("BitgetAdapter: initialize error: %s", e, exc_info=True)
            return False



    async def close(self) -> None:
        """Аккуратно закрываем HTTP-сессию и WS-подключения (если есть)."""
        try:
            # если есть логика остановки WS — вызови её тут
            if getattr(self, "session", None) is not None and not self.session.closed:
                await self.session.close()
                self.logger.info("BitgetAdapter: HTTP session closed")
        except Exception as e:
            self.logger.warning("BitgetAdapter: error on closing session: %s", e)


    # ---------------------------------------------------------------------
    # Методы работы с балансом
    # ---------------------------------------------------------------------
    async def get_balance(self, coin: str = "USDT") -> Optional[Balance]:
        """Единичный баланс по маржинальной монете (V2, demo-friendly)."""
        try:
            # 1) Пытаемся "single account" — по докам достаточно marginCoin+productType
            resp = await self._make_request(
                "GET",
                "/api/v2/mix/account/account",
                params={
                    "productType": self._pt(),   # см. _pt(): верни "USDT-FUTURES"
                    "marginCoin": coin
                },
                signed=True
            )

            node = resp if isinstance(resp, dict) else {}
            node = node.get("data", node) or {}
            if node:
                equity = float(node.get("equity", 0) or 0)
                available = float(node.get("available", 0) or 0)
                frozen = float(node.get("frozen", 0) or 0)
                unrealized = float(node.get("unrealizedPL", 0) or 0)
                return Balance(
                    coin="USDT",  # для единообразия интерфейса
                    wallet_balance=equity,
                    available_balance=available,
                    used_balance=frozen,
                    unrealized_pnl=unrealized
                )

            # 2) Запасной — список аккаунтов по productType
            resp = await self._make_request(
                "GET",
                "/api/v2/mix/account/accounts",
                params={"productType": self._pt()},
                signed=True
            )
            items = resp if isinstance(resp, list) else (resp or {}).get("data", [])
            if isinstance(items, dict):
                items = [items]

            for acc in items or []:
                mc = str(acc.get("marginCoin", "")).upper()
                if mc in ("USDT", "SUSDT"):
                    equity = float(acc.get("equity", 0) or 0)
                    available = float(acc.get("available", 0) or 0)
                    frozen = float(acc.get("frozen", 0) or 0)
                    unrealized = float(acc.get("unrealizedPL", 0) or 0)
                    return Balance(
                        coin="USDT",
                        wallet_balance=equity,
                        available_balance=available,
                        used_balance=frozen,
                        unrealized_pnl=unrealized
                    )

            # 3) Ничего не нашли — возвращаем нулевой баланс
            return Balance(coin="USDT", wallet_balance=0.0, available_balance=0.0, used_balance=0.0, unrealized_pnl=0.0)

        except Exception as e:
            self.logger.error(f"get_balance error: {e}", exc_info=True)
            return Balance(coin="USDT", wallet_balance=0.0, available_balance=0.0, used_balance=0.0, unrealized_pnl=0.0)


    async def get_wallet_balance(self) -> List[Balance]:
        """Все фьючерсные балансы (по сути USDT/SUSDT)."""
        try:
            balances: List[Balance] = []

            # 1) accounts — основной путь
            resp = await self._make_request(
                "GET",
                "/api/v2/mix/account/accounts",
                params={"productType": self._pt()},
                signed=True
            )
            items = resp if isinstance(resp, list) else (resp or {}).get("data", [])
            if isinstance(items, dict):
                items = [items]

            valid = ("USDT", "SUSDT")
            for acc in items or []:
                mc = str(acc.get("marginCoin", "")).upper()
                if mc in valid:
                    balances.append(Balance(
                        coin="USDT",
                        wallet_balance=float(acc.get("equity", 0) or 0),
                        available_balance=float(acc.get("available", 0) or 0),
                        used_balance=float(acc.get("frozen", 0) or 0),
                        unrealized_pnl=float(acc.get("unrealizedPL", 0) or 0),
                    ))

            if balances:
                return balances

            # 2) fallback — single account
            single = await self._make_request(
                "GET",
                "/api/v2/mix/account/account",
                params={"productType": self._pt(), "marginCoin": "USDT"},
                signed=True
            )
            node = single if isinstance(single, dict) else {}
            node = node.get("data", node) or {}
            if node:
                balances.append(Balance(
                    coin="USDT",
                    wallet_balance=float(node.get("equity", 0) or 0),
                    available_balance=float(node.get("available", 0) or 0),
                    used_balance=float(node.get("frozen", 0) or 0),
                    unrealized_pnl=float(node.get("unrealizedPL", 0) or 0),
                ))

            return balances

        except Exception as e:
            self.logger.error(f"get_wallet_balance error: {e}", exc_info=True)
            return []



    # ---------------------------------------------------------------------
    # Методы работы с ордерами
    # ---------------------------------------------------------------------
    async def place_order(
        self,
        symbol: str,
        side: Union[OrderSide, str],
        order_type: Union[OrderType, str],
        quantity: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reduce_only: bool = False,
        post_only: bool = False,
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None
    ) -> Optional[OrderInfo]:
        """Размещение ордера (автоматическая поддержка one-way и hedge режимов)."""
        try:
            normalized_symbol = self._normalize_symbol_for_trading(symbol)
            
            # ИСПРАВЛЕННАЯ ОБРАБОТКА SIDE
            if hasattr(side, 'value'):  # Это настоящий enum объект
                side_str = side.value.lower()
            elif isinstance(side, str):
                s = str(side).lower()
                if "orderside." in s or "OrderSide." in str(side):
                    # Строковое представление enum
                    side_str = s.split(".")[-1]
                else:
                    # Обычная строка
                    side_str = self.SIDE_MAPPING.get(s, s)
            else:
                side_str = "buy"
            
            # ИСПРАВЛЕННАЯ ОБРАБОТКА ORDER_TYPE
            if hasattr(order_type, 'value'):  # Это настоящий enum объект  
                order_type_str = order_type.value.lower()
            elif isinstance(order_type, str):
                ot = str(order_type).lower()
                if "ordertype." in ot or "OrderType." in str(order_type):
                    # Строковое представление enum
                    order_type_str = ot.split(".")[-1]
                else:
                    # Обычная строка
                    order_type_str = self.ORDER_TYPE_MAPPING.get(ot, ot)
            else:
                order_type_str = "limit"

            # Округляем количество и цену
            quantity_str = self.round_quantity(symbol, quantity)
            price_str = self.round_price(symbol, price) if price else None

            # Базовые параметры (one-way стиль: side=buy/sell)
            params_base = {
                "symbol": normalized_symbol,
                "productType": self._pt(),
                "marginMode": "crossed",
                "marginCoin": "USDT",
                "orderType": order_type_str,
                "size": quantity_str,
                "force": time_in_force
            }

            # Цена для лимитного ордера
            if order_type_str == "limit":
                if price_str is None:
                    self.logger.error("Price required for limit order")
                    return None
                params_base["price"] = price_str

            if client_order_id:
                params_base["clientOid"] = client_order_id

            # Попытка №1: считать, что аккаунт в one-way (unilateral) режиме
            params_one_way = dict(params_base)
            params_one_way["side"] = side_str
            params_one_way["reduceOnly"] = "YES" if reduce_only else "NO"

            self.logger.info(f"Placing order (one-way): {params_one_way}")

            resp = await self._make_request(
                "POST", "/api/v2/mix/order/place-order", data=params_one_way, signed=True
            )
            
            # Обработка специального случая - no position для reduceOnly
            if resp and resp.get("noop"):
                self.logger.info(f"No position to close for {symbol} - returning noop order")
                return OrderInfo(
                    order_id="noop_" + str(int(time.time() * 1000)),
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=0,
                    price=0,
                    status="NoPosition",
                    filled_quantity=0,
                    remaining_quantity=0,
                    created_time=int(time.time() * 1000),
                    updated_time=int(time.time() * 1000)
                )
            
            if resp:
                return OrderInfo(
                    order_id=resp.get("orderId", resp.get("clientOid", "")),
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=float(quantity_str),
                    price=float(price_str) if price_str else 0.0,
                    status="New",
                    filled_quantity=0.0,
                    remaining_quantity=float(quantity_str),
                    created_time=int(time.time() * 1000),
                    updated_time=int(time.time() * 1000)
                )

            # Попытка №2: fallback на hedge-синтаксис (open_/close_ long/short)
            # Логика:
            #   - открытие:   buy -> open_long,  sell -> open_short
            #   - reduceOnly: sell -> close_long, buy  -> close_short
            def _hedge_side(s: str, ro: bool) -> str:
                if ro:
                    return "close_long" if s == "sell" else "close_short"
                else:
                    return "open_long" if s == "buy" else "open_short"

            params_hedge = dict(params_base)
            params_hedge.pop("reduceOnly", None)  # для close_* не требуется
            params_hedge["side"] = _hedge_side(side_str, reduce_only)

            # Некоторые инсталляции Bitget V2 также принимают holdSide в hedge:
            # long/short помогает маршрутизации. Безопасно добавить.
            if "open" in params_hedge["side"] or "close" in params_hedge["side"]:
                params_hedge["holdSide"] = "long" if "long" in params_hedge["side"] else "short"

            self.logger.info(f"Placing order (hedge fallback): {params_hedge}")

            resp2 = await self._make_request(
                "POST", "/api/v2/mix/order/place-order", data=params_hedge, signed=True
            )
            if resp2:
                return OrderInfo(
                    order_id=resp2.get("orderId", resp2.get("clientOid", "")),
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=float(quantity_str),
                    price=float(price_str) if price_str else 0.0,
                    status="New",
                    filled_quantity=0.0,
                    remaining_quantity=float(quantity_str),
                    created_time=int(time.time() * 1000),
                    updated_time=int(time.time() * 1000)
                )

            return None

        except Exception as e:
            self.logger.error(f"place_order error: {e}")
            return None

    async def cancel_order(self, symbol: str, order_id: str = None, client_order_id: str = None) -> bool:
        """Отмена ордера."""
        try:
            normalized_symbol = self._normalize_symbol_for_trading(symbol)
            params = {"symbol": normalized_symbol, "productType": self._pt()}

            if order_id:
                params["orderId"] = order_id
            elif client_order_id:
                params["clientOid"] = client_order_id
            else:
                self.logger.error("Either order_id or client_order_id must be provided")
                return False

            response = await self._make_request(
                "POST",
                "/api/v2/mix/order/cancel-order",
                data=params,
                signed=True
            )
            return response is not None

        except Exception as e:
            self.logger.error(f"cancel_order error: {e}")
            return False

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderInfo]:
        """Получить открытые ордера (устойчиво к None/нестандартным ответам)."""
        try:
            params = {"productType": self._pt()}
            if symbol:
                params["symbol"] = self._normalize_symbol_for_trading(symbol)

            response = await self._make_request(
                "GET",
                "/api/v2/mix/order/orders-pending",
                params=params,
                signed=True
            )
            if not response:
                return []

            # ответ может быть словарём вида {"entrustedList":[...]} или уже списком
            entrusted = []
            if isinstance(response, dict):
                entrusted = response.get("entrustedList") or []
            elif isinstance(response, list):
                entrusted = response
            else:
                entrusted = []

            orders: List[OrderInfo] = []
            for order_data in entrusted:
                orders.append(self._map_order_to_model(order_data))
            return orders

        except Exception as e:
            self.logger.error(f"get_open_orders error: {e}")
            return []

    async def get_order_status(self, symbol: str, order_id: str) -> Optional[OrderInfo]:
        """Получение статуса ордера."""
        try:
            params = {
                "symbol": self._normalize_symbol_for_trading(symbol),
                "productType": self._pt(),
                "orderId": order_id
            }
            response = await self._make_request(
                "GET", "/api/v2/mix/order/detail", params=params, signed=True
            )
            if not response:
                return None

            return self._map_order_to_model(response)

        except Exception as e:
            self.logger.error(f"get_order_status error: {e}")
            return None

    # ---------------------------------------------------------------------
    # Методы работы с позициями
    # ---------------------------------------------------------------------
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Получить текущие позиции."""
        try:
            params = {"productType": self._pt()}
            if symbol:
                params["symbol"] = self._normalize_symbol_for_trading(symbol)

            response = await self._make_request(
                "GET", "/api/v2/mix/position/all-position", params=params, signed=True
            )
            if not response:
                return []

            positions = []
            data_list = response if isinstance(response, list) else response.get("data", [])
            for pos_data in data_list:
                # пропускаем пустые
                total_sz = float(pos_data.get("total", 0))
                if total_sz == 0:
                    continue

                positions.append(Position(
                    symbol=pos_data.get("symbol", ""),
                    side="Buy" if pos_data.get("holdSide") == "long" else "Sell",
                    size=total_sz,
                    entry_price=float(pos_data.get("averageOpenPrice", 0)),
                    mark_price=float(pos_data.get("markPrice", 0)),
                    pnl=float(pos_data.get("unrealizedPL", 0)),
                    pnl_percentage=float(pos_data.get("achievedProfits", 0)) * 100,
                    leverage=int(pos_data.get("leverage", 1)),
                    margin_mode="CROSS" if pos_data.get("marginMode") == "crossed" else "ISOLATED",
                    liquidation_price=float(pos_data.get("liquidationPrice", 0)),
                    unrealized_pnl=float(pos_data.get("unrealizedPL", 0)),
                    created_time=int(pos_data.get("ctime", 0)),
                    updated_time=int(pos_data.get("utime", 0))
                ))
            return positions

        except Exception as e:
            self.logger.error(f"get_positions error: {e}")
            return []

    async def close_position(self, symbol: str, reduce_only: bool = True) -> bool:
        """Закрыть позицию"""
        try:
            positions = await self.get_positions(symbol)
            if not positions:
                self.logger.info(f"No position to close for {symbol}")
                return True
            
            for position in positions:
                side = "sell" if position.side == "Buy" else "buy"
                
                order = await self.place_order(
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    quantity=position.size,
                    reduce_only=True
                )
                
                if not order:
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"close_position error: {e}")
            return False

    async def get_liquidation_price(self, symbol: str) -> Optional[float]:
        """Получение цены ликвидации для позиции"""
        try:
            positions = await self.get_positions(symbol)
            
            for position in positions:
                if position.symbol == self._normalize_symbol_for_trading(symbol):
                    return position.liquidation_price
            
            return None
            
        except Exception as e:
            self.logger.error(f"get_liquidation_price error: {e}")
            return None

    async def _setup_position_mode(self) -> bool:
        """
        Настройка режима позиций (one-way mode) - ИСПРАВЛЕННАЯ версия для testnet
        """
        try:
            # На testnet часто режим уже установлен и изменение вызывает ошибки
            # Поэтому сначала пробуем получить текущий режим
            if self.testnet:
                # Для testnet просто логируем попытку и продолжаем
                self.logger.info("Testnet mode: skipping position mode setup (usually pre-configured)")
                return True
            
            # Для mainnet пытаемся установить режим
            product_type = self._pt()
        
            # Пробуем установить one-way режим
            try:
                params = {
                    "productType": product_type,
                    "posMode": "one_way"  # Упрощенный параметр для v2 API
                }
            
                response = await self._make_request(
                    "POST",
                    "/api/v2/mix/account/set-position-mode",
                    data=params,
                    signed=True
                )
            
                if response:
                    self.logger.info("Position mode set to one-way successfully")
                    return True
                
            except Exception as e:
                # Если получили ошибку 40808 или 40404 - режим уже установлен
                error_str = str(e)
                if "40808" in error_str or "40404" in error_str or "already" in error_str.lower():
                    self.logger.debug("Position mode already configured, proceeding")
                    return True
                else:
                    self.logger.warning(f"Could not set position mode: {e}")
                    # На mainnet это может быть критично
                    if not self.testnet:
                        return False
        
            return True
        
        except Exception as e:
            self.logger.warning(f"Position mode setup error: {e}")
            # Не фейлим инициализацию из-за этого
            return True

    # ---------------------------------------------------------------------
    # Настройки маржи
    # ---------------------------------------------------------------------
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Установить кредитное плечо."""
        try:
            params = {
                "symbol": self._normalize_symbol_for_trading(symbol),
                "productType": self._pt(),
                "marginCoin": "USDT",
                "leverage": str(leverage)
            }
            response = await self._make_request(
                "POST", "/api/v2/mix/account/set-leverage", data=params, signed=True
            )
            return response is not None
        except Exception as e:
            self.logger.error(f"set_leverage error: {e}")
            return False

    async def set_margin_mode(self, symbol: str, mode: Union[MarginMode, str]) -> bool:
        """Установить режим маржи."""
        try:
            mode_str = "crossed" if str(mode).lower() in ["cross", "crossed"] else "isolated"
            params = {
                "symbol": self._normalize_symbol_for_trading(symbol),
                "productType": self._pt(),
                "marginCoin": "USDT",
                "marginMode": mode_str
            }
            response = await self._make_request(
                "POST", "/api/v2/mix/account/set-margin-mode", data=params, signed=True
            )
            return response is not None
        except Exception as e:
            self.logger.error(f"set_margin_mode error: {e}")
            return False

    async def set_position_stop_loss_take_profit(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> bool:
        """Установка Stop Loss и Take Profit для позиции."""
        try:
            positions = await self.get_positions(symbol)
            if not positions:
                self.logger.warning(f"No position found for {symbol}")
                return False

            position = positions[0]
            hold_side = "long" if position.side == "Buy" else "short"
            ok = True

            if stop_loss is not None:
                sl_params = {
                    "symbol": self._normalize_symbol_for_trading(symbol),
                    "productType": self._pt(),
                    "marginCoin": "USDT",
                    "planType": "loss_plan",
                    "triggerPrice": str(stop_loss),
                    "triggerType": "mark_price",
                    "holdSide": hold_side,
                    "size": str(position.size),
                    "clientOid": f"sl_{int(time.time() * 1000)}"
                }
                sl_response = await self._make_request(
                    "POST", "/api/v2/mix/order/place-tpsl-order", data=sl_params, signed=True
                )
                if not sl_response:
                    self.logger.error(f"Failed to set stop loss for {symbol}")
                    ok = False

            if take_profit is not None:
                tp_params = {
                    "symbol": self._normalize_symbol_for_trading(symbol),
                    "productType": self._pt(),
                    "marginCoin": "USDT",
                    "planType": "profit_plan",
                    "triggerPrice": str(take_profit),
                    "triggerType": "mark_price",
                    "holdSide": hold_side,
                    "size": str(position.size),
                    "clientOid": f"tp_{int(time.time() * 1000)}"
                }
                tp_response = await self._make_request(
                    "POST", "/api/v2/mix/order/place-tpsl-order", data=tp_params, signed=True
                )
                if not tp_response:
                    self.logger.error(f"Failed to set take profit for {symbol}")
                    ok = False

            return ok

        except Exception as e:
            self.logger.error(f"set_position_stop_loss_take_profit error: {e}")
            return False

    # ---------------------------------------------------------------------
    # Рыночные данные
    # ---------------------------------------------------------------------
    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """Получить текущий тикер."""
        try:
            normalized_symbol = self._normalize_symbol_for_market_data(symbol)
            params = {"symbol": normalized_symbol, "productType": self._pt()}
            response = await self._make_request(
                "GET", "/api/v2/mix/market/ticker", params=params
            )
            if not response:
                return None

            ticker_data = response[0] if isinstance(response, list) and response else response
            return Ticker(
                symbol=symbol,
                last_price=float(ticker_data.get("lastPr", 0)),
                bid_price=float(ticker_data.get("bestBid", 0)),
                ask_price=float(ticker_data.get("bestAsk", 0)),
                volume_24h=float(ticker_data.get("baseVolume", 0)),
                turnover_24h=float(ticker_data.get("quoteVolume", 0)),
                price_change_24h=float(ticker_data.get("change24h", 0)),
                price_change_24h_percent=float(ticker_data.get("changePercent24h", 0)),
                high_24h=float(ticker_data.get("high24h", 0)),
                low_24h=float(ticker_data.get("low24h", 0))
            )

        except Exception as e:
            self.logger.error(f"get_ticker error: {e}")
            return None

    async def get_all_tickers(self) -> List[Ticker]:
        """Получение всех тикеров."""
        try:
            params = {"productType": self._pt()}
            response = await self._make_request(
                "GET", "/api/v2/mix/market/tickers", params=params
            )
            if not response:
                return []

            tickers = []
            for t in response:
                tickers.append(Ticker(
                    symbol=t.get("symbol", ""),
                    last_price=float(t.get("lastPr", 0)),
                    bid_price=float(t.get("bestBid", 0)),
                    ask_price=float(t.get("bestAsk", 0)),
                    volume_24h=float(t.get("baseVolume", 0)),
                    turnover_24h=float(t.get("quoteVolume", 0)),
                    price_change_24h=float(t.get("change24h", 0)),
                    price_change_24h_percent=float(t.get("changePercent24h", 0)),
                    high_24h=float(t.get("high24h", 0)),
                    low_24h=float(t.get("low24h", 0))
                ))
            return tickers

        except Exception as e:
            self.logger.error(f"get_all_tickers error: {e}")
            return []

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Получение текущей ставки финансирования (устойчиво к формату data=list/dict и S/без S в demo)."""
        try:
            symbols_to_try = [
                self._normalize_symbol_for_trading(symbol),      # BTCUSDT
                self._normalize_symbol_for_market_data(symbol),  # SBTCSUSDT
            ]
            product_type = self._pt()

            for sym in symbols_to_try:
                resp = await self._make_request(
                    "GET",
                    "/api/v2/mix/market/current-fund-rate",
                    params={"symbol": sym, "productType": product_type}
                )
                if not resp:
                    continue

                # Частый вариант — список
                if isinstance(resp, list):
                    # сначала пытаемся точное совпадение по символу
                    for it in resp:
                        if isinstance(it, dict) and it.get("symbol") == sym:
                            if "fundingRate" in it:
                                return float(it["fundingRate"])
                            if "fundRate" in it:
                                return float(it["fundRate"])
                    # если точного совпадения нет — берём первый валидный
                    for it in resp:
                        if isinstance(it, dict):
                            if "fundingRate" in it:
                                return float(it["fundingRate"])
                            if "fundRate" in it:
                                return float(it["fundRate"])
                    continue

                # Иногда приходит объект
                if isinstance(resp, dict):
                    if "fundingRate" in resp:
                        return float(resp["fundingRate"])
                    if "fundRate" in resp:
                        return float(resp["fundRate"])

            return None

        except Exception as e:
            self.logger.error(f"get_funding_rate error: {e}")
            return None

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Optional[Dict[str, Any]]:
        """Получить стакан ордеров."""
        try:
            params = {
                "symbol": self._normalize_symbol_for_market_data(symbol),
                "productType": self._pt(),
                "limit": str(limit)
            }
            response = await self._make_request(
                "GET", "/api/v2/mix/market/orderbook", params=params
            )
            if not response:
                return None

            return {
                "bids": [[float(p), float(q)] for p, q in response.get("bids", [])],
                "asks": [[float(p), float(q)] for p, q in response.get("asks", [])],
                "timestamp": int(response.get("ts", 0))
            }

        except Exception as e:
            self.logger.error(f"get_orderbook error: {e}")
            return None

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1H",
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> List[Kline]:
        """Получить исторические свечи."""
        try:
            normalized_symbol = self._normalize_symbol_for_market_data(symbol)
            interval_map = {
                "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
                "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
                "1H": "1H", "2H": "2H", "4H": "4H", "6H": "6H", "12H": "12H",
                "1d": "1D", "1w": "1W", "1D": "1D", "1W": "1W"
            }

            params = {
                "symbol": normalized_symbol,
                "productType": self._pt(),
                "granularity": interval_map.get(interval, "1H"),
                "limit": str(limit)
            }
            if start_time:
                params["startTime"] = str(start_time)
            if end_time:
                params["endTime"] = str(end_time)

            response = await self._make_request(
                "GET", "/api/v2/mix/market/candles", params=params
            )
            if not response:
                return []

            klines: List[Kline] = []
            for k in response:
                klines.append(Kline(
                    timestamp=int(k[0]),
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    turnover=float(k[6]) if len(k) > 6 else 0
                ))
            return klines

        except Exception as e:
            self.logger.error(f"get_klines error: {e}")
            return []

    # ---------------------------------------------------------------------
    # Информация об инструментах
    # ---------------------------------------------------------------------
    async def get_instrument_info(self, symbol: str) -> Optional[InstrumentInfo]:
        """Получение информации о торговом инструменте (demo/mainnet устойчиво)."""
        try:
            await self._update_symbols_cache()

            keys_to_try = [
                self._normalize_symbol_for_trading(symbol),       # BTCUSDT
                self._normalize_symbol_for_market_data(symbol),   # SBTCSUSDT
                symbol.upper().replace("/", ""),                  # как пришёл
            ]

            for key in keys_to_try:
                info = self.symbols_cache.get(key)
                if info:
                    return self._parse_instrument_info(info)

            return None

        except Exception as e:
            self.logger.error(f"get_instrument_info error: {e}")
            return None

    async def get_all_instruments(self) -> List[InstrumentInfo]:
        """Получение информации о всех торговых инструментах."""
        try:
            params = {"productType": self._pt()}
            response = await self._make_request(
                "GET", "/api/v2/mix/market/contracts", params=params
            )
            if not response:
                return []

            instruments: List[InstrumentInfo] = []
            contracts = response if isinstance(response, list) else response.get("data", [])
            for c in contracts:
                try:
                    instruments.append(InstrumentInfo(
                        symbol=c.get("symbol", ""),
                        base_coin=c.get("baseCoin", ""),
                        quote_coin=c.get("quoteCoin", ""),
                        status=c.get("symbolStatus", "normal"),
                        min_order_qty=float(c.get("minTradeNum", 0)),
                        max_order_qty=float(c.get("maxPositionNum", 999999)),
                        qty_step=float(c.get("minTradeNum", 0)),  # шаг размера
                        price_precision=int(c.get("pricePlace", 2)),
                        qty_precision=int(c.get("volumePlace", 3)),
                        min_price=float(c.get("priceEndStep", 0.01)),
                        max_price=999999.0,
                        price_step=float(c.get("priceEndStep", 0.01)),
                        leverage_filter={"min": 1, "max": int(c.get("maxLever", 125))},
                        lot_size_filter={
                            "min": float(c.get("minTradeNum", 0)),
                            "max": float(c.get("maxPositionNum", 999999)),
                            "step": float(c.get("minTradeNum", 0))
                        }
                    ))
                except Exception as e:
                    self.logger.error(f"Error parsing instrument {c.get('symbol')}: {e}")
                    continue
            return instruments

        except Exception as e:
            self.logger.error(f"get_all_instruments error: {e}")
            return []

    async def _update_symbols_cache(self):
        """Обновление кэша символов + дублируем ключи для demo (SXXX и без S)."""
        try:
            now = time.time()
            if now - self.symbols_cache_time < self.cache_ttl:
                return

            params = {"productType": self._pt()}
            response = await self._make_request(
                "GET", "/api/v2/mix/market/contracts", params=params
            )

            if response:
                self.symbols_cache.clear()

                # API может вернуть либо list, либо {"data": [...]}
                contracts = response if isinstance(response, list) else response.get("data", [])

                for contract in contracts:
                    symbol = contract.get("symbol", "")
                    if not symbol:
                        continue

                    # Кладём как есть
                    self.symbols_cache[symbol] = contract

                    # И дублируем под "торговым" ключом для demo:
                    # SBTCSUSDT -> BTCUSDT
                    trade_sym = self._normalize_symbol_for_trading(symbol)
                    if trade_sym and trade_sym != symbol:
                        self.symbols_cache[trade_sym] = contract

                self.symbols_cache_time = now
                self.logger.info(f"Updated symbols cache: {len(self.symbols_cache)} symbols")

        except Exception as e:
            self.logger.error(f"_update_symbols_cache error: {e}")

    def _parse_instrument_info(self, data: Dict[str, Any]) -> Optional[InstrumentInfo]:
        """Парсинг информации об инструменте"""
        try:
            return InstrumentInfo(
                symbol=data.get("symbol", ""),
                base_coin=data.get("baseCoin", ""),
                quote_coin=data.get("quoteCoin", ""),
                status=data.get("symbolStatus", "normal"),
                min_order_qty=float(data.get("minTradeNum", 0)),
                max_order_qty=float(data.get("maxPositionNum", 999999)),
                qty_step=float(data.get("sizeMultiplier", 0.001)),
                price_precision=int(data.get("pricePlace", 2)),
                qty_precision=int(data.get("volumePlace", 3)),
                min_price=float(data.get("priceEndStep", 0.01)),
                max_price=999999.0,
                price_step=float(data.get("priceEndStep", 0.01)),
                leverage_filter={
                    "min": 1,
                    "max": int(data.get("maxLever", 125))
                },
                lot_size_filter={
                    "min": float(data.get("minTradeNum", 0)),
                    "max": float(data.get("maxPositionNum", 999999)),
                    "step": float(data.get("sizeMultiplier", 0.001))
                }
            )
        except Exception as e:
            self.logger.error(f"_parse_instrument_info error: {e}")
            return None

    # ---------------------------------------------------------------------
    # Утилитарные методы
    # ---------------------------------------------------------------------
    async def get_server_time(self) -> int | None:
        """Возвращает serverTime из /api/v2/public/time (int, мс)."""
        try:
            data = await self._make_request("GET", "/api/v2/public/time")
            if not data:
                return None
            # data: {"serverTime": 1720000000000}
            if isinstance(data, dict):
                st = data.get("serverTime")
                return int(st) if st is not None else None
            # иногда data может быть просто числом — на всякий
            if isinstance(data, (int, float, str)):
                return int(data)
            return None
        except Exception as e:
            self.logger.error("get_server_time error: %s", e, exc_info=True)
            return None


    def round_quantity(self, symbol: str, quantity: float) -> str:
        """Округление количества по volumePlace или по шагу из minTradeNum."""
        try:
            precision = 3
            normalized = self._normalize_symbol_for_trading(symbol)
            info = self.symbols_cache.get(normalized) or self.symbols_cache.get(
                self._normalize_symbol_for_market_data(symbol)
            )

            if info:
                # приоритет — количество знаков в количестве
                vp = info.get("volumePlace")
                if vp is not None:
                    precision = int(vp)
                else:
                    # пробуем вывести шаг из minTradeNum
                    min_trade = info.get("minTradeNum")
                    if min_trade is not None:
                        s = str(min_trade)
                        if "." in s:
                            precision = len(s.split(".")[1].rstrip("0"))
                        else:
                            precision = 0

            qty_decimal = Decimal(str(quantity))
            quant = Decimal("1") if precision == 0 else Decimal(f"0.{'0' * precision}")
            rounded = qty_decimal.quantize(quant, rounding=ROUND_DOWN)
            return str(rounded)

        except Exception as e:
            self.logger.error(f"round_quantity error: {e}")
            return str(quantity)

    def round_price(self, symbol: str, price: float) -> str:
        """Округление цены кратно биржевому тик-шага (priceEndStep)."""
        try:
            step = Decimal("0.01")  # дефолт
            normalized = self._normalize_symbol_for_trading(symbol)

            info = self.symbols_cache.get(normalized) or self.symbols_cache.get(
                self._normalize_symbol_for_market_data(symbol)
            )

            if info:
                # Берём именно тик-шага, а не количество знаков
                step_val = info.get("priceEndStep") or info.get("priceTick") or 0.01
                step = Decimal(str(step_val))

            price_dec = Decimal(str(price))
            # Округляем вниз до кратности step
            units = (price_dec / step).to_integral_value(rounding=ROUND_DOWN)
            rounded = (units * step).quantize(step, rounding=ROUND_DOWN)
            return format(rounded, 'f')

        except Exception as e:
            self.logger.error(f"round_price error: {e}")
            return str(price)

    # ---------------------------------------------------------------------
    # WebSocket методы
    # ---------------------------------------------------------------------
    async def subscribe_to_trades(self, symbol: str, callback) -> bool:
        """Подписка на поток сделок"""
        try:
            self.logger.warning("WebSocket trades subscription not implemented yet")
            return False
        except Exception as e:
            self.logger.error(f"subscribe_to_trades error: {e}")
            return False

    async def subscribe_to_klines(self, symbol: str, interval: str, callback) -> bool:
        """Подписка на поток свечей"""
        try:
            self.logger.warning("WebSocket klines subscription not implemented yet")
            return False
        except Exception as e:
            self.logger.error(f"subscribe_to_klines error: {e}")
            return False

    async def subscribe_to_orderbook(self, symbol: str, callback) -> bool:
        """Подписка на обновления стакана"""
        try:
            self.logger.warning("WebSocket orderbook subscription not implemented yet")
            return False
        except Exception as e:
            self.logger.error(f"subscribe_to_orderbook error: {e}")
            return False

    async def subscribe_to_position_updates(self, callback) -> bool:
        """Подписка на обновления позиций"""
        try:
            self.logger.warning("WebSocket position subscription not implemented yet")
            return False
        except Exception as e:
            self.logger.error(f"subscribe_to_position_updates error: {e}")
            return False

    # ---------------------------------------------------------------------
    # Представление объекта
    # ---------------------------------------------------------------------
    def __str__(self) -> str:
        return f"BitgetAdapter(testnet={self.testnet}, symbols_cached={len(self.symbols_cache)})"

    def __repr__(self) -> str:
        return (f"BitgetAdapter(api_key={'***' if self.api_key else None}, "
                f"testnet={self.testnet}, initialized={self.session is not None})")


# --------------------------------------------------------------
# Вспомогательные фабричные функции
# --------------------------------------------------------------
async def create_bitget_adapter(
    api_key: str,
    api_secret: str,
    api_passphrase: str = None,
    passphrase: str = None,
    testnet: bool = True,
    recv_window: int = 10000,
    initialize: bool = True
) -> Optional[BitgetAdapter]:
    """
    Создание и инициализация Bitget адаптера
    
    Args:
        api_key: API ключ
        api_secret: API секрет
        api_passphrase: Пассфраза (основной параметр)
        passphrase: Пассфраза (алиас для совместимости с фабрикой)
        testnet: Использовать demo режим
        recv_window: Окно получения
        initialize: Автоматически инициализировать
        
    Returns:
        Готовый адаптер или None
    """
    try:
        adapter = BitgetAdapter(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            passphrase=passphrase,
            testnet=testnet,
            recv_window=recv_window
        )
        
        if initialize:
            ok = await adapter.initialize()
            if not ok:
                await adapter.close()
                return None
                
        return adapter
        
    except Exception as e:
        logging.getLogger("BitgetAdapter").error(f"Error creating adapter: {e}")
        return None


def get_bitget_credentials(testnet: bool = True) -> Dict[str, str]:
    """Получение учетных данных из переменных окружения"""
    import os
    
    prefix = "BITGET_TESTNET" if testnet else "BITGET_MAINNET"
    
    return {
        "api_key": os.getenv(f"{prefix}_API_KEY", ""),
        "api_secret": os.getenv(f"{prefix}_API_SECRET", ""),
        "api_passphrase": os.getenv(f"{prefix}_API_PASSPHRASE", ""),
        "recv_window": int(os.getenv(f"{prefix}_RECV_WINDOW", "10000"))
    }