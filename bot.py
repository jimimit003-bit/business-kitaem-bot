import os
import time
import sqlite3
from typing import Optional, List

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
    owner_tg INTEGER NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    is_taken INTEGER NOT NULL DEFAULT 0,
    bump_count INTEGER NOT NULL DEFAULT 0,
    last_bump_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS item_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    photo_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_tg INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT 'Жалоба',
    created_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE(reporter_tg, item_id)
)
""")

conn.commit()

# =========================
# MEMORY
# =========================
user_index = {}
pending_search = set()
user_filters = {}
pending_edit = {}
pending_create = {}
pending_replace_photo = {}
view_state = {}

# =========================
# CONSTANTS
# =========================
POPULAR_CITIES = ["Москва", "СПб", "Казань", "Екатеринбург"]
CATEGORIES = [
    "Одежда",
    "Обувь",
    "Аксессуары",
    "Детские товары",
    "Электроника",
    "Красота и здоровье",
    "Для дома и дачи",
    "Авто и запчасти",
    "Спецтехника",
    "Другое"
]
BUMP_COOLDOWN_SECONDS = 12 * 60 * 60
MAX_PHOTOS_PER_ITEM = 5

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


def add_item(title: str, price: int, city: str, category: str, owner_tg: int) -> int:
    cursor.execute("""
        INSERT INTO items (title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?)
    """, (title, price, city, category, owner_tg, now_ts()))
    conn.commit()
    return cursor.lastrowid


def add_item_photos(item_id: int, photo_ids: List[str]):
    for idx, photo_id in enumerate(photo_ids[:MAX_PHOTOS_PER_ITEM]):
        cursor.execute("""
            INSERT INTO item_photos (item_id, photo_id, position)
            VALUES (?, ?, ?)
        """, (item_id, photo_id, idx))
    conn.commit()


def replace_item_photos(item_id: int, photo_ids: List[str]):
    cursor.execute("DELETE FROM item_photos WHERE item_id = ?", (item_id,))
    conn.commit()
    add_item_photos(item_id, photo_ids)


def get_item_photos(item_id: int) -> List[str]:
    cursor.execute("""
        SELECT photo_id
        FROM item_photos
        WHERE item_id = ?
        ORDER BY position ASC, id ASC
    """, (item_id,))
    return [row[0] for row in cursor.fetchall()]


def update_item(item_id: int, owner_tg: int, title: str, price: int, city: str, category: str) -> bool:
    cursor.execute("""
        UPDATE items
        SET title = ?, price = ?, city = ?, category = ?
        WHERE id = ? AND owner_tg = ?
    """, (title, price, city, category, item_id, owner_tg))
    conn.commit()
    return cursor.rowcount > 0


def get_item_by_id(item_id: int):
    cursor.execute("""
        SELECT id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
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
        SELECT id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
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
        SELECT id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ?
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_active_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ? AND is_taken = 0
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_archive_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ? AND is_taken = 1
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def delete_item(item_id: int, owner_tg: int) -> bool:
    cursor.execute("DELETE FROM item_photos WHERE item_id = ?", (item_id,))
    cursor.execute("DELETE FROM items WHERE id = ? AND owner_tg = ?", (item_id, owner_tg))
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
        SELECT title, price, city, category, owner_tg, views, is_taken, bump_count, created_at
        FROM items
        WHERE id = ? AND owner_tg = ?
    """, (item_id, owner_tg))
    row = cursor.fetchone()
    if not row:
        return False

    photos = get_item_photos(item_id)
    title, price, city, category, owner_tg, views, is_taken, bump_count, created_at = row

    cursor.execute("DELETE FROM item_photos WHERE item_id = ?", (item_id,))
    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()

    cursor.execute("""
        INSERT INTO items (title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title, price, city, category, owner_tg,
        views, is_taken, bump_count + 1, now_ts(), created_at
    ))
    conn.commit()
    new_item_id = cursor.lastrowid
    add_item_photos(new_item_id, photos)
    return new_item_id


def add_favorite(user_id: int, item_id: int):
    cursor.execute(
        "INSERT OR IGNORE INTO favorites (user_id, item_id) VALUES (?, ?)",
        (user_id, item_id)
    )
    conn.commit()


def get_favorites(user_id: int):
    cursor.execute("SELECT item_id FROM favorites WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    return [r[0] for r in rows]


def add_like(user_id: int, item_id: int):
    cursor.execute(
        "INSERT OR IGNORE INTO likes (user_id, item_id) VALUES (?, ?)",
        (user_id, item_id)
    )
    conn.commit()


def remove_like(user_id: int, item_id: int):
    cursor.execute("DELETE FROM likes WHERE user_id = ? AND item_id = ?", (user_id, item_id))
    conn.commit()


def has_like(user_id: int, item_id: int) -> bool:
    cursor.execute(
        "SELECT 1 FROM likes WHERE user_id = ? AND item_id = ? LIMIT 1",
        (user_id, item_id)
    )
    return cursor.fetchone() is not None


def get_likes_count(item_id: int) -> int:
    cursor.execute("SELECT COUNT(*) FROM likes WHERE item_id = ?", (item_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


def add_view(item_id: int):
    cursor.execute("UPDATE items SET views = views + 1 WHERE id = ?", (item_id,))
    conn.commit()


def search_items(chat_id: int, query_text: str):
    ensure_filters(chat_id)
    f = user_filters[chat_id]

    query = """
        SELECT id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at
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
        query += " AND price <= 400"

    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_popular_items(limit: int = 10):
    cursor.execute("""
        SELECT i.id, i.title, i.price, i.city, i.category, i.owner_tg, i.views, i.is_taken, i.bump_count, i.last_bump_at, i.created_at,
               COUNT(l.id) AS likes_count
        FROM items i
        LEFT JOIN likes l ON i.id = l.item_id
        WHERE i.is_taken = 0
        GROUP BY i.id
        ORDER BY likes_count DESC, i.views DESC, i.id DESC
        LIMIT ?
    """, (limit,))
    return cursor.fetchall()


def add_report(reporter_tg: int, item_id: int, reason: str = "Жалоба"):
    cursor.execute("""
        INSERT OR IGNORE INTO reports (reporter_tg, item_id, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (reporter_tg, item_id, reason, now_ts()))
    conn.commit()


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

    if len(title) < 2 or len(city) < 1 or len(category) < 1 or price < 0:
        return None

    return title, price, city, category


def item_to_text(item, photo_idx: int = 0) -> str:
    item_id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at = item
    likes_count = get_likes_count(item_id)
    photos = get_item_photos(item_id)
    photos_line = ""

    if photos:
        safe_idx = min(photo_idx, len(photos) - 1)
        photos_line = f"\n📸 Фото: {safe_idx + 1}/{len(photos)}"

    text = f"🧥 {title}\n"
    text += "🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n"
    text += f"📍 {city}\n"
    text += f"📦 {category}\n"
    text += f"❤️ {likes_count} лайков\n"
    text += f"👁 {views} просмотров"
    text += photos_line

    if bump_count > 0:
        text += f"\n🚀 Поднимали: {bump_count} раз"

    return text


def set_view_state(chat_id: int, item_id: int, mode: str = "feed", photo_idx: int = 0):
    view_state[chat_id] = {
        "item_id": item_id,
        "mode": mode,
        "photo_idx": photo_idx
    }


def get_view_state(chat_id: int):
    return view_state.get(chat_id, {"item_id": None, "mode": "feed", "photo_idx": 0})
