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

# =========================
# DB
# =========================
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
    owner_tg INTEGER NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    is_taken INTEGER NOT NULL DEFAULT 0,
    bump_count INTEGER NOT NULL DEFAULT 0
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inviter_tg INTEGER NOT NULL,
    invited_tg INTEGER NOT NULL,
    UNIQUE(inviter_tg, invited_tg)
)
""")

conn.commit()

# =========================
# MEMORY
# =========================
ITEMS = []
user_index = {}          # chat_id -> current card index
pending_addphoto = {}    # chat_id -> {"title","price","city","category"}

# фильтры для каждого пользователя
user_filters = {}        # chat_id -> {"city": None, "category": None, "price": "any"}


# =========================
# CONSTANTS
# =========================
CATEGORIES = [
    "Одежда",
    "Обувь",
    "Техника",
    "Дом",
    "Детское",
    "Другое"
]

POPULAR_CITIES = [
    "Москва",
    "СПб",
    "Казань",
    "Екатеринбург"
]


# =========================
# DB HELPERS
# =========================
def ensure_filters(chat_id: int):
    if chat_id not in user_filters:
        user_filters[chat_id] = {
            "city": None,
            "category": None,
            "price": "any"
        }


def refresh_items():
    global ITEMS
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count
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
        INSERT INTO items (title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count)
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0)
    """, (title, price, city, category, photo_id, owner_tg))
    conn.commit()
    refresh_items()


def get_filtered_items(chat_id: int):
    ensure_filters(chat_id)

    f = user_filters[chat_id]

    query = """
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count
        FROM items
        WHERE is_taken = 0
    """
    params = []

    if f["city"]:
        query += " AND city = ?"
        params.append(f["city"])

    if f["category"]:
        query += " AND category = ?"
        params.append(f["category"])

    if f["price"] == "free":
        query += " AND price = 0"
    elif f["price"] == "under400":
        query += " AND price > 0 AND price <= 400"

    query += " ORDER BY id DESC"

    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_item_by_index(chat_id: int, idx: int):
    items = get_filtered_items(chat_id)
    if not items:
        return None
    return items[idx % len(items)]


