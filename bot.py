import os
import time
import sqlite3
from typing import Optional

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
    city TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Без категории',
    photo_id TEXT,
    owner_tg INTEGER NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    is_taken INTEGER NOT NULL DEFAULT 0,
    bump_count INTEGER NOT NULL DEFAULT 0,
    last_bump_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT 0
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


def ensure_column(table: str, column: str, ddl: str):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cursor.fetchall()]
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        conn.commit()


ensure_column("items", "views", "views INTEGER NOT NULL DEFAULT 0")
ensure_column("items", "is_taken", "is_taken INTEGER NOT NULL DEFAULT 0")
ensure_column("items", "bump_count", "bump_count INTEGER NOT NULL DEFAULT 0")
ensure_column("items", "last_bump_at", "last_bump_at INTEGER NOT NULL DEFAULT 0")
ensure_column("items", "created_at", "created_at INTEGER NOT NULL DEFAULT 0")
ensure_column("items", "category", "category TEXT NOT NULL DEFAULT 'Без категории'")
ensure_column("items", "photo_id", "photo_id TEXT")

# =========================
# MEMORY
# =========================
user_index = {}
pending_addphoto = {}
pending_search = set()
user_filters = {}
pending_edit = {}
pending_create = {}
pending_replace_photo = {}

# =========================
# CONSTANTS
# =========================
POPULAR_CITIES = ["Москва", "СПб", "Казань", "Екатеринбург"]
CATEGORIES = ["Одежда", "Обувь", "Техника", "Дом", "Детское", "Другое"]
BUMP_COOLDOWN_SECONDS = 12 * 60 * 60

# =========================
# HELPERS
# =========================
def now_ts() -> int:
    return int(time.time())


def ensure_filters(chat_id: int):
    if chat_id not in user_filters:
        user_filters[chat_id] = {
            "city": None,
            "category": None,
            "price": "any",
        }


def reset_filters(chat_id: int):
    user_filters[chat_id] = {
        "city": None,
        "category": None,
        "price": "any",
    }


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


def add_item(title: str, price: int, city: str, category: str, owner_tg: int, photo_id: Optional[str] = None):
    cursor.execute("""
        INSERT INTO items (title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?)
    """, (title, price, city, category, photo_id, owner_tg, now_ts()))
    conn.commit()


def update_item(item_id: int, owner_tg: int, title: str, price: int, city: str, category: str) -> bool:
    cursor.execute("""
        UPDATE items
        SET title = ?, price = ?, city = ?, category = ?
        WHERE id = ? AND owner_tg = ?
    """, (title, price, city, category, item_id, owner_tg))
    conn.commit()
    return cursor.rowcount > 0


def update_item_photo(item_id: int, owner_tg: int, photo_id: str) -> bool:
    cursor.execute("""
        UPDATE items
        SET photo_id = ?
        WHERE id = ? AND owner_tg = ?
    """, (photo_id, item_id, owner_tg))
    conn.commit()
    return cursor.rowcount > 0


def get_item_by_id(item_id: int):
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE id = ?
    """, (item_id,))
    return cursor.fetchone()


def get_total_active_items() -> int:
    cursor.execute("SELECT COUNT(*) FROM items WHERE is_taken = 0")
    row = cursor.fetchone()
    return row[0] if row else 0


def get_filtered_items(chat_id: int):
    ensure_filters(chat_id)
    f = user_filters[chat_id]

    query = """
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
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
        query += " AND price >= 0 AND price <= 400"

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
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ?
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_active_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ? AND is_taken = 0
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_archive_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ? AND is_taken = 1
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def delete_item(item_id: int, owner_tg: int) -> bool:
    cursor.execute(
        "DELETE FROM items WHERE id = ? AND owner_tg = ?",
        (item_id, owner_tg)
    )
    conn.commit()
    return cursor.rowcount > 0


def mark_taken(item_id: int, owner_tg: int) -> bool:
    cursor.execute("""
        UPDATE items
        SET is_taken = 1
        WHERE id = ? AND owner_tg = ?
    """, (item_id, owner_tg))
    conn.commit()
    return cursor.rowcount > 0


