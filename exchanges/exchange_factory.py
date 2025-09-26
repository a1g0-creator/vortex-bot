"""
Фабрика для создания экземпляров биржевых адаптеров
Централизованное управление подключениями к различным биржам
Версия с поддержкой demo/mainnet окружений через config_loader
"""
import os
try:
    from dotenv import load_dotenv  # python-dotenv должен быть в requirements.txt
    load_dotenv()
except Exception:
    pass

import logging
from typing import Dict, Optional, Type, Any
from enum import Enum
from config.config_loader import config_loader  # единая точка конфигов
from exchanges.base_exchange import BaseExchange
from exchanges.bybit_adapter import BybitAdapter
from exchanges.bitget_adapter import BitgetAdapter
# from adapters.bitget_adapter import BitgetAdapter  # Будет добавлено позже

def _active_env_for(exchange: str) -> str:
    """
    Возвращает активное окружение из ConfigLoader для указанной биржи.
    Ожидаемые значения: 'demo'/'prod' (или эквиваленты).
    """
    return config_loader.get_active_exchange_env(exchange)  # у тебя уже логируется Active environment: demo/prod


def _pick_creds(exchange_cfg: Dict[str, Any], env: str) -> Dict[str, Any]:
    """
    Достаём креды из exchange_cfg['api_credentials'][env]-подобной структуры и
    нормализуем ключи под адаптеры: api_key, api_secret, (api_passphrase).
    Поддерживаем названия env: demo/testnet и prod/mainnet.
    """
    creds_root: Dict[str, Any] = dict(exchange_cfg.get("api_credentials") or {})

    # Поддержка вариантов названий окружений
    env_aliases = {
        "demo": ["demo", "testnet"],
        "prod": ["prod", "mainnet", "live"],
    }
    search_keys = env_aliases["demo"] if env.lower() in ("demo", "testnet") else env_aliases["prod"]

    creds: Dict[str, Any] = {}
    for k in search_keys:
        if k in creds_root and isinstance(creds_root[k], dict):
            creds = creds_root[k]
            break

    # Нормализация имён ключей
    api_key = creds.get("api_key") or creds.get("key") or creds.get("apiKey")
    api_secret = creds.get("api_secret") or creds.get("secret") or creds.get("apiSecret")
    api_passphrase = creds.get("api_passphrase") or creds.get("passphrase") or creds.get("apiPassphrase")

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        # passphrase требуется на Bitget (и некоторых других) — добавляем, если есть
        "api_passphrase": api_passphrase,
    }


def _merge_runtime_config(exchange: str) -> Dict[str, Any]:
    """
    Собираем итоговый конфиг для адаптера:
    - базовый блок биржи из exchanges.yaml,
    - активные сетевые параметры (base_url/ws_public/ws_private),
    - креды активного окружения, расплющенные в корень.
    """
    exchange_cfg = config_loader.get_exchange_config(exchange)  # dict по бирже
    env = _active_env_for(exchange)

    # Сетевые параметры (поддерживаем возможные структуры)
    net_root = (exchange_cfg.get("network") or {}).get(env) or exchange_cfg.get("network") or {}
    base_url = net_root.get("base_url") or net_root.get("rest") or net_root.get("http")
    ws_public = net_root.get("ws_public") or net_root.get("websocket_public") or net_root.get("wsPublic")
    ws_private = net_root.get("ws_private") or net_root.get("websocket_private") or net_root.get("wsPrivate")

    # Креды
    creds = _pick_creds(exchange_cfg, env)

    # Итог: плоский словарь, который ожидают адаптеры
    result: Dict[str, Any] = dict(exchange_cfg)  # копия на всякий
    result.update({
        "env": env,
        "base_url": base_url,
        "ws_public": ws_public,
        "ws_private": ws_private,
        "api_key": creds.get("api_key"),
        "api_secret": creds.get("api_secret"),
        "api_passphrase": creds.get("api_passphrase"),
    })
    return result

class ExchangeType(Enum):
    """Поддерживаемые типы бирж"""
    BYBIT = "bybit"
    BITGET = "bitget"


