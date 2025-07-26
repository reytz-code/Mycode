#!/bin/bash
# Установка без сборки из исходников
pip install --upgrade pip
pip install aiohttp==3.8.6 --no-binary :none: --only-binary :all:
pip install -r requirements.txt
python bot.py
