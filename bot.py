import os
import logging
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
from typing import Optional
import sqlite3
from datetime import datetime, timedelta
import asyncio

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Конфигурация бота
BOT_TOKEN = "7968236729:AAFBi3ma_p43qRQ_O7E9csOoTchJ6K2UlzI"
ADMIN_IDS = [7353415682]  # ID администраторов
SUPPORT_ID = "@Oxoxece"  # Новый ник поддержки
CHANNEL_ID = -1002850774775  # ID канала

# Настройка базы данных
DB_NAME = "bot_database.db"

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        is_admin BOOLEAN DEFAULT FALSE,
        join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        currency TEXT,
        status TEXT,
        payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS takes (
        take_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        content_type TEXT,
        content TEXT,
        media_id TEXT,  # Изменил media_path на media_id
        status TEXT DEFAULT 'pending',
        admin_id INTEGER,
        rating_change INTEGER DEFAULT 0,
        submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id),
        FOREIGN KEY (admin_id) REFERENCES users (user_id)
    )''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_stats (
        user_id INTEGER PRIMARY KEY,
        takes_count INTEGER DEFAULT 0,
        rating INTEGER DEFAULT 0,
        premium_until TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

# Состояния FSM
class TakeStates(StatesGroup):
    waiting_for_payment = State()
    waiting_for_content = State()
    waiting_for_edit = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_premium_user = State()
    waiting_for_premium_days = State()

# Инициализация бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ===================== КЛАВИАТУРЫ =====================
def get_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    """Главное меню"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
    is_admin = cursor.fetchone()
    conn.close()
    
    builder = ReplyKeyboardBuilder()
    buttons = [
        KeyboardButton(text="📤 Отправить тейк"),
        KeyboardButton(text="👤 Профиль"),
        KeyboardButton(text="🆘 Поддержка"),
        KeyboardButton(text="🏆 Рейтинг"),
        KeyboardButton(text="📚 Инструкция")
    ]
    
    if is_admin and is_admin[0]:
        buttons.append(KeyboardButton(text="👑 Админ панель"))
    
    builder.add(*buttons)
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup(resize_keyboard=True)

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Меню администратора"""
    builder = ReplyKeyboardBuilder()
    builder.add(
        KeyboardButton(text="📢 Рассылка"),
        KeyboardButton(text="📊 Статистика"),
        KeyboardButton(text="🎁 Выдать премиум"),
        KeyboardButton(text="⬅️ Назад")
    )
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_take_action_keyboard(take_id: int) -> InlineKeyboardMarkup:
    """Кнопки модерации"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{take_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{take_id}"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{take_id}")
    )
    return builder.as_markup()

def get_payment_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура оплаты"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить 15 Stars", pay=True)
    builder.button(text="❌ Отмена", callback_data="cancel_payment")
    builder.adjust(1)
    return builder.as_markup()

# ===================== БАЗА ДАННЫХ =====================
def add_user(user_id: int, username: Optional[str], full_name: str, is_admin: bool = False):
    """Добавление пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name, is_admin) VALUES (?, ?, ?, ?)",
        (user_id, username, full_name, is_admin)
    )
    conn.commit()
    conn.close()

def get_user_stats(user_id: int) -> Optional[dict]:
    """Получение статистики"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT u.user_id, u.username, u.full_name, 
           COALESCE(us.takes_count, 0) as takes_count, 
           COALESCE(us.rating, 0) as rating,
           us.premium_until
    FROM users u
    LEFT JOIN user_stats us ON u.user_id = us.user_id
    WHERE u.user_id = ?
    ''', (user_id,))
    result = cursor.fetchone()
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
    return None

def add_take(user_id: int, content_type: str, content: Optional[str], media_id: Optional[str] = None) -> int:
    """Добавление тейка"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO takes (user_id, content_type, content, media_id) VALUES (?, ?, ?, ?)",
        (user_id, content_type, content, media_id)
    )
    take_id = cursor.lastrowid
    
    cursor.execute('''
    INSERT OR IGNORE INTO user_stats (user_id, takes_count, rating) 
    VALUES (?, 0, 0)
    ''', (user_id,))
    
    cursor.execute('''
    UPDATE user_stats 
    SET takes_count = takes_count + 1 
    WHERE user_id = ?
    ''', (user_id,))
    
    conn.commit()
    conn.close()
    return take_id