class ExchangeFactory:
    """
    Фабрика для создания и управления биржевыми адаптерами
    Паттерн Factory + Registry для гибкого управления биржами
    """

    # Реестр доступных адаптеров
    _adapters: Dict[ExchangeType, Type[BaseExchange]] = {
        ExchangeType.BYBIT: BybitAdapter,
        ExchangeType.BITGET: BitgetAdapter,  # включён Bitget
    }

    # Кэш созданных экземпляров
    _instances: Dict[str, BaseExchange] = {}

    def __init__(self, cfg_loader=config_loader):
        self.logger = logging.getLogger("ExchangeFactory")
        self.cfg_loader = cfg_loader

    # ──────────────── РЕГИСТР / СПРАВКА ────────────────

    @classmethod
    def register_adapter(cls, exchange_type: ExchangeType, adapter_class: Type[BaseExchange]):
        cls._adapters[exchange_type] = adapter_class
        logging.getLogger("ExchangeFactory").info(
            f"Registered adapter {adapter_class.__name__} for {exchange_type.value}"
        )

    @classmethod
    def get_supported_exchanges(cls) -> list[str]:
        return [exchange.value for exchange in cls._adapters.keys()]

    # ──────────────── ВСПОМОГАТЕЛЬНО ────────────────

    def _get_active_env(self, exchange_name: str) -> str:
        """Активное окружение для биржи (demo/testnet или prod/mainnet)."""
        get_env = getattr(self.cfg_loader, "get_active_exchange_env", None)
        if callable(get_env):
            try:
                env = get_env(exchange_name)
                if env:
                    return env
            except Exception:
                pass
        # фоллбек — читаем из активного блока
        active_cfg = self.cfg_loader.get_active_exchange_config(exchange_name) or {}
        return active_cfg.get("environment") or active_cfg.get("env") or "demo"

    def _extract_network(self, cfg: Dict[str, Any], env: str) -> Dict[str, Any]:
        """
        Достаём base_url / ws_public / ws_private.
        Поддерживаем как плоскую схему, так и cfg["network"][env].
        """
        base_url = cfg.get("base_url")
        ws_public = cfg.get("ws_public") or cfg.get("ws_url")  # на случай другого ключа
        ws_private = cfg.get("ws_private")

        if not (base_url and (ws_public or ws_private)):
            network = cfg.get("network") or {}
            env_block = network.get(env) if isinstance(network.get(env), dict) else {}
            net_flat = env_block or network or {}
            base_url = base_url or net_flat.get("base_url") or net_flat.get("rest") or net_flat.get("http")
            ws_public = ws_public or net_flat.get("ws_public") or net_flat.get("websocket_public") \
                        or net_flat.get("wsPublic") or net_flat.get("ws_url")
            ws_private = ws_private or net_flat.get("ws_private") or net_flat.get("websocket_private") \
                         or net_flat.get("wsPrivate")

        return {"base_url": base_url, "ws_public": ws_public, "ws_private": ws_private}

    def _extract_creds(self, exchange: str, cfg: Dict[str, Any], env: str) -> Dict[str, Any]:
        """
        Креды берём в приоритете:
        1) из .env по стандартным именам,
        2) из плоских ключей активного конфига,
        3) из cfg["api_credentials"].
        """
        ex = exchange.lower()
        ev = env.lower()

        # ── 1) .env (приоритетно)
        env_map = {}
        if ex == "bybit":
            if ev in ("demo", "testnet"):
                env_map = {
                    "api_key": os.getenv("BYBIT_TESTNET_API_KEY"),
                    "api_secret": os.getenv("BYBIT_TESTNET_API_SECRET"),
                    "api_passphrase": None,  # Bybit не требует
                }
            else:  # prod/mainnet
                env_map = {
                    "api_key": os.getenv("BYBIT_MAINNET_API_KEY"),
                    "api_secret": os.getenv("BYBIT_MAINNET_API_SECRET"),
                    "api_passphrase": None,
                }
        elif ex == "bitget":
            if ev in ("demo", "testnet"):
                env_map = {
                    "api_key": os.getenv("BITGET_TESTNET_API_KEY"),
                    "api_secret": os.getenv("BITGET_TESTNET_API_SECRET"),
                    "api_passphrase": os.getenv("BITGET_TESTNET_API_PASSPHRASE"),
                }
            else:
                env_map = {
                    "api_key": os.getenv("BITGET_MAINNET_API_KEY"),
                    "api_secret": os.getenv("BITGET_MAINNET_API_SECRET"),
                    "api_passphrase": os.getenv("BITGET_MAINNET_API_PASSPHRASE"),
                }
        # отфильтруем пустое
        env_map = {k: v for k, v in env_map.items() if v}

        # ── 2) активный конфиг (плоские ключи)
        flat_map = {
            "api_key": cfg.get("api_key") or cfg.get("key") or cfg.get("apiKey"),
            "api_secret": cfg.get("api_secret") or cfg.get("secret") or cfg.get("apiSecret"),
            "api_passphrase": cfg.get("api_passphrase") or cfg.get("passphrase") or cfg.get("apiPassphrase"),
        }
        flat_map = {k: v for k, v in flat_map.items() if v}

        # ── 3) вложенные креды
        nested = cfg.get("api_credentials") or {}
        nested_map = {
            "api_key": nested.get("api_key") or nested.get("key") or nested.get("apiKey"),
            "api_secret": nested.get("api_secret") or nested.get("secret") or nested.get("apiSecret"),
            "api_passphrase": nested.get("api_passphrase") or nested.get("passphrase") or nested.get("apiPassphrase"),
        }
        nested_map = {k: v for k, v in nested_map.items() if v}

        # приоритет: .env -> flat -> nested
        out = {"api_key": None, "api_secret": None, "api_passphrase": None}
        out.update(nested_map)
        out.update(flat_map)
        out.update(env_map)
        return out


    def _build_runtime_config(self, exchange_name: str) -> Dict[str, Any]:
        """
        Собираем плоский runtime-конфиг активного окружения:
        - environment
        - base_url, ws_public, ws_private
        - api_key, api_secret, (api_passphrase)
        """
        exchange_cfg = dict(self.cfg_loader.get_active_exchange_config(exchange_name) or {})
        if not exchange_cfg:
            self.logger.error(f"No active configuration found for {exchange_name}")
            return {}

        env = self._get_active_env(exchange_name)
        network = self._extract_network(exchange_cfg, env)

        # ВАЖНО: теперь передаём сюда exchange_name и env
        creds = self._extract_creds(exchange_name, exchange_cfg, env)

        runtime = dict(exchange_cfg)
        runtime.update(
            {
                "environment": env,
                "env": env,
                **network,
                **creds,
            }
        )
        return runtime


    # ──────────────── СОЗДАНИЕ АДАПТЕРОВ ────────────────

    def create_exchange_from_config(self, exchange_name: str) -> Optional[BaseExchange]:
        """Создание экземпляра адаптера из активного конфига."""
        try:
            config = self._build_runtime_config(exchange_name)
            if not config:
                return None

            exchange_type = ExchangeType(exchange_name)
            if exchange_type not in self._adapters:
                self.logger.error(f"Unsupported exchange type: {exchange_name}")
                return None

            environment = config.get("environment", "unknown")
            api_key_for_cache = (config.get("api_key") or "")
            cache_key = f"{exchange_name}_{environment}_{hash(api_key_for_cache)}"

            # Кэш
            if cache_key in self._instances:
                self.logger.debug(f"Returning cached instance for {exchange_name} ({environment})")
                return self._instances[cache_key]

            # Предвалидация кредов
            if exchange_type == ExchangeType.BYBIT:
                if not (config.get("api_key") and config.get("api_secret")):
                    raise ValueError("Bybit credentials are required (api_key/api_secret)")
            elif exchange_type == ExchangeType.BITGET:
                if not (config.get("api_key") and config.get("api_secret") and config.get("api_passphrase")):
                    raise ValueError("Bitget credentials are required (api_key/api_secret/api_passphrase)")

            # Создание экземпляра адаптера
            import inspect

            adapter_class = self._adapters[exchange_type]

            # Определяем сигнатуру конструктора
            sig = inspect.signature(adapter_class.__init__)
            params = [p for p in sig.parameters.values() if p.name != "self"]
            param_names = [p.name for p in params]

            # Подготовим kwargs из собранного runtime-конфига
            creds_kwargs = {
                "api_key": config.get("api_key"),
                "api_secret": config.get("api_secret"),
                "api_passphrase": config.get("api_passphrase"),
            }
            common_kwargs = {
                "base_url": config.get("base_url"),
                "ws_public": config.get("ws_public"),
                "ws_private": config.get("ws_private"),
                "environment": config.get("environment") or config.get("env"),
    }
            init_kwargs = {k: v for k, v in {**creds_kwargs, **common_kwargs}.items() if v is not None}

            # Если конструктор явно ожидает api_key/api_secret/... — пробрасываем аргументами.
            # Если принимает один config (или только **kwargs) — отдаём целиком dict.
            if {"api_key", "api_secret"}.issubset(set(param_names)):
                # Для Bitget и др. параметрических адаптеров
                instance = adapter_class(**init_kwargs)
            else:
                # Для адаптеров, принимающих целиком config dict
                instance = adapter_class(config)


            # Кэшируем
            self._instances[cache_key] = instance
            self.logger.info(f"Created {exchange_name} adapter (environment: {environment})")
            return instance

        except Exception as e:
            self.logger.error(f"Error creating {exchange_name} adapter: {e}")
            import traceback
            traceback.print_exc()
            return None

    def create_bybit_from_config(self) -> Optional[BybitAdapter]:
        return self.create_exchange_from_config("bybit")

    def create_bitget_from_config(self) -> Optional[BaseExchange]:
        return self.create_exchange_from_config("bitget")

    # ──────────────── СЛУЖЕБНЫЕ (как у тебя) ────────────────

    async def initialize_exchange(self, exchange: BaseExchange) -> bool:
        try:
            self.logger.info(f"Initializing {exchange.exchange_name}...")
            success = await exchange.initialize()
            if success:
                self.logger.info(f"✅ {exchange.exchange_name} initialized successfully")
            else:
                self.logger.error(f"❌ Failed to initialize {exchange.exchange_name}")
            return success
        except Exception as e:
            self.logger.error(f"Error initializing {exchange.exchange_name}: {e}")
            return False

    async def test_connection(self, exchange: BaseExchange) -> Dict[str, Any]:
        results = {
            "exchange": exchange.exchange_name,
            "environment": getattr(exchange, "environment", "unknown"),
            "server_time": False,
            "balance": False,
            "instruments": False,
            "overall": False,
        }
        try:
            try:
                server_time = await exchange.get_server_time()
                results["server_time"] = server_time is not None
                self.logger.debug(f"{exchange.exchange_name}: Server time OK")
            except Exception as e:
                self.logger.warning(f"{exchange.exchange_name}: Server time failed: {e}")

            try:
                balance = await exchange.get_balance()
                results["balance"] = balance is not None
                self.logger.debug(f"{exchange.exchange_name}: Balance OK")
            except Exception as e:
                self.logger.warning(f"{exchange.exchange_name}: Balance failed: {e}")

            try:
                instruments = await exchange.get_all_instruments()
                results["instruments"] = len(instruments) > 0
                self.logger.debug(f"{exchange.exchange_name}: Instruments OK ({len(instruments)} found)")
            except Exception as e:
                self.logger.warning(f"{exchange.exchange_name}: Instruments failed: {e}")

            results["overall"] = all([results["server_time"], results["balance"], results["instruments"]])
            if results["overall"]:
                self.logger.info(f"✅ {exchange.exchange_name} connection test passed")
            else:
                self.logger.warning(f"⚠️ {exchange.exchange_name} connection test failed")
        except Exception as e:
            self.logger.error(f"Error testing {exchange.exchange_name}: {e}")
        return results

    def clear_cache(self):
        self.logger.info("Clearing exchange instances cache")
        self._instances.clear()

    async def close_all_connections(self):
        self.logger.info("Closing all exchange connections")
        for instance in self._instances.values():
            try:
                await instance.close()
            except Exception as e:
                self.logger.error(f"Error closing {instance.exchange_name}: {e}")
        self.clear_cache()

    def get_instance(self, exchange_name: str, environment: str = None) -> Optional[BaseExchange]:
        for key, instance in self._instances.items():
            if exchange_name in key and (environment is None or environment in key):
                return instance
        return None

    def list_active_connections(self) -> Dict[str, Dict[str, Any]]:
        connections = {}
        for key, instance in self._instances.items():
            connections[key] = {
                "exchange": instance.exchange_name,
                "environment": getattr(instance, "environment", "unknown"),
                "class": instance.__class__.__name__,
                "url": instance.base_url,
            }
        return connections




