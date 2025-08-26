import logging
import sqlite3
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler, Filters
import secrets
from config import BOT_TOKEN, CHANNEL_USERNAME, ADMIN_IDS

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Получаем абсолютный путь к базе данных
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'bot.db')

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        referrals INTEGER DEFAULT 0,
        stars INTEGER DEFAULT 0,
        ref_code TEXT UNIQUE,
        referrer_id INTEGER,
        joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица промокодов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY,
        activations_left INTEGER,
        stars INTEGER,
        created_by INTEGER,
        created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица использованных промокодов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS used_promo_codes (
        user_id INTEGER,
        code TEXT,
        used_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, code)
    )
    ''')
    
    conn.commit()
    conn.close()

# Функции для работы с базой данных
def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'username': user[1],
            'full_name': user[2],
            'referrals': user[3],
            'stars': user[4],
            'ref_code': user[5],
            'referrer_id': user[6]
        }
    return None

def create_user(user_id, username, full_name, ref_code, referrer_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
        INSERT INTO users (user_id, username, full_name, ref_code, referrer_id)
        VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, ref_code, referrer_id))
        
        # Если есть реферер, начисляем ему звёзды
        if referrer_id:
            cursor.execute('UPDATE users SET referrals = referrals + 1, stars = stars + 2 WHERE user_id = ?', (referrer_id,))
        
        conn.commit()
    except sqlite3.IntegrityError:
        # Пользователь уже существует
        pass
    finally:
        conn.close()

def update_user_stars(user_id, stars):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET stars = stars + ? WHERE user_id = ?', (stars, user_id))
    conn.commit()
    conn.close()

def get_promo_code(code):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM promo_codes WHERE code = ?', (code,))
    promo = cursor.fetchone()
    conn.close()
    
    if promo:
        return {
            'code': promo[0],
            'activations_left': promo[1],
            'stars': promo[2],
            'created_by': promo[3]
        }
    return None

def use_promo_code(user_id, code):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Проверяем, использовал ли пользователь уже этот промокод
        cursor.execute('SELECT 1 FROM used_promo_codes WHERE user_id = ? AND code = ?', (user_id, code))
        if cursor.fetchone():
            return False, "Вы уже использовали этот промокод"
        
        # Получаем информацию о промокоде
        promo = get_promo_code(code)
        if not promo:
            return False, "Промокод не найден"
        
        if promo['activations_left'] <= 0:
            return False, "Промокод больше не действителен"
        
        # Используем промокод
        cursor.execute('UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code = ?', (code,))
        cursor.execute('UPDATE users SET stars = stars + ? WHERE user_id = ?', (promo['stars'], user_id))
        cursor.execute('INSERT INTO used_promo_codes (user_id, code) VALUES (?, ?)', (user_id, code))
        
        conn.commit()
        return True, f"Промокод активирован! Получено {promo['stars']} звёзд"
    
    except Exception as e:
        return False, f"Ошибка при активации промокода: {str(e)}"
    finally:
        conn.close()

def create_promo_code(code, activations, stars, created_by):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
        INSERT INTO promo_codes (code, activations_left, stars, created_by)
        VALUES (?, ?, ?, ?)
        ''', (code, activations, stars, created_by))
        
        conn.commit()
        return True, "Промокод создан"
    except sqlite3.IntegrityError:
        return False, "Промокод уже существует"
    finally:
        conn.close()

def is_admin(user_id):
    return user_id in ADMIN_IDS

# Функция проверки подписки на канал
def check_subscription(context, user_id):
    try:
        member = context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.CREATOR]:
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки: {e}")
        return False

# Основные функции бота
def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    user_id = user.id
    
    # Проверяем, зарегистрирован ли пользователь
    if not get_user(user_id):
        # Генерируем реферальный код
        ref_code = secrets.token_hex(4).upper()
        
        # Проверяем реферальную ссылку
        referrer_id = None
        if context.args:
            try:
                referrer_id = int(context.args[0])
                # Проверяем, существует ли реферер
                if not get_user(referrer_id) or referrer_id == user_id:
                    referrer_id = None
            except ValueError:
                referrer_id = None
        
        # Создаем пользователя
        create_user(user_id, user.username, user.full_name, ref_code, referrer_id)
    
    # Проверка подписки на канал
    if not check_subscription(context, user_id):
        update.message.reply_text(
            f"Для использования бота, подпишитесь на канал {CHANNEL_USERNAME}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Подписаться", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
                [InlineKeyboardButton("Проверить подписку", callback_data="check_subscription")]
            ])
        )
        return
    
    # Показываем главное меню
    show_main_menu(update, user_id)

