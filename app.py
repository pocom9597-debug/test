import asyncio
import aiohttp
import os
import requests
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import threading
from itertools import count

# ==============================================================================
# ⚠️ 1. الإعدادات الأساسية (يجب عليك ملؤها) ⚠️
# ==============================================================================
TELEGRAM_BOT_TOKEN = '7841209852:AAH047KQNwmEUA2GPRyBi9OP8kP0fJgatOM'  # رمز روبوت التليجرام
ALLOWED_USER_ID = 6752807419
# مثال: 123456789 - يرجى تعيينه لأسباب أمنية

# مصادر التحميل التلقائي لكميات كبيرة من البروكسي
DOWNLOAD_URLS = [
    'https://raw.githubusercontent.com/iplocate/free-proxy-list/refs/heads/main/all-proxies.txt',
    'https://raw.githubusercontent.com/ErcinDedeoglu/proxies/refs/heads/main/proxies/http.txt',
    'https://raw.githubusercontent.com/ErcinDedeoglu/proxies/refs/heads/main/proxies/socks5.txt',
    'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt',
    'https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all',
    'https://api.openproxyspace.com/list.txt',
]

# إعدادات الفحص
INPUT_FILE_NAME = 'combined_raw_proxies.txt'
OUTPUT_FILE_HTTP = 'working_http_proxies.txt'
OUTPUT_FILE_SOCKS5 = 'working_socks5_proxies.txt'
OUTPUT_FILE_SOCKS4 = 'working_socks4_proxies.txt'
TEST_URL = 'http://httpbin.org/ip'
TIMEOUT = 7
CONCURRENT_LIMIT = 500 
REPORT_INTERVAL = 500 # تحديث البوت كل 500 بروكسي

# قوائم لتخزين البروكسيات العاملة (Sets لإزالة التكرار)
working_http_proxies = set()
working_socks5_proxies = set()
working_socks4_proxies = set()
lock = threading.Lock() # للمزامنة بين المهام
CLEAN_REGEX = re.compile(r'^\w+://|^\s*://')

# متغيرات حالة الفحص العالمية
checked_count = count(1)

# ==============================================================================
# 2. دوال التحميل والتنظيف التلقائي
# ==============================================================================

async def download_and_combine_proxies(urls, output_file, chat_id, context):
    """تحميل، تنظيف، ودمج البروكسيات."""
    
    await context.bot.send_message(chat_id=chat_id, text="بدء التحميل والتنظيف التلقائي لقوائم البروكسي...")
    all_proxies = set()
    
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url, timeout=10) as response:
                    response.raise_for_status() 
                    proxies_text = await response.text()
                    
                    for p in proxies_text.splitlines():
                        p_cleaned = CLEAN_REGEX.sub('', p.strip())
                        if ':' in p_cleaned:
                            all_proxies.add(p_cleaned)
            except Exception:
                continue
            
    proxies_to_check = sorted(list(all_proxies))
    if proxies_to_check:
        with open(output_file, 'w') as f:
            for proxy in proxies_to_check:
                f.write(f"{proxy}\n")
        
        await context.bot.send_message(chat_id=chat_id, text=f"✅ تم تجميع {len(proxies_to_check)} بروكسي فريد ونظيف. بدء الفحص الآن...")
        return len(proxies_to_check)
    else:
        await context.bot.send_message(chat_id=chat_id, text="❌ فشل: لم يتم تجميع أي بروكسيات، إيقاف الفحص.")
        return 0

# ==============================================================================
# 3. دالة فحص البروكسي (مع تحديث العداد والرسالة)
# ==============================================================================

async def check_proxy(session, proxy, semaphore, total_proxies, chat_id, context, status_message):
    """يفحص البروكسي ويحدد نوع البروتوكول العامل: HTTP، ثم SOCKS5، ثم SOCKS4."""
    global checked_count
    
    protocols_to_check = [
        ('HTTP', f'http://{proxy}', working_http_proxies),
        ('SOCKS5', f'socks5://{proxy}', working_socks5_proxies),
        ('SOCKS4', f'socks4://{proxy}', working_socks4_proxies)
    ]
    
    async with semaphore:
        for p_type, proxy_url, result_set in protocols_to_check:
            try:
                # استخدام aiohttp لفحص البروكسي
                async with session.get(
                    TEST_URL, 
                    proxy=proxy_url, 
                    timeout=TIMEOUT,
                    headers={'User-Agent': 'Mozilla/5.0'}
                ) as response:
                    
                    if response.status == 200:
                        with lock:
                            result_set.add(proxy)
                        break # توقف عند النجاح
            except Exception:
                continue
    
    # تحديث العداد بعد الانتهاء من فحص البروكسي بجميع البروتوكولات
    current_count = next(checked_count)
    
    # تحديث الرسالة كل REPORT_INTERVAL من البروكسيات التي تم فحصها
    if current_count % REPORT_INTERVAL == 0 or current_count == total_proxies:
        try:
            # يجب استخدام asyncio.sleep(0) لتفادي حجب الدورة في حالة وجود العديد من التحديثات
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message.message_id,
                text=f"🔄 **جاري الفحص...**\nتم فحص: **{current_count}** من أصل **{total_proxies}**\nالمتبقي: {total_proxies - current_count}\n\n**البروكسيات العاملة:**\nHTTP: {len(working_http_proxies)} | SOCKS5: {len(working_socks5_proxies)} | SOCKS4: {len(working_socks4_proxies)}",
                parse_mode='Markdown'
            )
        except Exception as e:
            # تجاهل الأخطاء البسيطة في التعديل (مثل محاولة التعديل كثيراً)
            pass

