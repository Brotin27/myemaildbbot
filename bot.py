import os
import logging
import json
import pyotp
import base64
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from pymongo import MongoClient

# === Flask Keep-Alive ===
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ Bot is alive!"
def run():
    app.run(host='0.0.0.0', port=8080)
def keep_alive():
    Thread(target=run).start()

# === Configuration ===
BOT_TOKEN = os.getenv("7714706865:AAGZW4Fq1tidbqsR0sruJBJ3_hSpaB0a0xk")
MONGO_URI = os.getenv("mongodb+srv://krystalesgande:krystalesgande@cluster0.nxry6ig.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
OWNER_ID = os.getenv("2085275594", "1623892821")
AUTHORIZED_USERS = set(OWNER_ID.split()) | {"123456789"}

client = MongoClient(MONGO_URI)
db = client['telegram_bot']
collection = db['shared_gmails']

# === Logging ===
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# === State Tracking ===
user_states = {}
temp_data = {}
current_index = {}

# === Utilities ===
def is_authorized(user_id):
    return str(user_id) in AUTHORIZED_USERS

def sanitize_secret(secret: str):
    return secret.replace(" ", "").upper()

def is_valid_base32(secret: str):
    try:
        base64.b32decode(secret, casefold=True)
        return True
    except Exception:
        return False

async def get_data_from_mongo(user_id: str):
    return list(collection.find({"user_ids": user_id}))

async def save_data_to_mongo(account: dict):
    user_ids = list(AUTHORIZED_USERS) | set(OWNER_ID.split())
    user_ids = list(set(user_ids))
    collection.insert_one({
        "gmail": account["gmail"],
        "password": account["password"],
        "secret": account["secret"],
        "user_ids": user_ids
    })

# === Access Management ===
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) not in OWNER_ID.split():
        await update.message.reply_text("❌ You are not the owner.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /adduser <user_id>")
        return
    AUTHORIZED_USERS.add(context.args[0])
    await update.message.reply_text(f"✅ User {context.args[0]} authorized.")

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) not in OWNER_ID.split():
        await update.message.reply_text("❌ You are not the owner.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeuser <user_id>")
        return
    AUTHORIZED_USERS.discard(context.args[0])
    await update.message.reply_text(f"🗑️ User {context.args[0]} removed.")

async def fletch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = str(update.effective_user.id)
    if not is_authorized(sender_id):
        await update.message.reply_text("❌ Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /fletch <target_user_id>")
        return
    target_id = context.args[0]
    accounts = await get_data_from_mongo(sender_id)
    if not accounts:
        await update.message.reply_text("⚠️ No accounts to share.")
        return
    for acc in accounts:
        if target_id not in acc["user_ids"]:
            acc["user_ids"].append(target_id)
            collection.update_one({"_id": acc["_id"]}, {"$set": {"user_ids": acc["user_ids"]}})
    await update.message.reply_text(f"✅ Accounts shared with {target_id}.")

# === Bot Commands ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Get Gmail"), KeyboardButton("Next Gmail")]],
        resize_keyboard=True,
    )
    await update.message.reply_text("👋 Welcome!\nUse /add to add Gmail accounts.", reply_markup=keyboard)

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) not in OWNER_ID.split():
        await update.message.reply_text("❌ Only the owner can add Gmail accounts.")
        return
    user_id = str(update.effective_user.id)
    user_states[user_id] = "awaiting_gmail"
    temp_data[user_id] = {}
    await update.message.reply_text("📧 Send the Gmail address:")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized.")
        return
    text = update.message.text.strip()
    if text == "Get Gmail":
        await send_gmail(update, context, reset=True)
        return
    elif text == "Next Gmail":
        await send_gmail(update, context)
        return
    if user_id not in user_states:
        return
    state = user_states[user_id]
    if state == "awaiting_gmail":
        temp_data[user_id]["gmail"] = text
        user_states[user_id] = "awaiting_password"
        await update.message.reply_text("🔑 Now send the password:")
    elif state == "awaiting_password":
        temp_data[user_id]["password"] = text
        user_states[user_id] = "awaiting_secret"
        await update.message.reply_text("🗝️ Now send the 2FA secret key:")
    elif state == "awaiting_secret":
        clean_secret = sanitize_secret(text)
        if not is_valid_base32(clean_secret):
            await update.message.reply_text("❌ Invalid 2FA secret key.")
            return
        temp_data[user_id]["secret"] = clean_secret
        await save_data_to_mongo(temp_data[user_id])
        del user_states[user_id]
        del temp_data[user_id]
        await update.message.reply_text("✅ Account saved.")

async def send_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE, reset=False):
    user_id = str(update.effective_user.id)
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized.")
        return
    accounts = await get_data_from_mongo(user_id)
    if not accounts:
        await update.message.reply_text("⚠️ No accounts saved.")
        return
    if reset or user_id not in current_index:
        current_index[user_id] = 0
    else:
        current_index[user_id] += 1
    if current_index[user_id] >= len(accounts):
        await update.message.reply_text("✅ All accounts shown.")
        return
    account = accounts[current_index[user_id]]
    info = (
        f"📧 Gmail: <code>{account['gmail']}</code>\n"
        f"🔑 Password: <code>{account['password']}</code>"
    )
    buttons = [[InlineKeyboardButton("🔐 Generate 2FA", callback_data=f"totp_{current_index[user_id]}")]]
    await update.message.reply_text(info, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    if not is_authorized(user_id):
        await query.edit_message_text("❌ Not authorized.")
        return
    accounts = await get_data_from_mongo(user_id)
    if not accounts:
        return
    if query.data.startswith("totp_"):
        index = int(query.data.split("_")[1])
        if index >= len(accounts):
            await query.edit_message_text("❌ Invalid index.")
            return
        account = accounts[index]
        try:
            totp = pyotp.TOTP(sanitize_secret(account["secret"])).now()
            await query.edit_message_text(f"🔐 2FA Code for {account['gmail']}: `{totp}`", parse_mode="Markdown")
        except Exception:
            await query.edit_message_text("❌ Failed to generate OTP.")

# === Run Bot ===
def main():
    keep_alive()
    app_bot = Application.builder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("add", add))
    app_bot.add_handler(CommandHandler("adduser", adduser))
    app_bot.add_handler(CommandHandler("removeuser", removeuser))
    app_bot.add_handler(CommandHandler("fletch", fletch))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_handler(CallbackQueryHandler(button_handler))
    print("✅ Bot is running...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
