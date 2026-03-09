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
CATEGORIES = ["Одежда", "Обувь", "Техника", "Дом", "Детское", "Другое"]
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
        photos_line = f"\n📸 Фото: {photo_idx + 1}/{len(photos)}"

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


def get_view_photo_idx(chat_id: int) -> int:
    st = view_state.get(chat_id)
    if not st:
        return 0
    return st.get("photo_idx", 0)


# =========================
# STEP CREATE
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


def photo_step_kb(photo_count: int = 0):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if photo_count < MAX_PHOTOS_PER_ITEM:
        kb.row("✅ Готово без ещё фото")
    if photo_count > 0:
        kb.row("✅ Опубликовать")
    kb.row("❌ Отмена")
    return kb


def replace_photo_step_kb(photo_count: int = 0):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if photo_count > 0:
        kb.row("✅ Сохранить фото")
    kb.row("❌ Отмена замены фото")
    return kb


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


def create_preview_text(data: dict) -> str:
    price = data.get("price", 0)
    photos_count = len(data.get("photos", []))
    text = "✅ Объявление создано:\n\n"
    text += f"🧥 {data.get('title', '')}\n"
    text += "🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n"
    text += f"📍 {data.get('city', '')}\n"
    text += f"📦 {data.get('category', '')}\n"
    text += f"📸 Фото: {photos_count}"
    return text


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
    bot.send_message(chat_id, create_preview_text(data), reply_markup=main_menu())
    return item_id


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

    photos = get_item_photos(item_id)
    kb = types.InlineKeyboardMarkup()

    if len(photos) > 1:
        kb.row(
            types.InlineKeyboardButton("⬅️ Фото", callback_data=f"prevphoto_{item_id}"),
            types.InlineKeyboardButton("Фото ➡️", callback_data=f"nextphoto_{item_id}")
        )

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
            types.InlineKeyboardButton("🚀 Поднять", callback_data=f"bump_{item_id}")
        )
        kb.row(
            types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{item_id}"),
            types.InlineKeyboardButton("🖼 Заменить фото", callback_data=f"replacephotos_{item_id}")
        )
        kb.row(
            types.InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{item_id}")
        )
    else:
        kb.row(
            types.InlineKeyboardButton("✅ Забрать", callback_data="take"),
            types.InlineKeyboardButton("⚠️ Жалоба", callback_data=f"report_{item_id}")
        )

    return kb


def build_archive_card_keyboard(item_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("♻️ Вернуть", callback_data=f"restore_{item_id}"),
        types.InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{item_id}")
    )
    return kb


def show_item(chat_id: int, item, send_new: bool = True, edit_message_id: Optional[int] = None, count_view: bool = True, mode: str = "feed"):
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
                "По текущим фильтрам объявлений нет 😕\n\nПопробуй ♻️ Сбросить фильтры",
                reply_markup=main_menu()
            )
        return

    item_id, title, price, city, category, owner_tg, views, is_taken, bump_count, last_bump_at, created_at = item

    if count_view:
        add_view(item_id)

    fresh_item = get_item_by_id(item_id)
    if not fresh_item:
        bot.send_message(chat_id, "Объявление уже недоступно", reply_markup=main_menu())
        return

    if fresh_item[7] == 1:
        bot.send_message(chat_id, "Это объявление уже в архиве", reply_markup=submenu_menu())
        return

    photos = get_item_photos(item_id)
    photo_idx = get_view_photo_idx(chat_id)
    if photo_idx >= len(photos):
        photo_idx = 0

    set_view_state(chat_id, item_id, mode=mode, photo_idx=photo_idx)

    text = item_to_text(fresh_item, photo_idx)
    reply_markup = build_card_keyboard(item_id, chat_id, owner_tg)

    if photos:
        selected_photo = photos[photo_idx]
        if edit_message_id is not None:
            try:
                media = types.InputMediaPhoto(selected_photo, caption=text)
                bot.edit_message_media(
                    media=media,
                    chat_id=chat_id,
                    message_id=edit_message_id,
                    reply_markup=reply_markup
                )
            except Exception:
                try:
                    bot.delete_message(chat_id, edit_message_id)
                except Exception:
                    pass
                bot.send_photo(chat_id, selected_photo, caption=text, reply_markup=reply_markup)
        else:
            bot.send_photo(chat_id, selected_photo, caption=text, reply_markup=reply_markup)
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

    set_view_state(chat_id, item_id, mode="my", photo_idx=0)
    show_item(chat_id, item, count_view=False, mode="my")


def show_archive_item(chat_id: int, item_id: int):
    item = get_item_by_id(item_id)
    if not item:
        bot.send_message(chat_id, "Архивное объявление не найдено", reply_markup=submenu_menu())
        return

    if item[5] != chat_id:
        bot.send_message(chat_id, "Это не твоё объявление", reply_markup=submenu_menu())
        return

    if item[7] == 0:
        bot.send_message(chat_id, "Это объявление уже активно", reply_markup=submenu_menu())
        return

    photos = get_item_photos(item_id)
    text = item_to_text(item, 0) + "\n\n🗂 Статус: в архиве"
    reply_markup = build_archive_card_keyboard(item[0])

    if photos:
        bot.send_photo(chat_id, photos[0], caption=text, reply_markup=reply_markup)
    else:
        bot.send_message(chat_id, text, reply_markup=reply_markup)


