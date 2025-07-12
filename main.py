import os
import logging
import random
import asyncio
import html
import time
from dotenv import load_dotenv
import re

from instagrapi import Client
from instagrapi.exceptions import LoginRequired, MediaNotFound, UserNotFound, BadPassword, TwoFactorRequired

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

# --- بارگذاری متغیرهای محیطی ---
load_dotenv()

# --- تنظیمات اولیه ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# ایجاد پوشه برای ذخیره session ها اگر وجود نداشته باشد
if not os.path.exists('sessions'):
    os.makedirs('sessions')

# --- اطلاعات حساس و ثابت‌ها ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("توکن ربات تلگرام در فایل .env یافت نشد!")
    exit()

# تعریف مراحل مکالمه برای خوانایی بهتر
# مکالمه ورود
LOGIN_GET_USERNAME, LOGIN_HANDLE_SESSION, LOGIN_GET_PASSWORD, LOGIN_HANDLE_2FA, LOGIN_GET_2FA_CODE = range(5)
# مکالمه تنظیمات لایک
LIKING_GET_DELAY, LIKING_GET_COUNT, LIKING_GET_SLEEP = range(5, 8)


# --- توابع کمکی ---
def get_session_path_by_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    """مسیر فایل session را بر اساس نام کاربری اینستاگرام برمی‌گرداند."""
    username = context.user_data.get('instagram_username')
    if username:
        return os.path.join('sessions', f"{username}.json")
    return None

def get_session_path_by_chat_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    """مسیر فایل session را بر اساس شناسه چت تلگرام کاربر برمی‌گرداند."""
    chat_id = context.user_data.get('chat_id')
    if chat_id:
        return os.path.join('sessions', f"{chat_id}.json")
    return None

async def _perform_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تابع اصلی برای انجام عملیات ورود و مدیریت خطاها."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    if query:
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)

    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ در حال تلاش برای ورود... لطفاً صبر کنید.")
    
    username = context.user_data['instagram_username']
    password = context.user_data['password']
    verification_code = context.user_data.get('verification_code', '')

    client = Client()
    try:
        client.login(username, password, verification_code=verification_code)
        
        context.user_data['client'] = client
        session_path = get_session_path_by_chat_id(context)
        client.dump_settings(session_path)
        
        await msg.edit_text(f"✅ ورود با موفقیت انجام شد!\n\n🎉 خوش آمدید {username}.\nاکنون می‌توانید لینک پست را ارسال کنید.")
        return ConversationHandler.END

    except BadPassword:
        await msg.edit_text("❌ رمز عبور اشتباه است. لطفاً دوباره با /login تلاش کنید.")
    except TwoFactorRequired:
        await msg.edit_text("📱 کد تایید دو مرحله‌ای اشتباه است یا حساب شما به آن نیاز دارد. لطفاً دوباره با /login تلاش کنید.")
    except Exception as e:
        logger.error(f"خطا در ورود برای کاربر {username}: {e}")
        await msg.edit_text(f"🚨 خطایی در هنگام ورود رخ داد: {e}\nلطفاً دوباره با /login تلاش کنید.")
    
    context.user_data.clear()
    return ConversationHandler.END

# --- توابع اصلی ربات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /start را مدیریت می‌کند."""
    user = update.effective_user
    context.user_data['chat_id'] = update.effective_chat.id
    welcome_message = (
        f"👋 سلام {user.mention_html()}\n\n"
        "به ربات لایکر اینستاگرام خوش آمدید.\n\n"
        "🔸 با دستور <code>/login</code> وارد حساب اینستاگرام خود شوید.\n"
        "🔸 با دستور <code>/logout</code> از حساب خود خارج شوید.\n"
        "🔸 با دستور <code>/status</code> وضعیت عملیات لایک را ببینید.\n"
        "🔸 با دستور <code>/cancel_liking</code> عملیات لایک را لغو کنید."
    )
    await update.message.reply_html(welcome_message)

