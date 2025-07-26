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
import asyncpg
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
ADMIN_IDS = [7353415682]
SUPPORT_ID = "@ReSigncf"
CHANNEL_ID = -1002850774775

# Настройка базы данных PostgreSQL
DB_CONFIG = {
    "user": "signdb_user",
    "password": "fqxpUJ3VUykQtz8CZD4Ghoijpsu0uoWn",
    "database": "signdb",
    "host": "dpg-d21vmg3e5dus73955mj0-a",
    "port": "5432",
    "ssl": "require"
}

# Состояния FSM
class TakeStates(StatesGroup):
    waiting_for_payment = State()
    waiting_for_content = State()
    waiting_for_edit = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_premium_username = State()
    waiting_for_premium_days = State()

# Инициализация бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

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
        KeyboardButton(text="📊 Статистика"),
        KeyboardButton(text="🎁 Выдать премиум"),
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

def get_days_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(*[KeyboardButton(text=str(days)) for days in [1, 3, 7, 14, 30]])
    builder.add(KeyboardButton(text="⬅️ Назад"))
    builder.adjust(3, 2, 1)
    return builder.as_markup(resize_keyboard=True)

# ===================== БАЗА ДАННЫХ =====================
async def init_db():
    """Инициализация базы данных с полной настройкой"""
    conn = await asyncpg.connect(**DB_CONFIG)
    
    try:
        # Основные таблицы
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            payment_id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            amount INTEGER,
            currency TEXT,
            status TEXT,
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS takes (
            take_id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            content_type TEXT,
            content TEXT,
            status TEXT DEFAULT 'pending',
            admin_id BIGINT REFERENCES users(user_id),
            rating_change INTEGER DEFAULT 0,
            submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            takes_count INTEGER DEFAULT 0,
            rating INTEGER DEFAULT 0,
            premium_until TIMESTAMP
        )''')
        
        # Оптимизационные индексы
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_takes_status ON takes(status)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_takes_user_id ON takes(user_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_takes_submission_date ON takes(submission_date)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_user_stats_premium ON user_stats(premium_until)')
        
        # Триггер для автоматического создания статистики
        await conn.execute('''
        CREATE OR REPLACE FUNCTION create_user_stats()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO user_stats (user_id) VALUES (NEW.user_id) 
            ON CONFLICT (user_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql''')
        
        await conn.execute('''
        DROP TRIGGER IF EXISTS trg_create_user_stats ON users;
        CREATE TRIGGER trg_create_user_stats
        AFTER INSERT ON users
        FOR EACH ROW
        EXECUTE FUNCTION create_user_stats()''')
        
        logger.info("База данных успешно инициализирована")
        
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        raise
    finally:
        await conn.close()

async def add_user(user_id: int, username: Optional[str], full_name: str, is_admin: bool = False):
    conn = await asyncpg.connect(**DB_CONFIG)
    try:
        await conn.execute(
            "INSERT INTO users (user_id, username, full_name, is_admin) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO NOTHING",
            user_id, username, full_name, is_admin
        )
    finally:
        await conn.close()

async def get_user_stats(user_id: int) -> Optional[dict]:
    conn = await asyncpg.connect(**DB_CONFIG)
    try:
        return await conn.fetchrow('''
        SELECT u.user_id, u.username, u.full_name, 
               COALESCE(us.takes_count, 0) as takes_count, 
               COALESCE(us.rating, 0) as rating,
               us.premium_until
        FROM users u
        LEFT JOIN user_stats us ON u.user_id = us.user_id
        WHERE u.user_id = $1
        ''', user_id)
    finally:
        await conn.close()

async def get_user_by_username(username: str) -> Optional[dict]:
    conn = await asyncpg.connect(**DB_CONFIG)
    try:
        return await conn.fetchrow(
            "SELECT user_id, username, full_name FROM users WHERE username = $1",
            username
        )
    finally:
        await conn.close()

async def add_take(user_id: int, content_type: str, content: Optional[str]) -> int:
    conn = await asyncpg.connect(**DB_CONFIG)
    try:
        take_id = await conn.fetchval(
            "INSERT INTO takes (user_id, content_type, content) VALUES ($1, $2, $3) RETURNING take_id",
            user_id, content_type, content
        )
        
        await conn.execute('''
        INSERT INTO user_stats (user_id, takes_count, rating) 
        VALUES ($1, 0, 0) ON CONFLICT (user_id) DO NOTHING
        ''', user_id)
        
        await conn.execute('''
        UPDATE user_stats 
        SET takes_count = takes_count + 1 
        WHERE user_id = $1
        ''', user_id)
        
        return take_id
    finally:
        await conn.close()

async def update_take_status(take_id: int, status: str, admin_id: int, rating_change: int = 0):
    conn = await asyncpg.connect(**DB_CONFIG)
    try:
        await conn.execute('''
        UPDATE takes 
        SET status = $1, admin_id = $2, rating_change = $3
        WHERE take_id = $4
        ''', status, admin_id, rating_change, take_id)
        
        if status == 'accepted' and rating_change > 0:
            await conn.execute('''
            UPDATE user_stats 
            SET rating = rating + $1
            WHERE user_id = (SELECT user_id FROM takes WHERE take_id = $2)
            ''', rating_change, take_id)
    finally:
        await conn.close()

async def add_premium(user_id: int, days: int):
    conn = await asyncpg.connect(**DB_CONFIG)
    try:
        current_premium = await conn.fetchval(
            "SELECT premium_until FROM user_stats WHERE user_id = $1",
            user_id
        )
        
        new_date = (current_premium + timedelta(days=days)) if current_premium else datetime.now() + timedelta(days=days)
        
        await conn.execute('''
        INSERT INTO user_stats (user_id, premium_until) 
        VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET premium_until = $2
        ''', user_id, new_date)
        
        try:
            await bot.send_message(
                user_id,
                f"🎉 Вам активирован премиум на {days} дней!\n"
                f"Доступно до: {new_date.strftime('%d.%m.%Y %H:%M')}"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления о премиуме: {e}")
    finally:
        await conn.close()

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    is_admin = user_id in ADMIN_IDS
    
    await add_user(user_id, username, full_name, is_admin)
    
    await message.answer(
        "👋 Добро пожаловать в бота для тейков!\n"
        "Отправляйте ваши мысли, фото и видео через кнопку ниже.",
        reply_markup=get_main_menu(user_id)
    )

@dp.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Ваш ID: `{message.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "⬅️ Назад")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
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

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = await asyncpg.connect(**DB_CONFIG)
    users = await conn.fetch("SELECT user_id FROM users")
    await conn.close()

    success = failed = 0
    for user in users:
        try:
            await bot.copy_message(
                chat_id=user['user_id'],
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            success += 1
        except Exception as e:
            logger.error(f"Ошибка рассылки для {user['user_id']}: {e}")
            failed += 1

    await message.answer(
        f"📊 Результат рассылки:\nУспешно: {success}\nНе удалось: {failed}",
        reply_markup=get_admin_menu()
    )
    await state.clear()

@dp.message(F.text == "🎁 Выдать премиум")
async def give_premium_start(message: Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Введите username пользователя (без @):",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(AdminStates.waiting_for_premium_username)

@dp.message(AdminStates.waiting_for_premium_username)
async def process_premium_username(message: Message, state: FSMContext):
    username = message.text.strip()
    user = await get_user_by_username(username)
    
    if not user:
        await message.answer("Пользователь не найден. Попробуйте еще раз:")
        return
    
    await state.update_data(user_id=user['user_id'], username=username)
    await message.answer(
        f"Пользователь: @{username}\nВыберите количество дней:",
        reply_markup=get_days_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_premium_days)

@dp.message(AdminStates.waiting_for_premium_days)
async def process_premium_days(message: Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer(
            "Админ панель:",
            reply_markup=get_admin_menu()
        )
        return
    
    if not message.text.isdigit():
        await message.answer("Пожалуйста, выберите количество дней из предложенных вариантов:")
        return
    
    days = int(message.text)
    data = await state.get_data()
    user_id = data['user_id']
    username = data['username']
    
    await add_premium(user_id, days)
    await message.answer(
        f"✅ Пользователю @{username} выдан премиум на {days} дней!",
        reply_markup=get_admin_menu()
    )
    await state.clear()

@dp.message(F.text == "📤 Отправить тейк")
async def send_take(message: Message, state: FSMContext):
    user_stats = await get_user_stats(message.from_user.id)
    
    if user_stats and user_stats['premium_until'] and datetime.now() < user_stats['premium_until']:
        await message.answer(
            "Отправьте ваш тейк (текст/фото/видео):",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(TakeStates.waiting_for_content)
    else:
        await message.answer_invoice(
            title="Оплата тейка",
            description="Публикация стоит 15 Stars",
            provider_token="YOUR_PAYMENT_TOKEN",  # Замените на реальный
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
    
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_payment(message: Message, state: FSMContext):
    payment = message.successful_payment
    user_id = message.from_user.id
    
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute(
        "INSERT INTO payments (user_id, amount, currency, status) VALUES ($1, $2, $3, $4)",
        user_id, payment.total_amount, payment.currency, "completed"
    )
    await conn.close()
    
    await add_premium(user_id, 1)
    await message.answer(
        "✅ Оплата прошла успешно! Теперь отправьте ваш тейк:",
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

@dp.message(TakeStates.waiting_for_content, F.text)
async def process_text_take(message: Message, state: FSMContext):
    take_id = await add_take(
        user_id=message.from_user.id,
        content_type="text",
        content=message.text
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📝 Новый тейк (ID: {take_id}):\n\n{message.text}",
                reply_markup=get_take_action_keyboard(take_id)
            )
        except Exception as e:
            logger.error(f"Ошибка отправки тейка админу {admin_id}: {e}")
    
    await message.answer(
        "✅ Тейк отправлен на модерацию!",
        reply_markup=get_main_menu(message.from_user.id)
    )
    await state.clear()

@dp.message(TakeStates.waiting_for_content, F.photo | F.video)
async def process_media_take(message: Message, state: FSMContext):
    content_type = "photo" if message.photo else "video"
    file_id = message.photo[-1].file_id if message.photo else message.video.file_id
    take_id = await add_take(
        user_id=message.from_user.id,
        content_type=content_type,
        content=message.caption
    )
    
    for admin_id in ADMIN_IDS:
        try:
            if content_type == "photo":
                await bot.send_photo(
                    admin_id,
                    photo=file_id,
                    caption=f"📸 Новый тейк (ID: {take_id}):\n\n{message.caption}" if message.caption else None,
                    reply_markup=get_take_action_keyboard(take_id)
                )
            else:
                await bot.send_video(
                    admin_id,
                    video=file_id,
                    caption=f"🎥 Новый тейк (ID: {take_id}):\n\n{message.caption}" if message.caption else None,
                    reply_markup=get_take_action_keyboard(take_id)
                )
        except Exception as e:
            logger.error(f"Ошибка отправки тейка админу {admin_id}: {e}")
    
    await message.answer(
        "✅ Тейк отправлен на модерацию!",
        reply_markup=get_main_menu(message.from_user.id)
    )
    await state.clear()

@dp.callback_query(F.data.startswith("accept_"))
async def accept_take(callback: CallbackQuery):
    take_id = int(callback.data.split("_")[1])
    admin_id = callback.from_user.id
    
    await update_take_status(take_id, "accepted", admin_id, 5)
    
    conn = await asyncpg.connect(**DB_CONFIG)
    take = await conn.fetchrow('''
    SELECT t.user_id, t.content_type, t.content
    FROM takes t
    WHERE t.take_id = $1
    ''', take_id)
    await conn.close()
    
    if take:
        user_id, content_type, content = take
        
        try:
            if content_type == "text":
                await bot.send_message(
                    CHANNEL_ID,
                    f"{content}"
                )
            else:
                file_id = callback.message.photo[-1].file_id if content_type == "photo" else callback.message.video.file_id
                
                if content_