def get_user_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count
        FROM items
        WHERE owner_tg = ?
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def delete_item(item_id: int, owner_tg: int) -> bool:
    cursor.execute(
        "DELETE FROM items WHERE id = ? AND owner_tg = ?",
        (item_id, owner_tg)
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    refresh_items()
    return deleted


def mark_taken(item_id: int):
    cursor.execute(
        "UPDATE items SET is_taken = 1 WHERE id = ?",
        (item_id,)
    )
    conn.commit()
    refresh_items()


def bump_item(item_id: int, owner_tg: int) -> bool:
    cursor.execute("""
        SELECT title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count
        FROM items
        WHERE id = ? AND owner_tg = ?
    """, (item_id, owner_tg))
    row = cursor.fetchone()

    if not row:
        return False

    title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count = row

    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()

    cursor.execute("""
        INSERT INTO items (title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count + 1))
    conn.commit()
    refresh_items()
    return True


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


def add_view(item_id: int):
    cursor.execute(
        "UPDATE items SET views = views + 1 WHERE id = ?",
        (item_id,)
    )
    conn.commit()


def add_referral(inviter_tg: int, invited_tg: int):
    if inviter_tg == invited_tg:
        return
    cursor.execute(
        "INSERT OR IGNORE INTO referrals (inviter_tg, invited_tg) VALUES (?, ?)",
        (inviter_tg, invited_tg)
    )
    conn.commit()


def get_referrals_count(inviter_tg: int) -> int:
    cursor.execute(
        "SELECT COUNT(*) FROM referrals WHERE inviter_tg = ?",
        (inviter_tg,)
    )
    row = cursor.fetchone()
    return row[0] if row else 0


# =========================
# UI HELPERS
# =========================
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton("🔎 Смотреть"),
        types.KeyboardButton("➕ Добавить")
    )
    kb.row(
        types.KeyboardButton("⚙️ Фильтры"),
        types.KeyboardButton("❤️ Избранное")
    )
    kb.row(
        types.KeyboardButton("🏠 Меню")
    )
    return kb


def submenu_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton("📦 Мои объявления"),
        types.KeyboardButton("📊 Статистика")
    )
    kb.row(
        types.KeyboardButton("🎁 Пригласить"),
        types.KeyboardButton("🆘 Помощь / правила")
    )
    kb.row(
        types.KeyboardButton("⬅️ Назад")
    )
    return kb


def filters_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton("📍 Город"),
        types.KeyboardButton("📦 Категория")
    )
    kb.row(
        types.KeyboardButton("💰 Цена"),
        types.KeyboardButton("♻️ Сбросить фильтры")
    )
    kb.row(
        types.KeyboardButton("⬅️ Назад")
    )
    return kb


def city_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton("Москва"),
        types.KeyboardButton("СПб")
    )
    kb.row(
        types.KeyboardButton("Казань"),
        types.KeyboardButton("Екатеринбург")
    )
    kb.row(
        types.KeyboardButton("🌍 Любой город")
    )
    kb.row(
        types.KeyboardButton("⬅️ Назад")
    )
    return kb


def category_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton("Одежда"),
        types.KeyboardButton("Обувь")
    )
    kb.row(
        types.KeyboardButton("Техника"),
        types.KeyboardButton("Дом")
    )
    kb.row(
        types.KeyboardButton("Детское"),
        types.KeyboardButton("Другое")
    )
    kb.row(
        types.KeyboardButton("🌍 Любая категория")
    )
    kb.row(
        types.KeyboardButton("⬅️ Назад")
    )
    return kb


def price_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton("🟢 Бесплатно"),
        types.KeyboardButton("🟡 До 400 ₽")
    )
    kb.row(
        types.KeyboardButton("⚪ Любая цена")
    )
    kb.row(
        types.KeyboardButton("⬅️ Назад")
    )
    return kb


def build_card_keyboard(item_id: int, viewer_tg: int, owner_tg: int):
    viewer_user_id = get_user_id(viewer_tg)
    likes_count = get_likes_count(item_id)

    if has_like(viewer_user_id, item_id):
        like_text = f"💔 Убрать лайк ({likes_count})"
    else:
        like_text = f"❤️ Лайк ({likes_count})"

    kb = types.InlineKeyboardMarkup()

    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton(like_text, callback_data="like")
    )

    kb.row(
        types.InlineKeyboardButton("❤️ В избранное", callback_data="fav"),
        types.InlineKeyboardButton("💬 Написать владельцу", url=f"tg://user?id={owner_tg}")
    )

    kb.row(
        types.InlineKeyboardButton("✅ Забрать", callback_data="take")
    )

    if owner_tg == viewer_tg:
        kb.row(
            types.InlineKeyboardButton("🚀 Поднять", callback_data="bump"),
            types.InlineKeyboardButton("🗑 Удалить", callback_data="delete")
        )

    return kb


def format_item_text(item) -> str:
    item_id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count = item

    text = f"🧥 {title}\n"
    text += "🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n"
    text += f"📍 {city}\n"
    text += f"📦 {category}\n"
    text += f"👁 {views} просмотров\n"

    if bump_count > 0:
        text += f"🚀 Поднимали: {bump_count} раз"

    return text


def show_item(chat_id: int, item, send_new: bool = True, edit_message_id: int | None = None):
    if not item:
        bot.send_message(
            chat_id,
            "По текущим фильтрам объявлений нет 😕",
            reply_markup=main_menu()
        )
        return

    item_id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count = item

    add_view(item_id)
    refresh_items()

    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count
        FROM items
        WHERE id = ?
    """, (item_id,))
    fresh_item = cursor.fetchone()

    text = format_item_text(fresh_item)
    reply_markup = build_card_keyboard(item_id, chat_id, owner_tg)
    photo_id = fresh_item[5]

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


# =========================
# COMMANDS
# =========================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    chat_id = message.chat.id

    parts = message.text.split()
    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            inviter_tg = int(parts[1].replace("ref_", ""))
            add_referral(inviter_tg, chat_id)
        except ValueError:
            pass

    get_user_id(chat_id)
    ensure_filters(chat_id)
    refresh_items()
    user_index[chat_id] = 0

    bot.send_message(chat_id, "Главное меню:", reply_markup=main_menu())

    item = get_item_by_index(chat_id, 0)
    show_item(chat_id, item)


@bot.message_handler(commands=['add'])
def add_cmd(message):
    chat_id = message.chat.id

    raw = message.text.replace('/add', '', 1).strip()
    parts = [p.strip() for p in raw.split(';')]

    if len(parts) < 4:
        bot.send_message(
            chat_id,
            "❌ Формат:\n/add Название;Цена;Город;Категория\n\n"
            "Пример:\n/add Куртка;0;Москва;Одежда"
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
        f"✅ Добавлено:\n🧥 {title}\n"
        + ("🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n")
        + f"📍 {city}\n"
        + f"📦 {category}",
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
            "❌ Формат:\n/addphoto Название;Цена;Город;Категория\n\n"
            "Пример:\n/addphoto Поло;0;Москва;Одежда"
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


# =========================
# PHOTO
# =========================
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


# =========================
# MAIN MENU BUTTONS
# =========================
@bot.message_handler(func=lambda m: m.text == "🔎 Смотреть")
def menu_watch(message):
    refresh_items()
    user_index[message.chat.id] = 0
    item = get_item_by_index(message.chat.id, 0)
    show_item(message.chat.id, item)


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
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count
        FROM items
        WHERE id IN ({placeholders}) AND is_taken = 0
        ORDER BY id DESC
    """, fav_ids)
    rows = cursor.fetchall()

    text = "❤️ Избранное:\n\n"
    for item in rows[:20]:
        item_id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count = item
        text += f"#{item_id} — {title} | "
        text += "Бесплатно" if price == 0 else f"{price} ₽"
        text += f" | {city} | {category} | 👁 {views}\n"

    bot.send_message(chat_id, text)


@bot.message_handler(func=lambda m: m.text == "⚙️ Фильтры")
def menu_filters(message):
    bot.send_message(message.chat.id, "Меню фильтров:", reply_markup=filters_menu())


@bot.message_handler(func=lambda m: m.text == "🏠 Меню")
def menu_sub(message):
    bot.send_message(message.chat.id, "Дополнительное меню:", reply_markup=submenu_menu())


# =========================
# SUBMENU
# =========================
@bot.message_handler(func=lambda m: m.text == "📦 Мои объявления")
def submenu_my_items(message):
    chat_id = message.chat.id
    rows = get_user_items(chat_id)

    if not rows:
        bot.send_message(chat_id, "У тебя пока нет своих объявлений")
        return

    text = "📦 Мои объявления:\n\n"
    for item in rows[:20]:
        item_id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count = item
        status = "Забрано" if is_taken else "Активно"
        text += f"#{item_id} — {title} | "
        text += "Бесплатно" if price == 0 else f"{price} ₽"
        text += f" | {city} | {category} | 👁 {views} | {status}\n"

    bot.send_message(chat_id, text, reply_markup=submenu_menu())


@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def submenu_stats(message):
    chat_id = message.chat.id
    refs = get_referrals_count(chat_id)
    my_items = get_user_items(chat_id)

    total_views = sum(item[7] for item in my_items) if my_items else 0
    total_likes = 0
    for item in my_items:
        total_likes += get_likes_count(item[0])

    text = (
        f"📊 Твоя статистика:\n\n"
        f"👥 Приглашено друзей: {refs}\n"
        f"📦 Твоих объявлений: {len(my_items)}\n"
        f"👁 Просмотров объявлений: {total_views}\n"
        f"❤️ Лайков на объявлениях: {total_likes}"
    )
    bot.send_message(chat_id, text, reply_markup=submenu_menu())


@bot.message_handler(func=lambda m: m.text == "🎁 Пригласить")
def submenu_invite(message):
    me = bot.get_me()
    invite_link = f"https://t.me/{me.username}?start=ref_{message.chat.id}"
    count = get_referrals_count(message.chat.id)

    bot.send_message(
        message.chat.id,
        "🎁 Пригласи друга по своей ссылке:\n\n"
        f"{invite_link}\n\n"
        f"Уже приглашено: {count}",
        reply_markup=submenu_menu()
    )


@bot.message_handler(func=lambda m: m.text == "🆘 Помощь / правила")
def submenu_help(message):
    text = (
        "🆘 Помощь / правила\n\n"
        "1. Размещай реальные вещи\n"
        "2. Не публикуй запрещённые товары\n"
        "3. Будь вежлив с другими пользователями\n"
        "4. Если вещь уже забрали — жми ✅ Забрать\n\n"
        "Команды:\n"
        "/add Название;Цена;Город;Категория\n"
        "/addphoto Название;Цена;Город;Категория"
    )
    bot.send_message(message.chat.id, text, reply_markup=submenu_menu())


@bot.message_handler(func=lambda m: m.text == "⬅️ Назад")
def any_back(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu())


# =========================
# FILTER MENUS
# =========================
@bot.message_handler(func=lambda m: m.text == "📍 Город")
def filter_city_menu(message):
    bot.send_message(message.chat.id, "Выбери город:", reply_markup=city_menu())


@bot.message_handler(func=lambda m: m.text == "📦 Категория")
def filter_category_menu(message):
    bot.send_message(message.chat.id, "Выбери категорию:", reply_markup=category_menu())


@bot.message_handler(func=lambda m: m.text == "💰 Цена")
def filter_price_menu(message):
    bot.send_message(message.chat.id, "Выбери цену:", reply_markup=price_menu())


@bot.message_handler(func=lambda m: m.text in POPULAR_CITIES or m.text == "🌍 Любой город")
def set_city_filter(message):
    ensure_filters(message.chat.id)
    user_filters[message.chat.id]["city"] = None if message.text == "🌍 Любой город" else message.text
    user_index[message.chat.id] = 0
    bot.send_message(message.chat.id, "Фильтр по городу обновлён", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text in CATEGORIES or m.text == "🌍 Любая категория")
def set_category_filter(message):
    ensure_filters(message.chat.id)
    user_filters[message.chat.id]["category"] = None if message.text == "🌍 Любая категория" else message.text
    user_index[message.chat.id] = 0
    bot.send_message(message.chat.id, "Фильтр по категории обновлён", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text in ["🟢 Бесплатно", "🟡 До 400 ₽", "⚪ Любая цена"])
def set_price_filter(message):
    ensure_filters(message.chat.id)

    if message.text == "🟢 Бесплатно":
        user_filters[message.chat.id]["price"] = "free"
    elif message.text == "🟡 До 400 ₽":
        user_filters[message.chat.id]["price"] = "under400"
    else:
        user_filters[message.chat.id]["price"] = "any"

    user_index[message.chat.id] = 0
    bot.send_message(message.chat.id, "Фильтр по цене обновлён", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text == "♻️ Сбросить фильтры")
def reset_filters(message):
    ensure_filters(message.chat.id)
    user_filters[message.chat.id] = {
        "city": None,
        "category": None,
        "price": "any"
    }
    user_index[message.chat.id] = 0
    bot.send_message(message.chat.id, "Все фильтры сброшены ♻️", reply_markup=main_menu())


# =========================
# CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    if call.data == "next":
        items = get_filtered_items(chat_id)

        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0)
        idx = (idx + 1) % len(items)
        user_index[chat_id] = idx

        item = items[idx]
        show_item(chat_id, item, send_new=False, edit_message_id=call.message.message_id)
        bot.answer_callback_query(call.id)

    elif call.data == "like":
        items = get_filtered_items(chat_id)
        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0) % len(items)
        item = items[idx]
        item_id = item[0]
        user_id = get_user_id(chat_id)

        if has_like(user_id, item_id):
            remove_like(user_id, item_id)
            bot.answer_callback_query(call.id, "Лайк убран")
        else:
            add_like(user_id, item_id)
            bot.answer_callback_query(call.id, "Лайк поставлен")

        items = get_filtered_items(chat_id)
        if items:
            idx = min(user_index.get(chat_id, 0), len(items) - 1)
            user_index[chat_id] = idx
            show_item(chat_id, items[idx], send_new=False, edit_message_id=call.message.message_id)

    elif call.data == "fav":
        items = get_filtered_items(chat_id)
        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0) % len(items)
        item = items[idx]
        item_id = item[0]
        user_id = get_user_id(chat_id)

        add_favorite(user_id, item_id)
        bot.answer_callback_query(call.id, "Добавлено в избранное ❤️")

    elif call.data == "take":
        items = get_filtered_items(chat_id)
        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0) % len(items)
        item = items[idx]
        item_id = item[0]
        owner_tg = item[6]

        if owner_tg == chat_id:
            mark_taken(item_id)
            bot.answer_callback_query(call.id, "Отмечено как забрано ✅")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass

            next_item = get_item_by_index(chat_id, 0)
            show_item(chat_id, next_item)
        else:
            bot.answer_callback_query(call.id, "Свяжись с владельцем через кнопку 💬")

    elif call.data == "delete":
        items = get_filtered_items(chat_id)
        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0) % len(items)
        item = items[idx]
        item_id = item[0]
        owner_tg = item[6]

        if owner_tg != chat_id:
            bot.answer_callback_query(call.id, "Это не твоё объявление")
            return

        ok = delete_item(item_id, chat_id)
        if ok:
            bot.answer_callback_query(call.id, "Удалено 🗑")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass

            next_item = get_item_by_index(chat_id, 0)
            show_item(chat_id, next_item)
        else:
            bot.answer_callback_query(call.id, "Не удалось удалить")

    elif call.data == "bump":
        items = get_filtered_items(chat_id)
        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0) % len(items)
        item = items[idx]
        item_id = item[0]
        owner_tg = item[6]

        if owner_tg != chat_id:
            bot.answer_callback_query(call.id, "Можно поднимать только свои объявления")
            return

        ok = bump_item(item_id, chat_id)
        if ok:
            bot.answer_callback_query(call.id, "Объявление поднято 🚀")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass

            user_index[chat_id] = 0
            next_item = get_item_by_index(chat_id, 0)
            show_item(chat_id, next_item)
        else:
            bot.answer_callback_query(call.id, "Не удалось поднять")


# =========================
# SAFE POLLING
# =========================
def run_bot():
    print("=== BOT STARTING ===", flush=True)
    bot.remove_webhook()
    print("Webhook removed", flush=True)

    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
        except ApiTelegramException as e:
            print(f"Telegram error: {e}", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"Unexpected error: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    refresh_items()
    run_bot()
