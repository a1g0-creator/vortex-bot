#!/usr/bin/env python3
import asyncio
import sys
import logging
from pathlib import Path
from datetime import datetime

# Корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import config_loader
# ВАЖНО: импорт из реального файла telegram_bot.py в корне проекта
from telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("TelegramSmoke")

EXPECTED_COMMANDS = {
    "start","status","balance","positions","mode",
    "risk","risk_show","risk_set","risk_enable","risk_disable","risk_reset","help"
}

async def test_telegram_smoke():
    logger.info("=" * 50)
    logger.info("🚀 Запуск Telegram smoke-теста")
    logger.info("=" * 50)

    # 1) Конфигурация
    logger.info("📁 Загрузка конфигурации telegram.yaml...")
    config_loader.load_all_configs()
    tg_cfg = config_loader.get_config("telegram")
    bot_cfg = tg_cfg.get("bot", {}) if isinstance(tg_cfg, dict) else {}
    token = bot_cfg.get("token") or bot_cfg.get("bot_token")
    chat_id = bot_cfg.get("chat_id") or bot_cfg.get("admin_chat_id") or bot_cfg.get("channel_id")
    if not token or not chat_id:
        raise RuntimeError("Отсутствует token или chat_id в telegram.yaml (bot.token/bot_token и chat_id/admin_chat_id/channel_id)")

    logger.info("✅ Конфигурация загружена:")
    logger.info(f"   Token: {token[:10]}...")
    logger.info(f"   Chat ID: {chat_id}")

    # 2) Экземпляр бота (без моков)
    logger.info("🤖 Создание TelegramBot...")
    bot = TelegramBot(token=str(token), chat_id=str(chat_id), trading_bot_instance=None)

    # 3) Инициализация
    logger.info("🔧 Инициализация бота...")
    ok = await bot.initialize()
    if not ok:
        raise RuntimeError("initialize() вернул False")
    logger.info("✅ Бот инициализирован")

    # Внутреннее состояние
    logger.info(f"   Consumer запущен: {bot.consumer_task is not None and not bot.consumer_task.done()}")
    logger.info(f"   Размер очереди: {bot.notification_queue.qsize() if bot.notification_queue else 'n/a'}")

    # 4) getMe
    logger.info("🔍 Проверка токена (getMe)...")
    me = await bot.application.bot.get_me()
    logger.info(f"✅ Бот: @{me.username} (id={me.id})")

    # 5) Проверка команд через API
    cmds = await bot.application.bot.get_my_commands()
    got = {c.command for c in cmds}
    if got != EXPECTED_COMMANDS:
        raise RuntimeError(f"Список команд не совпадает: expected={sorted(EXPECTED_COMMANDS)}, got={sorted(got)}")
    logger.info(f"✅ Команды совпадают: {len(got)}")

    # 6) Старт
    logger.info("▶️ Старт бота...")
    await bot.start()

    # 7) Отправка через send_message
    logger.info("📨 Отправка тестового сообщения...")
    assert await bot.send_message(f"TEST: telegram smoke • {datetime.now():%Y-%m-%d %H:%M:%S}"), "send_message() вернул False"

    # 8) Backward-compat API: notify / notify_risk / notify_signal
    logger.info("🧪 Проверка совместимости: notify/notify_risk/notify_signal")
    assert await bot.notify("📌 smoke: notify"), "notify() вернул False"
    assert await bot.notify_risk("🛡️ smoke: notify_risk"), "notify_risk() вернул False"
    assert await bot.notify_signal("BTCUSDT", "Открыть LONG", 63000.5, reason="smoke"), "notify_signal() вернул False"

    # Дать consumer отправить все
    await asyncio.sleep(2)

    logger.info("📊 Статистика отправки:")
    logger.info(f"   Отправлено: {bot.messages_sent}")
    logger.info(f"   Ошибок: {bot.messages_failed}")
    logger.info(f"   В очереди: {bot.notification_queue.qsize()}")

    # 9) Остановка
    logger.info("🛑 Остановка бота...")
    await bot.stop()
    logger.info("✅ Бот остановлен")

    logger.info("=" * 50)
    logger.info("✅ TELEGRAM SMOKE TEST PASSED")
    logger.info("=" * 50)
    return True

if __name__ == "__main__":
    try:
        asyncio.run(test_telegram_smoke())
    except SystemExit:
        raise
    except Exception as e:
        logger.exception("❌ TELEGRAM SMOKE TEST FAILED: %s", e)
        sys.exit(1)

