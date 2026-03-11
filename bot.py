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
    created_at INTEGER NOT NULL DEFAULT 0
)
""")

conn.commit()

# =========================
# MEMORY
# =========================
user_index = {}
pending_search = set()
user_filters = {}
pending_create = {}
pending_replace_photo = {}
pending_report = {}
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
# BASE HELPERS
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

# =========================
# ITEMS / PHOTOS
# =========================
def add_item(title: str, price: int, city: str, category: str, owner_tg: int) -> int:
    cursor.execute("""
        INSERT INTO items (
            title, price, city, category, owner_tg,
            views, is_taken, bump_count, last_bump_at, created_at
        )
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
        SELECT id, title, price, city, category, owner_tg,
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
        SELECT id, title, price, city, category, owner_tg,
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

    if f["price"] == "free":
        query += " AND price = 0"
    elif f["price"] == "under400":
        query += " AND price >= 0 AND price <= 400"

    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_user_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ?
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_active_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, owner_tg,
               views, is_taken, bump_count, last_bump_at, created_at
        FROM items
        WHERE owner_tg = ? AND is_taken = 0
        ORDER BY id DESC
    """, (owner_tg,))
    return cursor.fetchall()


def get_user_archive_items(owner_tg: int):
    cursor.execute("""
        SELECT id, title, price, city, category, owner_tg,
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
        SELECT title, price, city, category, owner_tg,
               views, is_taken, bump_count, created_at
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
        INSERT INTO items (
            title, price, city, category, owner_tg,
            views, is_taken, bump_count, last_bump_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title, price, city, category, owner_tg,
        views, is_taken, bump_count + 1, now_ts(), created_at
    ))
    conn.commit()

    new_item_id = cursor.lastrowid
    add_item_photos(new_item_id, photos)
    return new_item_id

