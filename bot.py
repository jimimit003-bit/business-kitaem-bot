import os
import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")

bot = telebot.TeleBot(TOKEN)


@bot.message_handler(commands=['start'])
def start(message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

    btn1 = types.KeyboardButton("📦 Найти товар")
    btn2 = types.KeyboardButton("🏭 Поставщики")
    btn3 = types.KeyboardButton("🚚 Карго")
    btn4 = types.KeyboardButton("💬 Консультация")

    keyboard.add(btn1)
    keyboard.add(btn2)
    keyboard.add(btn3)
    keyboard.add(btn4)

    bot.send_message(
        message.chat.id,
        "🇨🇳 Добро пожаловать в Бизнес с Китаем!\n\nВыберите раздел:",
        reply_markup=keyboard
    )


@bot.message_handler(func=lambda message: True)
def buttons(message):

    if message.text == "📦 Найти товар":
        bot.send_message(
            message.chat.id,
            "Отправьте название товара, который хотите найти в Китае."
        )

    elif message.text == "🏭 Поставщики":
        bot.send_message(
            message.chat.id,
            "Здесь будут поставщики и фабрики Китая."
        )

    elif message.text == "🚚 Карго":
        bot.send_message(
            message.chat.id,
            "Здесь будет информация по доставке и карго."
        )

    elif message.text == "💬 Консультация":
        bot.send_message(
            message.chat.id,
            "Напишите ваш вопрос, и мы поможем."
        )


print("Бот запущен")

bot.infinity_polling()
