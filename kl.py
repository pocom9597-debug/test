from flask import Flask, request, jsonify
import sqlite3
import hashlib
import secrets
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import threading
import asyncio

# ---------------- إعدادات ----------------
TOKEN = "7841209852:AAGMRX8lJfb6ho_58kzz8IG02oNtQkKskHY"
AUTHORIZED_IDS = [8419466882, 6752807419]
DB_NAME = "activation.db"

# ---------------- قاعدة البيانات ----------------
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS keys (
    key TEXT PRIMARY KEY,
    device_id TEXT,
    usage_count INTEGER
)
""")
conn.commit()

# ---------------- دوال المساعدة ----------------
def generate_signature(device_id, key, secret="X12Hsd90#.."):
    return hashlib.sha256(f"{device_id}{key}{secret}".encode()).hexdigest()

def register_device(key, device_id):
    c.execute("UPDATE keys SET device_id=?, usage_count=usage_count+1 WHERE key=?", (device_id, key))
    conn.commit()

def generate_random_key():
    return "K-" + secrets.token_urlsafe(20)

# ---------------- Flask ----------------
app = Flask(__name__)

@app.route("/activate", methods=["POST"])
def activate():
    data = request.get_json()
    device_id = data.get("device_id")
    key = data.get("key")
    sign = data.get("sign")

    if not device_id or not key or not sign:
        return jsonify({"status":"error","message":"missing_parameters"}),400

    expected_sign = generate_signature(device_id, key)
    if sign != expected_sign:
        return jsonify({"status":"error","message":"invalid_sign"}),403

    c.execute("SELECT * FROM keys WHERE key=?", (key,))
    row = c.fetchone()
    if not row:
        return jsonify({"status":"error","message":"invalid_key"}),404

    saved_device = row[1]

    if saved_device and saved_device != device_id:
        return jsonify({"status":"error","message":"key_used_on_another_device"}),403

    if not saved_device:
        register_device(key, device_id)

    return jsonify({"status":"activated"}),200

# ---------------- Telegram Bot ----------------
MAIN_BUTTONS = [
    ["➕ إضافة مفتاح"],
    ["❌ حذف مفتاح"],
    ["📋 عرض كل المفاتيح"]
]
main_keyboard = ReplyKeyboardMarkup(MAIN_BUTTONS, resize_keyboard=True)

async def show_delete_menu(update):
    rows = c.execute("SELECT key FROM keys").fetchall()
    if not rows:
        await update.message.reply_text("⚠️ لا يوجد مفاتيح للحذف")
        return
    buttons = [[KeyboardButton(r[0])] for r in rows]
    keyboard = ReplyKeyboardMarkup(buttons + [["⬅️ رجوع"]], resize_keyboard=True)
    await update.message.reply_text("اختر المفتاح الذي تريد حذفه:", reply_markup=keyboard)

# ---------------- أوامر البوت ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_IDS:
        return
    await update.message.reply_text("لوحة التحكم جاهزة 🔐", reply_markup=main_keyboard)

async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_IDS:
        return
    text = update.message.text.strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text("عدنا للقائمة الرئيسية 🔙", reply_markup=main_keyboard)
        return

    if text == "➕ إضافة مفتاح":
        key = generate_random_key()
        c.execute("INSERT INTO keys (key, device_id, usage_count) VALUES (?, ?, ?)", (key, "", 0))
        conn.commit()
        await update.message.reply_text(f"✔ تم إنشاء مفتاح جديد:\n\n🔑 `{key}`", parse_mode="Markdown")
        return

    if text == "❌ حذف مفتاح":
        await show_delete_menu(update)
        return

    keys_list = [r[0] for r in c.execute("SELECT key FROM keys").fetchall()]
    if text in keys_list:
        c.execute("DELETE FROM keys WHERE key=?", (text,))
        conn.commit()
        await update.message.reply_text(f"🗑️ تم حذف المفتاح:\n`{text}`", reply_markup=main_keyboard, parse_mode="Markdown")
        return

    if text == "📋 عرض كل المفاتيح":
        rows = c.execute("SELECT * FROM keys").fetchall()
        if not rows:
            await update.message.reply_text("⚠️ لا يوجد مفاتيح")
            return
        msg = "\n\n".join([
            f"🔑 مفتاح: `{r[0]}`\n📱 جهاز: {r[1] if r[1] else 'غير مستخدم'}\n🔁 مرات الاستخدام: {r[2]}"
            for r in rows
        ])
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

# ---------------- تشغيل البوت وFlask ----------------
bot = ApplicationBuilder().token(TOKEN).build()
bot.add_handler(CommandHandler("start", start))
bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler))

def run_flask():
    app.run(host="0.0.0.0", port=5000, ssl_context=("cert.pem", "key.pem"))

threading.Thread(target=run_flask).start()
asyncio.run(bot.run_polling())
