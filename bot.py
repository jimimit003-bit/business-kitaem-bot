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
    views INTEGER NOT NULL DEFAULT 0
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

# =========================
# DB HELPERS
# =========================
def refresh_items():
    global ITEMS
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views
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
        INSERT INTO items (title, price, city, category, photo_id, owner_tg, views)
        VALUES (?, ?, ?, ?, ?, ?, 0)
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
        SELECT id, title, price, city, category, photo_id, owner_tg, views
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


def get_views_count(item_id: int) -> int:
    cursor.execute(
        "SELECT views FROM items WHERE id = ?",
        (item_id,)
    )
    row = cursor.fetchone()
    return row[0] if row else 0


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
        types.KeyboardButton("🏠 Смотреть"),
        types.KeyboardButton("❤️ Избранное")
    )
    kb.row(
        types.KeyboardButton("📦 Мои объявления"),
        types.KeyboardButton("➕ Добавить")
    )
    kb.row(
        types.KeyboardButton("🎁 Пригласить друга"),
        types.KeyboardButton("📊 Моя статистика")
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
        types.InlineKeyboardButton("➡ Следующее", callback_data="next"),
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
            types.InlineKeyboardButton("🗑 Удалить мое объявление", callback_data="delete")
        )

    return kb


def format_item_text(item) -> str:
    item_id, title, price, city, category, photo_id, owner_tg, views = item

    text = f"🧥 {title}\n"
    text += "🟢 Бесплатно\n" if price == 0 else f"💰 {price} ₽\n"
    text += f"📍 {city}\n"
    text += f"📦 {category}\n"
    text += f"👁 {views} просмотров"
    return text


def show_item(chat_id: int, item, send_new: bool = True, edit_message_id: int | None = None):
    if not item:
        bot.send_message(
            chat_id,
            "Пока нет объявлений 😕\n"
            "Добавь первое командой:\n"
            "/add Куртка;0;Москва;Одежда",
            reply_markup=main_menu()
        )
        return

    item_id, title, price, city, category, photo_id, owner_tg, views = item

    add_view(item_id)
    refresh_items()

    # перечитываем обновлённый item с новым views
    cursor.execute("""
        SELECT id, title, price, city, category, photo_id, owner_tg, views
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

    # реферальная ссылка: /start ref_123456
    parts = message.text.split()
    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            inviter_tg = int(parts[1].replace("ref_", ""))
            add_referral(inviter_tg, chat_id)
        except ValueError:
            pass

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
# MENU BUTTONS
# =========================
@bot.message_handler(func=lambda m: m.text == "🏠 Смотреть")
def menu_watch(message):
    refresh_items()
    user_index[message.chat.id] = 0
    item = get_item_by_index(0)
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


@bot.message_handler(func=lambda m: m.text == "📦 Мои объявления")
def menu_my_items(message):
    chat_id = message.chat.id
    rows = get_user_items(chat_id)

    if not rows:
        bot.send_message(chat_id, "У тебя пока нет своих объявлений")
        return

    text = "📦 Мои объявления:\n\n"
    for item in rows[:20]:
        item_id, title, price, city, category, photo_id, owner_tg, views = item
        text += f"#{item_id} — {title} | "
        text += "Бесплатно" if price == 0 else f"{price} ₽"
        text += f" | {city} | {category} | 👁 {views}\n"

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
        SELECT id, title, price, city, category, photo_id, owner_tg, views
        FROM items
        WHERE id IN ({placeholders})
        ORDER BY id DESC
    """, fav_ids)
    rows = cursor.fetchall()

    text = "❤️ Избранное:\n\n"
    for item in rows[:20]:
        item_id, title, price, city, category, photo_id, owner_tg, views = item
        text += f"#{item_id} — {title} | "
        text += "Бесплатно" if price == 0 else f"{price} ₽"
        text += f" | {city} | {category} | 👁 {views}\n"

    bot.send_message(chat_id, text)


@bot.message_handler(func=lambda m: m.text == "🎁 Пригласить друга")
def menu_invite(message):
    me = bot.get_me()
    invite_link = f"https://t.me/{me.username}?start=ref_{message.chat.id}"

    count = get_referrals_count(message.chat.id)

    bot.send_message(
        message.chat.id,
        "🎁 Пригласи друга по своей ссылке:\n\n"
        f"{invite_link}\n\n"
        f"Уже приглашено: {count}"
    )


@bot.message_handler(func=lambda m: m.text == "📊 Моя статистика")
def menu_stats(message):
    chat_id = message.chat.id
    refs = get_referrals_count(chat_id)
    my_items = get_user_items(chat_id)

    total_views = sum(item[7] for item in my_items) if my_items else 0

    text = (
        f"📊 Твоя статистика:\n\n"
        f"👥 Приглашено друзей: {refs}\n"
        f"📦 Твоих объявлений: {len(my_items)}\n"
        f"👁 Просмотров объявлений: {total_views}"
    )
    bot.send_message(chat_id, text)


# =========================
# CALLBACKS
# =========================
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
        show_item(chat_id, item, send_new=False, edit_message_id=call.message.message_id)
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

        refresh_items()
        item = ITEMS[idx % len(ITEMS)]
        show_item(chat_id, item, send_new=False, edit_message_id=call.message.message_id)

    elif call.data == "fav":
        idx = user_index.get(chat_id, 0)
        item = ITEMS[idx]
        item_id = item[0]
        user_id = get_user_id(chat_id)

        add_favorite(user_id, item_id)
        bot.answer_callback_query(call.id, "Добавлено в избранное ❤️")

    elif call.data == "take":
        bot.answer_callback_query(call.id, "Свяжись с владельцем через кнопку 💬")

    elif call.data == "delete":
        idx = user_index.get(chat_id, 0)
        item = ITEMS[idx]
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

            refresh_items()
            if ITEMS:
                user_index[chat_id] = 0
                show_item(chat_id, get_item_by_index(0))
            else:
                bot.send_message(
                    chat_id,
                    "Объявлений больше нет",
                    reply_markup=main_menu()
                )
        else:
            bot.answer_callback_query(call.id, "Не удалось удалить")


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
