#!/bin/bash

# ===========================================
# СКРИПТ ДЛЯ ИСПРАВЛЕНИЯ ИМПОРТОВ
# ===========================================

echo "🔧 Исправляем импорты в проекте..."

# Функция замены относительных импортов на абсолютные
fix_imports() {
    local file=$1
    echo "Исправляем: $file"
    
    # Заменяем относительные импорты на абсолютные
    sed -i 's/from \.\.exchanges\./from exchanges\./g' "$file"
    sed -i 's/from \.\.config\./from config\./g' "$file"
    sed -i 's/from \.\.core\./from core\./g' "$file"
    sed -i 's/from \.\.telegram\./from telegram\./g' "$file"
    sed -i 's/from \.\.utils\./from utils\./g' "$file"
    
    # Убираем лишние точки
    sed -i 's/from \.\./from /g' "$file"
}

# Исправляем импорты в core/
if [ -d "core" ]; then
    for file in core/*.py; do
        if [ -f "$file" ]; then
            fix_imports "$file"
        fi
    done
fi

# Исправляем импорты в exchanges/
if [ -d "exchanges" ]; then
    for file in exchanges/*.py; do
        if [ -f "$file" ]; then
            fix_imports "$file"
        fi
    done
fi

# Исправляем импорты в config/
if [ -d "config" ]; then
    for file in config/*.py; do
        if [ -f "$file" ]; then
            fix_imports "$file"
        fi
    done
fi

# Исправляем импорты в telegram/
if [ -d "telegram" ]; then
    for file in telegram/*.py; do
        if [ -f "$file" ]; then
            fix_imports "$file"
        fi
    done
fi

echo "✅ Все импорты исправлены!"

# Показываем изменения
echo ""
echo "📋 Измененные файлы:"
find . -name "*.py" -path "./core/*" -o -path "./exchanges/*" -o -path "./config/*" -o -path "./telegram/*" | head -10