# Глобальный экземпляр фабрики (совместимо с остальным кодом)
exchange_factory = ExchangeFactory(config_loader)



# Удобные функции для быстрого создания адаптеров
def create_bybit_adapter(**overrides) -> BybitAdapter:
    """
    Создаёт BybitAdapter из активной конфигурации с возможностью точечных переопределений.
    Пример: create_bybit_adapter(api_key="...", api_secret="...", ws_public="...")
    """
    cfg = _merge_runtime_config("bybit")
    cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})
    # Жёсткая валидация — чтобы не ловить ошибку уже внутри адаптера
    if not cfg.get("api_key") or not cfg.get("api_secret"):
        raise ValueError("Bybit credentials are required (api_key/api_secret)")
    # ВНИМАНИЕ: создаём через именованные аргументы, а не позиционно
    return BybitAdapter(
        api_key=cfg["api_key"],
        api_secret=cfg["api_secret"],
        base_url=cfg.get("base_url"),
        ws_public=cfg.get("ws_public"),
        ws_private=cfg.get("ws_private"),
        testnet=bool(cfg.get("testnet", False)),
        recv_window=cfg.get("recv_window", 10000),
        # опционально:
        account_type=cfg.get("account_type"),
        environment=cfg.get("environment"),  # теперь BybitAdapter это принимает
    )


