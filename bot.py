import telebot
import sys
import sqlite3
print("=== BOT STARTING ===", flush=True)
sys.stdout.flush()
TOKEN = "8516444407:AAGseUj72idFT6b86hbg1W8qI48BIT_kd4Q"

bot = telebot.TeleBot(TOKEN)
from telebot import types
# ----- Данные карточек -----
ITEMS = [
    {"title": "Куртка зимняя", "price": 0, "city": "Москва"},
    {"title": "Футболка Nike", "price": 400, "city": "Москва"},
    {"title": "Кроссовки", "price": 1200, "city": "Москва"},
    {"title": "Шапка", "price": 200, "city": "Москва"},
]

# ----- Состояние пользователя -----
user_state = {}
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
    # --- Меню цены ---
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
    user_state[message.chat.id] = 0

    item = ITEMS[0]

    text = f"🧥 {item['title']}\n"
    text += f"🟢 Бесплатно\n" if item['price'] == 0 else f"🟡 До {item['price']} ₽\n"
    text += f"📍 {item['city']}"

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=build_card_keyboard()
    )

@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.send_message(message.chat.id, message.text)


@bot.callback_query_handler(func=lambda call: True)
def handle_buttons(call):
    chat_id = call.message.chat.id

    # текущий индекс (если не был задан — 0)
    idx = user_state.get(chat_id, 0)

    if call.data == "next":
        idx = (idx + 1) % len(ITEMS)
        user_state[chat_id] = idx

        item = ITEMS[idx]

        text = f"🧥 {item['title']}\n"
        text += f"🟢 Бесплатно\n" if item['price'] == 0 else f"🟡 {item['price']}₽\n"
        text += f"📍 {item['city']}"

        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            reply_markup=build_card_keyboard()
        )
        bot.answer_callback_query(call.id, "Следующая ✅")

    elif call.data == "take":
        bot.answer_callback_query(call.id, "Забрал ✅")

    elif call.data == "fav":
        bot.answer_callback_query(call.id, "Добавил в ❤️")

    elif call.data == "reset":
        user_state[chat_id] = 0
        bot.answer_callback_query(call.id, "Сбросил 🔄")

    elif call.data == "near":
        bot.answer_callback_query(call.id, "Ищу рядом 📍")

    elif call.data == "map":
        bot.answer_callback_query(call.id, "Открываю карту 📍")

    elif call.data == "f_price":
        bot.edit_message_text(
            "💰 Цена — выбери вариант:",
            chat_id,
            call.message.message_id,
            reply_markup=build_price_menu()
        )
        bot.answer_callback_query(call.id)

    elif call.data == "back_to_card":
        # возвращаем текущую карточку
        idx = user_state.get(chat_id, 0)
        item = ITEMS[idx]

        text = f"🧥 {item['title']}\n"
        text += f"🟢 Бесплатно\n" if item['price'] == 0 else f"🟡 {item['price']}₽\n"
        text += f"📍 {item['city']}"

        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            reply_markup=build_card_keyboard()
        )
        bot.answer_callback_query(call.id)

    else:
        bot.answer_callback_query(call.id, "Неизвестная кнопка")



    
print("Bot started...")
bot.infinity_polling()
