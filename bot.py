import os
import time
import sqlite3
from typing import Optional, List, Dict

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("TOKEN не найден в переменных окружения")

DB_PATH = os.getenv("DB_PATH", "darom.db")
bot = telebot.TeleBot(TOKEN)

# =========================
# CONFIG
# =========================
POPULAR_CITIES = ["Москва", "СПб", "Казань", "Екатеринбург"]

CATEGORIES: Dict[str, List[str]] = {
    "Одежда": ["Мужская", "Женская", "Детская"],
    "Обувь": ["Мужская", "Женская", "Детская"],
    "Аксессуары": ["Сумки", "Ремни", "Украшения", "Очки", "Другое"],
    "Детские товары": ["Игрушки", "Одежда", "Обувь", "Коляски", "Другое"],
    "Электроника": ["Телефоны", "Ноутбуки", "Планшеты", "Наушники", "Бытовая техника", "Другое"],
    "Красота и здоровье": ["Косметика", "Уход", "Парфюм", "Техника", "Другое"],
    "Для дома и дачи": ["Мебель", "Посуда", "Текстиль", "Инструменты", "Другое"],
    "Авто и запчасти": ["Шины", "Диски", "Запчасти", "Аксессуары", "Другое"],
    "Спецтехника": ["Инструменты", "Оборудование", "Запчасти", "Другое"],
    "Другое": ["Разное"]
}

BROWSE_CATEGORY_BUTTONS = {
    "👕 Одежда": "Одежда",
    "👟 Обувь": "Обувь",
    "📱 Электроника": "Электроника",
    "🧸 Детям": "Детские товары",
    "🏠 Дом и дача": "Для дома и дачи",
    "🚗 Авто": "Авто и запчасти",
}

REPORT_REASONS = [
    "Спам",
    "Мошенничество",
    "Запрещённый товар",
    "Оскорбление",
    "Другое"
]

