import os
import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
bot = telebot.TeleBot(TOKEN)

last_message = {}

sections = {
    "intro": {
        "title": "📚 Введение",
        "premium": False,
        "lessons": ["🤖 Ознакомление с ботом", "🎓 Ознакомление с обучением"]
    },
    "platforms": {
        "title": "🔎 Обзор китайских площадок",
        "premium": False,
        "lessons": ["Taobao", "1688", "Poizon", "Pinduoduo", "Alibaba"]
    },
    "registration": {
        "title": "👤 Регистрация и настройка аккаунтов",
        "premium": False,
        "lessons": ["Taobao", "1688", "Poizon", "Pinduoduo", "Alibaba", "WeChat", "Alipay"]
    },
    "delivery": {
        "title": "🚚 Настройка доставки",
        "premium": False,
        "lessons": ["📍 Как добавить адрес карго"]
    },
    "search": {
        "title": "📦 Поиск товара",
        "premium": False,
        "lessons": ["🔍 Поиск по фото", "🔑 Поиск по ключевым словам"]
    },
    "suppliers": {
        "title": "🏭 Работа с фабриками и поставщиками",
        "premium": True,
        "lessons": ["🏭 Как проверить фабрику или поставщика", "💬 Как написать китайцу", "💰 Как торговаться", "📦 Что такое MOQ"]
    },
    "orders": {
        "title": "📝 Как оформить заказ",
        "premium": True,
        "lessons": ["🛒 Оформление заказа", "💳 Оплата", "📍 Отслеживание товара", "📦 Поступление товара на склад"]
    },
    "returns": {
        "title": "↩️ Возврат и отмена заказа",
        "premium": True,
        "lessons": ["❌ Отмена заказа", "📦 Возврат товара", "⚖️ Открытие спора"]
    },
    "cargo": {
        "title": "🚛 Карго и логистика",
        "premium": True,
        "lessons": ["🚛 Что такое карго", "📄 Белая доставка", "📦 Карго доставка", "✅ Проверенные карго"]
    },
    "tools": {
        "title": "🛠️ Полезные инструменты",
        "premium": True,
        "lessons": ["🌐 Переводчики", "💬 Шаблоны сообщений китайцам"]
    },
    "support": {
        "title": "🎧 Поддержка",
        "premium": True,
        "lessons": ["🤖 AI-помощник", "👨‍💼 Помощь специалиста"]
    },
    "subscription": {
        "title": "💎 Подписка",
        "premium": False,
        "lessons": ["💎 Premium-подписка"]
    },
    "rules": {
        "title": "🛡️ Правила и безопасность",
        "premium": False,
        "lessons": ["📜 Правила бота", "⚠️ Защита от мошенников"]
    }
}


def bottom_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🏠 Меню", "👤 Профиль")
    markup.add("💎 Подписка", "❓ Помощь")
    return markup


def main_menu_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    for key, section in sections.items():
        keyboard.add(types.InlineKeyboardButton(section["title"], callback_data=f"section:{key}"))
    return keyboard


def back_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="main"))
    return keyboard


def section_keyboard(section_key):
    keyboard = types.InlineKeyboardMarkup()
    section = sections[section_key]

    for i, lesson in enumerate(section["lessons"]):
        keyboard.add(types.InlineKeyboardButton(lesson, callback_data=f"lesson:{section_key}:{i}"))

    keyboard.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="main"))
    return keyboard


def show_screen(chat_id, text, keyboard=None):
    if chat_id in last_message:
        try:
            bot.edit_message_text(
                text,
                chat_id,
                last_message[chat_id],
                reply_markup=keyboard
            )
            return
        except Exception:
            pass

    msg = bot.send_message(chat_id, text, reply_markup=keyboard)
    last_message[chat_id] = msg.message_id


@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "🇨🇳 Добро пожаловать в бот «Бизнес с Китаем»!",
        reply_markup=bottom_menu()
    )

    show_screen(
        message.chat.id,
        "🏠 Главное меню\n\nВыберите нужный раздел:",
        main_menu_keyboard()
    )


@bot.message_handler(func=lambda message: message.text == "🏠 Меню")
def menu_button(message):
    show_screen(
        message.chat.id,
        "🏠 Главное меню\n\nВыберите нужный раздел:",
        main_menu_keyboard()
    )


@bot.message_handler(func=lambda message: message.text == "👤 Профиль")
def profile_button(message):
    show_screen(
        message.chat.id,
        "👤 Ваш профиль\n\n💎 Статус: Free\n📚 Доступ: бесплатные разделы\n\nPremium-доступ скоро будет доступен.",
        back_keyboard()
    )


@bot.message_handler(func=lambda message: message.text == "💎 Подписка")
def subscription_button(message):
    show_screen(
        message.chat.id,
        "💎 Premium-подписка\n\nPremium откроет закрытые разделы обучения:\n\n• работа с поставщиками\n• оформление заказа\n• карго и логистика\n• полезные инструменты\n• поддержка\n\nОплату подключим позже.",
        back_keyboard()
    )


@bot.message_handler(func=lambda message: message.text == "❓ Помощь")
def help_button(message):
    show_screen(
        message.chat.id,
        "❓ Помощь\n\nЕсли возник вопрос — напишите администратору.\n\nПозже здесь будет кнопка связи со специалистом.",
        back_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == "main")
def callback_main(call):
    last_message[call.message.chat.id] = call.message.message_id

    bot.edit_message_text(
        "🏠 Главное меню\n\nВыберите нужный раздел:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("section:"))
def open_section(call):
    section_key = call.data.split(":")[1]
    section = sections[section_key]

    last_message[call.message.chat.id] = call.message.message_id

    if section["premium"]:
        text = (
            f"{section['title']}\n\n"
            "🔒 Этот раздел доступен только по Premium-подписке.\n\n"
            "Чтобы открыть доступ, перейдите в раздел 💎 Подписка."
        )

        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_keyboard()
        )
        return

    bot.edit_message_text(
        f"{section['title']}\n\nВыберите урок:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=section_keyboard(section_key)
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("lesson:"))
def open_lesson(call):
    _, section_key, lesson_index = call.data.split(":")
    lesson_index = int(lesson_index)

    section = sections[section_key]
    lesson = section["lessons"][lesson_index]

    text = (
        f"{lesson}\n\n"
        "Здесь будет текст урока.\n\n"
        "Позже сюда можно добавить видео, фото, ссылки и подробную инструкцию."
    )

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("⬅️ Назад к разделу", callback_data=f"section:{section_key}"))
    keyboard.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main"))

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboard
    )


bot.infinity_polling()
