import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота
BOT_TOKEN = "7968236729:AAEIaSxTlST7D-BbazdXcyCwSj3lBCnuQ1c"

# ID администраторов
ADMINS = [7353415682 , 8030716815]  # Замените на реальные ID

# Инициализация бота с правильными параметрами
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Здравствуйте! Отправьте свои сигны, и мы выложим его в @SignaReytzov.\n\n"
        "Бот не ИИ, а его админы не делают сигны."
    )

# Обработка всех сообщений от пользователей
@dp.message(F.chat.id.not_in(ADMINS))
async def forward_user_message(message: Message):
    for admin_id in ADMINS:
        try:
            builder = InlineKeyboardBuilder()
            builder.button(text="Ответить", callback_data=f"reply_{message.from_user.id}")
            
            await message.send_copy(
                admin_id,
                reply_markup=builder.as_markup()
            )
            await message.answer("✅ Ваше сообщение отправлено администраторам!")
        except Exception as e:
            logger.error(f"Ошибка при пересылке: {e}")

# Обработка кнопки "Ответить"
@dp.callback_query(F.data.startswith("reply_"))
async def process_admin_reply(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    await callback.answer()
    await callback.message.answer(f"✍️ Отправьте ответ пользователю (ID: {user_id}):")
    dp["reply_mode"] = user_id  # Временное хранение ID

# Обработка ответов администраторов
@dp.message(F.chat.id.in_(ADMINS))
async def handle_admin_message(message: Message):
    if "reply_mode" in dp and dp["reply_mode"]:
        user_id = dp["reply_mode"]
        try:
            await message.send_copy(user_id)
            await message.answer("✅ Ответ отправлен!")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
        finally:
            del dp["reply_mode"]
    
    # Команда /admins
    elif message.text and message.text.startswith("/admins"):
        if len(message.text.split()) > 1:
            try:
                new_admin = int(message.text.split()[1])
                if new_admin not in ADMINS:
                    ADMINS.append(new_admin)
                    await message.answer(f"✅ Добавлен администратор: {new_admin}")
                else:
                    await message.answer("⚠️ Уже есть в списке!")
            except ValueError:
                await message.answer("❌ Некорректный ID")
        else:
            admins_list = "\n".join(str(admin) for admin in ADMINS)
            await message.answer(f"👑 Администраторы:\n{admins_list}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


