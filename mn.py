import sqlite3
import aiohttp
import asyncio
import json
import logging
import html
import os
from types import SimpleNamespace

# --- 1. مكتبات جديدة (بدون تشفير) ---

# --- 2. إعدادات البوت والأدمن (من ملف .env) --- # تحميل المتغيرات من ملف .env

TOKEN = "7841209852:AAGu_75o1mszdHJuDmK9klgWcUFnqcLlscQ"
ADMIN_ID_STR = "8419466882"
if not TOKEN or not ADMIN_ID_STR:
    raise ValueError("خطأ: يجب تعيين TOKEN و ADMIN_ID في ملف .env")

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    raise ValueError("خطأ: ADMIN_ID يجب أن يكون رقماً صحيحاً.")

print("تم تحميل الإعدادات (بدون تشفير).")


# --- imports لـ Aiogram ---
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage 

# --- إعداد Aiogram ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) 
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logging.basicConfig(level=logging.INFO)
print("Bot is initializing (Aiogram)...")

# ## متغير لتخزين المهام (Tasks) بدلاً من الحالة
user_script_tasks = {} # {user_id: asyncio.Task}

# --- 4. إعداد قاعدة البيانات ---
# (لم يتغير)
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

# --- 5. دوال مساعدة (قاعدة البيانات) ---
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
    
    # !! قراءة كلمات المرور كنص عادي !!
    config.owner_pass = row[5]
    config.flying_member_id = row[6]
    config.flying_member_pass = row[7]
    
    config.fixed_member_id = row[8]
    config.rounds = row[9]

    # التحقق من أن البيانات موجودة
    if not config.owner_pass or not config.flying_member_pass:
        logging.warning(f"بيانات المستخدم {user_id} غير كاملة.")
        # قد لا تكون مشكلة إذا كان المستخدم لم يكمل الإعداد
    
    config.token_owner = None
    config.token_fly = None
    config.response = None
    config.round = 1
    
    return config

def check_is_admin(user_id):
    return user_id == ADMIN_ID

# --- 6. الأزرار (Keyboards) ---
# (لم تتغير)
def get_main_keyboard(user_id):
    status = get_user_status(user_id)
    kb = [
        [KeyboardButton(text="ℹ️ حالتي")]
    ]
    
    if status["is_active"]:
        kb.append([KeyboardButton(text="⚙️ إعداد/تعديل البيانات")])
        if status["is_configured"]:
            is_running = user_id in user_script_tasks and not user_script_tasks[user_id].done()
            if not is_running:
                kb.append([KeyboardButton(text="🚀 تشغيل الإسكريبت")])
            else:
                kb.append([KeyboardButton(text="🛑 إيقاف الإسكريبت")])
            
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


# --- 7. حالات FSM ---
# (لم تتغير)
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

# --- 8. أوامر البوت الأساسية ومعالجات الأدمن ---
# (لم تتغير)
@dp.message(Command("start"))
async def send_welcome(message: Message):
    user = message.from_user
    register_user(user.id, user.username or user.first_name)
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

# --- 9. خطوات إعداد بيانات المستخدم (FSM) ---
# (!! تم تعديلها لحفظ النص العادي !!)

async def start_config_conversation(message: Message, state: FSMContext):
    await state.clear() 
    await message.answer("--- خطوة 1 من 6 ---\nأدخل رقم المالك (owner_id):")
    await state.set_state(ConfigStates.awaiting_owner_id)

@dp.message(ConfigStates.awaiting_owner_id)
async def process_owner_id_step(message: Message, state: FSMContext):
    await state.update_data(owner_id=message.text)
    await message.answer("--- خطوة 2 من 6 ---\nأدخل كلمة مرور المالك (owner_pass):\n(⚠️ تحذير: سيتم حفظها كنص عادي)")
    await state.set_state(ConfigStates.awaiting_owner_pass)

@dp.message(ConfigStates.awaiting_owner_pass)
async def process_owner_pass_step(message: Message, state: FSMContext):
    # !! حفظ النص العادي !!
    await state.update_data(owner_pass=message.text)
    await message.answer("--- خطوة 3 من 6 ---\nأدخل رقم العضو الطائر (flying_member_id):")
    await state.set_state(ConfigStates.awaiting_flying_id)

