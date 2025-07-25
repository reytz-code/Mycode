import os
import logging
import json
from datetime import datetime, timedelta
from typing import Optional
import asyncio

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
from psycopg2 import sql

from flask import Flask
from threading import Thread
import requests
import time

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Конфигурация с вашими данными
BOT_TOKEN = "7968236729:AAFBi3ma_p43qRQ_O7E9csOoTchJ6K2UlzI"
ADMIN_IDS = [7353415682]
SUPPORT_ID = "@ReSigncf"
CHANNEL_ID = -1002850774775
DATABASE_URL = "postgresql://signdb_user:fqxpUJ3VUykQtz8CZD4Ghoijpsu0uoWn@dpg-d21vmg3e5dus73955mj0-a/signdb"
RENDER_APP_NAME = "Mycode-1"

# Инициализация бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Состояния FSM
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
        """
        CREATE TABLE IF NOT EXISTS payments (
            payment_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount INTEGER,
            currency TEXT,
            status TEXT,
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS takes (
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
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id BIGINT PRIMARY KEY,
            takes_count INTEGER DEFAULT 0,
            rating INTEGER DEFAULT 0,
            premium_until TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """
    )
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for command in commands:
            cursor.execute(command)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def add_user(user_id: int, username: Optional[str], full_name: str, is_admin: bool = False):
    """Добавление нового пользователя"""
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
    """Получение статистики пользователя"""
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
        
        if result:
            return {
                "user_id": result[0],
                "username": result[1],
                "full_name": result[2],
                "takes_count": result[3],
                "rating": result[4],
                "premium_until": result[5]
            }
    except Exception as e:
        logger.error(f"Error getting user stats: {e}")
    return None

def get_user_by_username(username: str) -> Optional[dict]:
    """Поиск пользователя по username"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT user_id, username, full_name 
        FROM users 
        WHERE username = %s
        ''', (username.lower().replace("@", ""),))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            return {
                "user_id": result[0],
                "username": result[1],
                "full_name": result[2]
            }
    except Exception as e:
        logger.error(f"Error getting user by username: {e}")
    return None

async def add_premium(user_id: int, days: int):
    """Добавление премиум-статуса пользователю"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT premium_until FROM user_stats WHERE user_id = %s", (user_id,))
        current_premium = cursor.fetchone()
        
        if current_premium and current_premium[0]:
            new_date = current_premium[0] + timedelta(days=days)
        else:
            new_date = datetime.now() + timedelta(days=days)
        
        cursor.execute('''
        INSERT INTO user_stats (user_id, premium_until) 
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE 
        SET premium_until = %s
        ''', (user_id, new_date, new_date))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        try:
            await bot.send_message(
                user_id,
                f"🎉 Вам активирован премиум на {days} дней!\n"
                f"Доступно до: {new_date.strftime('%d.%m.%Y %H:%M')}"
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка уведомления о премиуме: {e}")
            return False
    except Exception as e:
        logger.error(f"Error adding premium: {e}")
        return False

# ===================== КЛАВИАТУРЫ =====================
def get_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    """Главное меню с учетом прав администратора"""
    is_admin = user_id in ADMIN_IDS
    
    builder = ReplyKeyboardBuilder()
    buttons = [
        KeyboardButton(text="📤 Отправить тейк"),
        KeyboardButton(text="👤 Профиль"),
        KeyboardButton(text="🆘 Поддержка"),
        KeyboardButton(text="🏆 Рейтинг"),
        KeyboardButton(text="📚 Инструкция")
    ]
    
    if is_admin:
        buttons.append(KeyboardButton(text="👑 Админ панель"))
    
    builder.add(*buttons)
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup(resize_keyboard=True)

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Меню администратора"""
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
    """Кнопки действий с тейком для администратора"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{take_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{take_id}"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{take_id}")
    )
    return builder.as_markup()

