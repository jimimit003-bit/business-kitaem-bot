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
            "Что такое бизнес с Китаем",
            "Как работает схема закупки",
            "Что понадобится для начала"
        ]
    },

    "platforms": {
        "title": "🔎 Обзор китайских площадок",
        "premium": False,
        "lessons": [
            "Taobao",
            "1688",
            "Poizon",
            "Pinduoduo",
            "Alibaba"
        ]
    },

    "registration": {
        "title": "👤 Регистрация и настройка аккаунтов",
        "premium": False,
        "lessons": [
            "Taobao",
            "1688",
            "Poizon",
            "Pinduoduo",
            "Alibaba",
            "WeChat",
            "Alipay"
        ]
    },

    "delivery": {
        "title": "🚚 Настройка доставки",
        "premium": False,
        "lessons": [
            "Как добавить адрес карго",
            "Как правильно заполнить адрес"
        ]
    },

    "search": {
        "title": "📦 Поиск товара",
        "premium": False,
        "lessons": [
            "Поиск по фото",
            "Поиск по ключевым словам",
            "Как искать выгодный товар"
        ]
    },

    "suppliers": {
        "title": "🏭 Работа с фабриками и поставщиками",
        "premium": True,
        "lessons": [
            "Как проверить фабрику или поставщика",
            "Как написать китайцу",
            "Как торговаться",
            "Что такое MOQ"
        ]
    },

    "orders": {
        "title": "📝 Как оформить заказ",
        "premium": True,
        "lessons": [
            "Оформление заказа",
            "Оплата",
            "Отслеживание товара",
            "Поступление товара на склад"
        ]
    },

    "returns": {
        "title": "↩️ Возврат и отмена заказа",
        "premium": True,
        "lessons": [
            "Отмена заказа",
            "Возврат товара",
            "Открытие спора"
        ]
    },

    "cargo": {
        "title": "🚛 Карго и логистика",
        "premium": True,
        "lessons": [
            "Что такое карго",
            "Белая доставка",
            "Карго доставка",
            "Проверенные карго"
        ]
    },

    "tools": {
        "title": "🛠️ Полезные инструменты",
        "premium": True,
        "lessons": [
            "Переводчики",
            "Шаблоны сообщений китайцам"
        ]
    },

    "support": {
        "title": "🎧 Поддержка",
        "premium": True,
        "lessons": [
            "AI-помощник",
            "Помощь специалиста"
        ]
    },

    "subscription": {
        "title": "💎 Подписка",
        "premium": False,
        "lessons": [
            "Бесплатный доступ",
            "Premium-подписка"
        ]
    },

    "rules": {
        "title": "🛡️ Правила и безопасность",
        "premium": False,
        "lessons": [
            "Правила бота",
            "Защита от мошенников",
            "Безопасность платежей"
        ]
    }

} 


def create_main_menu():
    keyboard = types.InlineKeyboardMarkup()

    for key, section in sections.items():
        button = types.InlineKeyboardButton(
            section["title"],
            callback_data=f"section_{key}"
        )

        keyboard.add(button)

    return keyboard


@bot.message_handler(commands=['start'])
def start(message):

    bot.send_message(
        message.chat.id,
        "🇨🇳 Добро пожаловать в бот «Бизнес с Китаем»!\n\n"
        "Выберите нужный раздел:",
        reply_markup=types.ReplyKeyboardRemove()
    )

    bot.send_message(
        message.chat.id,
        "📚 Разделы обучения:",
        reply_markup=create_main_menu()
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("section_"))
def open_section(call):
    section_key = call.data.replace("section_", "")
    section = sections[section_key]

    if section["premium"]:
        bot.answer_callback_query(call.id)

        bot.send_message(
            call.message.chat.id,
            "🔒 Этот раздел доступен только по Premium-подписке."
        )
        return

    text = f"{section['title']}\n\n"

    for lesson in section["lessons"]:
        text += f"• {lesson}\n"

    bot.answer_callback_query(call.id)

    bot.send_message(
        call.message.chat.id,
        text
    )


bot.infinity_polling()
