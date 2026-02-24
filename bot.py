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

    return kb# --- Меню цены ---
def build_price_menu():
    kb = types.InlineKeyboardMarkup()

    kb.row(
        types.InlineKeyboardButton("🟢 Бесплатно", callback_data="set_price_free"),
        types.InlineKeyboardButton("🟡 До 400 ₽", callback_data="set_price_400"),
    )

    kb.row(
        types.InlineKeyboardButton("⚪ Любая", callback_data="set_price_any"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_card"),
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


@bot.callback_query_handler(func=lambda call: True)
def handle_buttons(call):

    if call.data == "next":
        bot.edit_message_text(
            "👕 Футболка Nike\n🟡 До 400 ₽\n📍 Москва",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=build_card_keyboard()
        )
elif call.data == "f_price":
    bot.edit_message_text(
        "💰 Цена — выбери вариант:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=build_price_menu()
    )

elif call.data == "back_to_card":
    bot.edit_message_text(
        "🧥 Куртка зимняя\n🟢 Бесплатно\n📍 Москва",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=build_card_keyboard()
    )
    elif call.data == "take":
        bot.answer_callback_query(call.id, "Свяжись с владельцем 😉")

    elif call.data == "fav":
        bot.answer_callback_query(call.id, "Добавлено в избранное ❤️")

    elif call.data == "reset":
        bot.answer_callback_query(call.id, "Фильтры сброшены ♻️")
print("Bot started...")
bot.infinity_polling()
