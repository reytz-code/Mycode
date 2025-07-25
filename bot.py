import os
import logging
import json
from datetime import datetime, timedelta
from typing import Optional
import asyncio
from threading import Thread
import requests
import time

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import psycopg2
from flask import Flask

# ===================== КОНФИГУРАЦИЯ =====================
BOT_TOKEN = "7968236729:AAFBi3ma_p43qRQ_O7E9csOoTchJ6K2UlzI"
ADMIN_IDS = [7353415682]
SUPPORT_ID = "@ReSigncf"
CHANNEL_ID = -1002850774775
DATABASE_URL = "postgresql://signdb_user:fqxpUJ3VUykQtz8CZD4Ghoijpsu0uoWn@dpg-d21vmg3e5dus73955mj0-a/signdb"
RENDER_APP_NAME = "Mycode-1"
PORT = 10000

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ===================== МОДЕЛИ СОСТОЯНИЙ =====================
class TakeStates(StatesGroup):
    waiting_for_payment = State()
    waiting_for_content = State()
    waiting_for_edit = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_premium_days = State()
    waiting_for_premium_username = State()

# ===================== БАЗА ДАННЫХ =====================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """Инициализация таблиц в базе данных"""
    commands = (
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """CREATE TABLE IF NOT EXISTS payments (
            payment_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount INTEGER,
            currency TEXT,
            status TEXT,
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS takes (
            take_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            content_type TEXT,
            content TEXT,
            file_id TEXT,
            status TEXT DEFAULT 'pending',
            admin_id BIGINT,
            rating_change INTEGER DEFAULT 0,
            submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (admin_id) REFERENCES users (user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS user_stats (
            user_id BIGINT PRIMARY KEY,
            takes_count INTEGER DEFAULT 0,
            rating INTEGER DEFAULT 0,
            premium_until TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )"""
    )
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for command in commands:
            cursor.execute(command)
        conn.commit()
        cursor.close()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def add_user(user_id: int, username: Optional[str], full_name: str, is_admin: bool = False):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (user_id, username, full_name, is_admin) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, username, full_name, is_admin)
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error adding user: {e}")

def get_user_stats(user_id: int) -> Optional[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT u.user_id, u.username, u.full_name, 
               COALESCE(us.takes_count, 0) as takes_count, 
               COALESCE(us.rating, 0) as rating,
               us.premium_until
        FROM users u
        LEFT JOIN user_stats us ON u.user_id = us.user_id
        WHERE u.user_id = %s
        ''', (user_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return {
            "user_id": result[0],
            "username": result[1],
            "full_name": result[2],
            "takes_count": result[3],
            "rating": result[4],
            "premium_until": result[5]
        } if result else None
    except Exception as e:
        logger.error(f"Error getting user stats: {e}")
        return None

def get_user_by_username(username: str) -> Optional[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT user_id, username, full_name FROM users WHERE username = %s
        ''', (username.lower().replace("@", ""),))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return {
            "user_id": result[0],
            "username": result[1],
            "full_name": result[2]
        } if result else None
    except Exception as e:
        logger.error(f"Error getting user by username: {e}")
        return None

async def add_premium(user_id: int, days: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT premium_until FROM user_stats WHERE user_id = %s", (user_id,))
        current_premium = cursor.fetchone()
        
        new_date = (current_premium[0] + timedelta(days=days)) if current_premium and current_premium[0] else datetime.now() + timedelta(days=days)
        
        cursor.execute('''
        INSERT INTO user_stats (user_id, premium_until) 
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE 
        SET premium_until = %s
        ''', (user_id, new_date, new_date))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        await bot.send_message(
            user_id,
            f"🎉 Вам активирован премиум на {days} дней!\nДоступно до: {new_date.strftime('%d.%m.%Y %H:%M')}"
        )
        return True
    except Exception as e:
        logger.error(f"Error adding premium: {e}")
        return False

# ===================== КЛАВИАТУРЫ =====================
def get_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    buttons = [
        KeyboardButton(text="📤 Отправить тейк"),
        KeyboardButton(text="👤 Профиль"),
        KeyboardButton(text="🆘 Поддержка"),
        KeyboardButton(text="🏆 Рейтинг"),
        KeyboardButton(text="📚 Инструкция")
    ]
    
    if user_id in ADMIN_IDS:
        buttons.append(KeyboardButton(text="👑 Админ панель"))
    
    builder.add(*buttons)
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup(resize_keyboard=True)

def get_admin_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(
        KeyboardButton(text="📢 Рассылка"),
        KeyboardButton(text="🎁 Выдать премиум"),
        KeyboardButton(text="📊 Статистика"),
        KeyboardButton(text="⬅️ Назад")
    )
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_take_action_keyboard(take_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{take_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{take_id}"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{take_id}")
    )
    return builder.as_markup()

def get_payment_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить 15 Stars", pay=True)
    builder.button(text="❌ Отмена", callback_data="cancel_payment")
    builder.adjust(1)
    return builder.as_markup()

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.full_name, user_id in ADMIN_IDS)
    await message.answer(
        "👋 Добро пожаловать в бота для тейков!\nОтправляйте ваши мысли, фото и видео через кнопку ниже.",
        reply_markup=get_main_menu(user_id)
    )

@dp.message(F.text == "👑 Админ панель")
async def admin_panel(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Админ панель:", reply_markup=get_admin_menu())
    else:
        await message.answer("🚫 Доступ запрещен")

@dp.message(F.text == "🎁 Выдать премиум")
async def give_premium_start(message: Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Введите количество дней премиума:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(AdminStates.waiting_for_premium_days)

@dp.message(AdminStates.waiting_for_premium_days)
async def process_premium_days(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введите число дней!")
        return
    
    days = int(message.text)
    if days <= 0:
        await message.answer("❌ Число дней должно быть положительным!")
        return
    
    await state.update_data(days=days)
    await message.answer("Теперь введите username пользователя (без @):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AdminStates.waiting_for_premium_username)

@dp.message(AdminStates.waiting_for_premium_username)
async def process_premium_username(message: Message, state: FSMContext):
    data = await state.get_data()
    days = data['days']
    username = message.text.strip().replace("@", "")
    
    if user := get_user_by_username(username):
        if await add_premium(user['user_id'], days):
            await message.answer(f"✅ Пользователю @{username} выдан премиум на {days} дней!", reply_markup=get_admin_menu())
        else:
            await message.answer(f"❌ Не удалось выдать премиум", reply_markup=get_admin_menu())
    else:
        await message.answer(f"❌ Пользователь @{username} не найден!", reply_markup=get_admin_menu())
    await state.clear()

# [Другие обработчики команд...]

# ===================== WEB SERVER =====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    app.run(host='0.0.0.0', port=PORT)

def keep_alive():
    Thread(target=run_web_server, daemon=True).start()
    Thread(target=lambda: [time.sleep(300), requests.get(f"https://{RENDER_APP_NAME}.onrender.com") for _ in iter(int, 1)], daemon=True).start()

# ===================== ЗАПУСК =====================
async def main():
    init_db()
    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    logging.info("Starting application...")
    asyncio.run(main())