def create_bitget_adapter(**overrides) -> BitgetAdapter:
    """
    Создаёт BitgetAdapter из активной конфигурации с возможностью точечных переопределений.
    """
    cfg = _merge_runtime_config("bitget")
    cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if not (cfg.get("api_key") and cfg.get("api_secret") and cfg.get("api_passphrase")):
        raise ValueError("Bitget credentials are required (api_key/api_secret/api_passphrase)")
    return BitgetAdapter(
        api_key=cfg["api_key"],
        api_secret=cfg["api_secret"],
        api_passphrase=cfg["api_passphrase"],
        testnet=bool(cfg.get("testnet", False)),
        recv_window=cfg.get("recv_window", 10000),
        # всё лишнее спокойно пролетит через **kwargs в адаптере
        **{
            k: v
            for k, v in cfg.items()
            if k
            not in {
                "api_key",
                "api_secret",
                "api_passphrase",
                "recv_window",
                "testnet",
            }
        },
    )


def _cfg_get_active(cfg_loader, name: str) -> dict:
    """
    Унифицированный доступ к активной конфигурации биржи.
    Предпочитаем get_active_exchange_config(name), иначе пробуем get_exchange_config(name).
    """
    if hasattr(cfg_loader, "get_active_exchange_config"):
        return cfg_loader.get_active_exchange_config(name)
    if hasattr(cfg_loader, "get_exchange_config"):
        # допустим, старый интерфейс возвращает уже активный блок
        return cfg_loader.get_exchange_config(name)
    raise AttributeError("ConfigLoader must expose get_active_exchange_config(name) or get_exchange_config(name)")