def build_my_items_keyboard(owner_tg: int):
    items = get_user_active_items(owner_tg)
    kb = types.InlineKeyboardMarkup()
    for item in items[:20]:
        kb.row(types.InlineKeyboardButton(short_item_label(item), callback_data=f"myitem_{item[0]}"))
    return kb


def build_archive_keyboard(owner_tg: int):
    items = get_user_archive_items(owner_tg)
    kb = types.InlineKeyboardMarkup()
    for item in items[:20]:
        kb.row(types.InlineKeyboardButton(short_item_label(item), callback_data=f"architem_{item[0]}"))
    return kb


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
    reset_filters(chat_id)
    user_index[chat_id] = 0

    bot.send_message(chat_id, "Главное меню:", reply_markup=main_menu())
    item = get_item_by_index(chat_id, 0)
    show_item(chat_id, item, mode="feed")


@bot.message_handler(commands=['add'])
def add_cmd(message):
    chat_id = message.chat.id
    raw = message.text.replace('/add', '', 1).strip()
    parsed = parse_edit_text(raw)

    if not parsed:
        bot.send_message(
            chat_id,
            "❌ Формат:\n/add Название;Цена;Город;Категория\n\nПример:\n/add Куртка;0;Москва;Одежда"
        )
        return

    title, price, city, category = parsed
    item_id = add_item(title, price, city, category, chat_id)

    bot.send_message(
        chat_id,
        f"✅ Добавлено:\n🧥 {title}\n"
        + ("🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n")
        + f"📍 {city}\n"
        + f"📦 {category}",
        reply_markup=main_menu()
    )
    show_my_item(chat_id, item_id)


# =========================
# PHOTO
# =========================
@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    chat_id = message.chat.id
    photo_id = message.photo[-1].file_id

    # замена фото
    if chat_id in pending_replace_photo:
        item_id = pending_replace_photo[chat_id]["item_id"]
        photos = pending_replace_photo[chat_id]["photos"]

        if len(photos) >= MAX_PHOTOS_PER_ITEM:
            bot.send_message(chat_id, f"Можно максимум {MAX_PHOTOS_PER_ITEM} фото. Нажми «✅ Сохранить фото».")
            return

        photos.append(photo_id)
        pending_replace_photo[chat_id]["photos"] = photos

        if len(photos) >= MAX_PHOTOS_PER_ITEM:
            replace_item_photos(item_id, photos)
            pending_replace_photo.pop(chat_id, None)
            bot.send_message(chat_id, "✅ Фото объявления обновлены", reply_markup=main_menu())
            show_my_item(chat_id, item_id)
        else:
            bot.send_message(
                chat_id,
                f"Фото добавлено: {len(photos)}/{MAX_PHOTOS_PER_ITEM}\nОтправь ещё фото или нажми «✅ Сохранить фото»",
                reply_markup=replace_photo_step_kb(len(photos))
            )
        return

    # пошаговое добавление
    if chat_id in pending_create and pending_create[chat_id]["step"] == "photo":
        photos = pending_create[chat_id]["data"]["photos"]

        if len(photos) >= MAX_PHOTOS_PER_ITEM:
            bot.send_message(chat_id, f"Можно максимум {MAX_PHOTOS_PER_ITEM} фото. Нажми «✅ Опубликовать».")
            return

        photos.append(photo_id)
        pending_create[chat_id]["data"]["photos"] = photos

        if len(photos) >= MAX_PHOTOS_PER_ITEM:
            item_id = finish_create(chat_id)
            show_my_item(chat_id, item_id)
        else:
            bot.send_message(
                chat_id,
                f"Фото добавлено: {len(photos)}/{MAX_PHOTOS_PER_ITEM}\nОтправь ещё фото или нажми «✅ Опубликовать»",
                reply_markup=photo_step_kb(len(photos))
            )
        return

    bot.send_message(chat_id, "Фото получено, но сейчас бот не ждёт фото")


# =========================
# EDIT TEXT
# =========================
@bot.message_handler(func=lambda m: m.chat.id in pending_edit)
def handle_edit_text(message):
    chat_id = message.chat.id
    item_id = pending_edit[chat_id]

    parsed = parse_edit_text(message.text.strip())
    if not parsed:
        bot.send_message(
            chat_id,
            "❌ Ошибка формата.\nОтправь так:\nНазвание;Цена;Город;Категория\n\nПример:\nКуртка зимняя;0;Москва;Одежда"
        )
        return

    title, price, city, category = parsed
    ok = update_item(item_id, chat_id, title, price, city, category)

    if not ok:
        bot.send_message(chat_id, "Не удалось обновить объявление")
        return

    pending_edit.pop(chat_id, None)
    bot.send_message(chat_id, "✅ Объявление обновлено")
    show_my_item(chat_id, item_id)


# =========================
# CREATE FLOW
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
        bot.send_message(message.chat.id, "Название слишком короткое. Напиши нормальное название вещи.")
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
        f"🖼 Теперь отправь до {MAX_PHOTOS_PER_ITEM} фото товара.\n"
        "Можно отправлять по одному.\n"
        "Если фото не нужно — нажми «✅ Готово без ещё фото».",
        reply_markup=photo_step_kb(0)
    )


@bot.message_handler(func=lambda m: m.chat.id in pending_create and pending_create[m.chat.id]["step"] == "photo" and m.text in ["✅ Готово без ещё фото", "✅ Опубликовать"])
def create_finish_handler(message):
    item_id = finish_create(message.chat.id)
    show_my_item(message.chat.id, item_id)