# ==============================================================================
# 4. دوال الحفظ والإرسال عبر تليجرام
# ==============================================================================

def save_results(file_name, proxies_set):
    """يحفظ قائمة البروكسيات العاملة في ملف."""
    if proxies_set:
        with open(file_name, 'w') as f:
            for proxy in sorted(list(proxies_set)):
                f.write(f"{proxy}\n")
        return True
    else:
        with open(file_name, 'w') as f:
            f.write("No working proxies found.")
        return False

async def send_file_to_telegram_async(file_path, chat_id, context):
    """يرسل ملفاً إلى تليجرام باستخدام context.bot.send_document."""
    try:
        with open(file_path, 'rb') as document:
            # إضافة تسمية توضيحية لتمييز نوع الملف
            caption = f"✅ بروكسيات عاملة - البروتوكول: {os.path.basename(file_path).split('_')[1].upper()}"
            await context.bot.send_document(chat_id=chat_id, document=document, caption=caption)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ فشل إرسال الملف {os.path.basename(file_path)}: {e}")

# ==============================================================================
# 5. دالة التشغيل الرئيسية (مهمة الخلفية)
# ==============================================================================

async def run_check_task(chat_id, context):
    """الدالة التي تقوم بتنفيذ الفحص الفعلي."""
    global checked_count

    try:
        # 1. تهيئة العداد والقوائم
        checked_count = count(1)
        working_http_proxies.clear()
        working_socks5_proxies.clear()
        working_socks4_proxies.clear()
        
        # 2. التحميل والتنظيف التلقائي
        total_proxies = await download_and_combine_proxies(DOWNLOAD_URLS, INPUT_FILE_NAME, chat_id, context)
        if total_proxies == 0:
            return

        # 3. قراءة البروكسيات
        with open(INPUT_FILE_NAME, 'r') as f:
            proxies = [line.strip() for line in f if line.strip()]

        # إرسال رسالة الحالة الأولى لتعديلها لاحقاً
        status_message = await context.bot.send_message(
            chat_id=chat_id, 
            text=f"🔄 **جاري الفحص...**\nتم فحص: **0** من أصل **{total_proxies}**", 
            parse_mode='Markdown'
        )

        # 4. بدء الفحص المتزامن (Async)
        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        tasks = []
        
        async with aiohttp.ClientSession() as session:
            for proxy in proxies:
                task = check_proxy(session, proxy, semaphore, total_proxies, chat_id, context, status_message)
                tasks.append(task)
            
            await asyncio.gather(*tasks)

        # 5. حفظ النتائج (يتم فصلها هنا)
        saved_files = []
        if save_results(OUTPUT_FILE_HTTP, working_http_proxies): saved_files.append(OUTPUT_FILE_HTTP)
        if save_results(OUTPUT_FILE_SOCKS5, working_socks5_proxies): saved_files.append(OUTPUT_FILE_SOCKS5)
        if save_results(OUTPUT_FILE_SOCKS4, working_socks4_proxies): saved_files.append(OUTPUT_FILE_SOCKS4)
        
        total_working = len(working_http_proxies) + len(working_socks5_proxies) + len(working_socks4_proxies)
        
        # تحديث الرسالة النهائية
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message.message_id,
            text=f"✅ **انتهى الفحص بنجاح!**\n\nإجمالي البروكسيات التي تم فحصها: **{total_proxies}**\nإجمالي العاملة: **{total_working}**\n\n**تم إرسال الملفات المفصولة (HTTP, SOCKS5, SOCKS4) إلى الدردشة.**",
            parse_mode='Markdown'
        )

        # 6. الإرسال عبر تليجرام (يتم الإرسال حسب الملفات المنفصلة)
        for file_path in saved_files:
            await send_file_to_telegram_async(file_path, chat_id, context)

        # 7. تنظيف الملفات المؤقتة والنهائية
        os.remove(INPUT_FILE_NAME)
        for file_path in saved_files:
            try:
                os.remove(file_path)
            except Exception:
                pass

    except Exception as e:
        # إرسال رسالة خطأ إلى المستخدم
        await context.bot.send_message(chat_id=chat_id, text=f"❌ حدث خطأ غير متوقع أثناء الفحص. يرجى مراجعة سجلات السيرفر.\nالخطأ: {e}")

# ==============================================================================
# 6. دوال البوت وإدارة الأوامر
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """استقبال الأمر /start."""
    await update.message.reply_text('مرحباً! أنا فاحص البروكسيات الآلي. استخدم الأمر /run لبدء عملية الفحص.')

async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """استقبال الأمر /run وبدء مهمة الفحص في الخلفية."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # التحقق من أن المستخدم مسموح له بالتشغيل (أمان)
    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("❌ غير مصرح لك بتشغيل هذا الأمر.")
        return

    await update.message.reply_text("⏳ تم استلام الأمر! بدء مهمة فحص البروكسيات في الخلفية. ستتلقى الملفات عند الانتهاء.")
    
    # بدء المهمة في الخلفية بشكل غير حاجِب (Non-Blocking)
    asyncio.create_task(run_check_task(chat_id, context))

def main():
    """تشغيل البوت."""

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # إدارة الأوامر
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("run", run_command))

    print("🤖 بدء تشغيل البوت...")
    # بدء البوت واستقبال الرسائل بشكل غير حاجِب (Async)
    application.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