def _safe_bool(v, default=False):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return default

def test_all_exchanges(config_loader=None) -> None:
    """
    Лёгкий тест доступности REST для включённых бирж.
    Не бросает AttributeError при несовпадении интерфейса ConfigLoader.
    """
    logger = logging.getLogger("ExchangeFactory")
    try:
        # определяем, какие биржи включены
        enabled = []
        if config_loader and hasattr(config_loader, "get_enabled_exchanges"):
            enabled = list(config_loader.get_enabled_exchanges() or [])
        else:
            # бэкап: если интерфейс неизвестен — пробуем обе
            enabled = ["bybit", "bitget"]

        for name in enabled:
            try:
                cfg = _cfg_get_active(config_loader, name) if config_loader else {}
                # собираем рантайм-конфиг (env → yaml) твоей фабричной логикой
                merged = _merge_runtime_config(name)
                testnet = _safe_bool(merged.get("testnet", False))
                logger.info(f"[test] {name}: environment={'demo' if testnet else 'mainnet'}")

                # лёгкая проверка: базовый URL и ключи на месте
                if name == "bybit":
                    required = ["api_key", "api_secret", "base_url"]
                elif name == "bitget":
                    required = ["api_key", "api_secret", "api_passphrase", "base_url"]
                else:
                    required = ["base_url"]

                missing = [k for k in required if not merged.get(k)]
                if missing:
                    logger.warning(f"[test] {name}: missing required fields: {missing}")
                    continue

                # если нужно — можно добавить HEAD/GET ping, но здесь не бьёмся в сеть
                logger.info(f"[test] {name}: config OK")

            except Exception as e:
                logger.warning(f"[test] {name} failed: {e}")

    except Exception as e:
        # важное изменение: переходим на logger.error, но не выбрасываем наружу
        # чтобы основной запуск не ронять (как у тебя в логе)
        logging.getLogger("VortexTradingBot").error(f"Ошибка тестирования подключений: {e}")