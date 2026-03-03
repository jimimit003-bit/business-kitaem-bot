import telebot
import sys
import sqlite3
print("=== BOT STARTING ===", flush=True)
sys.stdout.flush()
TOKEN = "8516444407:AAGseUj72idFT6b86hbg1W8qI48BIT_kd4Q"
bot = telebot.TeleBot(TOKEN)
from telebot import types
ITEMS = []
# === CREATE FLOW STATES ===
ST_NONE = 0
ST_ADD_KIND = 10
ST_ADD_TITLE = 11
ST_ADD_PRICE = 12
ST_ADD_CITY = 13
ST_ADD_CONFIRM = 14

user_state = {}     # chat_id -> state
user_draft = {}     # chat_id -> dict(kind,title,price,city)
# === DATABASE ===
conn = sqlite3.connect("darom.db", check_same_thread=False)
cursor = conn.cursor()

# Таблица пользователей
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE
)
""")

# Таблица объявлений
cursor.execute("""
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    price INTEGER,
    city TEXT
)
""")
# добавляем колонку kind если её нет
try:
    cursor.execute("ALTER TABLE items ADD COLUMN kind TEXT DEFAULT 'regular'")
    conn.commit()
except:
    pass
# Таблица избранного
cursor.execute("""
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    item_id INTEGER
)
""")
# --- Антиспам/баны ---
cursor.execute("""
CREATE TABLE IF NOT EXISTS bans (
  user_id INTEGER PRIMARY KEY,
  reason TEXT,
  created_at INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS spam_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  created_at INTEGER
)
""")
conn.commit()
def add_favorite(user_id: int, item_id: int):
    cursor.execute(
        "INSERT INTO favorites (user_id, item_id) VALUES (?, ?)",
        (user_id, item_id)
    )
    conn.commit()

def remove_favorite(user_id: int, item_id: int):
    cursor.execute(
        "DELETE FROM favorites WHERE user_id = ? AND item_id = ?",
        (user_id, item_id)
    )
    conn.commit()
def add_item(title: str, price: int, city: str, kind: str = "regular"):
    cursor.execute(
        "INSERT INTO items(title, price, city, kind) VALUES (?, ?, ?, ?)",
        (title, price, city, kind)
    )
    conn.commit()
@bot.message_handler(commands=['addfree'])
def addfree_command(message):
    # формат: /addfree Название;Город
    text = message.text.replace('/addfree', '', 1).strip()
    parts = [p.strip() for p in text.split(';')]

    if len(parts) < 2 or parts[0] == '' or parts[1] == '':
        bot.send_message(message.chat.id, "❌ Формат:\n/addfree Название;Город\nПример:\n/addfree Куртка;Москва")
        return

    title = parts[0]
    city = parts[1]
    price = 0

    add_item(title, price, city, kind="free")
    bot.send_message(message.chat.id, f"✅ Добавлено (Бесплатно):\n{title}\n🟢 Бесплатно\n📍 {city}")


@bot.message_handler(commands=['add400'])
def add400_command(message):
    # формат: /add400 Название;Цена;Город
    text = message.text.replace('/add400', '', 1).strip()
    parts = [p.strip() for p in text.split(';')]

    if len(parts) < 3 or parts[0] == '' or parts[1] == '' or parts[2] == '':
        bot.send_message(message.chat.id, "❌ Формат:\n/add400 Название;Цена;Город\nПример:\n/add400 Кроссовки;350;Москва")
        return

    title = parts[0]
    city = parts[2]

    try:
        price = int(parts[1])
    except:
        bot.send_message(message.chat.id, "❌ Цена должна быть числом.")
        return

    if price < 1 or price > 400:
        bot.send_message(message.chat.id, "❌ Для /add400 цена должна быть от 1 до 400 ₽.")
        return

    add_item(title, price, city, kind="under400")
    bot.send_message(message.chat.id, f"✅ Добавлено (До 400₽):\n{title}\n🟡 {price} ₽\n📍 {city}")

def get_items():
    cursor.execute("SELECT id, title, price, city FROM items ORDER BY id DESC")
    return cursor.fetchall()


def get_item_by_index(idx: int):
    items = get_items()
    if not items:
        return None
    return items[idx % len(items)]


def get_favorites(user_id: int):
    cursor.execute("SELECT item_id FROM favorites WHERE user_id = ?", (user_id,))
    return [row[0] for row in cursor.fetchall()]



def get_user_id(telegram_id: int) -> int:
    cursor.execute("INSERT OR IGNORE INTO users(telegram_id) VALUES(?)", (telegram_id,))
    conn.commit()
    cursor.execute("SELECT id FROM users WHERE telegram_id=?", (telegram_id,))
    return cursor.fetchone()[0]



def get_item_db_id(idx: int) -> int:
    # предполагаем, что items загружены в том же порядке, что ITEMS
    cursor.execute("SELECT id FROM items LIMIT 1 OFFSET ?", (idx,))
    row = cursor.fetchone()
    return row[0] if row else None

def add_favorite(user_id: int, item_id: int):
    cursor.execute(
        "INSERT INTO favorites(user_id, item_id) VALUES(?,?)",
        (user_id, item_id)
    )
    conn.commit()

def clear_favorites(user_id: int):
    cursor.execute("DELETE FROM favorites WHERE user_id=?", (user_id,))
    conn.commit()




# ----- Состояние пользователя -----
user_state = {}
add_state = {}   # хранит шаг добавления объявления
# --- Кнопки карточки (PRO режим) ---
def build_card_keyboard():
    kb = types.InlineKeyboardMarkup()

    kb.row(
        types.InlineKeyboardButton("✅ Забрать", callback_data="take"),
        types.InlineKeyboardButton("❤️", callback_data="fav")
    )

    kb.row(
        types.InlineKeyboardButton("➡️ Следующее", callback_data="next"),
        types.InlineKeyboardButton("📍 Карта", callback_data="map")
    )

    kb.row(
        types.InlineKeyboardButton("💰 Цена", callback_data="f_price"),
        types.InlineKeyboardButton("🗂 Категория", callback_data="f_cat"),
        types.InlineKeyboardButton("🏙 Город", callback_data="f_city")
    )

    kb.row(
        types.InlineKeyboardButton("📍 Рядом со мной", callback_data="near"),
        types.InlineKeyboardButton("♻️ Сброс", callback_data="reset")
    )
    kb.row(
    types.InlineKeyboardButton("➕ Добавить объявление", callback_data="add_item")
    )
    return kb
    # --- Меню цены ---
def build_price_menu():
    kb = types.InlineKeyboardMarkup()

    kb.row(
        types.InlineKeyboardButton("🟢 Бесплатно", callback_data="set_price_free"),
        types.InlineKeyboardButton("🟡 До 400 ₽", callback_data="set_price_400"),
    )

    kb.row(
        types.InlineKeyboardButton("⚪ Любая", callback_data="set_price_any"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_card"),
    )

    return kb
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_state[chat_id] = 0

    item = get_item_by_index(0)  # берем первое объявление из БД
    if not item:
        bot.send_message(chat_id, "Пока нет объявлений 😕\nДобавь первое командой:\n/add Куртка;0;Москва")
        return

    # item = (id, title, price, city)
    text = f"🧥 {item[1]}\n"
    text += ("🟢 Бесплатно\n" if item[2] == 0 else f"🟡 {item[2]}\n")
    text += f"📍 {item[3]}"

    bot.send_message(chat_id, text, reply_markup=build_card_keyboard())
@bot.message_handler(commands=['add'])
def add_command(message):
    text = message.text.replace('/add', '', 1).strip()
    parts = [p.strip() for p in text.split(';')]

    if len(parts) < 1 or parts[0] == '':
        bot.send_message(
            message.chat.id,
            "❌ Формат:\n/add Название; цена; город\n\n"
            "Пример:\n/add Куртка; 0; Москва"
        )
        return

    title = parts[0]
    price = 0
    city = ""

    if len(parts) >= 2:
        try:
            price = int(parts[1])
        except:
            price = 0

    if len(parts) >= 3:
        city = parts[2]
    if len(title) < 3:
        bot.send_message(message.chat.id, "❌ Название слишком короткое")
        return
        add_item(title, price, city)

    bot.send_message(
        message.chat.id,
        f"✅ Добавлено:\n{title}\n💰 {price}\n📍 {city}"
    )
@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.send_message(message.chat.id, message.text)


@bot.callback_query_handler(func=lambda call: True)
def handle_buttons(call):
    chat_id = call.message.chat.id

    # текущий индекс (если не был задан — 0)
    idx = user_state.get(chat_id, 0)

    if call.data == "next":
        idx = (idx + 1) % len(ITEMS)
        user_state[chat_id] = idx

        item = ITEMS[idx]

        text = f"🧥 {item['title']}\n"
        text += f"🟢 Бесплатно\n" if item['price'] == 0 else f"🟡 {item['price']}₽\n"
        text += f"📍 {item['city']}"

        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            reply_markup=build_card_keyboard()
        )
        bot.answer_callback_query(call.id, "Следующая ✅")

    elif call.data == "take":
        bot.answer_callback_query(call.id, "Забрал ✅")

    elif call.data == "fav":
        idx = user_state.get(chat_id, 0)
        add_favorite(chat_id, idx)
        bot.answer_callback_query(call.id, "Добавил в ❤️")

    elif call.data == "reset":
        user_state[chat_id] = 0
        bot.answer_callback_query(call.id, "Сбросил 🔄")

    elif call.data == "near":
        bot.answer_callback_query(call.id, "Ищу рядом 📍")

    elif call.data == "map":
        bot.answer_callback_query(call.id, "Открываю карту 📍")

    elif call.data == "f_price":
        bot.edit_message_text(
            "💰 Цена — выбери вариант:",
            chat_id,
            call.message.message_id,
            reply_markup=build_price_menu()
        )
        bot.answer_callback_query(call.id)

    elif call.data == "back_to_card":
        # возвращаем текущую карточку
        idx = user_state.get(chat_id, 0)
        item = ITEMS[idx]

        text = f"🧥 {item['title']}\n"
        text += f"🟢 Бесплатно\n" if item['price'] == 0 else f"🟡 {item['price']}₽\n"
        text += f"📍 {item['city']}"

        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            reply_markup=build_card_keyboard()
        )
        bot.answer_callback_query(call.id)
    elif call.data == "add_item":
        user_state[chat_id] = ST_ADD_KIND
        user_draft[chat_id] = {}
        bot.send_message(chat_id, "Выберите тип объявления:", reply_markup=kb_choose_kind())
        bot.answer_callback_query(call.id)

    elif call.data in ("set_price_free", "set_price_400", "set_price_any"):
        # если фильтр цены пока не делаем — хотя бы не падаем
        bot.answer_callback_query(call.id)

    else:
        bot.answer_callback_query(call.id)
    
def kb_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("❌ Отмена"))
    return kb

def kb_choose_kind():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(types.KeyboardButton("🟢 Бесплатно"), types.KeyboardButton("🟡 До 400 ₽"))
    kb.row(types.KeyboardButton("⚪️ Обычное"), types.KeyboardButton("❌ Отмена"))
    return kb

@bot.message_handler(func=lambda m: m.text == "➕ Добавить объявление")
def start_add_flow(message):
    chat_id = message.chat.id
    user_state[chat_id] = ST_ADD_KIND
    user_draft[chat_id] = {}
    bot.send_message(chat_id, "Выбери тип объявления:", reply_markup=kb_choose_kind())
@bot.message_handler(func=lambda m: m.text == "❌ Отмена")
def cancel_flow(message):
    chat_id = message.chat.id
    user_state[chat_id] = ST_NONE
    user_draft.pop(chat_id, None)
    bot.send_message(chat_id, "Ок, отменено.", reply_markup=types.ReplyKeyboardRemove())


    
print("Bot started...")
bot.infinity_polling()