# --- بخش مدیریت ورود ---
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند ورود. درخواست نام کاربری."""
    context.user_data['chat_id'] = update.effective_chat.id
    if 'client' in context.user_data:
        client = context.user_data['client']
        await update.message.reply_text(f"✅ شما از قبل با اکانت {client.username} وارد شده‌اید.\nبرای خروج از /logout استفاده کنید.")
        return ConversationHandler.END
        
    await update.message.reply_text("👤 لطفاً نام کاربری اینستاگرام خود را وارد کنید:\n\nبرای لغو از /cancel استفاده کنید.")
    return LOGIN_GET_USERNAME

async def login_get_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نام کاربری را دریافت و وجود session را بررسی می‌کند."""
    username = update.message.text.strip().lower()
    context.user_data['instagram_username'] = username
    session_path = get_session_path_by_username(context)

    if os.path.exists(session_path):
        keyboard = [[InlineKeyboardButton("✔️ بله", callback_data='session_yes'), InlineKeyboardButton("✖️ خیر", callback_data='session_no')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"📂 یک session برای کاربر '{username}' پیدا شد. آیا می‌خواهید با آن وارد شوید؟", reply_markup=reply_markup)
        return LOGIN_HANDLE_SESSION
    else:
        await update.message.reply_text("🔑 Session پیدا نشد. لطفاً رمز عبور خود را وارد کنید:")
        return LOGIN_GET_PASSWORD

async def login_handle_session_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پاسخ کاربر برای استفاده از session را مدیریت می‌کند."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'session_yes':
        session_path = get_session_path_by_username(context)
        client = Client()
        try:
            client.load_settings(session_path)
            client.get_timeline_feed()
            context.user_data['client'] = client
            client.dump_settings(get_session_path_by_chat_id(context))
            await query.edit_message_text(text=f"✅ ورود با session موفقیت آمیز بود! خوش آمدید {client.username}.")
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"خطا در ورود با session: {e}")
            await query.edit_message_text(text="❌ ورود با session ناموفق بود. لطفاً رمز عبور را وارد کنید:")
            return LOGIN_GET_PASSWORD
    else:
        await query.edit_message_text(text="🔑 بسیار خب. لطفاً رمز عبور خود را وارد کنید:")
        return LOGIN_GET_PASSWORD

