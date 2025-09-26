#!/usr/bin/env python3
"""
Утилита для принудительной очистки и переустановки команд Telegram бота
Запускать отдельно при проблемах с кэшированием меню
"""

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем путь к корню проекта
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from telegram import Bot, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from config.config_loader import config_loader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("ResetCommands")


async def force_reset_commands():
    """Принудительная очистка и установка новых команд"""
    
    try:
        # Загружаем конфигурацию
        telegram_config = config_loader.get_config("telegram")
        bot_config = telegram_config.get("bot", {})
        
        token = (bot_config.get("token") or 
                bot_config.get("bot_token") or 
                telegram_config.get("token"))
        
        chat_id = (bot_config.get("chat_id") or 
                  bot_config.get("channel_id") or 
                  telegram_config.get("chat_id"))
        
        if not token:
            logger.error("❌ Токен не найден")
            return False
        
        logger.info(f"🔧 Подключение к боту...")
        bot = Bot(token=token)
        
        # Проверяем подключение
        bot_info = await bot.get_me()
        logger.info(f"✅ Подключен к @{bot_info.username}")
        
        # Шаг 1: Очищаем ВСЕ старые команды для всех возможных scopes
        logger.info("🗑️ Удаление всех старых команд...")
        
        # Глобальные команды
        await bot.delete_my_commands()
        logger.info("  • Удалены глобальные команды")
        
        # Для разных языков
        for lang in [None, "ru", "en", "es", "de", "fr"]:
            try:
                await bot.delete_my_commands(language_code=lang)
                if lang:
                    logger.info(f"  • Удалены команды для языка: {lang}")
            except:
                pass
        
        # Для конкретного чата (если указан)
        if chat_id:
            try:
                scope = BotCommandScopeChat(chat_id=int(chat_id))
                await bot.delete_my_commands(scope=scope)
                logger.info(f"  • Удалены команды для чата: {chat_id}")
            except:
                pass
        
        logger.info("✅ Все старые команды удалены")
        
        # Ждём немного для синхронизации
        await asyncio.sleep(1)
        
        # Шаг 2: Устанавливаем новые команды Vortex Trading Bot
        logger.info("📋 Установка новых команд...")
        
        commands = [
            BotCommand("start", "🚀 Запуск бота"),
            BotCommand("status", "📊 Статус системы"),
            BotCommand("balance", "💰 Баланс аккаунта"),
            BotCommand("positions", "📈 Открытые позиции"),
            BotCommand("mode", "⚙️ Режим работы"),
            BotCommand("risk", "🛡️ Статус рисков"),
            BotCommand("risk_show", "📋 Детальные лимиты"),
            BotCommand("risk_set", "⚙️ Изменить лимит"),
            BotCommand("risk_enable", "🔄 Вкл/выкл риски"),
            BotCommand("risk_reset", "🔄 Сброс счетчиков"),
            BotCommand("help", "🆘 Помощь")
        ]
        
        # Устанавливаем глобально
        await bot.set_my_commands(commands)
        logger.info(f"✅ Установлено {len(commands)} команд глобально")
        
        # Устанавливаем описания
        await bot.set_my_description(
            "Vortex Trading Bot v2.1 - Профессиональный торговый бот с управлением рисками"
        )
        await bot.set_my_short_description(
            "Торговый бот с Vortex Bands стратегией"
        )
        logger.info("✅ Установлены описания бота")
        
        # Шаг 3: Проверяем результат
        current_commands = await bot.get_my_commands()
        logger.info(f"\n📋 Текущие команды бота:")
        for cmd in current_commands:
            logger.info(f"  • /{cmd.command} - {cmd.description}")
        
        logger.info("\n✅ Команды успешно обновлены!")
        logger.info("\n⚠️ ВАЖНО:")
        logger.info("1. Перезапустите Telegram на всех устройствах")
        logger.info("2. Или очистите кэш в настройках Telegram")
        logger.info("3. В чате с ботом нажмите '/' для обновления меню")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def main():
    success = await force_reset_commands()
    
    if not success:
        logger.error("\n❌ Не удалось обновить команды")
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())