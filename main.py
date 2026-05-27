import os
import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
bot = telebot.TeleBot(TOKEN)


sections = {
    "intro": {
        "title": "📚 Введение",
        "premium": False,
        "lessons": [
            "🤖 Ознакомление с ботом",
            "🎓 Ознакомление с обучением"
        ]
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


def main_inline_menu():
    keyboard = types.InlineKeyboardMarkup()

    for key, section in sections.items():
        keyboard.add(
            types.InlineKeyboardButton(
                section["title"],
                callback_data=f"section:{key}"
            )
        )

    return keyboard


def back_to_menu_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu"))
    return keyboard


def show_main_menu(chat_id):
    bot.send_message(
        chat_id,
        "🏠 Главное меню\n\nВыберите нужный раздел:",
        reply_markup=main_inline_menu()
    )


@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "🇨🇳 Добро пожаловать в бот «Бизнес с Китаем»!\n\n"
        "Этот бот поможет вам изучить закупки товаров из Китая.",
        reply_markup=bottom_menu()
    )

    show_main_menu(message.chat.id)


@bot.message_handler(func=lambda message: message.text == "🏠 Меню")
def menu_button(message):
    show_main_menu(message.chat.id)


@bot.message_handler(func=lambda message: message.text == "👤 Профиль")
def profile_button(message):
    bot.send_message(
        message.chat.id,
        "👤 Ваш профиль\n\n"
        "💎 Статус: Free\n"
        "📚 Доступ: бесплатные разделы\n\n"
        "Premium-доступ скоро будет доступен.",
        reply_markup=bottom_menu()
    )


@bot.message_handler(func=lambda message: message.text == "💎 Подписка")
def subscription_button(message):
    bot.send_message(
        message.chat.id,
        "💎 Premium-подписка\n\n"
        "Premium откроет закрытые разделы обучения:\n"
        "• работа с поставщиками\n"
        "• оформление заказа\n"
        "• карго и логистика\n"
        "• полезные инструменты\n"
        "• поддержка\n\n"
        "Оплату подключим позже.",
        reply_markup=bottom_menu()
    )


@bot.message_handler(func=lambda message: message.text == "❓ Помощь")
def help_button(message):
    bot.send_message(
        message.chat.id,
        "❓ Помощь\n\n"
        "Если возник вопрос — напишите администратору.\n\n"
        "Позже здесь будет кнопка связи со специалистом.",
        reply_markup=bottom_menu()
    )


@bot.callback_query_handler(func=lambda call: call.data == "main_menu")
def callback_main_menu(call):
    bot.edit_message_text(
        "🏠 Главное меню\n\nВыберите нужный раздел:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_inline_menu()
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("section:"))
def open_section(call):
    section_key = call.data.split(":")[1]
    section = sections[section_key]

    if section["premium"]:
        text = (
            f"{section['title']}\n\n"
            "🔒 Этот раздел доступен только по Premium-подписке.\n\n"
            "Чтобы открыть доступ, перейдите в раздел 💎 Подписка."
        )
    else:
        text = f"{section['title']}\n\n"
        for lesson in section["lessons"]:
            text += f"• {lesson}\n"

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=back_to_menu_keyboard()
    )


bot.infinity_polling()
