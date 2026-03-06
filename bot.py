import os
import sqlite3
import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
DB_PATH = os.getenv("DB_PATH", "darom.db")

if not TOKEN:
    raise ValueError("TOKEN не найден в переменных окружения")

bot = telebot.TeleBot(TOKEN)

# =========================
# DB
# =========================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0,
    city TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Другое',
    photo_id TEXT,
    owner_tg INTEGER NOT NULL,
    claimed INTEGER NOT NULL DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_tg INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    UNIQUE(user_tg, item_id)
)
""")

conn.commit()

# =========================
# MEMORY STATE
# =========================
pending_photo = {}   # chat_id -> dict(title, price, city, category)
browse_state = {}    # chat_id -> dict(index, city, category)

CATEGORIES = [
    "Одежда",
    "Обувь",
    "Техника",
    "Дом",
    "Детское",
    "Другое"
]

# =========================
# HELPERS
# =========================
def ensure_state(chat_id: int):
    if chat_id not in browse_state:
        browse_state[chat_id] = {
            "index": 0,
            "city": None,
            "category": None
        }

def normalize_kind_from_price(price: int) -> str:
    if price == 0:
        return "free"
    if 1 <= price <= 400:
        return "under400"
    return "regular"

def add_item(title: str, price: int, city: str, category: str, owner_tg: int, photo_id: str | None = None):
    cursor.execute("""
        INSERT INTO items (title, price, city, category, photo_id, owner_tg, claimed)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (title, price, city, category, photo_id, owner_tg))
    conn.commit()

def delete_item(item_id: int, owner_tg: int) -> bool:
    cursor.execute("DELETE FROM items WHERE id = ? AND owner_tg = ?", (item_id, owner_tg))
    deleted = cursor.rowcount > 0
    conn.commit()
    return deleted

def mark_claimed(item_id: int):
    cursor.execute("UPDATE items SET claimed = 1 WHERE id = ?", (item_id,))
    conn.commit()

def get_filtered_items(chat_id: int):
    ensure_state(chat_id)
    state = browse_state[chat_id]

    query = """
        SELECT id, title, price, city, category, photo_id, owner_tg, claimed
        FROM items
        WHERE claimed = 0
    """
    params = []

    if state["city"]:
        query += " AND city = ?"
        params.append(state["city"])

    if state["category"]:
        query += " AND category = ?"
        params.append(state["category"])

    query += " ORDER BY id DESC"

    cursor.execute(query, tuple(params))
    return cursor.fetchall()