def get_payment_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для оплаты"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить 15 Stars", pay=True)
    builder.button(text="❌ Отмена", callback_data="cancel_payment")
    builder.adjust(1)
    return builder.as_markup()

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    is_admin = user_id in ADMIN_IDS
    
    add_user(user_id, username, full_name, is_admin)
    
    await message.answer(
        "👋 Добро пожаловать в бота для тейков!\n"
        "Отправляйте ваши мысли, фото и видео через кнопку ниже.",
        reply_markup=get_main_menu(user_id)
    )

@dp.message(Command("id"))
async def cmd_id(message: Message):
    """Показывает ID пользователя"""
    await message.answer(f"Ваш ID: `{message.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "⬅️ Назад")
async def back_to_main(message: Message):
    """Возврат в главное меню"""
    await message.answer(
        "Главное меню:",
        reply_markup=get_main_menu(message.from_user.id)
    )

@dp.message(F.text == "👑 Админ панель")
async def admin_panel(message: Message):
    """Отображение админ-панели"""
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Админ панель:",
            reply_markup=get_admin_menu()
        )
    else:
        await message.answer("🚫 Доступ запрещен")

@dp.message(F.text == "📢 Рассылка")
async def broadcast_menu(message: Message, state: FSMContext):
    """Начало рассылки сообщений"""
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Введите сообщение для рассылки:",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    """Обработка рассылки"""
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error getting users for broadcast: {e}")
        await message.answer("❌ Ошибка при получении списка пользователей")
        await state.clear()
        return

    success = failed = 0
    for user in users:
        try:
            await bot.copy_message(
                chat_id=user[0],
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            success += 1
        except Exception as e:
            logger.error(f"Ошибка рассылки для {user[0]}: {e}")
            failed += 1

    await message.answer(
        f"📊 Результат рассылки:\nУспешно: {success}\nНе удалось: {failed}",
        reply_markup=get_admin_menu()
    )
    await state.clear()

@dp.message(F.text == "🎁 Выдать премиум")
async def give_premium_start(message: Message, state: FSMContext):
    """Начало процесса выдачи премиума"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await message.answer(
        "Введите количество дней премиума:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_premium_days)

@dp.message(AdminStates.waiting_for_premium_days)
async def process_premium_days(message: Message, state: FSMContext):
    """Обработка количества дней премиума"""
    if not message.text.isdigit():
        await message.answer("❌ Введите число дней!")
        return
    
    days = int(message.text)
    if days <= 0:
        await message.answer("❌ Число дней должно быть положительным!")
        return
    
    await state.update_data(days=days)
    await message.answer(
        "Теперь введите username пользователя (без @):",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_premium_username)

@dp.message(AdminStates.waiting_for_premium_username)
async def process_premium_username(message: Message, state: FSMContext):
    """Обработка username и выдача премиума"""
    data = await state.get_data()
    days = data['days']
    username = message.text.strip().replace("@", "")
    
    user = get_user_by_username(username)
    if not user:
        await message.answer(
            f"❌ Пользователь @{username} не найден!",
            reply_markup=get_admin_menu()
        )
        await state.clear()
        return
    
    success = await add_premium(user['user_id'], days)
    if success:
        await message.answer(
            f"✅ Пользователю @{username} успешно выдан премиум на {days} дней!",
            reply_markup=get_admin_menu()
        )
    else:
        await message.answer(
            f"❌ Не удалось выдать премиум пользователю @{username}",
            reply_markup=get_admin_menu()
        )
    
    await state.clear()