@dp.message(ConfigStates.awaiting_flying_id)
async def process_flying_id_step(message: Message, state: FSMContext):
    await state.update_data(flying_member_id=message.text)
    await message.answer("--- خطوة 4 من 6 ---\nأدخل كلمة مرور العضو الطائر (flying_member_pass):\n(⚠️ تحذير: سيتم حفظها كنص عادي)")
    await state.set_state(ConfigStates.awaiting_flying_pass)

@dp.message(ConfigStates.awaiting_flying_pass)
async def process_flying_pass_step(message: Message, state: FSMContext):
    # !! حفظ النص العادي !!
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
        # ستُحفظ كلمات المرور كنص عادي
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

# --- 10. دوال الإسكريبت (مُعدّلة بالكامل لـ Async) ---
# (هذه الدوال هي نفسها من الكود السابق، لأنها لا علاقة لها بالتشفير)

async def dynamic_countdown_async(bot: Bot, chat_id: int, total_seconds: int, message_prefix: str):
    start_text = f"⏳ {message_prefix} بدء العد: {total_seconds} ثانية..."
    try:
        sent_msg = await bot.send_message(chat_id, start_text)
        msg_id = sent_msg.message_id
    except Exception as e:
        logging.error(f"Failed to send initial countdown message to {chat_id}: {e}")
        raise asyncio.CancelledError(f"Failed to send message (Maybe Telegram Flood?)")
    last_text = ""
    last_edit_time = asyncio.get_event_loop().time()
    for i in range(total_seconds, 0, -1):
        await asyncio.sleep(1) 
        m, s = divmod(i, 60)
        timer_text = f"{m:02d}:{s:02d}"
        new_text = f"⏳ {message_prefix} {timer_text}"
        current_time = asyncio.get_event_loop().time()
        if (new_text != last_text) and (current_time - last_edit_time > 10 or i == 1): 
            try:
                await bot.edit_message_text(new_text, chat_id=chat_id, message_id=msg_id)
                last_text = new_text
                last_edit_time = current_time
            except Exception as e:
                if "message is not modified" not in str(e):
                    logging.warning(f"Failed to edit countdown message: {e}")
    try:
        await bot.edit_message_text(f"✅ {message_prefix} اكتمل الانتظار", chat_id=chat_id, message_id=msg_id)
    except Exception as e:
        logging.warning(f"Failed to edit final countdown message: {e}")

async def signin_async(session: aiohttp.ClientSession, user, pas):
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    payload = {
        'grant_type': "password", 'username': user, 'password': pas,
        'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a", 'client_id': "ana-vodafone-app"
    }
    headers = {'User-Agent': "okhttp/3.12.13"}
    try:
        async with session.post(url, data=payload, headers=headers) as response:
            if response.status == 200:
                return await response.json(), None
            else:
                return None, f"Status {response.status}: {await response.text()}"
    except Exception as e:
        return None, f"Exception: {e}"

async def tokens_async(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession) -> bool:
    owner_data, owner_error = await signin_async(session, config.owner_id, config.owner_pass)
    if owner_data:
        await bot.send_message(chat_id, "تم تسجيل دخول المالك ✅")
        config.token_owner = owner_data["access_token"]
    else:
        await bot.send_message(chat_id, f"فشل تسجيل الدخول للمالك ❌ - {owner_error}")
        return False
    fly_data, fly_error = await signin_async(session, config.flying_member_id, config.flying_member_pass)
    if fly_data:
        await bot.send_message(chat_id, "تم تسجيل دخول العضو الطائر ✅")
        config.token_fly = fly_data["access_token"]
    else:
        await bot.send_message(chat_id, f"فشل تسجيل الدخول للعضو الطائر ❌ - {fly_error}")
        return False
    return True

async def getflex_async(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession):
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
        async with session.get(url, headers=headers) as response:
            response_json = await response.json()
            await bot.send_message(chat_id, "عدد الفلكسات الحالي : " + str(response_json[3]["bucket"][3]["bucketBalance"][0]["remainingValue"]["amount"]))
    except Exception as e:
        await bot.send_message(chat_id, f"خطأ في جلب الفليكسات: {e}")
    await dynamic_countdown_async(bot, chat_id, 300, "انتظر : ")