def restore_item(item_id: int, owner_tg: int) -> bool:
    cursor.execute("""
        UPDATE items
        SET is_taken = 0
        WHERE id = ? AND owner_tg = ?
    """, (item_id, owner_tg))
    conn.commit()
    return cursor.rowcount > 0


def can_bump_item(item_id: int, owner_tg: int):
    cursor.execute("""
        SELECT last_bump_at
        FROM items
        WHERE id = ? AND owner_tg = ?
    """, (item_id, owner_tg))
    row = cursor.fetchone()

    if not row:
        return False, 0

    last_bump = row[0] or 0
    if last_bump == 0:
        return True, 0

    passed = now_ts() - last_bump
    if passed >= BUMP_COOLDOWN_SECONDS:
        return True, 0

    remain = BUMP_COOLDOWN_SECONDS - passed
    return False, remain


def bump_item(item_id: int, owner_tg: int):
    cursor.execute("""
        SELECT title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, created_at
        FROM items
        WHERE id = ? AND owner_tg = ?
    """, (item_id, owner_tg))
    row = cursor.fetchone()
    if not row:
        return False

    title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, created_at = row

    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()

    cursor.execute("""
        INSERT INTO items (title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title, price, city, category, photo_id, owner_tg,
        views, is_taken, bump_count + 1, now_ts(), created_at
    ))
    conn.commit()
    return True


def add_favorite(user_id: int, item_id: int):
    cursor.execute(
        "INSERT OR IGNORE INTO favorites (user_id, item_id) VALUES (?, ?)",
        (user_id, item_id)
    )
    conn.commit()


def get_favorites(user_id: int):
    cursor.execute(
        "SELECT item_id FROM favorites WHERE user_id = ?",
        (user_id,)
    )
    rows = cursor.fetchall()
    return [r[0] for r in rows]


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


def search_items(chat_id: int, query_text: str):
    ensure_filters(chat_id)
    f = user_filters[chat_id]

    query = """
        SELECT id, title, price, city, category, photo_id, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE is_taken = 0
          AND LOWER(title) LIKE ?
    """
    params = [f"%{query_text.lower()}%"]

    if f["city"]:
        query += " AND city = ?"
        params.append(f["city"])

    if f["category"]:
        query += " AND category = ?"
        params.append(f["category"])

    if f["price"] == "free":
        query += " AND price = 0"
    elif f["price"] == "under400":
        query += " AND price >= 0 AND price <= 400"

    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_popular_items(limit: int = 10):
    cursor.execute("""
        SELECT i.id, i.title, i.price, i.city, i.category, i.photo_id, i.owner_tg, i.views, i.is_taken, i.bump_count, i.last_bump_at, i.created_at,
               COUNT(l.id) AS likes_count
        FROM items i
        LEFT JOIN likes l ON i.id = l.item_id
        WHERE i.is_taken = 0
        GROUP BY i.id
        ORDER BY likes_count DESC, i.views DESC, i.id DESC
        LIMIT ?
    """, (limit,))
    return cursor.fetchall()


def format_seconds_to_human(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def filters_status_text(chat_id: int) -> str:
    ensure_filters(chat_id)
    f = user_filters[chat_id]

    city = f["city"] if f["city"] else "Любой"
    category = f["category"] if f["category"] else "Любая"

    if f["price"] == "free":
        price = "Бесплатно"
    elif f["price"] == "under400":
        price = "До 400 ₽"
    else:
        price = "Любая"

    return (
        "Текущие фильтры:\n\n"
        f"📍 Город: {city}\n"
        f"📦 Категория: {category}\n"
        f"💰 Цена: {price}"
    )


def short_item_label(item):
    item_id, title, price, city, category, *_ = item
    price_text = "Бесплатно" if price == 0 else f"{price} ₽"
    return f"#{item_id} {title} | {price_text} | {city}"


def parse_edit_text(text: str):
    parts = [p.strip() for p in text.split(";")]
    if len(parts) < 4:
        return None

    title, price_raw, city, category = parts[0], parts[1], parts[2], parts[3]

    try:
        price = int(price_raw)
    except ValueError:
        return None

    if len(title) < 2 or len(city) < 1 or len(category) < 1:
        return None

    return title, price, city, category


# =========================
# STEP-BY-STEP CREATE
# =========================
def cancel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("❌ Отмена")
    return kb


def category_pick_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Одежда", "Обувь")
    kb.row("Техника", "Дом")
    kb.row("Детское", "Другое")
    kb.row("❌ Отмена")
    return kb


def photo_step_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("⏭️ Без фото")
    kb.row("❌ Отмена")
    return kb


def start_create_flow(chat_id: int):
    pending_create[chat_id] = {
        "step": "title",
        "data": {}
    }
    bot.send_message(
        chat_id,
        "➕ Добавление объявления\n\nНапиши название вещи:",
        reply_markup=cancel_kb()
    )


def create_preview_text(data: dict) -> str:
    price = data.get("price", 0)
    text = "✅ Объявление создано:\n\n"
    text += f"🧥 {data.get('title', '')}\n"
    text += "🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n"
    text += f"📍 {data.get('city', '')}\n"
    text += f"📦 {data.get('category', '')}"
    return text


def finish_create_without_photo(chat_id: int):
    data = pending_create[chat_id]["data"]
    add_item(
        title=data["title"],
        price=data["price"],
        city=data["city"],
        category=data["category"],
        owner_tg=chat_id,
        photo_id=None
    )
    pending_create.pop(chat_id, None)
    bot.send_message(chat_id, create_preview_text(data), reply_markup=main_menu())


def finish_create_with_photo(chat_id: int, photo_id: str):
    data = pending_create[chat_id]["data"]
    add_item(
        title=data["title"],
        price=data["price"],
        city=data["city"],
        category=data["category"],
        owner_tg=chat_id,
        photo_id=photo_id
    )
    pending_create.pop(chat_id, None)
    bot.send_message(chat_id, create_preview_text(data), reply_markup=main_menu())


# =========================
# UI
# =========================
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🔎 Смотреть", "➕ Добавить")
    kb.row("⚙️ Фильтры", "❤️ Избранное")
    kb.row("🏠 Меню")
    return kb


def submenu_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📦 Мои объявления", "🗂 Архив")
    kb.row("📊 Статистика", "🎁 Пригласить")
    kb.row("🆘 Помощь / правила", "🔥 Популярное")
    kb.row("🔍 Поиск")
    kb.row("⬅️ Назад")
    return kb


def filters_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📍 Город", "📦 Категория")
    kb.row("💰 Цена", "🔎 Показать")
    kb.row("♻️ Сбросить фильтры")
    kb.row("⬅️ Назад")
    return kb


def city_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Москва", "СПб")
    kb.row("Казань", "Екатеринбург")
    kb.row("🌍 Любой город")
    kb.row("⬅️ К фильтрам")
    return kb


def category_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Одежда", "Обувь")
    kb.row("Техника", "Дом")
    kb.row("Детское", "Другое")
    kb.row("🌍 Любая категория")
    kb.row("⬅️ К фильтрам")
    return kb


def price_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🟢 Бесплатно", "🟡 До 400 ₽")
    kb.row("⚪ Любая цена")
    kb.row("⬅️ К фильтрам")
    return kb


def show_filters_menu(chat_id: int, notice: Optional[str] = None):
    text = filters_status_text(chat_id)
    if notice:
        text = f"{notice}\n\n{text}"
    bot.send_message(chat_id, text, reply_markup=filters_menu())


def build_card_keyboard(item_id: int, viewer_tg: int, owner_tg: int):
    viewer_user_id = get_user_id(viewer_tg)
    likes_count = get_likes_count(item_id)
    like_text = f"💔 Убрать лайк ({likes_count})" if has_like(viewer_user_id, item_id) else f"❤️ Лайк ({likes_count})"

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton(like_text, callback_data="like")
    )
    kb.row(
        types.InlineKeyboardButton("❤️ В избранное", callback_data="fav"),
        types.InlineKeyboardButton("💬 Написать владельцу", url=f"tg://user?id={owner_tg}")
    )

    if owner_tg == viewer_tg:
        kb.row(
            types.InlineKeyboardButton("✅ Отдано", callback_data=f"done_{item_id}"),
            types.InlineKeyboardButton("🚀