def update_take_status(take_id: int, status: str, admin_id: int, rating_change: int = 0):
    """Обновление статуса тейка"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
    UPDATE takes 
    SET status = ?, admin_id = ?, rating_change = ?
    WHERE take_id = ?
    ''', (status, admin_id, rating_change, take_id))
    
    if status == 'accepted' and rating_change > 0:
        cursor.execute('''
        UPDATE user_stats 
        SET rating = rating + ?
        WHERE user_id = (SELECT user_id FROM takes WHERE take_id = ?)
        ''', (rating_change, take_id))
    
    conn.commit()
    conn.close()

async def add_premium(user_id: int, days: int):
    """Добавление премиума"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT premium_until FROM user_stats WHERE user_id = ?", (user_id,))
    current_premium = cursor.fetchone()
    
    new_date = (datetime.strptime(current_premium[0], "%Y-%m-%d %H:%M:%S") + timedelta(days=days) 
               if current_premium and current_premium[0] 
               else datetime.now() + timedelta(days=days))
    
    cursor.execute('''
    INSERT OR IGNORE INTO user_stats (user_id, premium_until) 
    VALUES (?, ?)
    ''', (user_id, new_date.strftime("%Y-%m-%d %H:%M:%S")))
    
    cursor.execute('''
    UPDATE user_stats 
    SET premium_until = ?
    WHERE user_id = ?
    ''', (new_date.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    
    conn.commit()
    conn.close()
    
    try:
        await bot.send_message(
            user_id,
            f"🎉 Вам активирован премиум на {days} дней!\n"
            f"Доступно до: {new_date.strftime('%d.%m.%Y %H:%M')}"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления о премиуме: {e}")

# ===================== ОБРАБОТЧИКИ =====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик старта"""
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
    await message.answer(
        "Главное меню:",
        reply_markup=get_main_menu(message.from_user.id)
    )

@dp.message(F.text == "👑 Админ панель")
async def admin_panel(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Админ панель:",
            reply_markup=get_admin_menu()
        )
    else:
        await message.answer("🚫 Доступ запрещен")

@dp.message(F.text == "📢 Рассылка")
async def broadcast_menu(message: Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Введите сообщение для рассылки:",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(F.text == "🎁 Выдать премиум")
async def give_premium_menu(message: Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Введите ID пользователя для выдачи премиума:",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(AdminStates.waiting_for_premium_user)

@dp.message(AdminStates.waiting_for_premium_user)
async def process_premium_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await state.update_data(user_id=user_id)
        await message.answer(
            "Введите количество дней премиума:",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(AdminStates.waiting_for_premium_days)
    except ValueError:
        await message.answer("Некорректный ID пользователя. Попробуйте снова:")

@dp.message(AdminStates.waiting_for_premium_days)
async def process_premium_days(message: Message, state: FSMContext):
    try:
        days = int(message.text)
        if days <= 0:
            raise ValueError
        
        data = await state.get_data()
        user_id = data['user_id']
        
        await add_premium(user_id, days)
        await message.answer(
            f"✅ Пользователю {user_id} выдан премиум на {days} дней",
            reply_markup=get_admin_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("Некорректное количество дней. Введите целое положительное число:")

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

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

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 Доступ запрещен")
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM takes")
    total_takes = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM takes WHERE status = 'accepted'")
    accepted_takes = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM takes WHERE status = 'rejected'")
    rejected_takes = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_stats WHERE premium_until > datetime('now')")
    premium_users = cursor.fetchone()[0]
    
    # Топ пользователей по рейтингу
    cursor.execute('''
    SELECT u.user_id, u.username, u.full_name, us.rating 
    FROM users u
    JOIN user_stats us ON u.user_id = us.user_id
    ORDER BY us.rating DESC
    LIMIT 10
    ''')
    top_users = cursor.fetchall()
    
    conn.close()
    
    stats_text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"📤 Всего тейков: <b>{total_takes}</b>\n"
        f"✅ Одобрено: <b>{accepted_takes}</b>\n"
        f"❌ Отклонено: <b>{rejected_takes}</b>\n"
        f"⭐ Премиум пользователей: <b>{premium_users}</b>\n\n"
        "🏆 <b>Топ пользователей:</b>\n"
    )
    
    for i, user in enumerate(top_users, 1):
        username = f"@{user[1]}" if user[1] else user[2]
        stats_text += f"{i}. {username}: {user[3]} ★\n"
    
    await message.answer(stats_text)

@dp.message(F.text == "📤 Отправить тейк")
async def send_take(message: Message, state: FSMContext):
    """Обработчик отправки тейка"""
    user_stats = get_user_stats(message.from_user.id)
    
    if user_stats and user_stats.get('premium_until') and datetime.now() < datetime.strptime(user_stats['premium_until'], "%Y-%m-%d %H:%M:%S"):
        await message.answer(
            "Отправьте ваш тейк (текст/фото/видео):",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(TakeStates.waiting_for_content)
    else:
        await message.answer_invoice(
            title="Оплата тейка",
            description="Публикация стоит 15 Stars",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="15 Stars", amount=15)],
            payload="take_payment",
            reply_markup=get_payment_keyboard()
        )
        await state.set_state(TakeStates.waiting_for_payment)

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
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
    payment = message.successful_payment
    user_id = message.from_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO payments (user_id, amount, currency, status) VALUES (?, ?, ?, ?)",
        (user_id, payment.total_amount, payment.currency, "completed")
    )
    conn.commit()
    conn.close()
    
    await add_premium(user_id, 1)
    
    await message.answer(
        "✅ Оплата прошла успешно! Теперь отправьте ваш тейк (текст, фото или видео):",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(TakeStates.waiting_for_content)

@dp.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: CallbackQuery, state: FSMContext):
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
    media_id = None
    
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.video:
        media_id = message.video.file_id
    
    take_id = add_take(user_id, content_type, content, media_id)
    
    # Отправка админам на модерацию
    for admin_id in ADMIN_IDS:
        try:
            if content_type == "text":
                await bot.send_message(
                    admin_id,
                    f"📝 Новый тейк (ID: {take_id}):\n\n{content}",
                    reply_markup=get_take_action_keyboard(take_id)
                )
            elif content_type == "photo":
                await bot.send_photo(
                    admin_id,
                    photo=media_id,
                    caption=f"📸 Новый тейк (ID: {take_id}):\n\n{content}" if content else None,
                    reply_markup=get_take_action_keyboard(take_id)
                )
            elif content_type == "video":
                await bot.send_video(
                    admin_id,
                    video=media_id,
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
    """Одобрение тейка без имени автора"""
    take_id = int(callback.data.split("_")[1])
    admin_id = callback.from_user.id
    
    update_take_status(take_id, "accepted", admin_id, 5)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT t.user_id, t.content_type, t.content, t.media_id
    FROM takes t
    WHERE t.take_id = ?
    ''', (take_id,))
    take = cursor.fetchone()
    conn.close()
    
    if take:
        user_id, content_type, content, media_id = take
        
        try:
            if content_type == "text":
                await bot.send_message(
                    CHANNEL_ID,
                    f"{content}"  # Только контент
                )
            elif content_type == "photo":
                await bot.send_photo(
                    CHANNEL_ID,
                    photo=media_id,
                    caption=content if content else None  # Без упоминания автора
                )
            elif content_type == "video":
                await bot.send_video(
                    CHANNEL_ID,
                    video=media_id,
                    caption=content if content else None  # Без упоминания автора
                )
            
            # Уведомление автору
            await bot.send_message(
                user_id,
                "🎉 Ваш тейк одобрен! +5 к рейтингу!"
            )
        except Exception as e:
            logger.error(f"Ошибка публикации тейка: {e}")
    
    await callback.message.edit_text("✅ Тейк опубликован")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_take(callback: CallbackQuery):
    take_id = int(callback.data.split("_")[1])
    admin_id = callback.from_user.id
    
    update_take_status(take_id, "rejected", admin_id)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM takes WHERE take_id = ?", (take_id,))
    user_id = cursor.fetchone()[0]
    conn.close()
    
    try:
        await bot.send_message(
            user_id,
            "❌ Ваш тейк был отклонен модератором."
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя: {e}")
    
    await callback.message.edit_text("❌ Тейк отклонен")

@dp.callback_query(F.data.startswith("edit_"))
async def edit_take(callback: CallbackQuery, state: FSMContext):
    take_id = int(callback.data.split("_")[1])
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM takes WHERE take_id = ?", (take_id,))
    take_content = cursor.fetchone()[0]
    conn.close()
    
    await callback.message.answer(
        f"Текущий текст тейка:\n\n{take_content}\n\nОтправьте новый текст:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(TakeStates.waiting_for_edit)
    await state.update_data(take_id=take_id)

@dp.message(TakeStates.waiting_for_edit)
async def process_edited_take(message: Message, state: FSMContext):
    data = await state.get_data()
    take_id = data['take_id']
    admin_id = message.from_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE takes SET content = ?, status = 'accepted', admin_id = ?, rating_change = 5 WHERE take_id = ?",
        (message.text, admin_id, take_id)
    )
    
    cursor.execute('''
    UPDATE user_stats 
    SET rating = rating + 5
    WHERE user_id = (SELECT user_id FROM takes WHERE take_id = ?)
    ''', (take_id,))
    
    cursor.execute('''
    SELECT t.user_id, t.content_type, t.media_id
    FROM takes t
    WHERE t.take_id = ?
    ''', (take_id,))
    take_info = cursor.fetchone()
    conn.close()
    
    if take_info:
        user_id, content_type, media_id = take_info
        
        try:
            if content_type == "text":
                await bot.send_message(
                    CHANNEL_ID,
                    message.text
                )
            elif content_type == "photo":
                await bot.send_photo(
                    CHANNEL_ID,
                    photo=media_id,
                    caption=message.text if message.text else None
                )
            elif content_type == "video":
                await bot.send_video(
                    CHANNEL_ID,
                    video=media_id,
                    caption=message.text if message.text else None
                )
            
            await bot.send_message(
                user_id,
                "🎉 Ваш отредактированный тейк опубликован! +5 к рейтингу!"
            )
        except Exception as e:
            logger.error(f"Ошибка публикации отредактированного тейка: {e}")
    
    await message.answer(
        "✅ Тейк отредактирован и опубликован!",
        reply_markup=get_admin_menu() if admin_id in ADMIN_IDS else get_main_menu(user_id)
    )
    await state.clear()

@dp.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    user_stats = get_user_stats(message.from_user.id)
    if not user_stats:
        await message.answer("Профиль не найден")
        return
    
    premium_status = "✅ Активен" if user_stats['premium_until'] and datetime.now() < datetime.strptime(user_stats['premium_until'], "%Y-%m-%d %H:%M:%S") else "❌ Не активен"
    
    profile_text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user_stats['user_id']}</code>\n"
        f"📛 Имя: {user_stats['full_name']}\n"
        f"🌟 Рейтинг: {user_stats['rating']} ★\n"
        f"📤 Тейков отправлено: {user_stats['takes_count']}\n"
        f"💎 Премиум: {premium_status}\n"
    )
    
    if user_stats['premium_until'] and datetime.now() < datetime.strptime(user_stats['premium_until'], "%Y-%m-%d %H:%M:%S"):
        premium_until = datetime.strptime(user_stats['premium_until'], "%Y-%m-%d %H:%M:%S")
        profile_text += f"⏳ Премиум до: {premium_until.strftime('%d.%m.%Y %H:%M')}\n"
    
    profile_text += f"\n🆘 Поддержка: {SUPPORT_ID}"
    
    await message.answer(profile_text)

@dp.message(F.text == "🆘 Поддержка")
async def show_support(message: Message):
    await message.answer(
        f"По всем вопросам обращайтесь к поддержке: {SUPPORT_ID}\n"
        "Мы всегда готовы помочь!"
    )

@dp.message(F.text == "🏆 Рейтинг")
async def show_rating(message: Message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT u.user_id, u.username, u.full_name, us.rating 
    FROM users u
    JOIN user_stats us ON u.user_id = us.user_id
    ORDER BY us.rating DESC
    LIMIT 10
    ''')
    top_users = cursor.fetchall()
    
    cursor.execute('''
    SELECT COUNT(*) FROM user_stats WHERE rating > 0
    ''')
    total_rated = cursor.fetchone()[0]
    conn.close()
    
    rating_text = "🏆 <b>Топ пользователей по рейтингу</b>\n\n"
    
    for i, user in enumerate(top_users, 1):
        username = f"@{user[1]}" if user[1] else user[2]
        rating_text += f"{i}. {username}: {user[3]} ★\n"
    
    rating_text += f"\nВсего пользователей с рейтингом: {total_rated}"
    
    await message.answer(rating_text)

@dp.message(F.text == "📚 Инструкция")
async def show_instructions(message: Message):
    instructions = (
        "📚 <b>Инструкция по использованию бота</b>\n\n"
        "1. <b>Отправка тейков</b>\n"
        "Используйте кнопку '📤 Отправить тейк' для публикации вашего контента (текст, фото или видео)\n\n"
        "2. <b>Рейтинг</b>\n"
        "За каждый одобренный тейк вы получаете +5 к рейтингу\n\n"
        "3. <b>Премиум</b>\n"
        "С премиумом вы можете публиковать тейки без ограничений\n\n"
        "4. <b>Поддержка</b>\n"
        f"По любым вопросам обращайтесь к {SUPPORT_ID}"
    )
    await message.answer(instructions)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
