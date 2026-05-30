import os
import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
bot = telebot.TeleBot(TOKEN)


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
        "title": "🔒 🏭 Работа с фабриками и поставщиками",
        "premium": True,
        "lessons": ["🏭 Как проверить фабрику или поставщика", "💬 Как написать китайцу", "💰 Как торговаться", "📦 Что такое MOQ"]
    },
    "orders": {
        "title": "🔒 📝 Как оформить заказ",
        "premium": True,
        "lessons": ["🛒 Оформление заказа", "💳 Оплата", "📍 Отслеживание товара", "📦 Поступление товара на склад"]
    },
    "returns": {
        "title": "🔒 ↩️ Возврат и отмена заказа",
        "premium": True,
        "lessons": ["❌ Отмена заказа", "📦 Возврат товара", "⚖️ Открытие спора"]
    },
    "cargo": {
        "title": "🔒 🚛 Карго и логистика",
        "premium": True,
        "lessons": ["🚛 Что такое карго", "📄 Белая доставка", "📦 Карго доставка", "✅ Проверенные карго"]
    },
    "tools": {
        "title": "🔒 🛠️ Полезные инструменты",
        "premium": True,
        "lessons": ["🌐 Переводчики", "💬 Шаблоны сообщений китайцам"]
    },
    "support": {
        "title": "🔒 🎧 Поддержка",
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


def main_keyboard():
    keyboard = types.InlineKeyboardMarkup()

    keyboard.row(
        types.InlineKeyboardButton("📚 Обучение", callback_data="learning"),
        types.InlineKeyboardButton("👤 Профиль", callback_data="profile")
    )

    keyboard.row(
        types.InlineKeyboardButton("💎 Подписка", callback_data="subscription"),
        types.InlineKeyboardButton("❓ Помощь", callback_data="help")
    )

    return keyboard


def learning_keyboard():
    keyboard = types.InlineKeyboardMarkup()

    for key, section in sections.items():
        if key not in ["subscription"]:
            keyboard.add(
                types.InlineKeyboardButton(
                    section["title"],
                    callback_data=f"section:{key}"
                )
            )

    keyboard.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="main"))
    return keyboard


def section_keyboard(section_key):
    keyboard = types.InlineKeyboardMarkup()
    section = sections[section_key]

    for index, lesson in enumerate(section["lessons"]):
        keyboard.add(
            types.InlineKeyboardButton(
                lesson,
                callback_data=f"lesson:{section_key}:{index}"
            )
        )

    keyboard.add(types.InlineKeyboardButton("⬅️ Назад к обучению", callback_data="learning"))
    keyboard.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main"))
    return keyboard


def premium_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("💎 Открыть Premium", callback_data="subscription"))
    keyboard.add(types.InlineKeyboardButton("⬅️ Назад к обучению", callback_data="learning"))
    return keyboard


def lesson_keyboard(section_key):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("⬅️ Назад к разделу", callback_data=f"section:{section_key}"))
    keyboard.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main"))
    return keyboard


def send_or_edit(call_or_message, text, keyboard):
    try:
        if hasattr(call_or_message, "message"):
            bot.edit_message_text(
                text,
                call_or_message.message.chat.id,
                call_or_message.message.message_id,
                reply_markup=keyboard
            )
        else:
            bot.send_message(
                call_or_message.chat.id,
                text,
                reply_markup=keyboard
            )
    except Exception:
        if hasattr(call_or_message, "message"):
            bot.send_message(
                call_or_message.message.chat.id,
                text,
                reply_markup=keyboard
            )


@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "🇨🇳 Добро пожаловать в бот «Бизнес с Китаем»!\n\n"
        "Здесь вы сможете изучить закупки товаров из Китая.\n\n"
        "Выберите нужный раздел:",
        reply_markup=types.ReplyKeyboardRemove()
    )

    bot.send_message(
        message.chat.id,
        "🏠 Главное меню\n\nВыберите действие:",
        reply_markup=main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == "main")
def open_main(call):
    bot.answer_callback_query(call.id)
    send_or_edit(
        call,
        "🏠 Главное меню\n\nВыберите действие:",
        main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == "learning")
def open_learning(call):
    bot.answer_callback_query(call.id)
    send_or_edit(
        call,
        "📚 Обучение\n\nВыберите раздел:",
        learning_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == "profile")
def open_profile(call):
    bot.answer_callback_query(call.id)
    send_or_edit(
        call,
        "👤 Ваш профиль\n\n"
        "💎 Статус: Free\n"
        "📚 Доступ: бесплатные разделы\n\n"
        "Premium-доступ скоро будет доступен.",
        back_to_main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == "subscription")
def open_subscription(call):
    bot.answer_callback_query(call.id)
    send_or_edit(
        call,
        "💎 Premium-подписка\n\n"
        "Premium откроет закрытые разделы обучения:\n\n"
        "• работа с фабриками и поставщиками\n"
        "• оформление заказа\n"
        "• возврат и отмена заказа\n"
        "• карго и логистика\n"
        "• полезные инструменты\n"
        "• поддержка\n\n"
        "Оплату подключим позже.",
        back_to_main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == "help")
def open_help(call):
    bot.answer_callback_query(call.id)
    send_or_edit(
        call,
        "❓ Помощь\n\n"
        "Если возник вопрос — напишите администратору.\n\n"
        "Позже здесь будет кнопка связи со специалистом.",
        back_to_main_keyboard()
    )


def back_to_main_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main"))
    return keyboard


@bot.callback_query_handler(func=lambda call: call.data.startswith("section:"))
def open_section(call):
    bot.answer_callback_query(call.id)

    section_key = call.data.split(":")[1]
    section = sections[section_key]

    if section["premium"]:
        send_or_edit(
            call,
            f"{section['title']}\n\n"
            "Этот раздел доступен только по Premium-подписке.\n\n"
            "В Premium входят закрытые уроки, практические инструкции и полезные материалы.",
            premium_keyboard()
        )
        return

    send_or_edit(
        call,
        f"{section['title']}\n\nВыберите урок:",
        section_keyboard(section_key)
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("lesson:"))
def open_lesson(call):
    bot.answer_callback_query(call.id)

    _, section_key, lesson_index = call.data.split(":")
    lesson_index = int(lesson_index)

    lesson = sections[section_key]["lessons"][lesson_index]

    send_or_edit(
        call,
        f"{lesson}\n\n"
        "Здесь будет текст урока.\n\n"
        "Позже сюда можно добавить видео, фото, ссылки и подробную инструкцию.",
        lesson_keyboard(section_key)
    )


bot.infinity_polling()