async def flexMember_async(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession):
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
    try:
        async with session.patch(url, json=payload, headers=headers) as response:
            if response.status == 201:
                await bot.send_message(chat_id, "تم تغيير النسبه الي 10% ✅")
            elif response.status == 429:
                await bot.send_message(chat_id, "تم حظرك (تغيير النسبة) ❌")
            elif response.status == 555:
                await bot.send_message(chat_id, "النسبه 10% بالفعل ✅")
            else:
                await bot.send_message(chat_id, f"خطاء (تغيير النسبة) ❌ - {response.status}")
    except Exception as e:
         await bot.send_message(chat_id, f"خطاء استثناء (تغيير النسبة) ❌ - {e}")
    await dynamic_countdown_async(bot, chat_id, 300, "انتظر : ")

async def SendInvitation_async(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession):
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
    try:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status == 201:
                await bot.send_message(chat_id, "تم ارسال دعوه الي العضو الطائر ✅")
            elif response.status == 429:
                await bot.send_message(chat_id, "تم حظرك (ارسال دعوة) ❌")
            else:
                await bot.send_message(chat_id, f"خطاء (ارسال دعوة) ❌ - {response.status}")
    except Exception as e:
        await bot.send_message(chat_id, f"خطاء استثناء (ارسال دعوة) ❌ - {e}")
    await dynamic_countdown_async(bot, chat_id, 300, "انتظر : ")

async def QuotaRedistribution(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession):
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
    try:
        async with session.patch(url, json=payload, headers=headers) as response:
            if response.status == 201:
                await bot.send_message(chat_id, "✅ تم الهجوم (Quota)")
            elif response.status == 429:
                await bot.send_message(chat_id, "تم حظرك (Quota) ❌")
            else:
                await bot.send_message(chat_id, f"خطاء (Quota) ❌ - {response.status}")
    except Exception as e:
        await bot.send_message(chat_id, f"خطاء استثناء (Quota) ❌ - {e}")

async def AcceptInvitation(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession):
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
    try:
        async with session.patch(url, json=payload, headers=headers) as response:
            if response.status == 201:
                await bot.send_message(chat_id, "تم قبول الدعوه الي العائله ✅")
            elif response.status == 429:
                await bot.send_message(chat_id, "تم حظرك (قبول الدعوة) ❌")
            elif response.status == 500:
                await bot.send_message(chat_id, "هناك مشكله في العائله ❌")
            else:
                await bot.send_message(chat_id, f"خطاء (قبول الدعوة) ❌ -> {response.status}")
    except Exception as e:
        await bot.send_message(chat_id, f"خطاء استثناء (قبول الدعوة) ❌ - {e}")

async def run_parallel(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession):
    await asyncio.gather(
        QuotaRedistribution(bot, chat_id, config, session),
        AcceptInvitation(bot, chat_id, config, session)
    )

async def FamilyRemoveMember_async(bot: Bot, chat_id: int, config: SimpleNamespace, session: aiohttp.ClientSession):
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
    try:
        async with session.patch(url, json=payload, headers=headers) as response:
            if response.status == 201:
                await bot.send_message(chat_id, "✅ تم حذف العضو الطائر")
            elif response.status == 429:
                await bot.send_message(chat_id, "تم حظرك (حذف العضو) ❌")
            else:
                await bot.send_message(chat_id, f"خطاء (حذف العضو) ❌ -> {response.status}")
    except Exception as e:
        await bot.send_message(chat_id, f"خطاء استثناء (حذف العضو) ❌ - {e}")


