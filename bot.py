import os
import time
import sqlite3
import telebot

from telebot import types
from telebot.apihelper import ApiTelegramException


TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("TOKEN не найден в переменных окружения")

DB_PATH = os.getenv("DB_PATH", "darom.db")

bot = telebot.TeleBot(TOKEN)

# ========= DATABASE =========
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
    city TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Без категории',
    photo_id TEXT,
    owner_tg INTEGER NOT NULL
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    UNIQUE(user_id, item_id)
)
""")

conn.commit()

# ========= MEMORY =========
ITEMS = []
user_index = {}          # chat_id -> current card index
pending_addphoto = {}    # chat_id -> {"title":..., "price":..., "city":..., "category":...}


# ========= DB HELPERS =========
def refresh_items():
    global ITEMS
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg
        FROM items
        ORDER BY id DESC
    """)
    ITEMS = cursor.fetchall()


def get_user_id(telegram_id: int) -> int:
    cursor.execute(
        "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
        (telegram_id,)
    )
    conn.commit()

    cursor.execute(
        "SELECT id FROM users WHERE telegram_id = ?",
        (telegram_id,)
    )
    row = cursor.fetchone()
    return row[0]


def add_item(title: str, price: int, city: str, category: str, owner_tg: int, photo_id: str | None = None):
    cursor.execute("""
        INSERT INTO items (title, price, city, category, photo_id, owner_tg)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (title, price, city, category, photo_id, owner_tg))
    conn.commit()
    refresh_items()


def get_item_by_index(idx: int):
    refresh_items()
    if not ITEMS:
        return None
    return ITEMS[idx % len(ITEMS)]


def get_user_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg
        FROM items
        WHERE owner_tg = ?
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def add_favorite(user_id: int, item_id: int):
    cursor.execute(
        "INSERT OR IGNORE INTO favorites (user_id, item_id) VALUES (?, ?)",
        (user_id, item_id)
    )
    conn.commit()


def get_favorites(user_id: int):
    cursor.execute("""
        SELECT item_id
        FROM favorites
        WHERE user_id = ?
    """, (user_id,))
    rows = cursor.fetchall()
    return [row[0] for row in rows]


def add_like(user_id: int, item_id: int):
    cursor.execute(
        "INSERT OR IGNORE INTO likes (user_id, item_id) VALUES (?, ?)",
        (user_id, item_id)
    )
    conn.commit()


def remove_like(user_id: int, item_id: int):
    cursor.execute(
        "DELETE FROM likes WHERE user_id = ? AND item_id = ?",
        (user_id, item_id)
    )
    conn.commit()


def has_like(user_id: int, item_id: int) -> bool:
    cursor.execute(
        "SELECT 1 FROM likes WHERE user_id = ? AND item_id = ? LIMIT 1",
        (user_id, item_id)
    )
    return cursor.fetchone() is not None


def get_likes_count(item_id: int) -> int:
    cursor.execute(
        "SELECT COUNT(*) FROM likes WHERE item_id = ?",
        (item_id,)
    )
    row = cursor.fetchone()
    return row[0] if row else 0


# ========= UI HELPERS =========
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton("🏠 Смотреть"),
        types.KeyboardButton("❤️ Избранное")
    )
    kb.row(
        types.KeyboardButton("📦 Мои объявления"),
        types.KeyboardButton("➕ Добавить")
    )
    kb.row(
        types.KeyboardButton("♻ Сбросить фильтры")
    )
    return kb


def build_card_keyboard(item_id: int, viewer_tg: int):
    viewer_user_id = get_user_id(viewer_tg)
    likes_count = get_likes_count(item_id)

    if has_like(viewer_user_id, item_id):
        like_text = f"💔 Убрать лайк ({likes_count})"
    else:
        like_text = f"❤️ Лайк ({likes_count})"

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("➡ Следующее", callback_data="next"),
        types.InlineKeyboardButton(like_text, callback_data="like")
    )
    kb.row(
        types.InlineKeyboardButton("❤️ В избранное", callback_data="fav"),
        types.InlineKeyboardButton("✅ Забрать", callback_data="take")
    )
    return kb


def format_item_text(item) -> str:
    item_id, title, price, city, category, photo_id, owner_tg = item

    text = f"🧥 {title}\n"
    text += "🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n"
    text += f"📍 {city}\n"
    text += f"📦 {category}"
    return text


def show_item(chat_id: int, item, send_new: bool = True, edit_message_id: int | None = None):
    if not item:
        bot.send_message(
            chat_id,
            "Пока нет объявлений 😕\nДобавь первое командой:\n/add Куртка;0;Москва;Одежда",
            reply_markup=main_menu()
        )
        return

    item_id, title, price, city, category, photo_id, owner_tg = item
    text = format_item_text(item)
    reply_markup = build_card_keyboard(item_id, chat_id)

    if photo_id:
        if edit_message_id is not None:
            try:
                bot.delete_message(chat_id, edit_message_id)
            except Exception:
                pass
        bot.send_photo(chat_id, photo_id, caption=text, reply_markup=reply_markup)
    else:
        if send_new:
            bot.send_message(chat_id, text, reply_markup=reply_markup)
        else:
            bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=edit_message_id,
                reply_markup=reply_markup
            )


# ========= COMMANDS =========
@bot.message_handler(commands=['start'])
def start_cmd(message):
    chat_id = message.chat.id
    get_user_id(chat_id)

    refresh_items()
    user_index[chat_id] = 0

    bot.send_message(chat_id, "Главное меню:", reply_markup=main_menu())

    item = get_item_by_index(0)
    show_item(chat_id, item)


@bot.message_handler(commands=['add'])
def add_cmd(message):
    chat_id = message.chat.id

    raw = message.text.replace('/add', '', 1).strip()
    parts = [p.strip() for p in raw.split(';')]

    if len(parts) < 4:
        bot.send_message(
            chat_id,
            "❌ Формат:\n/add Название;Цена;Город;Категория\n\nПример:\n/add Куртка;0;Москва;Одежда"
        )
        return

    title = parts[0]
    city = parts[2]
    category = parts[3]

    try:
        price = int(parts[1])
    except ValueError:
        bot.send_message(chat_id, "❌ Цена должна быть числом")
        return

    if len(title) < 2:
        bot.send_message(chat_id, "❌ Слишком короткое название")
        return

    add_item(title, price, city, category, chat_id)

    bot.send_message(
        chat_id,
        f"✅ Добавлено:\n🧥 {title}\n" +
        ("🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n") +
        f"📍 {city}\n📦 {category}",
        reply_markup=main_menu()
    )


@bot.message_handler(commands=['addphoto'])
def addphoto_cmd(message):
    chat_id = message.chat.id

    raw = message.text.replace('/addphoto', '', 1).strip()
    parts = [p.strip() for p in raw.split(';')]

    if len(parts) < 4:
        bot.send_message(
            chat_id,
            "❌ Формат:\n/addphoto Название;Цена;Город;Категория\n\nПример:\n/addphoto Поло;0;Москва;Одежда"
        )
        return

    title = parts[0]
    city = parts[2]
    category = parts[3]

    try:
        price = int(parts[1])
    except ValueError:
        bot.send_message(chat_id, "❌ Цена должна быть числом")
        return

    pending_addphoto[chat_id] = {
        "title": title,
        "price": price,
        "city": city,
        "category": category
    }

    bot.send_message(chat_id, "📸 Теперь отправь фото одним следующим сообщением")


# ========= PHOTO =========
@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    chat_id = message.chat.id

    if chat_id not in pending_addphoto:
        bot.send_message(chat_id, "Фото получено, но активного /addphoto сейчас нет")
        return

    draft = pending_addphoto.pop(chat_id)
    photo_id = message.photo[-1].file_id

    add_item(
        title=draft["title"],
        price=draft["price"],
        city=draft["city"],
        category=draft["category"],
        owner_tg=chat_id,
        photo_id=photo_id
    )

    bot.send_message(
        chat_id,
        f"✅ Объявление с фото добавлено:\n"
        f"🧥 {draft['title']}\n"
        + ("🟢 Бесплатно\n" if draft["price"] == 0 else f"💰 {draft['price']} ₽\n")
        + f"📍 {draft['city']}\n"
        + f"📦 {draft['category']}",
        reply_markup=main_menu()
    )


# ========= MENU BUTTONS =========
@bot.message_handler(func=lambda m: m.text == "🏠 Смотреть")
def menu_watch(message):
    start_cmd(message)


@bot.message_handler(func=lambda m: m.text == "➕ Добавить")
def menu_add(message):
    bot.send_message(
        message.chat.id,
        "Команды:\n\n"
        "/add Название;Цена;Город;Категория\n"
        "/addphoto Название;Цена;Город;Категория\n\n"
        "Примеры:\n"
        "/add Куртка;0;Москва;Одежда\n"
        "/addphoto Поло;0;Москва;Одежда"
    )


@bot.message_handler(func=lambda m: m.text == "📦 Мои объявления")
def menu_my_items(message):
    chat_id = message.chat.id
    rows = get_user_items(chat_id)

    if not rows:
        bot.send_message(chat_id, "У тебя пока нет своих объявлений")
        return

    text = "📦 Мои объявления:\n\n"
    for item in rows[:20]:
        item_id, title, price, city, category, photo_id, owner_tg = item
        text += f"#{item_id} — {title} | "
        text += "Бесплатно" if price == 0 else f"{price} ₽"
        text += f" | {city} | {category}\n"

    bot.send_message(chat_id, text)


@bot.message_handler(func=lambda m: m.text == "❤️ Избранное")
def menu_favorites(message):
    chat_id = message.chat.id
    user_id = get_user_id(chat_id)
    fav_ids = get_favorites(user_id)

    if not fav_ids:
        bot.send_message(chat_id, "В избранном пока пусто ❤️")
        return

    placeholders = ",".join(["?"] * len(fav_ids))
    cursor.execute(f"""
        SELECT id, title, price, city, category, photo_id, owner_tg
        FROM items
        WHERE id IN ({placeholders})
        ORDER BY id DESC
    """, fav_ids)
    rows = cursor.fetchall()

    text = "❤️ Избранное:\n\n"
    for item in rows[:20]:
        item_id, title, price, city, category, photo_id, owner_tg = item
        text += f"#{item_id} — {title} | "
        text += "Бесплатно" if price == 0 else f"{price} ₽"
        text += f" | {city} | {category}\n"

    bot.send_message(chat_id, text)


@bot.message_handler(func=lambda m: m.text == "♻ Сбросить фильтры")
def menu_reset(message):
    bot.send_message(message.chat.id, "Фильтры сброшены ♻", reply_markup=main_menu())


# ========= CALLBACKS =========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    refresh_items()

    if not ITEMS:
        bot.answer_callback_query(call.id, "Объявлений нет")
        return

    if call.data == "next":
        idx = user_index.get(chat_id, 0)
        idx = (idx + 1) % len(ITEMS)
        user_index[chat_id] = idx

        item = ITEMS[idx]
        item_id, title, price, city, category, photo_id, owner_tg = item

        text = format_item_text(item)
        reply_markup = build_card_keyboard(item_id, chat_id)

        if photo_id:
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            bot.send_photo(chat_id, photo_id, caption=text, reply_markup=reply_markup)
        else:
            bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=reply_markup
            )

        bot.answer_callback_query(call.id)

    elif call.data == "like":
        idx = user_index.get(chat_id, 0)
        item = ITEMS[idx]
        item_id = item[0]
        user_id = get_user_id(chat_id)

        if has_like(user_id, item_id):
            remove_like(user_id, item_id)
            bot.answer_callback_query(call.id, "Лайк убран")
        else:
            add_like(user_id, item_id)
            bot.answer_callback_query(call.id, "Лайк поставлен")

        text = format_item_text(item)
        reply_markup = build_card_keyboard(item_id, chat_id)
        photo_id = item[5]

        if photo_id:
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            bot.send_photo(chat_id, photo_id, caption=text, reply_markup=reply_markup)
        else:
            bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=reply_markup
            )

    elif call.data == "fav":
        idx = user_index.get(chat_id, 0)
        item = ITEMS[idx]
        item_id = item[0]
        user_id = get_user_id(chat_id)

        add_favorite(user_id, item_id)
        bot.answer_callback_query(call.id, "Добавлено в избранное ❤️")

    elif call.data == "take":
        bot.answer_callback_query(call.id, "Отмечено ✅")


# ========= SAFE POLLING =========
def run_bot():
    print("=== BOT STARTING ===", flush=True)
    bot.remove_webhook()
    print("Webhook removed", flush=True)

    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except ApiTelegramException as e:
            print(f"Telegram error: {e}", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"Unexpected error: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    refresh_items()
    run_bot()