def show_main_menu(update: Update, user_id: int) -> None:
    keyboard = [
        [InlineKeyboardButton("⭐️ Профиль", callback_data="profile")],
        [InlineKeyboardButton("💫 Заработать", callback_data="earn")],
        [InlineKeyboardButton("🎁 Промокоды", callback_data="promo")],
        [InlineKeyboardButton("💰 Вывести", callback_data="withdraw")]
    ]
    
    if hasattr(update, 'message') and update.message:
        update.message.reply_text("Главное меню:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        query = update.callback_query
        query.edit_message_text("Главное меню:", reply_markup=InlineKeyboardMarkup(keyboard))

def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    query.answer()

    if query.data == "check_subscription":
        if check_subscription(context, user_id):
            show_main_menu(query, user_id)
        else:
            query.edit_message_text("Вы всё ещё не подписались на канал!")
        return
    elif query.data == "profile":
        show_profile(query, user_id)
    elif query.data == "earn":
        show_earn_menu(query, user_id)
    elif query.data == "promo":
        show_promo_menu(query, user_id)
    elif query.data == "withdraw":
        handle_withdraw(query, user_id)
    elif query.data == "main_menu":
        show_main_menu(query, user_id)
    elif query.data == "enter_promo":
        query.edit_message_text(
            "Введите промокод:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="promo")]])
        )
    elif query.data == "create_promo":
        if is_admin(user_id):
            query.edit_message_text(
                "Введите промокод в формате: /create_promo CODE ACTIVATIONS STARS",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="promo")]])
            )
        else:
            query.edit_message_text("У вас нет прав администратора!")

def show_profile(query, user_id: int):
    user = get_user(user_id)
    if not user:
        query.edit_message_text("Ошибка: пользователь не найден")
        return
    
    text = (
        f"👤 ID: {user['user_id']}\n"
        f"📛 Имя: {user['full_name']}\n\n"
        f"👥 Рефералов: {user['referrals']}\n"
        f"⭐️ Звёзды: {user['stars']}\n"
        f"🔗 Реф. код: {user['ref_code']}"
    )
    
    query.edit_message_text(
        text=text, 
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Назад", callback_data="main_menu")]
        ])
    )

def show_earn_menu(query, user_id: int):
    user = get_user(user_id)
    if not user:
        query.edit_message_text("Ошибка: пользователь не найден")
        return
    
    ref_link = f"https://t.me/{(query.bot.username)}?start={user_id}"
    
    query.edit_message_text(
        f"💫 Приглашайте друзей и получайте звёзды!\n\n"
        f"За каждого приглашённого друга вы получаете 2 звезды 🌟🌟\n\n"
        f"Ваша реферальная ссылка:\n`{ref_link}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Поделиться", url=f"https://t.me/share/url?url={ref_link}&text=Присоединяйтесь%20к%20нашему%20боте!")],
            [InlineKeyboardButton("Назад", callback_data="main_menu")]
        ])
    )

def show_promo_menu(query, user_id: int):
    keyboard = [
        [InlineKeyboardButton("Ввести промокод", callback_data="enter_promo")],
        [InlineKeyboardButton("Назад", callback_data="main_menu")]
    ]
    
    # Добавляем кнопку для админов
    if is_admin(user_id):
        keyboard.insert(0, [InlineKeyboardButton("Создать промокод (ADMIN)", callback_data="create_promo")])
    
    query.edit_message_text(
        "🎁 Введите промокод, чтобы получить звёзды:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def handle_withdraw(query, user_id: int):
    user = get_user(user_id)
    if not user:
        query.edit_message_text("Ошибка: пользователь не найден")
        return
    
    if user['stars'] >= 50:
        query.edit_message_text(
            "Запрос на вывод отправлен администратору! Ожидайте обработки.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Назад", callback_data="main_menu")]
            ])
        )
        
        # Уведомление администратору
        for admin_id in ADMIN_IDS:
            try:
                query.bot.send_message(
                    admin_id,
                    f"📥 Новый запрос на вывод!\n\n"
                    f"Пользователь: {user['full_name']} (@{user['username']})\n"
                    f"ID: {user_id}\n"
                    f"Звёзд: {user['stars']}"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
    else:
        query.edit_message_text(
            f"Вы пока не можете вывести! Необходимо 50 звёзд, у вас {user['stars']}.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Назад", callback_data="main_menu")]
            ])
        )

def handle_message(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    user_id = user.id
    text = update.message.text
    
    # Обработка ввода промокода
    if len(text) <= 20:  # Промокоды обычно короткие
        success, message = use_promo_code(user_id, text.upper())
        update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Назад в меню", callback_data="main_menu")]
            ])
        )
        return
    
    update.message.reply_text("Неизвестная команда")

# Команды для админов
def admin_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        update.message.reply_text("У вас нет прав для выполнения этой команды")
        return
    
    if not context.args:
        update.message.reply_text("Использование: /admin create_promo CODE ACTIVATIONS STARS")
        return
    
    if context.args[0] == "create_promo":
        if len(context.args) != 4:
            update.message.reply_text("Использование: /admin create_promo CODE ACTIVATIONS STARS")
            return
        
        try:
            code = context.args[1].upper()
            activations = int(context.args[2])
            stars = int(context.args[3])
            
            success, message = create_promo_code(code, activations, stars, user_id)
            update.message.reply_text(message)
        except ValueError:
            update.message.reply_text("ACTIVATIONS и STARS должны быть числами")

def my_id_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    update.message.reply_text(f"Ваш ID: {user.id}")

def main() -> None:
    # Инициализация базы данных
    init_db()
    
    # Создание updater и dispatcher
    updater = Updater(BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    
    # Добавление обработчиков
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("admin", admin_command))
    dispatcher.add_handler(CommandHandler("my_id", my_id_command))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    # Запуск бота с long polling
    updater.start_polling()
    logger.info("Бот запущен с long polling")
    updater.idle()

if __name__ == "__main__":
    main()
