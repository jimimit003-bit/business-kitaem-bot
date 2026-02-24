import telebot

TOKEN = "8516444407:AAGseUj72idFT6b86hbg1W8qI48BIT_kd4Q"

bot = telebot.TeleBot(TOKEN)
from telebot import types

# --- Кнопки карточки (PRO режим) ---
def build_card_keyboard():
    kb = types.InlineKeyboardMarkup()

    kb.row(
        types.InlineKeyboardButton("✅ Забрать", callback_data="take"),
        types.InlineKeyboardButton("❤️", callback_data="fav")
    )

    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton("📍 Карта", callback_data="map")
    )

    kb.row(
        types.InlineKeyboardButton("💰 Цена", callback_data="f_price"),
        types.InlineKeyboardButton("🗂 Категория", callback_data="f_cat"),
        types.InlineKeyboardButton("🏙 Город", callback_data="f_city")
    )

    kb.row(
        types.InlineKeyboardButton("📍 Рядом со мной", callback_data="near"),
        types.InlineKeyboardButton("♻️ Сброс", callback_data="reset")
    )

    return kb
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
    message.chat.id,
    "🧥 Куртка зимняя\n🟢 Бесплатно\n📍 Москва",
    reply_markup=build_card_keyboard()
)

@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.send_message(message.chat.id, message.text)

print("Bot started...")
bot.infinity_polling()
