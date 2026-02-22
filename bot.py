import telebot

TOKEN = "PASTE_YOUR_TOKEN_HERE"

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Привет! Я твой первый бот 🤖")

@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.send_message(message.chat.id, message.text)

print("Bot started...")
bot.infinity_polling()