# =========================
# SOCIAL / SEARCH / REPORTS
# =========================
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
        SELECT id, title, price, city, category, owner_tg,
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

    if f["price"] == "free":
        query += " AND price = 0"
    elif f["price"] == "under400":
        query += " AND price <= 400"

    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_popular_items(limit: int = 10):
    cursor.execute("""
        SELECT i.id, i.title, i.price, i.city, i.category, i.owner_tg,
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
# =========================
# TEXT HELPERS
# =========================
def format_seconds_to_human(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def short_item_label(item):
    item_id, title, price, city, category, *_ = item
    price_text = "Бесплатно" if price == 0 else f"{price} ₽"
    return f"#{item_id} {title} | {price_text} | {city}"


def item_to_text(item):
    item_id, title, price, city, category, owner_tg, views, *_ = item

    likes_count = get_likes_count(item_id)
    reports_count = get_reports_count(item_id)

    text = f"🧥 {title}\n"

    if price == 0:
        text += "🟢 Бесплатно\n"
    else:
        text += f"💰 {price} ₽\n"

    text += f"📍 {city}\n"
    text += f"📦 {category}\n"
    text += f"❤️ {likes_count} лайков\n"
    text += f"👁 {views} просмотров"

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

    if price == 0:
        price_text = "Бесплатно"
    else:
        price_text = f"{price} ₽"

    username = bot.get_me().username

    return (
        "📍 Поделиться объявлением\n\n"
        f"🧥 {title}\n"
        f"💰 {price_text}\n"
        f"📍 {city}\n"
        f"📦 {category}\n\n"
        f"https://t.me/{username}?start=item_{item_id}"
    )


# =========================
# KEYBOARDS
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
    kb.row("👤 Профиль", "🆘 Помощь / правила")
    kb.row("🔥 Популярное", "🔍 Поиск")
    kb.row("⬅️ Назад")

    return kb


def category_pick_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    kb.row("Одежда", "Обувь")
    kb.row("Аксессуары", "Детские товары")
    kb.row("Электроника", "Красота и здоровье")
    kb.row("Для дома и дачи", "Авто и запчасти")
    kb.row("Спецтехника", "Другое")
    kb.row("❌ Отмена")

    return kb


def filters_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    kb.row("📍 Город", "📦 Категория")
    kb.row("💰 Цена", "🔎 Показать")
    kb.row("♻️ Сбросить фильтры")
    kb.row("⬅️ Назад")

    return kb


def report_reason_kb(item_id: int):
    kb = types.InlineKeyboardMarkup()

    for reason in REPORT_REASONS:
        kb.row(types.InlineKeyboardButton(
            reason,
            callback_data=f"reportreason_{item_id}_{reason}"
        ))

    kb.row(types.InlineKeyboardButton(
        "❌ Отмена",
        callback_data="reportcancel"
    ))

    return kb


# =========================
# CARD KEYBOARD
# =========================
def build_card_keyboard(item_id: int, viewer_tg: int, owner_tg: int):
    viewer_user_id = get_user_id(viewer_tg)

    likes_count = get_likes_count(item_id)

    if has_like(viewer_user_id, item_id):
        like_text = f"💔 Убрать лайк ({likes_count})"
    else:
        like_text = f"❤️ Лайк ({likes_count})"

    kb = types.InlineKeyboardMarkup()

    # фото листание
    kb.row(
        types.InlineKeyboardButton("⬅️ Фото", callback_data=f"ph_left_{item_id}"),
        types.InlineKeyboardButton("Фото ➡️", callback_data=f"ph_right_{item_id}")
    )

    # навигация
    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton(like_text, callback_data=f"like_{item_id}")
    )

    # избранное / чат
    kb.row(
        types.InlineKeyboardButton("❤️ В избранное", callback_data=f"fav_{item_id}"),
        types.InlineKeyboardButton("💬 Написать", url=f"tg://user?id={owner_tg}")
    )

    # поделиться
    kb.row(
        types.InlineKeyboardButton("📍 Поделиться объявлением", callback_data=f"share_{item_id}")
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
            types.InlineKeyboardButton("⚠️ Жалоба", callback_data=f"report_{item_id}")
        )

    return kb


# =========================
# SHOW ITEM
# =========================
def show_item(chat_id: int, item, count_view=True, message_id=None):

    if count_view:
        add_view(item[0])

    text = item_to_text(item)
    photos = get_item_photos(item[0])

    reply_markup = build_card_keyboard(item[0], chat_id, item[5])

    if photos:

        if message_id:

            try:

                media = types.InputMediaPhoto(
                    photos[0],
                    caption=text
                )

                bot.edit_message_media(
                    media=media,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup
                )

                return

            except:
                pass

        bot.send_photo(
            chat_id,
            photos[0],
            caption=text,
            reply_markup=reply_markup
        )

    else:

        if message_id:

            try:

                bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup
                )

                return

            except:
                pass

        bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup
        )
# =========================
# EXTRA KEYBOARDS
# =========================
def cancel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
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
    kb.row("Аксессуары", "Детские товары")
    kb.row("Электроника", "Красота и здоровье")
    kb.row("Для дома и дачи", "Авто и запчасти")
    kb.row("Спецтехника", "Другое")
    kb.row("🌍 Любая категория")
    kb.row("⬅️ К фильтрам")
    return kb


def price_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🟢 Бесплатно", "🟡 До 400 ₽")
    kb.row("⚪ Любая цена")
    kb.row("⬅️ К фильтрам")
    return kb


# =========================
# PROFILE / MY ITEMS
# =========================
def get_user_profile_text(chat_id: int) -> str:
    my_items = get_user_items(chat_id)
    active_items = [x for x in my_items if x[7] == 0]
    archive_items = [x for x in my_items if x[7] == 1]
    total_views = sum(item[6] for item in my_items) if my_items else 0
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


def show_my_item(chat_id: int, item_id: int):
    item = get_item_by_id(item_id)
    if not item:
        bot.send_message(chat_id, "Объявление не найдено", reply_markup=submenu_menu())
        return

    if item[5] != chat_id:
        bot.send_message(chat_id, "Это не твоё объявление", reply_markup=submenu_menu())
        return

    if item[7] == 1:
        bot.send_message(chat_id, "Это объявление уже в архиве", reply_markup=submenu_menu())
        return

    show_item(chat_id, item, count_view=False)


def show_archive_item(chat_id: int, item_id: int):
    item = get_item_by_id(item_id)
    if not item:
        bot.send_message(chat_id, "Архивное объявление не найдено", reply_markup=submenu_menu())
        return

    if item[5] != chat_id:
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
# CREATE FLOW
# =========================
def start_create_flow(chat_id: int):
    pending_create[chat_id] = {
        "step": "title",
        "data": {
            "photos": []
        }
    }
    bot.send_message(
        chat_id,
        "➕ Добавление объявления\n\nНапиши название вещи:",
        reply_markup=cancel_kb()
    )


def finish_create(chat_id: int):
    data = pending_create[chat_id]["data"]

    item_id = add_item(
        title=data["title"],
        price=data["price"],
        city=data["city"],
        category=data["category"],
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
        except:
            pass

    if len(parts) > 1 and parts[1].startswith("item_"):
        try:
            item_id = int(parts[1].replace("item_", ""))
            item = get_item_by_id(item_id)
            if item and item[7] == 0:
                show_item(chat_id, item)
                return
        except:
            pass

    reset_filters(chat_id)
    user_index[chat_id] = 0

    bot.send_message(
        chat_id,
        "Добро пожаловать в Даром 🎁\n\nЗдесь можно отдавать вещи бесплатно.",
        reply_markup=main_menu()
    )


# =========================
# MAIN MENU
# =========================
@bot.message_handler(func=lambda m: m.text == "🔎 Смотреть")
def watch_items(message):
    chat_id = message.chat.id
    items = get_filtered_items(chat_id)

    if not items:
        bot.send_message(chat_id, "По текущим фильтрам объявлений нет 😔", reply_markup=main_menu())
        return

    user_index[chat_id] = 0
    show_item(chat_id, items[0])


@bot.message_handler(func=lambda m: m.text == "➕ Добавить")
def add_menu(message):
    start_create_flow(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "❤️ Избранное")
def favorites_menu(message):
    chat_id = message.chat.id
    user_id = get_user_id(chat_id)
    fav_ids = get_favorites(user_id)

    if not fav_ids:
        bot.send_message(chat_id, "В избранном пока пусто ❤️", reply_markup=main_menu())
        return

    kb = types.InlineKeyboardMarkup()
    for item_id in fav_ids:
        item = get_item_by_id(item_id)
        if item and item[7] == 0:
            kb.row(types.InlineKeyboardButton(short_item_label(item), callback_data=f"favopen_{item_id}"))

    if not kb.keyboard:
        bot.send_message(chat_id, "В избранном сейчас нет активных объявлений", reply_markup=main_menu())
        return

    bot.send_message(chat_id, "❤️ Избранное:", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "⚙️ Фильтры")
def filters(message):
    show_filters_menu(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "🏠 Меню")
def menu(message):
    bot.send_message(message.chat.id, "Дополнительное меню:", reply_markup=submenu_menu())


@bot.message_handler(func=lambda m: m.text == "⬅️ Назад")
def back(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu())


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


@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def stats_menu(message):
    chat_id = message.chat.id
    my_items = get_user_items(chat_id)
    total_views = sum(item[6] for item in my_items) if my_items else 0
    total_likes = sum(get_likes_count(item[0]) for item in my_items) if my_items else 0
    refs = get_referrals_count(chat_id)

    text = (
        "📊 Твоя статистика:\n\n"
        f"👥 Приглашено друзей: {refs}\n"
        f"📦 Твоих объявлений: {len(my_items)}\n"
        f"👁 Просмотров: {total_views}\n"
        f"❤️ Лайков: {total_likes}"
    )
    bot.send_message(chat_id, text, reply_markup=submenu_menu())


@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def profile_menu(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, get_user_profile_text(chat_id), reply_markup=submenu_menu())


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


@bot.message_handler(func=lambda m: m.text == "🆘 Помощь / правила")
def help_menu(message):
    text = (
        "🆘 Помощь / правила\n\n"
        "1. Размещай реальные вещи\n"
        "2. Не публикуй запрещённые товары\n"
        "3. Будь вежлив\n"
        "4. Если вещь уже отдана — нажми ✅ Отдано\n"
        f"5. Можно загрузить до {MAX_PHOTOS_PER_ITEM} фото\n"
        "6. Для добавления объявления нажми ➕ Добавить"
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


@bot.message_handler(func=lambda m: m.text == "🔍 Поиск")
def search_menu(message):
    pending_search.add(message.chat.id)
    bot.send_message(message.chat.id, "Напиши слово для поиска.\nНапример: куртка", reply_markup=submenu_menu())


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
    show_item(chat_id, items[0])


@bot.message_handler(func=lambda m: (m.text in POPULAR_CITIES or m.text == "🌍 Любой город") and m.chat.id not in pending_create)
def set_city_filter(message):
    ensure_filters(message.chat.id)
    user_filters[message.chat.id]["city"] = None if message.text == "🌍 Любой город" else message.text
    show_filters_menu(message.chat.id, "📍 Фильтр по городу обновлён")


@bot.message_handler(func=lambda m: (m.text in CATEGORIES or m.text == "🌍 Любая категория") and m.chat.id not in pending_create)
def set_category_filter(message):
    ensure_filters(message.chat.id)
    user_filters[message.chat.id]["category"] = None if message.text == "🌍 Любая категория" else message.text
    show_filters_menu(message.chat.id, "📦 Фильтр по категории обновлён")


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
# CREATE FLOW
# =========================
@bot.message_handler(func=lambda m: m.text == "❌ Отмена" and m.chat.id in pending_create)
def cancel_create(message):
    pending_create.pop(message.chat.id, None)
    bot.send_message(message.chat.id, "Создание объявления отменено", reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "title")
def create_title_handler(message):
    text = (message.text or "").strip()
    if len(text) < 2:
        bot.send_message(message.chat.id, "Название слишком короткое")
        return

    pending_create[message.chat.id]["data"]["title"] = text
    pending_create[message.chat.id]["step"] = "price"
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
    bot.send_message(message.chat.id, "📍 Напиши город", reply_markup=cancel_kb())


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "city")
def create_city_handler(message):
    text = (message.text or "").strip()
    if len(text) < 1:
        bot.send_message(message.chat.id, "Напиши город.")
        return

    pending_create[message.chat.id]["data"]["city"] = text
    pending_create[message.chat.id]["step"] = "category"
    bot.send_message(message.chat.id, "📦 Выбери категорию", reply_markup=category_pick_kb())


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "category")
def create_category_handler(message):
    text = (message.text or "").strip()
    if text not in CATEGORIES:
        bot.send_message(message.chat.id, "Выбери категорию кнопкой ниже.")
        return

    pending_create[message.chat.id]["data"]["category"] = text
    pending_create[message.chat.id]["step"] = "photo"
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

    if chat_id not in pending_create:
        return

    if pending_create[chat_id]["step"] != "photo":
        return

    photos = pending_create[chat_id]["data"]["photos"]

    if len(photos) >= MAX_PHOTOS_PER_ITEM:
        bot.send_message(chat_id, f"Можно максимум {MAX_PHOTOS_PER_ITEM} фото.")
        return

    photos.append(message.photo[-1].file_id)

    bot.send_message(
        chat_id,
        f"Фото добавлено: {len(photos)}/{MAX_PHOTOS_PER_ITEM}",
        reply_markup=photo_step_kb(len(photos))
    )


# =========================
# CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    data = call.data

    if data == "next":
        items = get_filtered_items(chat_id)
        if not items:
            bot.answer_callback_query(call.id, "Нет объявлений")
            return

        idx = user_index.get(chat_id, 0)
        idx = (idx + 1) % len(items)
        user_index[chat_id] = idx
        show_item(chat_id, items[idx], message_id=call.message.message_id)
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
        if item and item[7] == 0:
            show_item(chat_id, item, count_view=False, message_id=call.message.message_id)
        return

    if data.startswith("fav_"):
        item_id = int(data.split("_")[1])
        user_id = get_user_id(chat_id)
        add_favorite(user_id, item_id)
        bot.answer_callback_query(call.id, "Добавлено в избранное ❤️")
        return

    if data.startswith("share_"):
        item_id = int(data.split("_")[1])
        bot.send_message(chat_id, build_share_text(item_id), reply_markup=main_menu())
        bot.answer_callback_query(call.id, "Ссылка отправлена")
        return

    if data.startswith("take_"):
        item_id = int(data.split("_")[1])
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
        ok, remain = can_bump_item(item_id, chat_id)

        if not ok:
            bot.answer_callback_query(call.id, f"Можно через {format_seconds_to_human(remain)}")
            return

        new_item_id = bump_item(item_id, chat_id)
        if new_item_id:
            bot.answer_callback_query(call.id, "Объявление поднято 🚀")
            show_my_item(chat_id, new_item_id)
        else:
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
        if item and item[7] == 0:
            show_item(chat_id, item)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("searchopen_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)
        if item and item[7] == 0:
            show_item(chat_id, item)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("popular_"):
        item_id = int(data.split("_")[1])
        item = get_item_by_id(item_id)
        if item and item[7] == 0:
            show_item(chat_id, item)
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