BUMP_COOLDOWN_SECONDS = 12 * 60 * 60
MAX_PHOTOS_PER_ITEM = 5

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
    subcategory TEXT NOT NULL DEFAULT '',
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
    created_at INTEGER NOT NULL DEFAULT 0
)
""")

conn.commit()


def ensure_column(table_name: str, column_name: str, column_def: str):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
        conn.commit()


ensure_column("items", "subcategory", "TEXT NOT NULL DEFAULT ''")

# =========================
# MEMORY
# =========================
user_index = {}
pending_search = set()
user_filters = {}
pending_create = {}
pending_replace_photo = {}
view_state = {}

# =========================
# HELPERS
# =========================
def now_ts() -> int:
    return int(time.time())
    
def go_main_menu(chat_id: int, text: str = "Главное меню:"):
    pending_search.discard(chat_id)
    bot.send_message(chat_id, text, reply_markup=main_menu())

def category_names() -> List[str]:
    return list(CATEGORIES.keys())


def get_subcategories(category: str) -> List[str]:
    return CATEGORIES.get(category, ["Разное"])


def ensure_filters(chat_id: int):
    if chat_id not in user_filters:
        user_filters[chat_id] = {
            "city": None,
            "category": None,
            "subcategory": None,
            "price": "any",
        }


def reset_filters(chat_id: int):
    user_filters[chat_id] = {
        "city": None,
        "category": None,
        "subcategory": None,
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


def add_item(title: str, price: int, city: str, category: str, subcategory: str, owner_tg: int) -> int:
    cursor.execute("""
        INSERT INTO items (
            title, price, city, category, subcategory, owner_tg,
            views, is_taken, bump_count, last_bump_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?)
    """, (title, price, city, category, subcategory, owner_tg, now_ts()))
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


def update_item(item_id: int, owner_tg: int, title: str, price: int, city: str, category: str, subcategory: str) -> bool:
    cursor.execute("""
        UPDATE items
        SET title = ?, price = ?, city = ?, category = ?, subcategory = ?
        WHERE id = ? AND owner_tg = ?
    """, (title, price, city, category, subcategory, item_id, owner_tg))
    conn.commit()
    return cursor.rowcount > 0


def get_item_by_id(item_id: int):
    cursor.execute("""
        SELECT id, title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
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
        SELECT id, title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
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

    if f["subcategory"]:
        query += " AND subcategory = ?"
        params.append(f["subcategory"])

    if f["price"] == "free":
        query += " AND price = 0"
    elif f["price"] == "under400":
        query += " AND price > 0 AND price <= 400"

    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_items_for_browse(mode: str, category: Optional[str] = None):
    query = """
        SELECT id, title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE is_taken = 0
    """
    params = []

    if mode == "free":
        query += " AND price = 0"
    elif mode == "cheap":
        query += " AND price > 0 AND price <= 400"

    if category:
        query += " AND category = ?"
        params.append(category)

    if mode == "all":
        query += """
            ORDER BY
                CASE
                    WHEN price = 0 THEN 0
                    WHEN price > 0 AND price <= 400 THEN 1
                    ELSE 2
                END,
                id DESC
        """
    else:
        query += " ORDER BY id DESC"

    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_user_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ?
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_active_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ? AND is_taken = 0
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_archive_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
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
        SELECT title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, created_at
        FROM items
        WHERE id = ? AND owner_tg = ?
    """, (item_id, owner_tg))
    row = cursor.fetchone()
    if not row:
        return False

    photos = get_item_photos(item_id)
    title, price, city, category, subcategory, owner_tg, views, is_taken, bump_count, created_at = row

    cursor.execute("SELECT user_id FROM likes WHERE item_id = ?", (item_id,))
    likes_rows = cursor.fetchall()

    cursor.execute("SELECT user_id FROM favorites WHERE item_id = ?", (item_id,))
    fav_rows = cursor.fetchall()

    cursor.execute("SELECT reporter_tg, reason, created_at FROM reports WHERE item_id = ?", (item_id,))
    report_rows = cursor.fetchall()

    cursor.execute("DELETE FROM item_photos WHERE item_id = ?", (item_id,))
    cursor.execute("DELETE FROM likes WHERE item_id = ?", (item_id,))
    cursor.execute("DELETE FROM favorites WHERE item_id = ?", (item_id,))
    cursor.execute("DELETE FROM reports WHERE item_id = ?", (item_id,))
    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()

    cursor.execute("""
        INSERT INTO items (
            title, price, city, category, subcategory, owner_tg,
            views, is_taken, bump_count, last_bump_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title, price, city, category, subcategory, owner_tg,
        views, is_taken, bump_count + 1, now_ts(), created_at
    ))
    conn.commit()

    new_item_id = cursor.lastrowid
    add_item_photos(new_item_id, photos)

    for like_row in likes_rows:
        cursor.execute(
            "INSERT OR IGNORE INTO likes (user_id, item_id) VALUES (?, ?)",
            (like_row[0], new_item_id)
        )

    for fav_row in fav_rows:
        cursor.execute(
            "INSERT OR IGNORE INTO favorites (user_id, item_id) VALUES (?, ?)",
            (fav_row[0], new_item_id)
        )

    for reporter_tg, reason, created_at_value in report_rows:
        cursor.execute("""
            INSERT INTO reports (reporter_tg, item_id, reason, created_at)
            VALUES (?, ?, ?, ?)
        """, (reporter_tg, new_item_id, reason, created_at_value))

    conn.commit()
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
        SELECT id, title, price, city, category, subcategory, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
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

    if f["subcategory"]:
        query += " AND subcategory = ?"
        params.append(f["subcategory"])

    if f["price"] == "free":
        query += " AND price = 0"
    elif f["price"] == "under400":
        query += " AND price > 0 AND price <= 400"

    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_popular_items(limit: int = 10):
    cursor.execute("""
        SELECT i.id, i.title, i.price, i.city, i.category, i.subcategory, i.owner_tg,
               i.views, i.is_taken, i.bump_count, i.last_bump_at, i.created_at,
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
        INSERT INTO reports (reporter_tg, item_id, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (reporter_tg, item_id, reason, now_ts()))
    conn.commit()


def get_reports_count(item_id: int) -> int:
    cursor.execute("SELECT COUNT(*) FROM reports WHERE item_id = ?", (item_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


def format_seconds_to_human(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def short_item_label(item):
    item_id, title, price, city, category, subcategory, *_ = item
    price_text = "Бесплатно" if price == 0 else f"{price} ₽"
    return f"#{item_id} {title} | {price_text} | {city}"


def item_to_text(item, photo_idx: int = 0) -> str:
    item_id, title, price, city, category, subcategory, owner_tg, views, is_taken, bump_count, last_bump_at, created_at = item

    likes_count = get_likes_count(item_id)
    photos = get_item_photos(item_id)
    reports_count = get_reports_count(item_id)

    text = f"🧥 {title}\n"
    text += "🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n"
    text += f"📍 {city}\n"
    text += f"📦 {category}\n"
    if subcategory:
        text += f"📂 {subcategory}\n"
    text += f"❤️ {likes_count} лайков\n"
    text += f"👁 {views} просмотров"

    if photos:
        safe_idx = min(photo_idx, len(photos) - 1)
        text += f"\n📸 Фото: {safe_idx + 1}/{len(photos)}"

    if bump_count > 0:
        text += f"\n🚀 Поднимали: {bump_count} раз"

    if reports_count > 0:
        text += f"\n⚠️ Жалоб: {reports_count}"

    return text


def build_share_text(item_id: int) -> str:
    item = get_item_by_id(item_id)
    if not item:
        return "Объявление не найдено"

    title = item[1]
    price = item[2]
    city = item[3]
    category = item[4]
    subcategory = item[5]
    price_text = "Бесплатно" if price == 0 else f"{price} ₽"
    username = bot.get_me().username

    subcategory_text = f"\n📂 {subcategory}" if subcategory else ""

    return (
        "📍 Поделиться объявлением\n\n"
        f"🧥 {title}\n"
        f"💰 {price_text}\n"
        f"📍 {city}\n"
        f"📦 {category}{subcategory_text}\n\n"
        "Открыть в боте:\n"
        f"https://t.me/{username}?start=item_{item_id}"
    )


def send_owner_notification(item_id: int, text: str, exclude_tg: Optional[int] = None):
    item = get_item_by_id(item_id)
    if not item:
        return

    owner_tg = item[6]
    if exclude_tg is not None and owner_tg == exclude_tg:
        return

    try:
        bot.send_message(owner_tg, text)
    except Exception:
        pass


def get_user_profile_text(chat_id: int) -> str:
    my_items = get_user_items(chat_id)
    active_items = [x for x in my_items if x[8] == 0]
    archive_items = [x for x in my_items if x[8] == 1]
    total_views = sum(item[7] for item in my_items) if my_items else 0
    total_likes = sum(get_likes_count(item[0]) for item in my_items) if my_items else 0
    referrals = get_referrals_count(chat_id)

    return (
        "👤 Профиль\n\n"
        f"📦 Всего объявлений: {len(my_items)}\n"
        f"🟢 Активных: {len(active_items)}\n"
        f"🗂 В архиве: {len(archive_items)}\n"
        f"👁 Всего просмотров: {total_views}\n"
        f"❤️ Всего лайков: {total_likes}\n"
        f"👥 Приглашено друзей: {referrals}"
    )


def set_view_state(chat_id: int, item_id: int, mode: str = "feed", photo_idx: int = 0):
    view_state[chat_id] = {
        "item_id": item_id,
        "mode": mode,
        "photo_idx": photo_idx
    }


def get_view_state(chat_id: int):
    return view_state.get(chat_id, {"item_id": None, "mode": "feed", "photo_idx": 0})


# =========================
# KEYBOARDS
# =========================
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🔎 Смотреть все", "✍️ Поиск по названию")
    kb.row("⚙️ Фильтры", "➕ Добавить")
    kb.row("👤 Профиль")
    return kb


def submenu_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📦 Мои объявления", "🗂 Архив")
    kb.row("🔥 Популярное", "❤️ Избранное")
    kb.row("📊 Статистика", "🎁 Пригласить")
    kb.row("🆘 Помощь", "📜 Правила")
    kb.row("⬅️ Назад")
    return kb


def browse_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📦 Все объявления")
    kb.row("🎁 Бесплатно", "💸 За копейки")
    kb.row("👕 Одежда", "👟 Обувь")
    kb.row("📱 Электроника", "🧸 Детям")
    kb.row("🏠 Дом и дача", "🚗 Авто")
    kb.row("⬅️ Назад")
    return kb


def cancel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("❌ Отмена")
    return kb


def category_pick_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    names = category_names()
    for i in range(0, len(names), 2):
        if i + 1 < len(names):
            kb.row(names[i], names[i + 1])
        else:
            kb.row(names[i])
    kb.row("❌ Отмена")
    return kb


def subcategory_pick_kb(category: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    subs = get_subcategories(category)
    for i in range(0, len(subs), 2):
        if i + 1 < len(subs):
            kb.row(subs[i], subs[i + 1])
        else:
            kb.row(subs[i])
    kb.row("❌ Отмена")
    return kb


def photo_step_kb(photo_count: int = 0):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if photo_count == 0:
        kb.row("✅ Готово без фото")
    else:
        kb.row("✅ Опубликовать")
    kb.row("❌ Отмена")
    return kb


def replace_photo_step_kb(photo_count: int = 0):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if photo_count > 0:
        kb.row("✅ Сохранить фото")
    kb.row("❌ Отмена замены фото")
    return kb


def filters_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📍 Город", "📦 Категория")
    kb.row("💰 Цена", "📂 Подкатегория")
    kb.row("♻️ Сбросить фильтры", "🔎 Показать")
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
    names = category_names()
    for i in range(0, len(names), 2):
        if i + 1 < len(names):
            kb.row(names[i], names[i + 1])
        else:
            kb.row(names[i])
    kb.row("🌍 Любая категория")
    kb.row("⬅️ К фильтрам")
    return kb


def subcategory_menu(category: Optional[str]):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    if not category:
        kb.row("🌍 Любая подкатегория")
        kb.row("⬅️ К фильтрам")
        return kb

    subs = get_subcategories(category)
    for i in range(0, len(subs), 2):
        if i + 1 < len(subs):
            kb.row(subs[i], subs[i + 1])
        else:
            kb.row(subs[i])

    kb.row("🌍 Любая подкатегория")
    kb.row("⬅️ К фильтрам")
    return kb


def price_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🟢 Бесплатно", "🟡 До 400 ₽")
    kb.row("⚪ Любая цена")
    kb.row("⬅️ К фильтрам")
    return kb


def report_reason_kb(item_id: int):
    kb = types.InlineKeyboardMarkup()
    for reason in REPORT_REASONS:
        kb.row(types.InlineKeyboardButton(reason, callback_data=f"reportreason_{item_id}_{reason}"))
    kb.row(types.InlineKeyboardButton("❌ Отмена", callback_data="reportcancel"))
    return kb


def show_filters_menu(chat_id: int, notice: Optional[str] = None):
    ensure_filters(chat_id)
    f = user_filters[chat_id]

    city = f["city"] if f["city"] else "Любой"
    category = f["category"] if f["category"] else "Любая"
    subcategory = f["subcategory"] if f["subcategory"] else "Любая"

    if f["price"] == "free":
        price = "Бесплатно"
    elif f["price"] == "under400":
        price = "До 400 ₽"
    else:
        price = "Любая"

    text = (
        "Текущие фильтры:\n\n"
        f"📍 Город: {city}\n"
        f"📦 Категория: {category}\n"
        f"📂 Подкатегория: {subcategory}\n"
        f"💰 Цена: {price}"
    )

    if notice:
        text = f"{notice}\n\n{text}"

    bot.send_message(chat_id, text, reply_markup=filters_menu())


# =========================
# CARD / SHOW ITEM
# =========================
def build_card_keyboard(item_id: int, viewer_tg: int, owner_tg: int):
    viewer_user_id = get_user_id(viewer_tg)
    likes_count = get_likes_count(item_id)
    photos = get_item_photos(item_id)

    if has_like(viewer_user_id, item_id):
        like_text = f"💔 Убрать лайк ({likes_count})"
    else:
        like_text = f"❤️ Лайк ({likes_count})"

    kb = types.InlineKeyboardMarkup()

    if len(photos) > 1:
        kb.row(
            types.InlineKeyboardButton("⬅️ Фото", callback_data=f"prevphoto_{item_id}"),
            types.InlineKeyboardButton("Фото ➡️", callback_data=f"nextphoto_{item_id}")
        )

    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton(like_text, callback_data=f"like_{item_id}")
    )

    kb.row(
        types.InlineKeyboardButton("❤️ В избранное", callback_data=f"fav_{item_id}"),
        types.InlineKeyboardButton("💬 Написать", url=f"tg://user?id={owner_tg}")
    )

    kb.row(
        types.InlineKeyboardButton("📍 Поделиться объявлением", callback_data=f"share_{item_id}")
    )

    kb.row(
    types.InlineKeyboardButton("⚠️ Жалоба", callback_data=f"report_{item_id}")
    )
    
    if owner_tg == viewer_tg:
        kb.row(
            types.InlineKeyboardButton("✅ Отдано", callback_data=f"taken_{item_id}"),
            types.InlineKeyboardButton("🚀 Поднять", callback_data=f"bump_{item_id}")
        )
        kb.row(
            types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{item_id}"),
            types.InlineKeyboardButton("📷 Заменить фото", callback_data=f"replacephoto_{item_id}")
        )
        kb.row(
            types.InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{item_id}")
        )
    else:
        kb.row(
            types.InlineKeyboardButton("✅ Забрать", callback_data=f"take_{item_id}"),
            types.InlineKeyboardButton("⚠️ Жалоба", callback_data=f"report_{item_id}")
        )

    return kb


def show_item(chat_id: int, item, count_view: bool = True, mode: str = "feed", message_id: Optional[int] = None):
    if not item:
        total_active = get_total_active_items()
        if total_active == 0:
            bot.send_message(
                chat_id,
                "Пока вообще нет активных объявлений 😕\n\nДобавь первое через ➕ Добавить",
                reply_markup=main_menu()
            )
        else:
            bot.send_message(
                chat_id,
                "По текущим условиям объявлений нет 😕",
                reply_markup=main_menu()
            )
        return

    item_id, title, price, city, category, subcategory, owner_tg, views, is_taken, bump_count, last_bump_at, created_at = item

    if count_view:
        add_view(item_id)

    fresh_item = get_item_by_id(item_id)
    if not fresh_item:
        bot.send_message(chat_id, "Объявление уже недоступно", reply_markup=main_menu())
        return

    if fresh_item[8] == 1:
        bot.send_message(chat_id, "Это объявление уже в архиве", reply_markup=submenu_menu())
        return

    state = get_view_state(chat_id)
    photos = get_item_photos(item_id)
    photo_idx = state.get("photo_idx", 0)

    if photos:
        photo_idx = min(photo_idx, len(photos) - 1)
    else:
        photo_idx = 0

    set_view_state(chat_id, item_id, mode=mode, photo_idx=photo_idx)
    text = item_to_text(fresh_item, photo_idx)
    reply_markup = build_card_keyboard(item_id, chat_id, owner_tg)

    if photos:
        if message_id:
            try:
                media = types.InputMediaPhoto(photos[photo_idx], caption=text)
                bot.edit_message_media(
                    media=media,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup
                )
                return
            except Exception:
                pass

        try:
            bot.send_photo(chat_id, photos[photo_idx], caption=text, reply_markup=reply_markup)
            return
        except ApiTelegramException:
            pass

    if message_id:
        try:
            bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup
            )
            return
        except Exception:
            pass

    bot.send_message(chat_id, text, reply_markup=reply_markup)


def show_my_item(chat_id: int, item_id: int):
    item = get_item_by_id(item_id)
    if not item:
        bot.send_message(chat_id, "Объявление не найдено", reply_markup=submenu_menu())
        return

    if item[6] != chat_id:
        bot.send_message(chat_id, "Это не твоё объявление", reply_markup=submenu_menu())
        return

    if item[8] == 1:
        bot.send_message(chat_id, "Это объявление уже в архиве", reply_markup=submenu_menu())
        return

    set_view_state(chat_id, item_id, mode="my", photo_idx=0)
    show_item(chat_id, item, count_view=False, mode="my")


def show_archive_item(chat_id: int, item_id: int):
    item = get_item_by_id(item_id)
    if not item:
        bot.send_message(chat_id, "Архивное объявление не найдено", reply_markup=submenu_menu())
        return

    if item[6] != chat_id:
        bot.send_message(chat_id, "Это не твоё объявление", reply_markup=submenu_menu())
        return

    text = item_to_text(item) + "\n\n🗂 В архиве"
    photos = get_item_photos(item_id)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("♻️ Вернуть", callback_data=f"restore_{item_id}"),
        types.InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{item_id}")
    )

    if photos:
        bot.send_photo(chat_id, photos[0], caption=text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


# =========================
# CREATE / EDIT
# =========================
def start_create_flow(chat_id: int):
    pending_create[chat_id] = {
        "step": "title",
        "mode": "create",
        "data": {
            "photos": []
        }
    }
    bot.send_message(
        chat_id,
        "➕ Добавление объявления\n\nНапиши название вещи:",
        reply_markup=cancel_kb()
    )


def start_edit_flow(chat_id: int, item_id: int):
    item = get_item_by_id(item_id)
    if not item:
        bot.send_message(chat_id, "Объявление не найдено", reply_markup=main_menu())
        return

    if item[6] != chat_id:
        bot.send_message(chat_id, "Это не твоё объявление", reply_markup=main_menu())
        return

    pending_create[chat_id] = {
        "step": "title",
        "mode": "edit",
        "edit_item_id": item_id,
        "data": {
            "title": item[1],
            "price": item[2],
            "city": item[3],
            "category": item[4],
            "subcategory": item[5],
            "photos": get_item_photos(item_id)
        }
    }

    bot.send_message(
        chat_id,
        f"✏️ Редактирование объявления\n\nТекущее название: {item[1]}\n\nНапиши новое название:",
        reply_markup=cancel_kb()
    )


def finish_create(chat_id: int):
    flow = pending_create[chat_id]
    data = flow["data"]

    if flow["mode"] == "edit":
        item_id = flow["edit_item_id"]
        ok = update_item(
            item_id=item_id,
            owner_tg=chat_id,
            title=data["title"],
            price=data["price"],
            city=data["city"],
            category=data["category"],
            subcategory=data["subcategory"]
        )

        if ok:
            replace_item_photos(item_id, data.get("photos", []))
            pending_create.pop(chat_id, None)
            bot.send_message(chat_id, "✅ Объявление обновлено", reply_markup=main_menu())
            show_my_item(chat_id, item_id)
            return item_id

        pending_create.pop(chat_id, None)
        bot.send_message(chat_id, "Не удалось обновить объявление", reply_markup=main_menu())
        return None

    item_id = add_item(
        title=data["title"],
        price=data["price"],
        city=data["city"],
        category=data["category"],
        subcategory=data["subcategory"],
        owner_tg=chat_id,
    )
    add_item_photos(item_id, data.get("photos", []))
    pending_create.pop(chat_id, None)

    bot.send_message(chat_id, "✅ Объявление опубликовано", reply_markup=main_menu())
    show_my_item(chat_id, item_id)
    return item_id


# =========================
# COMMANDS
# =========================
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    parts = message.text.split()

    get_user_id(chat_id)

    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            inviter_tg = int(parts[1].replace("ref_", ""))
            add_referral(inviter_tg, chat_id)
        except Exception:
            pass

    if len(parts) > 1 and parts[1].startswith("item_"):
        try:
            item_id = int(parts[1].replace("item_", ""))
            item = get_item_by_id(item_id)
            if item and item[8] == 0:
                set_view_state(chat_id, item_id, mode="feed", photo_idx=0)
                show_item(chat_id, item, mode="feed")
                return
        except Exception:
            pass

    reset_filters(chat_id)
    user_index[chat_id] = 0

    bot.send_message(
        chat_id,
        "Добро пожаловать в Даром 🎁\n\nЗдесь можно находить вещи бесплатно и за копейки.",
        reply_markup=main_menu()
    )


# =========================
# MAIN MENU
# =========================
@bot.message_handler(func=lambda m: m.text == "🏠 Домой")
def menu(message):
    pending_search.discard(message.chat.id)
    bot.send_message(
        message.chat.id,
        "Дополнительное меню:",
        reply_markup=submenu_menu()
    )


@bot.message_handler(func=lambda m: m.text == "🔎 Смотреть все")
def open_browse_menu(message):
    bot.send_message(
        message.chat.id,
        "Выберите категорию или смотрите все объявления",
        reply_markup=browse_menu()
    )


@bot.message_handler(func=lambda m: m.text == "📦 Все объявления")
def browse_all_items(message):
    chat_id = message.chat.id
    items = get_items_for_browse("all")

    if not items:
        bot.send_message(chat_id, "Сейчас нет активных объявлений 😔", reply_markup=browse_menu())
        return

    user_index[chat_id] = 0
    set_view_state(chat_id, items[0][0], mode="browse_all", photo_idx=0)
    show_item(chat_id, items[0], mode="browse_all")


@bot.message_handler(func=lambda m: m.text == "🎁 Бесплатно")
def browse_free_items(message):
    chat_id = message.chat.id
    items = get_items_for_browse("free")

    if not items:
        bot.send_message(chat_id, "Бесплатных объявлений пока нет 😔", reply_markup=browse_menu())
        return

    user_index[chat_id] = 0
    set_view_state(chat_id, items[0][0], mode="browse_free", photo_idx=0)
    show_item(chat_id, items[0], mode="browse_free")


@bot.message_handler(func=lambda m: m.text == "💸 За копейки")
def browse_cheap_items(message):
    chat_id = message.chat.id
    items = get_items_for_browse("cheap")

    if not items:
        bot.send_message(chat_id, "Объявлений за копейки пока нет 😔", reply_markup=browse_menu())
        return

    user_index[chat_id] = 0
    set_view_state(chat_id, items[0][0], mode="browse_cheap", photo_idx=0)
    show_item(chat_id, items[0], mode="browse_cheap")


@bot.message_handler(func=lambda m: m.text in BROWSE_CATEGORY_BUTTONS)
def browse_category_items(message):
    chat_id = message.chat.id
    category = BROWSE_CATEGORY_BUTTONS[message.text]
    items = get_items_for_browse("all", category=category)

    if not items:
        bot.send_message(chat_id, f"В категории «{category}» пока нет объявлений 😔", reply_markup=browse_menu())
        return

    user_index[chat_id] = 0
    set_view_state(chat_id, items[0][0], mode=f"browse_cat:{category}", photo_idx=0)
    show_item(chat_id, items[0], mode=f"browse_cat:{category}")


@bot.message_handler(func=lambda m: m.text == "✍️ Поиск по названию")
def open_search(message):
    pending_search.add(message.chat.id)
    bot.send_message(
        message.chat.id,
        "Напиши слово для поиска.\nНапример: куртка",
        reply_markup=main_menu()
    )


@bot.message_handler(func=lambda m: m.text == "➕ Добавить")
def add_menu(message):
    start_create_flow(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "⚙️ Фильтры")
def filters(message):
    show_filters_menu(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "❤️ Избранное")
def favorites_menu(message):
    chat_id = message.chat.id
    pending_search.discard(chat_id)

    user_id = get_user_id(chat_id)
    fav_ids = get_favorites(user_id)

    if not fav_ids:
        bot.send_message(chat_id, "В избранном пока пусто ❤️", reply_markup=submenu_menu())
        return

    items = []

    for item_id in fav_ids:
        item = get_item_by_id(item_id)
        if item and item[8] == 0:
            items.append(item)

    if not items:
        bot.send_message(
            chat_id,
            "Все объявления из избранного уже неактивны",
            reply_markup=submenu_menu()
        )
        return

    user_index[chat_id] = 0
    set_view_state(chat_id, items[0][0])

    show_item(chat_id, items[0], mode="favorites")


@bot.message_handler(func=lambda m: m.text == "⬅️ Назад")
def back(message):
    chat_id = message.chat.id
    pending_search.discard(chat_id)

    st = get_view_state(chat_id)
    mode = st.get("mode", "")

    if mode in ["browse_all", "browse_free", "browse_cheap"] or str(mode).startswith("browse_cat:"):
        bot.send_message(
            chat_id,
            "Выберите категорию или смотрите все объявления",
            reply_markup=browse_menu()
        )
        return

    bot.send_message(chat_id, "Главное меню:", reply_markup=main_menu())


# =========================
# SUBMENU
# =========================
@bot.message_handler(func=lambda m: m.text == "📦 Мои объявления")
def my_items_menu(message):
    chat_id = message.chat.id
    rows = get_user_active_items(chat_id)

    if not rows:
        bot.send_message(chat_id, "У тебя пока нет активных объявлений", reply_markup=submenu_menu())
        return

    kb = types.InlineKeyboardMarkup()
    for item in rows[:20]:
        kb.row(types.InlineKeyboardButton(short_item_label(item), callback_data=f"myitem_{item[0]}"))

    bot.send_message(chat_id, "📦 Мои объявления:", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "🗂 Архив")
def archive_menu_handler(message):
    chat_id = message.chat.id
    rows = get_user_archive_items(chat_id)

    if not rows:
        bot.send_message(chat_id, "Архив пуст 🗂", reply_markup=submenu_menu())
        return

    kb = types.InlineKeyboardMarkup()
    for item in rows[:20]:
        kb.row(types.InlineKeyboardButton(short_item_label(item), callback_data=f"architem_{item[0]}"))

    bot.send_message(chat_id, "🗂 Архив:", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def profile_menu(message):
    pending_search.discard(message.chat.id)
    bot.send_message(
        message.chat.id,
        "Профиль:",
        reply_markup=submenu_menu()
    )
    
@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def stats_menu(message):
    chat_id = message.chat.id
    pending_search.discard(chat_id)

    my_items = get_user_items(chat_id)
    total_items = len(my_items)
    active_items = len([item for item in my_items if item[8] == 0])
    archive_items = len([item for item in my_items if item[8] == 1])
    total_views = sum(item[7] for item in my_items) if my_items else 0
    total_likes = sum(get_likes_count(item[0]) for item in my_items) if my_items else 0
    refs = get_referrals_count(chat_id)

    text = (
        "📊 Статистика\n\n"
        f"📦 Всего объявлений: {total_items}\n"
        f"🟢 Активных: {active_items}\n"
        f"🗂 В архиве: {archive_items}\n\n"
        f"👁 Просмотров: {total_views}\n"
        f"❤️ Лайков: {total_likes}\n"
        f"👥 Приглашено друзей: {refs}"
    )

    bot.send_message(chat_id, text, reply_markup=submenu_menu())

@bot.message_handler(func=lambda m: m.text == "🎁 Пригласить")
def invite_menu(message):
    me = bot.get_me()
    invite_link = f"https://t.me/{me.username}?start=ref_{message.chat.id}"
    count = get_referrals_count(message.chat.id)

    bot.send_message(
        message.chat.id,
        f"🎁 Пригласи друга по своей ссылке:\n\n{invite_link}\n\nУже приглашено: {count}",
        reply_markup=submenu_menu()
    )


@bot.message_handler(func=lambda m: m.text == "🆘 Помощь")
def help_menu(message):
    text = (
        "🆘 Помощь\n\n"
        "Как пользоваться ботом:\n\n"
        "1. Нажми ➕ Добавить, чтобы создать объявление\n"
        "2. Укажи название, цену, город, категорию и подкатегорию\n"
        "3. Добавь фото или нажми «✅ Готово без фото»\n"
        "4. Для поиска вещей используй «✍️ Поиск по названию»\n"
        "5. Для точного отбора используй «⚙️ Фильтры»\n"
        "6. Если вещь уже отдана — открой объявление и нажми «✅ Отдано»\n"
        "7. В разделе «📦 Мои объявления» можно редактировать, поднимать и удалять объявления"
    )
    bot.send_message(message.chat.id, text, reply_markup=submenu_menu())
    
@bot.message_handler(func=lambda m: m.text == "📜 Правила")
def rules_menu(message):
    text = (
        "📜 Правила\n\n"
        "1. Размещай только реальные вещи и честные объявления.\n\n"
        "2. Запрещено публиковать:\n"
        "• запрещённые товары\n"
        "• мошеннические объявления\n"
        "• спам и дубли объявлений\n\n"
        "3. В описании запрещены:\n"
        "• оскорбления\n"
        "• ненормативная лексика\n"
        "• политические и религиозные высказывания\n"
        "• любые формы дискриминации\n\n"
        "4. Если вещь уже отдана — пометь объявление кнопкой ✅ Отдано.\n\n"
        "5. Уважительно общайся с другими пользователями.\n\n"
        "⚠️ Администрация бота не является участником сделок "
        "и не несёт ответственности за действия пользователей.\n\n"
        "🚫 За нарушение правил объявления могут быть удалены."
    )

    bot.send_message(message.chat.id, text, reply_markup=submenu_menu())

@bot.message_handler(func=lambda m: m.text == "🔥 Популярное")
def popular_menu(message):
    rows = get_popular_items(10)

    if not rows:
        bot.send_message(message.chat.id, "Пока нет популярных объявлений", reply_markup=submenu_menu())
        return

    kb = types.InlineKeyboardMarkup()
    for row in rows:
        item_id = row[0]
        title = row[1]
        kb.row(types.InlineKeyboardButton(f"#{item_id} {title}", callback_data=f"popular_{item_id}"))

    bot.send_message(message.chat.id, "🔥 Популярное:", reply_markup=kb)


@bot.message_handler(func=lambda m: m.chat.id in pending_search and m.chat.id not in pending_create)
def search_text_handler(message):
    chat_id = message.chat.id
    pending_search.discard(chat_id)

    query_text = (message.text or "").strip().lower()
    if not query_text:
        bot.send_message(chat_id, "Пустой запрос", reply_markup=submenu_menu())
        return

    rows = search_items(chat_id, query_text)
    if not rows:
        bot.send_message(chat_id, f"По запросу «{query_text}» ничего не найдено", reply_markup=submenu_menu())
        return

    kb = types.InlineKeyboardMarkup()
    for item in rows[:20]:
        kb.row(types.InlineKeyboardButton(short_item_label(item), callback_data=f"searchopen_{item[0]}"))

    bot.send_message(chat_id, f"🔍 Результаты поиска: {query_text}", reply_markup=kb)


# =========================
# FILTERS
# =========================
@bot.message_handler(func=lambda m: m.text == "📍 Город" and m.chat.id not in pending_create)
def filter_city(message):
    bot.send_message(message.chat.id, "Выбери город:", reply_markup=city_menu())


@bot.message_handler(func=lambda m: m.text == "📦 Категория" and m.chat.id not in pending_create)
def filter_category(message):
    bot.send_message(message.chat.id, "Выбери категорию:", reply_markup=category_menu())


@bot.message_handler(func=lambda m: m.text == "📂 Подкатегория" and m.chat.id not in pending_create)
def filter_subcategory(message):
    ensure_filters(message.chat.id)
    category = user_filters[message.chat.id]["category"]

    if not category:
        bot.send_message(
            message.chat.id,
            "Сначала выбери категорию в фильтрах.",
            reply_markup=filters_menu()
        )
        return

    bot.send_message(
        message.chat.id,
        f"Выбери подкатегорию для «{category}»:",
        reply_markup=subcategory_menu(category)
    )


@bot.message_handler(func=lambda m: m.text == "💰 Цена" and m.chat.id not in pending_create)
def filter_price(message):
    bot.send_message(message.chat.id, "Выбери цену:", reply_markup=price_menu())


@bot.message_handler(func=lambda m: m.text == "♻️ Сбросить фильтры" and m.chat.id not in pending_create)
def reset_filter(message):
    reset_filters(message.chat.id)
    show_filters_menu(message.chat.id, "♻️ Фильтры сброшены")


@bot.message_handler(func=lambda m: m.text == "🔎 Показать" and m.chat.id not in pending_create)
def show_filtered(message):
    chat_id = message.chat.id
    items = get_filtered_items(chat_id)

    if not items:
        bot.send_message(chat_id, "По текущим фильтрам объявлений нет 😔", reply_markup=filters_menu())
        return

    user_index[chat_id] = 0
    set_view_state(chat_id, items[0][0], "filtered", 0)
    show_item(chat_id, items[0], mode="filtered")


@bot.message_handler(func=lambda m: (m.text in POPULAR_CITIES or m.text == "🌍 Любой город") and m.chat.id not in pending_create)
def set_city_filter(message):
    ensure_filters(message.chat.id)
    user_filters[message.chat.id]["city"] = None if message.text == "🌍 Любой город" else message.text
    show_filters_menu(message.chat.id, "📍 Фильтр по городу обновлён")


@bot.message_handler(func=lambda m: (m.text in category_names() or m.text == "🌍 Любая категория") and m.chat.id not in pending_create)
def set_category_filter(message):
    ensure_filters(message.chat.id)
    if message.text == "🌍 Любая категория":
        user_filters[message.chat.id]["category"] = None
        user_filters[message.chat.id]["subcategory"] = None
    else:
        user_filters[message.chat.id]["category"] = message.text
        user_filters[message.chat.id]["subcategory"] = None

    show_filters_menu(message.chat.id, "📦 Фильтр по категории обновлён")


@bot.message_handler(func=lambda m: m.chat.id not in pending_create and (m.text == "🌍 Любая подкатегория" or any(m.text in subs for subs in CATEGORIES.values())))
def set_subcategory_filter(message):
    ensure_filters(message.chat.id)

    if message.text == "🌍 Любая подкатегория":
        user_filters[message.chat.id]["subcategory"] = None
        show_filters_menu(message.chat.id, "📂 Фильтр по подкатегории обновлён")
        return

    category = user_filters[message.chat.id]["category"]
    if not category:
        bot.send_message(message.chat.id, "Сначала выбери категорию.", reply_markup=filters_menu())
        return

    if message.text not in get_subcategories(category):
        return

    user_filters[message.chat.id]["subcategory"] = message.text
    show_filters_menu(message.chat.id, "📂 Фильтр по подкатегории обновлён")


@bot.message_handler(func=lambda m: m.text in ["🟢 Бесплатно", "🟡 До 400 ₽", "⚪ Любая цена"] and m.chat.id not in pending_create)
def set_price_filter(message):
    ensure_filters(message.chat.id)

    if message.text == "🟢 Бесплатно":
        user_filters[message.chat.id]["price"] = "free"
    elif message.text == "🟡 До 400 ₽":
        user_filters[message.chat.id]["price"] = "under400"
    else:
        user_filters[message.chat.id]["price"] = "any"

    show_filters_menu(message.chat.id, "💰 Фильтр по цене обновлён")


@bot.message_handler(func=lambda m: m.text == "⬅️ К фильтрам" and m.chat.id not in pending_create)
def back_to_filters(message):
    show_filters_menu(message.chat.id)


# =========================
# CREATE / EDIT FLOW
# =========================
@bot.message_handler(func=lambda m: m.text == "❌ Отмена" and m.chat.id in pending_create)
def cancel_create(message):
    pending_create.pop(message.chat.id, None)
    bot.send_message(message.chat.id, "Создание объявления отменено", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text == "❌ Отмена замены фото" and m.chat.id in pending_replace_photo)
def cancel_replace_photo(message):
    pending_replace_photo.pop(message.chat.id, None)
    bot.send_message(message.chat.id, "Замена фото отменена", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "title")
def create_title_handler(message):
    text = (message.text or "").strip()
    if len(text) < 2:
        bot.send_message(message.chat.id, "Название слишком короткое")
        return

    pending_create[message.chat.id]["data"]["title"] = text
    pending_create[message.chat.id]["step"] = "price"

    flow = pending_create[message.chat.id]
    if flow["mode"] == "edit":
        bot.send_message(message.chat.id, "💰 Новая цена.\nЕсли бесплатно — напиши 0", reply_markup=cancel_kb())
    else:
        bot.send_message(message.chat.id, "💰 Укажи цену.\nЕсли бесплатно — напиши 0", reply_markup=cancel_kb())


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "price")
def create_price_handler(message):
    text = (message.text or "").strip()
    try:
        price = int(text)
        if price < 0:
            raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "Цена должна быть числом. Если бесплатно — напиши 0.")
        return

    pending_create[message.chat.id]["data"]["price"] = price
    pending_create[message.chat.id]["step"] = "city"

    flow = pending_create[message.chat.id]
    if flow["mode"] == "edit":
        bot.send_message(message.chat.id, "📍 Новый город", reply_markup=cancel_kb())
    else:
        bot.send_message(message.chat.id, "📍 Напиши город", reply_markup=cancel_kb())


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "city")
def create_city_handler(message):
    text = (message.text or "").strip()
    if len(text) < 1:
        bot.send_message(message.chat.id, "Напиши город.")
        return

    pending_create[message.chat.id]["data"]["city"] = text
    pending_create[message.chat.id]["step"] = "category"

    flow = pending_create[message.chat.id]
    if flow["mode"] == "edit":
        bot.send_message(message.chat.id, "📦 Новая категория", reply_markup=category_pick_kb())
    else:
        bot.send_message(message.chat.id, "📦 Выбери категорию", reply_markup=category_pick_kb())


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "category")
def create_category_handler(message):
    text = (message.text or "").strip()
    if text not in category_names():
        bot.send_message(message.chat.id, "Выбери категорию кнопкой ниже.")
        return

    pending_create[message.chat.id]["data"]["category"] = text
    pending_create[message.chat.id]["data"]["subcategory"] = ""
    pending_create[message.chat.id]["step"] = "subcategory"

    flow = pending_create[message.chat.id]
    if flow["mode"] == "edit":
        bot.send_message(message.chat.id, "📂 Новая подкатегория", reply_markup=subcategory_pick_kb(text))
    else:
        bot.send_message(message.chat.id, "📂 Выбери подкатегорию", reply_markup=subcategory_pick_kb(text))


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "subcategory")
def create_subcategory_handler(message):
    flow = pending_create[message.chat.id]
    category = flow["data"]["category"]
    text = (message.text or "").strip()

    if text not in get_subcategories(category):
        bot.send_message(message.chat.id, "Выбери подкатегорию кнопкой ниже.")
        return

    pending_create[message.chat.id]["data"]["subcategory"] = text
    pending_create[message.chat.id]["step"] = "photo"

    if flow["mode"] == "edit":
        pending_create[message.chat.id]["data"]["photos"] = []
        bot.send_message(
            message.chat.id,
            f"🖼 Теперь можешь обновить фото.\nОтправь до {MAX_PHOTOS_PER_ITEM} фото.\nЕсли фото менять не нужно — нажми «✅ Готово без фото».",
            reply_markup=photo_step_kb(0)
        )
    else:
        bot.send_message(
            message.chat.id,
            f"🖼 Теперь отправь до {MAX_PHOTOS_PER_ITEM} фото товара.\nМожно отправлять по одному.\nЕсли фото не нужно — нажми «✅ Готово без фото».",
            reply_markup=photo_step_kb(0)
        )


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "photo" and m.text in ["✅ Готово без фото", "✅ Опубликовать"])
def create_finish_handler(message):
    finish_create(message.chat.id)


@bot.message_handler(content_types=["photo"])
def photo_handler(message):
    chat_id = message.chat.id

    if chat_id in pending_replace_photo:
        item_id = pending_replace_photo[chat_id]["item_id"]
        photos = pending_replace_photo[chat_id]["photos"]

        if len(photos) >= MAX_PHOTOS_PER_ITEM:
            bot.send_message(chat_id, f"Можно максимум {MAX_PHOTOS_PER_ITEM} фото. Нажми «✅ Сохранить фото».")
            return

        photos.append(message.photo[-1].file_id)
        pending_replace_photo[chat_id]["photos"] = photos

        bot.send_message(
            chat_id,
            f"Фото добавлено: {len(photos)}/{MAX_PHOTOS_PER_ITEM}",
            reply_markup=replace_photo_step_kb(len(photos))
        )
        return

    if chat_id not in pending_create:
        return

    if pending_create[chat_id]["step"] != "photo":
        return

    photos = pending_create[chat_id]["data"]["photos"]

    if len(photos) >= MAX_PHOTOS_PER_ITEM:
        bot.send_message(chat_id, f"Можно максимум {MAX_PHOTOS_PER_ITEM} фото.")
        return

    photos.append(message.photo[-1].file_id)
    pending_create[chat_id]["data"]["photos"] = photos

    bot.send_message(
        chat_id,
        f"Фото добавлено: {len(photos)}/{MAX_PHOTOS_PER_ITEM}",
        reply_markup=photo_step_kb(len(photos))
    )


@bot.message_handler(func=lambda m: m.chat.id in pending_replace_photo and m.text == "✅ Сохранить фото")
def finish_replace_photos(message):
    chat_id = message.chat.id
    data = pending_replace_photo.get(chat_id)

    if not data or not data["photos"]:
        bot.send_message(chat_id, "Сначала отправь хотя бы одно фото.")
        return

    item_id = data["item_id"]
    replace_item_photos(item_id, data["photos"])
    pending_replace_photo.pop(chat_id, None)

    bot.send_message(chat_id, "✅ Фото объявления обновлены", reply_markup=main_menu())
    show_my_item(chat_id, item_id)


# =========================
# CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    data = call.data

    if data == "next":
        state = get_view_state(chat_id)
        mode = state.get("mode", "feed") if state else "feed"

        if mode == "favorites":
            user_id = get_user_id(chat_id)
            fav_ids = get_favorites(user_id)

            items = []
            for item_id in fav_ids:
                item = get_item_by_id(item_id)
                if item and item[8] == 0:
                    items.append(item)

            if not items:
                bot.answer_callback_query(call.id, "Больше объявлений нет")
                return

            idx = user_index.get(chat_id, 0)
            idx = (idx + 1) % len(items)
            user_index[chat_id] = idx

            set_view_state(chat_id, items[idx][0], mode="favorites", photo_idx=0)
            show_item(chat_id, items[idx], mode="favorites")
            bot.answer_callback_query(call.id)
            return

        if mode == "browse_all":
            items = get_items_for_browse("all")
        elif mode == "browse_free":
            items = get_items_for_browse("free")
        elif mode == "browse_cheap":
            items = get_items_for_browse("cheap")
        elif str(mode).startswith("browse_cat:"):
            category = mode.split(":", 1)[1]
            items = get_items_for_browse("all", category=category)
        elif mode == "filtered":
            items = get_filtered_items(chat_id)
        else:
            items = get_filtered_items(chat_id)

        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0)
        idx = (idx + 1) % len(items)
        user_index[chat_id] = idx

        set_view_state(chat_id, items[idx][0], mode=mode, photo_idx=0)
        show_item(chat_id, items[idx], message_id=call.message.message_id, mode=mode)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("prevphoto_") or data.startswith("nextphoto_"):
        try:
            item_id = int(data.split("_", 1)[1])
        except ValueError:
            bot.answer_callback_query(call.id, "Ошибка")
            return

        item = get_item_by_id(item_id)
        if not item:
            bot.answer_callback_query(call.id, "Объявление не найдено")
            return

        photos = get_item_photos(item_id)
        if len(photos) <= 1:
            bot.answer_callback_query(call.id, "Фото только одно")
            return

        st = get_view_state(chat_id)
        idx = st.get("photo_idx", 0)

        if data.startswith("prevphoto_"):
            idx = (idx - 1) % len(photos)
        else:
            idx = (idx + 1) % len(photos)

        set_view_state(chat_id, item_id, mode=st.get("mode", "feed"), photo_idx=idx)
        show_item(chat_id, item, count_view=False, mode=st.get("mode", "feed"), message_id=call.message.message_id)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("like_"):
        item_id = int(data.split("_")[1])
        user_id = get_user_id(chat_id)

        if has_like(user_id, item_id):
            remove_like(user_id, item_id)
            bot.answer_callback_query(call.id, "Лайк убран")
        else:
            add_like(user_id, item_id)
            bot.answer_callback_query(call.id, "Лайк поставлен")

            item = get_item_by_id(item_id)
            if item and item[6] != chat_id:
                send_owner_notification(
                    item_id,
                    f"❤️ Кто-то поставил лайк твоему объявлению\n\n🧥 {item[1]}\n📍 {item[3]}",
                    exclude_tg=chat_id
                )

        item = get_item_by_id(item_id)
        if item and item[8] == 0:
            st = get_view_state(chat_id)
            show_item(chat_id, item, count_view=False, mode=st.get("mode", "feed"), message_id=call.message.message_id)
        return

    if data.startswith("fav_"):
        item_id = int(data.split("_")[1])
        user_id = get_user_id(chat_id)
        add_favorite(user_id, item_id)
        bot.answer_callback_query(call.id, "Добавлено в избранное ❤️")

        item = get_item_by_id(item_id)
        if item and item[6] != chat_id:
            send_owner_notification(
                item_id,
                f"⭐ Кто-то добавил твоё объявление в избранное\n\n🧥 {item[1]}\n📍 {item[3]}",
                exclude_tg=chat_id
            )
        return

    if call.data.startswith("report_"):
        item_id = int(call.data.split("_")[1])

        bot.send_message(
            ADMIN_ID,
            f"🚩 Жалоба на объявление #{item_id}\n"
            f"От пользователя: {call.from_user.id}"
        )

        bot.answer_callback_query(call.id, "Жалоба отправлена администратору")
        return
        
    if data.startswith("share_"):
        item_id = int(data.split("_")[1])
        bot.send_message(chat_id, build_share_text(item_id), reply_markup=main_menu())
        bot.answer_callback_query(call.id, "Ссылка отправлена")
        return

    if data.startswith("take_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)
        if not item:
            bot.answer_callback_query(call.id, "Объявление не найдено")
            return

        if item[6] == chat_id:
            bot.answer_callback_query(call.id, "Это твоё объявление")
            return

        send_owner_notification(
            item_id,
            f"📩 Кто-то заинтересовался твоим объявлением\n\n🧥 {item[1]}\n📍 {item[3]}",
            exclude_tg=chat_id
        )
        bot.answer_callback_query(call.id, "Напиши владельцу через кнопку 💬")
        return

    if data == "reportcancel":
        bot.answer_callback_query(call.id, "Отменено")
        return

    if data.startswith("reportreason_"):
        rest = data.replace("reportreason_", "", 1)
        item_id_str, reason = rest.split("_", 1)
        item_id = int(item_id_str)

        add_report(chat_id, item_id, reason)

        item = get_item_by_id(item_id)
        if item and item[6] != chat_id:
            send_owner_notification(
                item_id,
                f"⚠️ На твоё объявление пожаловались\n\nПричина: {reason}\n🧥 {item[1]}\n📍 {item[3]}",
                exclude_tg=chat_id
            )

        bot.answer_callback_query(call.id, "Жалоба отправлена")
        bot.send_message(chat_id, f"Жалоба сохранена: {reason}")
        return

    if data.startswith("report_"):
        item_id = int(data.split("_")[1])
        bot.send_message(chat_id, "Выбери причину жалобы:", reply_markup=report_reason_kb(item_id))
        bot.answer_callback_query(call.id)
        return

    if data.startswith("bump_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)
        if not item:
            bot.answer_callback_query(call.id, "Объявление не найдено")
            return

        if item[6] != chat_id:
            bot.answer_callback_query(call.id, "Можно поднимать только свои объявления")
            return

        if item[8] == 1:
            bot.answer_callback_query(call.id, "Архивные объявления нельзя поднимать")
            return

        ok, remain = can_bump_item(item_id, chat_id)
        if not ok:
            bot.answer_callback_query(call.id, f"Можно через {format_seconds_to_human(remain)}")
            return

        new_item_id = bump_item(item_id, chat_id)
        if new_item_id:
            bot.answer_callback_query(call.id, "Объявление поднято 🚀")

            new_item = get_item_by_id(new_item_id)
            if new_item:
                set_view_state(chat_id, new_item_id, mode="my", photo_idx=0)
                show_item(
                    chat_id,
                    new_item,
                    count_view=False,
                    mode="my",
                    message_id=call.message.message_id
                )
            return

        bot.answer_callback_query(call.id, "Ошибка")
        return

    if data.startswith("delete_"):
        item_id = int(data.split("_")[1])

        if delete_item(item_id, chat_id):
            bot.answer_callback_query(call.id, "Удалено 🗑")
            bot.send_message(chat_id, "Объявление удалено", reply_markup=main_menu())
        else:
            bot.answer_callback_query(call.id, "Ошибка")
        return

    if data.startswith("taken_"):
        item_id = int(data.split("_")[1])

        if mark_taken(item_id, chat_id):
            bot.answer_callback_query(call.id, "Перенесено в архив ✅")
            bot.send_message(chat_id, "Объявление отправлено в архив", reply_markup=main_menu())
        else:
            bot.answer_callback_query(call.id, "Ошибка")
        return

    if data.startswith("restore_"):
        item_id = int(data.split("_")[1])

        if restore_item(item_id, chat_id):
            bot.answer_callback_query(call.id, "Объявление восстановлено ♻️")
            show_my_item(chat_id, item_id)
        else:
            bot.answer_callback_query(call.id, "Ошибка")
        return

    if data.startswith("edit_"):
        item_id = int(data.split("_")[1])
        start_edit_flow(chat_id, item_id)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("replacephoto_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)

        if not item or item[6] != chat_id:
            bot.answer_callback_query(call.id, "Это не твоё объявление")
            return

        pending_replace_photo[chat_id] = {
            "item_id": item_id,
            "photos": []
        }
        bot.answer_callback_query(call.id, "Отправь новые фото")
        bot.send_message(
            chat_id,
            f"📷 Отправь до {MAX_PHOTOS_PER_ITEM} новых фото.\nКогда закончишь — нажми «✅ Сохранить фото»",
            reply_markup=replace_photo_step_kb(0)
        )
        return

    if data.startswith("myitem_"):
        item_id = int(data.split("_")[1])
        show_my_item(chat_id, item_id)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("architem_"):
        item_id = int(data.split("_")[1])
        show_archive_item(chat_id, item_id)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("favopen_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)
        if item and item[8] == 0:
            set_view_state(chat_id, item_id, mode="feed", photo_idx=0)
            show_item(chat_id, item, mode="feed")
        bot.answer_callback_query(call.id)
        return

    if data.startswith("searchopen_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)
        if item and item[8] == 0:
            set_view_state(chat_id, item_id, mode="feed", photo_idx=0)
            show_item(chat_id, item, mode="feed")
        bot.answer_callback_query(call.id)
        return

    if data.startswith("popular_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)
        if item and item[8] == 0:
            set_view_state(chat_id, item_id, mode="feed", photo_idx=0)
            show_item(chat_id, item, mode="feed")
        bot.answer_callback_query(call.id)
        return


# =========================
# RUN
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
    run_bot()