def get_my_items(user_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, claimed
        FROM items
        WHERE owner_tg = ?
        ORDER BY id DESC
    """, (user_tg,))
    return cursor.fetchall()

def get_favorites(user_tg: int):
    cursor.execute("""
        SELECT items.id, items.title, items.price, items.city, items.category, items.photo_id, items.owner_tg, items.claimed
        FROM favorites
        JOIN items ON items.id = favorites.item_id
        WHERE favorites.user_tg = ? AND items.claimed = 0
        ORDER BY favorites.id DESC
    """, (user_tg,))
    return cursor.fetchall()

def is_favorite(user_tg: int, item_id: int) -> bool:
    cursor.execute("""
        SELECT 1 FROM favorites
        WHERE user_tg = ? AND item_id = ?
        LIMIT 1
    """, (user_tg, item_id))
    return cursor.fetchone() is not None

def add_favorite(user_tg: int, item_id: int):
    cursor.execute("""
        INSERT OR IGNORE INTO favorites (user_tg, item_id)
        VALUES (?, ?)
    """, (user_tg, item_id))
    conn.commit()

def remove_favorite(user_tg: int, item_id: int):
    cursor.execute("""
        DELETE FROM favorites
        WHERE user_tg = ? AND item_id = ?
    """, (user_tg, item_id))
    conn.commit()

def format_price(price: int) -> str:
    if price == 0:
        return "🟢 Бесплатно"
    if 1 <= price <= 400:
        return f"🟡 {price} ₽"
    return f"⚪ {price} ₽"

def format_item_text(item):
    item_id, title, price, city, category, photo_id, owner_tg, claimed = item
    text = f"🧥 {title}\n"
    text += f"{format_price(price)}\n"
    text += f"📍 {city if city else 'Не указан'}\n"
    text += f"📦 {category}\n"
    text += f"🆔 #{item_id}"
    return text

def build_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Смотреть", "❤️ Избранное")
    kb.row("📦 Мои объявления", "➕ Добавить")
    kb.row("♻️ Сбросить фильтры")
    return kb

def build_card_keyboard(item, user_tg: int):
    item_id, title, price, city, category, photo_id, owner_tg, claimed = item
    fav_text = "💔 Убрать лайк" if is_favorite(user_tg, item_id) else "❤️ Лайк"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton(fav_text, callback_data=f"fav:{item_id}")
    )
    kb.row(
        types.InlineKeyboardButton("📩 Забрать", callback_data=f"take:{item_id}"),
        types.InlineKeyboardButton("📦 Категория", callback_data="menu_category")
    )
    kb.row(
        types.InlineKeyboardButton("📍 Город", callback_data="menu_city"),
        types.InlineKeyboardButton("♻️ Сброс", callback_data="reset_filters")
    )

    if owner_tg == user_tg:
        kb.row(types.InlineKeyboardButton("🗑 Удалить мое объявление", callback_data=f"delete:{item_id}"))

    return kb

def build_category_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    for cat in CATEGORIES:
        kb.add(types.InlineKeyboardButton(cat, callback_data=f"set_category:{cat}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cards"))
    return kb

def build_city_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    cities = ["Москва", "СПб", "Казань", "Екатеринбург", "Любой"]
    for city in cities:
        value = "ANY" if city == "Любой" else city
        kb.add(types.InlineKeyboardButton(city, callback_data=f"set_city:{value}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cards"))
    return kb

def send_item(chat_id: int):
    ensure_state(chat_id)
    items = get_filtered_items(chat_id)

    if not items:
        bot.send_message(
            chat_id,
            "Пока нет объявлений 😕\n"
            "Добавь первое командой:\n"
            "/add Куртка;0;Москва;Одежда",
            reply_markup=build_main_menu()
        )
        return

    idx = browse_state[chat_id]["index"] % len(items)
    browse_state[chat_id]["index"] = idx

    item = items[idx]
    text = format_item_text(item)
    kb = build_card_keyboard(item, chat_id)
    photo_id = item[5]

    if photo_id:
        bot.send_photo(chat_id, photo_id, caption=text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)

def edit_or_send_item(call, new_idx: int):
    chat_id = call.message.chat.id
    ensure_state(chat_id)
    items = get_filtered_items(chat_id)

    if not items:
        bot.answer_callback_query(call.id, "Нет объявлений")
        try:
            bot.edit_message_text(
                "По этому фильтру объявлений нет 😕",
                chat_id=chat_id,
                message_id=call.message.message_id
            )
        except Exception:
            pass
        return

    browse_state[chat_id]["index"] = new_idx % len(items)
    item = items[browse_state[chat_id]["index"]]
    text = format_item_text(item)
    kb = build_card_keyboard(item, chat_id)
    photo_id = item[5]

    try:
        if photo_id and call.message.content_type == "photo":
            bot.edit_message_caption(
                caption=text,
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=kb
            )
        elif photo_id:
            bot.send_photo(chat_id, photo_id, caption=text, reply_markup=kb)
        else:
            bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=kb
            )
    except Exception:
        if photo_id:
            bot.send_photo(chat_id, photo_id, caption=text, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)

# =========================
# COMMANDS
# =========================
@bot.message_handler(commands=["start"])
def start_cmd(message):
    ensure_state(message.chat.id)
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=build_main_menu())
    send_item(message.chat.id)

@bot.message_handler(commands=["add"])
def add_cmd(message):
    text = message.text.replace("/add", "", 1).strip()
    parts = [p.strip() for p in text.split(";")]

    if len(parts) < 4:
        bot.send_message(
            message.chat.id,
            "❌ Формат:\n/add Название;Цена;Город;Категория\n\n"
            "Пример:\n/add Куртка;0;Москва;Одежда"
        )
        return

    title, price_raw, city, category = parts[0], parts[1], parts[2], parts[3]

    try:
        price = int(price_raw)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Цена должна быть числом")
        return

    if category not in CATEGORIES:
        bot.send_message(
            message.chat.id,
            "❌ Категория неизвестна.\n"
            f"Доступно: {', '.join(CATEGORIES)}"
        )
        return

    add_item(title, price, city, category, message.chat.id, None)

    bot.send_message(
        message.chat.id,
        f"✅ Добавлено:\n"
        f"🧥 {title}\n"
        f"{format_price(price)}\n"
        f"📍 {city}\n"
        f"📦 {category}",
        reply_markup=build_main_menu()
    )

@bot.message_handler(commands=["addphoto"])
def addphoto_cmd(message):
    text = message.text.replace("/addphoto", "", 1).strip()
    parts = [p.strip() for p in text.split(";")]

    if len(parts) < 4:
        bot.send_message(
            message.chat.id,
            "❌ Формат:\n/addphoto Название;Цена;Город;Категория\n\n"
            "Пример:\n/addphoto Поло;0;Москва;Одежда"
        )
        return

    title, price_raw, city, category = parts[0], parts[1], parts[2], parts[3]

    try:
        price = int(price_raw)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Цена должна быть числом")
        return

    if category not in CATEGORIES:
        bot.send_message(
            message.chat.id,
            "❌ Категория неизвестна.\n"
            f"Доступно: {', '.join(CATEGORIES)}"
        )
        return

    pending_photo[message.chat.id] = {
        "title": title,
        "price": price,
        "city": city,
        "category": category
    }

    bot.send_message(
        message.chat.id,
        "📸 Теперь отправь фото одним следующим сообщением"
    )

@bot.message_handler(commands=["myads"])
def myads_cmd(message):
    rows = get_my_items(message.chat.id)
    if not rows:
        bot.send_message(message.chat.id, "У тебя пока нет объявлений")
        return

    text = "📦 Мои объявления:\n\n"
    for item in rows[:20]:
        text += format_item_text(item) + "\n\n"

    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["myfav"])
def myfav_cmd(message):
    rows = get_favorites(message.chat.id)
    if not rows:
        bot.send_message(message.chat.id, "❤️ У тебя пока нет лайков")
        return

    text = "❤️ Избранное:\n\n"
    for item in rows[:20]:
        text += format_item_text(item) + "\n\n"

    bot.send_message(message.chat.id, text)

# =========================
# REPLY BUTTONS
# =========================
@bot.message_handler(func=lambda m: m.text == "🏠 Смотреть")
def btn_show(message):
    send_item(message.chat.id)

@bot.message_handler(func=lambda m: m.text == "❤️ Избранное")
def btn_fav(message):
    myfav_cmd(message)

@bot.message_handler(func=lambda m: m.text == "📦 Мои объявления")
def btn_myads(message):
    myads_cmd(message)

@bot.message_handler(func=lambda m: m.text == "➕ Добавить")
def btn_add(message):
    bot.send_message(
        message.chat.id,
        "Добавить без фото:\n"
        "/add Название;Цена;Город;Категория\n\n"
        "Добавить с фото:\n"
        "/addphoto Название;Цена;Город;Категория"
    )

@bot.message_handler(func=lambda m: m.text == "♻️ Сбросить фильтры")
def btn_reset(message):
    ensure_state(message.chat.id)
    browse_state[message.chat.id]["city"] = None
    browse_state[message.chat.id]["category"] = None
    browse_state[message.chat.id]["index"] = 0
    bot.send_message(message.chat.id, "Фильтры сброшены")
    send_item(message.chat.id)

# =========================
# PHOTO
# =========================
@bot.message_handler(content_types=["photo"])
def photo_handler(message):
    if message.chat.id not in pending_photo:
        bot.send_message(message.chat.id, "Фото получено, но активного /addphoto сейчас нет")
        return

    data = pending_photo.pop(message.chat.id)
    photo_id = message.photo[-1].file_id

    add_item(
        title=data["title"],
        price=data["price"],
        city=data["city"],
        category=data["category"],
        owner_tg=message.chat.id,
        photo_id=photo_id
    )

    bot.send_message(
        message.chat.id,
        f"✅ Объявление с фото добавлено:\n"
        f"🧥 {data['title']}\n"
        f"{format_price(data['price'])}\n"
        f"📍 {data['city']}\n"
        f"📦 {data['category']}",
        reply_markup=build_main_menu()
    )

# =========================
# CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    ensure_state(chat_id)

    data = call.data

    if data == "next":
        current = browse_state[chat_id]["index"]
        edit_or_send_item(call, current + 1)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("fav:"):
        item_id = int(data.split(":")[1])

        if is_favorite(chat_id, item_id):
            remove_favorite(chat_id, item_id)
            bot.answer_callback_query(call.id, "Лайк убран 💔")
        else:
            add_favorite(chat_id, item_id)
            bot.answer_callback_query(call.id, "Добавлено в избранное ❤️")

        items = get_filtered_items(chat_id)
        if items:
            idx = browse_state[chat_id]["index"] % len(items)
            item = items[idx]
            kb = build_card_keyboard(item, chat_id)
            try:
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=kb
                )
            except Exception:
                pass
        return

    if data.startswith("take:"):
        item_id = int(data.split(":")[1])
        mark_claimed(item_id)
        bot.answer_callback_query(call.id, "✅ Отмечено как забрано")
        try:
            bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=None
            )
        except Exception:
            pass
        send_item(chat_id)
        return

    if data.startswith("delete:"):
        item_id = int(data.split(":")[1])
        if delete_item(item_id, chat_id):
            bot.answer_callback_query(call.id, "🗑 Удалено")
            try:
                if call.message.content_type == "photo":
                    bot.delete_message(chat_id, call.message.message_id)
                else:
                    bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            send_item(chat_id)
        else:
            bot.answer_callback_query(call.id, "Это не твое объявление")
        return

    if data == "menu_category":
        try:
            if call.message.content_type == "photo":
                bot.edit_message_caption(
                    caption="Выбери категорию:",
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=build_category_menu()
                )
            else:
                bot.edit_message_text(
                    text="Выбери категорию:",
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=build_category_menu()
                )
        except Exception:
            bot.send_message(chat_id, "Выбери категорию:", reply_markup=build_main_menu())
        bot.answer_callback_query(call.id)
        return

    if data == "menu_city":
        try:
            if call.message.content_type == "photo":
                bot.edit_message_caption(
                    caption="Выбери город:",
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=build_city_menu()
                )
            else:
                bot.edit_message_text(
                    text="Выбери город:",
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=build_city_menu()
                )
        except Exception:
            bot.send_message(chat_id, "Выбери город:", reply_markup=build_main_menu())
        bot.answer_callback_query(call.id)
        return

    if data.startswith("set_category:"):
        category = data.split(":", 1)[1]
        browse_state[chat_id]["category"] = category
        browse_state[chat_id]["index"] = 0
        bot.answer_callback_query(call.id, f"Категория: {category}")
        edit_or_send_item(call, 0)
        return

    if data.startswith("set_city:"):
        city = data.split(":", 1)[1]
        browse_state[chat_id]["city"] = None if city == "ANY" else city
        browse_state[chat_id]["index"] = 0
        bot.answer_callback_query(call.id, "Город обновлен")
        edit_or_send_item(call, 0)
        return

    if data == "reset_filters":
        browse_state[chat_id]["city"] = None
        browse_state[chat_id]["category"] = None
        browse_state[chat_id]["index"] = 0
        bot.answer_callback_query(call.id, "Фильтры сброшены")
        edit_or_send_item(call, 0)
        return

    if data == "back_to_cards":
        edit_or_send_item(call, browse_state[chat_id]["index"])
        bot.answer_callback_query(call.id)
        return

# =========================
# RUN
# =========================
print("=== BOT STARTING ===", flush=True)
print("BOT OBJECT CREATED", flush=True)

bot.remove_webhook()
print("Webhook removed", flush=True)

bot.infinity_polling(timeout=30, long_polling_timeout=20)