# --- 11. دالة تشغيل الإسكريبت (في مهمة Async) ---
async def run_script_loop_async(bot: Bot, user_id: int, chat_id: int):
    try:
        config = get_user_config(user_id)
        if not config or not config.owner_pass or not config.flying_member_pass:
            await bot.send_message(chat_id, "❌ خطأ: بياناتك غير مكتملة. يرجى استخدام '⚙️ إعداد/تعديل البيانات' مرة أخرى.", reply_markup=get_main_keyboard(user_id))
            return

        await bot.send_message(chat_id, f"🚀 ... بدء تشغيل الإسكريبت لـ {config.rounds} دورة ... 🚀", reply_markup=get_main_keyboard(user_id))
        
        async with aiohttp.ClientSession() as session:
            for i in range(config.rounds):
                config.round = i + 1
                await bot.send_message(chat_id, f"--- 🔁 بدء الدورة رقم: {config.round} ---")
                
                if not await tokens_async(bot, chat_id, config, session):
                    await bot.send_message(chat_id, "فشل تسجيل الدخول. إيقاف الإسكريبت.")
                    break 
                
                await flexMember_async(bot, chat_id, config, session)
                await SendInvitation_async(bot, chat_id, config, session)
                await run_parallel(bot, chat_id, config, session)
                await FamilyRemoveMember_async(bot, chat_id, config, session)
                await getflex_async(bot, chat_id, config, session)
                
                await bot.send_message(chat_id, f"--- ✅ تم الانتهاء من الدورة رقم: {config.round} ---")
                await asyncio.sleep(2) 
            
        await bot.send_message(chat_id, "🎉 اكتمل تشغيل جميع الدورات بنجاح.")

    except asyncio.CancelledError:
        await bot.send_message(chat_id, "🛑 تم إيقاف الإسكريبت بناءً على طلبك.")
    except aiohttp.ClientError as e:
        await bot.send_message(chat_id, f"❌ توقف الإسكريبت بسبب خطأ في الاتصال: {e}")
        logging.exception(f"ClientError in script for user {user_id}")
    except Exception as e:
        await bot.send_message(chat_id, f"❌ توقف الإسكريبت بسبب خطأ فادح: {e}")
        logging.exception(f"Unhandled error in script for user {user_id}")
    finally:
        if user_id in user_script_tasks:
            del user_script_tasks[user_id]
        await bot.send_message(chat_id, "تم إيقاف تشغيل الإسكريبت. يمكنك تحديث الحالة.", reply_markup=get_main_keyboard(user_id))

# --- 12. معالج الرسائل الرئيسي (للأزرار) ---
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
    is_running = user_id in user_script_tasks and not user_script_tasks[user_id].done()

    if text == "ℹ️ حالتي":
        state_text = "مُفعّل ✅" if status["is_active"] else "غير مُفعّل ❌"
        config_text = "مسجل ⚙️" if status["is_configured"] else "غير مسجل ➖"
        running_text = "يعمل 🏃‍♂️" if is_running else "متوقف 💤"
        
        safe_first_name = html.escape(message.from_user.first_name)
        response = f"مرحباً {safe_first_name}\n"
        response += f"الـ ID الخاص بك: <code>{user_id}</code>\n"
        response += f"حالة الحساب: {state_text}\n"
        response += f"حالة البيانات: {config_text}\n"
        response += f"حالة الإسكريبت: {running_text}"
        await message.reply(response, reply_markup=get_main_keyboard(user_id))

    elif text == "⚙️ إعداد/تعديل البيانات":
        if not status["is_active"]:
            await message.reply("يجب أن يكون حسابك مُفعّل من قبل الأدمن أولاً.")
            return
        if is_running:
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
        
        if is_running:
            await message.reply("الإسكريبت يعمل بالفعل! 🏃‍♂️")
            return
            
        task = asyncio.create_task(run_script_loop_async(bot, user_id, chat_id))
        user_script_tasks[user_id] = task

    elif text == "🛑 إيقاف الإسكريبت":
        if not is_running:
            await message.reply("الإسكريبت متوقف بالفعل 💤")
            return
            
        user_script_tasks[user_id].cancel()
        await message.reply("تم إرسال إشارة الإيقاف... ✋\nسيحاول الإسكريبت التوقف عند أقرب نقطة.")

# --- 13. التشغيل ---
async def main():
    print("Setting up database...")
    setup_database()
    print("Database ready.")
    print("Bot is running (Polling)...")
    
    await dp.storage.close()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
