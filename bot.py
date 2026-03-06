import os
import sqlite3
import telebot
from telebot import types

print("=== BOT STARTING ===", flush=True)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN not found in environment variables")

DB_PATH = os.getenv("DB_PATH", "darom.db")

bot = telebot.TeleBot(TOKEN)
print("BOT OBJECT CREATED", flush=True)

# ===== DATABASE =====
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0,
    city TEXT NOT NULL DEFAULT ''
)
""")
conn.commit()


def add_item(title: str, price: int, city: str) -> None:
    cursor.execute(
        "INSERT INTO items (title, price, city) VALUES (?, ?, ?)",
        (title, price, city)
    )
    conn.commit()


def get_items():
    cursor.execute("SELECT id, title, price, city FROM items ORDER BY id DESC")
    return cursor.fetchall()


def get_item_by_index(idx: int):
    items = get_items()
    if not items:
        return None
    return items[idx % len(items)]


# ===== STATE =====
user_state = {}


# ===== KEYBOARDS =====
def build_card_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next")
    )
    return kb


# ===== HELPERS =====
def format_item_text(item) -> str:
    _, title, price, city = item
    text = f"🧥 {title}\n"
    text += ("🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n")
    text += f"📍 {city}"
    return text


def send_current_item(chat_id: int, idx: int = 0):
    item = get_item_by_index(idx)
    if not item:
        bot.send_message(
            chat_id,
            "Пока нет объявлений 😕\n"
            "Добавь первое командой:\n"
            "/add Куртка;0;Москва"
        )
        return

    user_state[chat_id] = idx
    text = format_item_text(item)
    bot.send_message(chat_id, text, reply_markup=build_card_keyboard())


# ===== COMMANDS =====
@bot.message_handler(commands=["start"])
def start_command(message):
    chat_id = message.chat.id
    user_state[chat_id] = 0
    send_current_item(chat_id, 0)


@bot.message_handler(commands=["add"])
def add_command(message):
    chat_id = message.chat.id
    raw_text = message.text.replace("/add", "", 1).strip()

    if not raw_text:
        bot.send_message(
            chat_id,
            "❌ Формат:\n/add Название;Цена;Город\n\n"
            "Пример:\n/add Куртка;0;Москва"
        )
        return

    parts = [part.strip() for part in raw_text.split(";")]

    if len(parts) != 3:
        bot.send_message(
            chat_id,
            "❌ Формат:\n/add Название;Цена;Город\n\n"
            "Пример:\n/add Куртка;0;Москва"
        )
        return

    title, price_str, city = parts

    if len(title) < 2:
        bot.send_message(chat_id, "❌ Название слишком короткое.")
        return

    try:
        price = int(price_str)
    except ValueError:
        bot.send_message(chat_id, "❌ Цена должна быть числом.")
        return

    if price < 0:
        bot.send_message(chat_id, "❌ Цена не может быть меньше 0.")
        return

    if len(city) < 2:
        bot.send_message(chat_id, "❌ Город слишком короткий.")
        return

    add_item(title, price, city)

    bot.send_message(
        chat_id,
        f"✅ Добавлено:\n"
        f"{title}\n"
        f"{'💰 ' + str(price) + ' ₽' if price > 0 else '🟢 Бесплатно'}\n"
        f"📍 {city}"
    )


@bot.callback_query_handler(func=lambda call: call.data == "next")
def next_callback(call):
    chat_id = call.message.chat.id
    items = get_items()

    if not items:
        bot.answer_callback_query(call.id, "Объявлений пока нет")
        return

    current_idx = user_state.get(chat_id, 0)
    next_idx = (current_idx + 1) % len(items)
    user_state[chat_id] = next_idx

    item = items[next_idx]
    text = format_item_text(item)

    try:
        bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=build_card_keyboard()
        )
    except Exception:
        bot.send_message(chat_id, text, reply_markup=build_card_keyboard())

    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda message: True)
def fallback(message):
    bot.send_message(
        message.chat.id,
        "Я понимаю только:\n"
        "/start\n"
        "/add Название;Цена;Город\n\n"
        "Пример:\n/add Куртка;0;Москва"
    )


if __name__ == "__main__":
    bot.remove_webhook()
    print("Webhook removed", flush=True)
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
