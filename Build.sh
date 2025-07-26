#!/usr/bin/env bash
set -e

# Установка системных зависимостей
apt-get update
apt-get install -y python3-dev build-essential libssl-dev libffi-dev

# Установка Python-зависимостей
pip install --upgrade pip
pip install --no-cache-dir -r requirements.txt
