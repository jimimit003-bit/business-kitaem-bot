import os
import sqlite3
import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
DB_PATH = os.getenv("DB_PATH", "darom.db")

if not TOKEN:
    raise ValueError("TOKEN не найден в переменных окружения")

bot = telebot.TeleBot(TOKEN)

# ---------------- DB ----------------

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0,
    city TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'free',
    photo_id TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    UNIQUE(user_id, item_id)
)
""")

conn.commit()

# -------------- STATE --------------

user_state = {}       # chat_id -> current index
pending_photo = {}    # chat_id -> dict(title, price, city, kind)

# -------------- HELPERS --------------

def get_user_id(telegram_id: int) -> int:
    cursor.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (telegram_id,))
    conn.commit()
    cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    return row[0]

def add_item(title: str, price: int, city: str, kind: str = "free", photo_id: str | None = None):
    cursor.execute("""
        INSERT INTO items (title, price, city, kind, photo_id)
        VALUES (?, ?, ?, ?, ?)
    """, (title, price, city, kind, photo_id))
    conn.commit()

def get_items():
    cursor.execute("""
        SELECT id, title, price, city, kind, photo_id
        FROM items
        ORDER BY id DESC
    """)
    return cursor.fetchall()

def get_item_by_index(idx: int):
    items = get_items()
    if not items:
        return None
    return items[idx % len(items)]

def add_favorite(user_id: int, item_id: int):
    cursor.execute("""
        INSERT OR IGNORE INTO favorites (user_id, item_id)
        VALUES (?, ?)
    """, (user_id, item_id))
    conn.commit()

def remove_favorite(user_id: int, item_id: int):
    cursor.execute("""
        DELETE FROM favorites
        WHERE user_id = ? AND item_id = ?
    """, (user_id, item_id))
    conn.commit()

def is_favorite(user_id: int, item_id: int) -> bool:
    cursor.execute("""
        SELECT 1 FROM favorites
        WHERE user_id = ? AND item_id = ?
        LIMIT 1
    """, (user_id, item_id))
    return cursor.fetchone() is not None

def format_item_text(item) -> str:
    # item = (id, title, price, city, kind, photo_id)
    _, title, price, city, kind, _ = item

    text = f"🧥 {title}\n"
    if kind == "free" or price == 0:
        text += "🟢 Бесплатно\n"
    elif kind == "under400":
        text += f"🟡 {price} ₽\n"
    else:
        text += f"⚪ {price} ₽\n"

    text += f"📍 {city if city else 'Не указан'}"
    return text

def build_card_keyboard(item, telegram_user_id: int):
    item_id = item[0]
    fav = is_favorite(get_user_id(telegram_user_id), item_id)

    kb = types.InlineKeyboardMarkup(row_width=2)
    fav_text = "💔 Убрать лайк" if fav else "❤️ Лайк"

    kb.add(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton(fav_text, callback_data=f"fav:{item_id}")
    )
    return kb

def show_item(chat_id: int, idx: int = 0):
    items = get_items()
    if not items:
        bot.send_message(
            chat_id,
            "Пока нет объявлений 😕\n"
            "Добавь первое командой:\n"
            "/add Куртка;0;Москва"
        )
        return

    user_state[chat_id] = idx % len(items)
    item = items[user_state[chat_id]]
    text = format_item_text(item)
    kb = build_card_keyboard(item, chat_id)
    photo_id = item[5]

    if photo_id:
        bot.send_photo(chat_id, photo_id, caption=text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)

# -------------- COMMANDS --------------

@bot.message_handler(commands=["start"])
def start_cmd(message):
    show_item(message.chat.id, 0)

@bot.message_handler(commands=["add"])
def add_cmd(message):
    text = message.text.replace("/add", "", 1).strip()
    parts = [p.strip() for p in text.split(";")]

    if len(parts) < 3:
        bot.send_message(
            message.chat.id,
            "❌ Формат:\n/add Название;Цена;Город\n\nПример:\n/add Куртка;0;Москва"
        )
        return

    title = parts[0]
    city = parts[2]

    try:
        price = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "❌ Цена должна быть числом")
        return

    if price == 0:
        kind = "free"
    elif 1 <= price <= 400:
        kind = "under400"
    else:
        kind = "regular"

    add_item(title, price, city, kind, None)

    bot.send_message(
        message.chat.id,
        f"✅ Добавлено:\n{title}\n"
        f"{'🟢 Бесплатно' if price == 0 else f'💰 {price} ₽'}\n"
        f"📍 {city}"
    )

@bot.message_handler(commands=["addphoto"])
def addphoto_cmd(message):
    text = message.text.replace("/addphoto", "", 1).strip()
    parts = [p.strip() for p in text.split(";")]

    if len(parts) < 3:
        bot.send_message(
            message.chat.id,
            "❌ Формат:\n/addphoto Название;Цена;Город\n\nПример:\n/addphoto Куртка;0;Москва"
        )
        return

    title = parts[0]
    city = parts[2]

    try:
        price = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "❌ Цена должна быть числом")
        return

    if price == 0:
        kind = "free"
    elif 1 <= price <= 400:
        kind = "under400"
    else:
        kind = "regular"

    pending_photo[message.chat.id] = {
        "title": title,
        "price": price,
        "city": city,
        "kind": kind
    }

    bot.send_message(
        message.chat.id,
        "📸 Теперь отправь фото для этого объявления одним сообщением"
    )

@bot.message_handler(commands=["myfav"])
def myfav_cmd(message):
    user_id = get_user_id(message.chat.id)

    cursor.execute("""
        SELECT items.id, items.title, items.price, items.city, items.kind, items.photo_id
        FROM favorites
        JOIN items ON items.id = favorites.item_id
        WHERE favorites.user_id = ?
        ORDER BY favorites.id DESC
    """, (user_id,))
    rows = cursor.fetchall()

    if not rows:
        bot.send_message(message.chat.id, "У тебя пока нет лайкнутых объявлений ❤️")
        return

    text = "❤️ Твои лайки:\n\n"
    for item in rows[:20]:
        item_text = format_item_text(item)
        text += item_text + "\n\n"

    bot.send_message(message.chat.id, text)

# -------------- PHOTO HANDLER --------------

@bot.message_handler(content_types=["photo"])
def photo_handler(message):
    if message.chat.id not in pending_photo:
        bot.send_message(message.chat.id, "Фото получено, но сейчас нет активного /addphoto")
        return

    data = pending_photo.pop(message.chat.id)
    photo_id = message.photo[-1].file_id

    add_item(
        title=data["title"],
        price=data["price"],
        city=data["city"],
        kind=data["kind"],
        photo_id=photo_id
    )

    bot.send_message(
        message.chat.id,
        f"✅ Объявление с фото добавлено:\n{data['title']}\n"
        f"{'🟢 Бесплатно' if data['price'] == 0 else f'💰 {data['price']} ₽'}\n"
        f"📍 {data['city']}"
    )

# -------------- CALLBACKS --------------

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    data = call.data
    items = get_items()

    if not items:
        bot.answer_callback_query(call.id, "Объявлений пока нет")
        return

    current_idx = user_state.get(chat_id, 0)

    if data == "next":
        new_idx = (current_idx + 1) % len(items)
        user_state[chat_id] = new_idx
        item = items[new_idx]
        text = format_item_text(item)
        kb = build_card_keyboard(item, chat_id)
        photo_id = item[5]

        try:
            if photo_id:
                # если текущая карточка была текстом — проще отправить новую
                bot.send_photo(chat_id, photo_id, caption=text, reply_markup=kb)
            else:
                bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=kb
                )
        except Exception:
            # запасной вариант
            if photo_id:
                bot.send_photo(chat_id, photo_id, caption=text, reply_markup=kb)
            else:
                bot.send_message(chat_id, text, reply_markup=kb)

        bot.answer_callback_query(call.id)
        return

    if data.startswith("fav:"):
        try:
            item_id = int(data.split(":")[1])
        except ValueError:
            bot.answer_callback_query(call.id, "Ошибка")
            return

        tg_user_id = chat_id
        user_id = get_user_id(tg_user_id)

        if is_favorite(user_id, item_id):
            remove_favorite(user_id, item_id)
            bot.answer_callback_query(call.id, "Лайк убран 💔")
        else:
            add_favorite(user_id, item_id)
            bot.answer_callback_query(call.id, "Добавлено в лайки ❤️")

        # обновляем клавиатуру текущего сообщения
        cursor.execute("""
            SELECT id, title, price, city, kind, photo_id
            FROM items
            WHERE id = ?
        """, (item_id,))
        item = cursor.fetchone()

        if item:
            kb = build_card_keyboard(item, tg_user_id)
            try:
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=kb
                )
            except Exception:
                pass

        return

# -------------- RUN --------------

print("=== BOT STARTING ===", flush=True)
bot.remove_webhook()
print("Webhook removed", flush=True)
bot.infinity_polling(timeout=30, long_polling_timeout=20)