@dp.message(F.text == "📤 Отправить тейк")
async def send_take(message: Message, state: FSMContext):
    """Обработчик отправки тейка"""
    user_stats = get_user_stats(message.from_user.id)
    
    if user_stats and user_stats.get('premium_until') and datetime.now() < user_stats['premium_until']:
        await message.answer(
            "Отправьте ваш тейк (текст/фото/видео):",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(TakeStates.waiting_for_content)
    else:
        await message.answer_invoice(
            title="Оплата тейка",
            description="Публикация стоит 15 Stars",
            provider_token="",  # Укажите ваш платежный токен
            currency="XTR",
            prices=[LabeledPrice(label="15 Stars", amount=15)],
            payload="take_payment",
            reply_markup=get_payment_keyboard()
        )
        await state.set_state(TakeStates.waiting_for_payment)

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Проверка платежа"""
    if pre_checkout_query.invoice_payload != "take_payment":
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Неверный платеж"
        )
        return
    
    if pre_checkout_query.total_amount != 15:
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Неверная сумма"
        )
        return
    
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_payment(message: Message, state: FSMContext):
    """Обработка успешного платежа"""
    payment = message.successful_payment
    user_id = message.from_user.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO payments (user_id, amount, currency, status) VALUES (%s, %s, %s, %s)",
            (user_id, payment.total_amount, payment.currency, "completed")
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error saving payment: {e}")
    
    await add_premium(user_id, 1)
    
    await message.answer(
        "✅ Оплата прошла успешно! Теперь отправьте ваш тейк (текст, фото или видео):",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(TakeStates.waiting_for_content)

@dp.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: CallbackQuery, state: FSMContext):
    """Отмена платежа"""
    await callback.message.edit_text("Оплата отменена.")
    await state.clear()
    await callback.message.answer(
        "Главное меню:",
        reply_markup=get_main_menu(callback.from_user.id)
    )

@dp.message(TakeStates.waiting_for_content, F.text | F.photo | F.video)
async def process_take_content(message: Message, state: FSMContext):
    """Обработка контента тейка"""
    user_id = message.from_user.id
    content_type = "text" if message.text else "photo" if message.photo else "video"
    content = message.text or message.caption
    file_id = None
    
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    
    take_id = add_take(user_id, content_type, content, file_id)
    
    if take_id == -1:
        await message.answer("❌ Ошибка при отправке тейка. Попробуйте позже.")
        await state.clear()
        return
    
    # Отправка админам на модерацию
    for admin_id in ADMIN_IDS:
        try:
            if content_type == "text":
                await bot.send_message(
                    admin_id,
                    f"📝 Новый тейк (ID: {take_id}):\n\n{content}",
                    reply_markup=get_take_action_keyboard(take_id)
                )
            else:
                if content_type == "photo":
                    await bot.send_photo(
                        admin_id,
                        photo=file_id,
                        caption=f"📸 Новый тейк (ID: {take_id}):\n\n{content}" if content else None,
                        reply_markup=get_take_action_keyboard(take_id)
                    )
                else:
                    await bot.send_video(
                        admin_id,
                        video=file_id,
                        caption=f"🎥 Новый тейк (ID: {take_id}):\n\n{content}" if content else None,
                        reply_markup=get_take_action_keyboard(take_id)
                    )
        except Exception as e:
            logger.error(f"Ошибка отправки тейка админу {admin_id}: {e}")
    
    await message.answer(
        "✅ Тейк отправлен на модерацию!",
        reply_markup=get_main_menu(user_id)
    )
    await state.clear()

@dp.callback_query(F.data.startswith("accept_"))
async def accept_take(callback: CallbackQuery):
    """Одобрение тейка"""
    take_id = int(callback.data.split("_")[1])
    admin_id = callback.from_user.id
    
    update_take_status(take_id, "accepted", admin_id, 5)
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT t.user_id, t.content_type, t.content, t.file_id
        FROM takes t
        WHERE t.take_id = %s
        ''', (take_id,))
        take = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error fetching take: {e}")
        await callback.message.edit_text("❌ Ошибка при публикации")
        return
    
    if take:
        user_id, content_type, content, file_id = take
        
        try:
            if content_type == "text":
                await bot.send_message(
                    CHANNEL_ID,
                    f"{content}"
                )
            else:
       