async def login_get_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """رمز عبور را دریافت و در مورد 2FA سوال می‌کند."""
    context.user_data['password'] = update.message.text
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)

    keyboard = [[InlineKeyboardButton("✔️ بله", callback_data='2fa_yes'), InlineKeyboardButton("✖️ خیر", callback_data='2fa_no')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📱 آیا حساب شما نیاز به تایید دو مرحله‌ای (2FA) دارد؟", reply_markup=reply_markup)
    return LOGIN_HANDLE_2FA

async def login_handle_2fa_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پاسخ کاربر برای 2FA را مدیریت می‌کند."""
    query = update.callback_query
    
    if query.data == '2fa_yes':
        await query.edit_message_text(text="🔢 لطفاً کد تایید دو مرحله‌ای را وارد کنید:")
        return LOGIN_GET_2FA_CODE
    else:
        context.user_data['verification_code'] = ''
        return await _perform_login(update, context)

async def login_get_2fa_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """کد 2FA را دریافت و تلاش برای ورود می‌کند."""
    context.user_data['verification_code'] = update.message.text
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    return await _perform_login(update, context)

async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مکالمه ورود را لغو می‌کند."""
    await update.message.reply_text("🛑 عملیات ورود لغو شد.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """از حساب کاربری خارج و session را حذف می‌کند."""
    context.user_data['chat_id'] = update.effective_chat.id
    session_path = get_session_path_by_chat_id(context)
    if session_path and os.path.exists(session_path):
        os.remove(session_path)
    
    context.user_data.clear()
    await update.message.reply_text("✔️ شما با موفقیت از حساب خود خارج شدید.")

# --- بخش مدیریت وضعیت ---
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """وضعیت فعلی فرآیند لایک را نمایش می‌دهد."""
    if not context.user_data.get('is_liking', False):
        await update.message.reply_text("💤 در حال حاضر هیچ فرآیند لایکی در حال اجرا نیست.")
        return

    total = context.user_data.get('total_public_users', 0)
    processed = context.user_data.get('processed_users', 0)
    start_time = context.user_data.get('start_time', 0)
    
    elapsed_time = time.monotonic() - start_time
    
    eta_str = "نامشخص"
    if processed > 0:
        avg_time_per_user = elapsed_time / processed
        remaining_users = total - processed
        eta_seconds = remaining_users * avg_time_per_user
        eta_str = time.strftime("%H:%M:%S", time.gmtime(eta_seconds))

    last_status_raw = context.user_data.get('last_like_status', 'نامشخص')
    last_status_escaped = html.escape(last_status_raw)
    
    percentage = (processed / total) * 100 if total > 0 else 0

    status_message = (
        f"📊 <b>وضعیت فرآیند لایک</b>\n\n"
        f"👥 کاربران: <b>{processed}</b> از <b>{total}</b>\n"
        f"📈 درصد پیشرفت: <b>{percentage:.2f}%</b>\n"
        f"⏳ زمان سپری شده: <b>{time.strftime('%H:%M:%S', time.gmtime(elapsed_time))}</b>\n"
        f"⏱️ تخمین زمان باقی‌مانده (ETA): <b>{eta_str}</b>\n\n"
        f"❤️‍🔥 لایک‌های موفق: <b>{context.user_data.get('total_likes_done', 0)}</b>\n"
        f"🟡 از قبل لایک شده: <b>{context.user_data.get('already_liked_count', 0)}</b>\n"
        f"❌ خطاها: <b>{context.user_data.get('errors_encountered', 0)}</b>\n\n"
        f"<b>آخرین عملیات:</b>\n<code>{last_status_escaped}</code>"
    )
    await update.message.reply_html(status_message)

async def cancel_liking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فرآیند لایک کردن را لغو می‌کند."""
    if context.user_data.get('is_liking', False):
        context.user_data['is_liking'] = False
        await update.message.reply_text("✋ درخواست لغو فرآیند ارسال شد. لطفاً تا توقف کامل حلقه صبر کنید...")
    else:
        await update.message.reply_text("🤔 هیچ فرآیندی برای لغو کردن در حال اجرا نیست.")

# --- بخش فرآیند لایک ---
async def liking_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    """وظیفه پس‌زمینه که حلقه لایک کردن را اجرا می‌کند."""
    chat_id = context.user_data['chat_id']
    cl = context.user_data['client']
    public_users = context.user_data['public_users']
    likes_per_user = context.user_data['likes_per_user']
    sleep_range = context.user_data['sleep_range']
    
    cl.delay_range = context.user_data['delay_range']

    try:
        for user in public_users:
            if not context.user_data.get('is_liking', False):
                await context.bot.send_message(chat_id, "🛑 عملیات لایک توسط شما لغو شد.")
                break
            
            try:
                user_medias = cl.user_medias(user.pk, amount=likes_per_user)
                if not user_medias:
                    context.user_data['last_like_status'] = f"ℹ️ اطلاعات: کاربر {user.username} پستی برای لایک نداشت."
                    continue

                for media in user_medias:
                    if media.has_liked:
                        context.user_data['already_liked_count'] += 1
                        context.user_data['last_like_status'] = f"🟡 قبلاً لایک شده: پست کاربر {user.username}"
                        logger.info(f"پست کاربر {user.username} ({media.code}) قبلاً لایک شده بود.")
                        continue

                    cl.media_like(media.pk)
                    context.user_data['total_likes_done'] += 1
                    context.user_data['last_like_status'] = f"❤️‍🔥 موفق: پست کاربر {user.username} لایک شد."
                    logger.info(f"پست کاربر {user.username} لایک شد.")
                    await asyncio.sleep(random.uniform(sleep_range[0], sleep_range[1]))

            except (UserNotFound, Exception) as e:
                context.user_data['errors_encountered'] += 1
                logger.warning(f"خطا در پردازش کاربر {user.username}: {e}")
                error_summary = str(e).split('\n')[0]
                context.user_data['last_like_status'] = f"❌ خطا در پردازش کاربر {user.username}: {error_summary}"
            finally:
                context.user_data['processed_users'] += 1
        
        if context.user_data.get('is_liking', False):
            final_report = (
                f"🎉 <b>گزارش نهایی عملیات</b> 🎉\n\n"
                f"تعداد کل کاربران عمومی: <b>{context.user_data['total_public_users']}</b>\n"
                f"❤️‍🔥 لایک‌های موفق: <b>{context.user_data['total_likes_done']}</b>\n"
                f"🟡 از قبل لایک شده: <b>{context.user_data['already_liked_count']}</b>\n"
                f"❌ خطاها: <b>{context.user_data['errors_encountered']}</b>"
            )
            await context.bot.send_message(chat_id, final_report, parse_mode='HTML')

    except Exception as e:
        logger.error(f"خطای جدی در وظیفه پس‌زمینه لایک: {e}")
        await context.bot.send_message(chat_id, f"🚨 یک خطای جدی در وظیفه پس‌زمینه رخ داد: {e}")
    finally:
        context.user_data['is_liking'] = False
        for key in ['public_users', 'delay_range', 'likes_per_user', 'sleep_range', 'start_time']:
            if key in context.user_data:
                del context.user_data[key]

async def liking_setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع مکالمه برای تنظیمات لایک."""
    if context.user_data.get('is_liking', False):
        await update.message.reply_text("⏳ یک فرآیند لایک دیگر در حال اجراست.")
        return ConversationHandler.END

    if 'client' not in context.user_data:
        await update.message.reply_text("🔒 شما هنوز وارد حساب اینستاگرام خود نشده‌اید. لطفاً ابتدا از دستور /login استفاده کنید.")
        return ConversationHandler.END

    # بررسی و اعتبارسنجی لینک‌ها
    urls = [url.strip() for url in update.message.text.split(',')]
    valid_urls = []
    insta_pattern = re.compile(r'https?://www\.instagram\.com/(p(ost)?|reel)/.*')
    for url in urls:
        if insta_pattern.match(url):
            valid_urls.append(url)

    if not valid_urls:
        await update.message.reply_text("🔗 هیچ لینک معتبر اینستاگرامی یافت نشد. لطفاً لینک‌ها را بررسی کنید.")
        return ConversationHandler.END
    
    context.user_data['post_urls'] = valid_urls
    await update.message.reply_text(
        f"✅ {len(valid_urls)} لینک معتبر شناسایی شد.\n\n"
        "⏱️ لطفاً محدوده تاخیر بین درخواست‌ها را وارد کنید (مثال: 5,10).\n"
        "این تنظیم برای جلوگیری از بلاک شدن مهم است."
    )
    return LIKING_GET_DELAY

async def liking_get_delay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """محدوده تاخیر را دریافت می‌کند."""
    try:
        parts = [int(p.strip()) for p in update.message.text.split(',')]
        if len(parts) != 2: raise ValueError
        context.user_data['delay_range'] = [min(parts), max(parts)]
        await update.message.reply_text("👍 بسیار خب.\n\n🔢 حالا تعداد پست‌هایی که از هر کاربر لایک شود را وارد کنید (مثال: 1):")
        return LIKING_GET_COUNT
    except (ValueError, IndexError):
        await update.message.reply_text("❌ ورودی نامعتبر است. لطفاً دو عدد را با کاما جدا کنید (مثال: 5,10).")
        return LIKING_GET_DELAY

async def liking_get_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تعداد لایک برای هر کاربر را دریافت می‌کند."""
    try:
        count = int(update.message.text.strip())
        if count <= 0: raise ValueError
        context.user_data['likes_per_user'] = count
        await update.message.reply_text(
            "👍 عالی!\n\n"
            "😴 در آخر، محدوده زمان انتظار (به ثانیه) بعد از هر لایک را وارد کنید (مثال: 10,20):"
        )
        return LIKING_GET_SLEEP
    except ValueError:
        await update.message.reply_text("❌ لطفاً یک عدد صحیح و مثبت وارد کنید.")
        return LIKING_GET_COUNT

async def liking_get_sleep_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """محدوده خواب را دریافت و فرآیند اصلی را شروع می‌کند."""
    try:
        parts = [int(p.strip()) for p in update.message.text.split(',')]
        if len(parts) != 2: raise ValueError
        context.user_data['sleep_range'] = [min(parts), max(parts)]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ ورودی نامعتبر است. لطفاً دو عدد را با کاما جدا کنید (مثال: 10,20).")
        return LIKING_GET_SLEEP

    # شروع فرآیند اصلی
    chat_id = update.effective_chat.id
    cl = context.user_data['client']
    urls = context.user_data['post_urls']
    msg = await context.bot.send_message(chat_id, f"⏳ در حال پردازش {len(urls)} لینک... لطفاً صبر کنید.")
    
    try:
        all_likers = {} # استفاده از دیکشنری برای حذف خودکار کاربران تکراری
        for i, url in enumerate(urls):
            await msg.edit_text(f"📄 در حال دریافت لایک‌کنندگان از لینک {i+1} از {len(urls)}...")
            media_pk = cl.media_pk_from_url(url)
            likers = cl.media_likers(media_pk)
            for liker in likers:
                all_likers[liker.pk] = liker

        await msg.edit_text(f"👥 تعداد کل لایک‌کنندگان منحصر به فرد: {len(all_likers)}. در حال فیلتر کردن اکانت‌های عمومی...")

        public_users = [user for user in all_likers.values() if not user.is_private]
        
        # مقداردهی اولیه متغیرهای وضعیت
        context.user_data['is_liking'] = True
        context.user_data['total_public_users'] = len(public_users)
        context.user_data['processed_users'] = 0
        context.user_data['total_likes_done'] = 0
        context.user_data['already_liked_count'] = 0
        context.user_data['errors_encountered'] = 0
        context.user_data['start_time'] = time.monotonic()
        context.user_data['last_like_status'] = "فرآیند هنوز شروع نشده است."
        context.user_data['public_users'] = public_users

        await msg.edit_text(f"🚀 تعداد کاربران عمومی: {len(public_users)}. شروع فرآیند لایک...\nبرای لغو از /cancel_liking استفاده کنید.")
        asyncio.create_task(liking_task(context))
        return ConversationHandler.END

    except (MediaNotFound, LoginRequired, Exception) as e:
        error_message = f"🚨 خطای پیش‌بینی نشده: {e}"
        if isinstance(e, MediaNotFound):
            error_message = "❌ خطا: پستی با این لینک پیدا نشد."
        elif isinstance(e, LoginRequired):
            error_message = "🔑 خطا: نیاز به ورود مجدد است. لطفاً با /login وارد شوید."
            context.user_data.clear()
        
        await msg.edit_text(error_message)
        return ConversationHandler.END

async def liking_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مکالمه تنظیمات لایک را لغو می‌کند."""
    await update.message.reply_text("🛑 عملیات تنظیم لایک لغو شد.")
    for key in ['post_urls', 'delay_range', 'likes_per_user', 'sleep_range']:
        if key in context.user_data:
            del context.user_data[key]
    return ConversationHandler.END

def main() -> None:
    """ربات را راه‌اندازی و اجرا می‌کند."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    login_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states={
            LOGIN_GET_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_get_username)],
            LOGIN_HANDLE_SESSION: [CallbackQueryHandler(login_handle_session_choice)],
            LOGIN_GET_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_get_password)],
            LOGIN_HANDLE_2FA: [CallbackQueryHandler(login_handle_2fa_choice)],
            LOGIN_GET_2FA_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_get_2fa_code)],
        },
        fallbacks=[CommandHandler('cancel', login_cancel)],
    )

    liking_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, liking_setup_start)],
        states={
            LIKING_GET_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, liking_get_delay)],
            LIKING_GET_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, liking_get_count)],
            LIKING_GET_SLEEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, liking_get_sleep_and_start)],
        },
        fallbacks=[CommandHandler('cancel', liking_cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("cancel_liking", cancel_liking))
    application.add_handler(login_handler)
    application.add_handler(liking_handler)

    application.run_polling()
    print("Bot is now running. Press Ctrl+C to stop.")

if __name__ == '__main__':
    main()
