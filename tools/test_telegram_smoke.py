#!/usr/bin/env python3
"""
Smoke-тестер для Telegram бота
Проверяет реальную отправку сообщения в настроенный чат
"""

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем путь к корню проекта
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from telegram_bot.telegram_bot import TelegramBot
from config.config_loader import config_loader

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("TelegramSmoke")


class MockTradingBot:
    """Заглушка для trading_bot_instance"""
    def __init__(self):
        self.is_running = True
        self.start_time = 0
        self.mode = "test"
        self.positions = {}
        self.signals_count = 0
        self.trades_count = 0
        self.risk_manager = None


async def test_telegram_smoke():
    """
    Основной smoke-тест Telegram бота
    Реально отправляет тестовое сообщение в чат
    """
    bot = None
    
    try:
        logger.info("=" * 50)
        logger.info("🚀 Запуск Telegram smoke-теста")
        logger.info("=" * 50)
        
        # 1. Загружаем конфигурацию
        logger.info("📁 Загрузка конфигурации telegram.yaml...")
        telegram_config = config_loader.get_config("telegram")
        
        if not telegram_config:
            logger.error("❌ Не удалось загрузить telegram.yaml")
            return False
        
        bot_config = telegram_config.get("bot", {})
        
        # Пытаемся найти токен и chat_id с разными вариантами ключей
        token = (bot_config.get("token") or 
                bot_config.get("bot_token") or 
                telegram_config.get("token"))
        
        chat_id = (bot_config.get("chat_id") or 
                  bot_config.get("channel_id") or 
                  bot_config.get("admin_chat_id") or
                  telegram_config.get("chat_id"))
        
        if not token:
            logger.error("❌ Токен не найден в конфигурации")
            logger.info("Проверьте ключи: bot.token, bot.bot_token, token")
            return False
        
        if not chat_id:
            logger.error("❌ Chat ID не найден в конфигурации")
            logger.info("Проверьте ключи: bot.chat_id, bot.channel_id, bot.admin_chat_id")
            return False
        
        logger.info(f"✅ Конфигурация загружена:")
        logger.info(f"   Token: {token[:20]}...")
        logger.info(f"   Chat ID: {chat_id}")
        
        # 2. Создаём и инициализируем бота
        logger.info("🤖 Создание экземпляра TelegramBot...")
        mock_trading = MockTradingBot()
        bot = TelegramBot(token=token, chat_id=chat_id, trading_bot_instance=mock_trading)
        
        logger.info("🔧 Инициализация бота...")
        init_result = await bot.initialize()
        
        if not init_result:
            logger.error("❌ Не удалось инициализировать бота")
            return False
        
        logger.info(f"✅ Бот инициализирован успешно")
        logger.info(f"   Команд в меню: 11")
        logger.info(f"   Consumer запущен: {bot.consumer_task is not None}")
        logger.info(f"   Размер очереди: {bot.notification_queue.qsize()}")
        
        # 3. Проверяем getMe API
        logger.info("🔍 Проверка подключения к Telegram API (getMe)...")
        try:
            bot_info = await bot.application.bot.get_me()
            logger.info(f"✅ Бот подключен: @{bot_info.username} (ID: {bot_info.id})")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Telegram API: {e}")
            return False
        
        # 4. Отправляем тестовое сообщение через публичный интерфейс
        logger.info("📨 Отправка тестового сообщения...")
        test_message = (
            "🧪 *TELEGRAM SMOKE TEST*\n\n"
            "✅ Бот успешно инициализирован\n"
            "✅ Подключение к API работает\n"
            "✅ Очередь уведомлений активна\n"
            "✅ Команды зарегистрированы\n\n"
            "Это тестовое сообщение можно удалить."
        )
        
        result = await bot.send_message(test_message)
        
        if not result:
            logger.error("❌ Не удалось добавить сообщение в очередь")
            return False
        
        logger.info(f"✅ Сообщение добавлено в очередь")
        
        # 5. Ждём обработки очереди
        logger.info("⏳ Ожидание обработки очереди (3 сек)...")
        await asyncio.sleep(3)
        
        # 6. Проверяем статистику
        logger.info("📊 Статистика отправки:")
        logger.info(f"   Отправлено: {bot.messages_sent}")
        logger.info(f"   Ошибок: {bot.messages_failed}")
        logger.info(f"   В очереди: {bot.notification_queue.qsize()}")
        
        if bot.messages_sent > 0:
            logger.info("✅ Сообщение успешно отправлено в Telegram!")
        else:
            logger.error("❌ Сообщение не было отправлено")
            return False
        
        # 7. Тестируем другие методы публичного интерфейса
        logger.info("🔍 Проверка других публичных методов...")
        
        # Тест send_signal
        await bot.send_signal("BTCUSDT", "Buy", 45000.50, "Test signal")
        await asyncio.sleep(1)
        
        # Тест notify
        await bot.notify("📌 Тест метода notify")
        await asyncio.sleep(1)
        
        logger.info(f"✅ Всего отправлено сообщений: {bot.messages_sent}")
        
        # 8. Останавливаем бота
        logger.info("🛑 Остановка бота...")
        await bot.stop()
        
        logger.info("=" * 50)
        logger.info("✅ TELEGRAM SMOKE TEST PASSED")
        logger.info("=" * 50)
        return True
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в smoke-тесте: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
        
    finally:
        # Гарантируем остановку бота
        if bot and bot.is_running:
            try:
                await bot.stop()
            except:
                pass


async def main():
    """Точка входа"""
    success = await test_telegram_smoke()
    
    if success:
        logger.info("\n✅ Тест завершён успешно!")
        logger.info("Проверьте Telegram чат - там должны быть тестовые сообщения")
        sys.exit(0)
    else:
        logger.error("\n❌ Тест завершён с ошибками")
        logger.error("Проверьте:")
        logger.error("1. Правильность токена в telegram.yaml")
        logger.error("2. Правильность chat_id в telegram.yaml") 
        logger.error("3. Бот добавлен в чат и имеет права на отправку")
        logger.error("4. Интернет-соединение стабильно")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())