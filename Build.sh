#!/bin/bash
# Установка зависимостей
python -m pip install --upgrade pip
pip install wheel setuptools
pip install --no-cache-dir --prefer-binary -r requirements.txt

# Запуск бота
python bot.py
