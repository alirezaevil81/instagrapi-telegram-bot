import os
import logging
import random
import asyncio
import html
import time
from dotenv import load_dotenv
import re
from functools import wraps

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
ADMIN_USER_ID_STR = os.getenv("ADMIN_USER_ID")

if not TELEGRAM_BOT_TOKEN or not ADMIN_USER_ID_STR:
    logger.error("توکن ربات یا شناسه ادمین در فایل .env یافت نشد!")
    exit()

try:
    ADMIN_USER_ID = int(ADMIN_USER_ID_STR)
except ValueError:
    logger.error("شناسه ادمین در فایل .env یک عدد صحیح معتبر نیست!")
    exit()


# تعریف مراحل مکالمه برای خوانایی بهتر
# مکالمه ورود
LOGIN_GET_USERNAME, LOGIN_HANDLE_SESSION, LOGIN_GET_PASSWORD, LOGIN_HANDLE_2FA, LOGIN_GET_2FA_CODE = range(5)
# مکالمه تنظیمات لایک
LIKING_GET_DELAY, LIKING_GET_COUNT, LIKING_GET_SLEEP = range(5, 8)


# --- Decorator برای محدود کردن دسترسی به ادمین ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_USER_ID:
            logger.warning(f"دسترسی غیرمجاز توسط کاربر {user_id} رد شد.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped


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

    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ در حال اتصال به اینستاگرام... لطفاً صبر کنید.")
    
    username_input = context.user_data['instagram_username']
    password = context.user_data['password']
    verification_code = context.user_data.get('verification_code', '')

    client = Client()
    try:
        # اجرای ورود در یک رشته جداگانه برای جلوگیری از بلاک شدن
        await asyncio.to_thread(client.login, username_input, password, verification_code=verification_code)
        
        context.user_data['client'] = client
        session_path = get_session_path_by_chat_id(context)
        client.dump_settings(session_path)
        
        await msg.edit_text(f"✅ ورود با موفقیت انجام شد!\n\n🎉 خوش آمدید <b>{client.username}</b>.\nاکنون آماده دریافت لینک پست‌ها هستید.", parse_mode='HTML')
        return ConversationHandler.END

    except BadPassword:
        await msg.edit_text("❌ رمز عبور اشتباه است. لطفاً با /login دوباره تلاش کنید.")
    except TwoFactorRequired:
        await msg.edit_text("📱 کد تایید دو مرحله‌ای اشتباه است یا حساب شما به آن نیاز دارد. لطفاً با /login دوباره تلاش کنید.")
    except Exception as e:
        logger.error(f"خطا در ورود برای کاربر {username_input}: {e}")
        await msg.edit_text(f"🚨 خطایی در هنگام ورود رخ داد: {e}\nلطفاً با /login دوباره تلاش کنید.")
    
    context.user_data.clear()
    return ConversationHandler.END

# --- توابع اصلی ربات ---
@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /start را مدیریت می‌کند."""
    user = update.effective_user
    context.user_data['chat_id'] = update.effective_chat.id
    welcome_message = (
        f"👋 سلام <b>{user.mention_html()}</b>\n\n"
        "به ربات پیشرفته لایکر اینستاگرام خوش آمدید. برای شروع، لطفاً از دستورات زیر استفاده کنید:\n\n"
        "<b>دستورات اصلی:</b>\n"
        "/login - 🔑 ورود به حساب اینستاگرام\n"
        "/logout - 🚪 خروج از حساب فعلی\n"
        "/status - 📊 مشاهده وضعیت عملیات\n"
        "/cancel - 🛑 لغو مکالمه (مثل ورود یا تنظیمات)\n"
        "/cancel_liking - ✋ لغو فرآیند لایک در حال اجرا\n\n"
        "<i>می‌توانید با ارسال یک یا چند لینک پست (جدا شده با کاما) فرآیند لایک را شروع کنید.</i>\n\n"
        "ℹ️ همچنین می‌توانید از منوی دستورات (دکمه /) برای دسترسی سریع‌تر استفاده کنید."
    )
    await update.message.reply_html(welcome_message)

# --- بخش مدیریت ورود ---
@admin_only
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند ورود. درخواست نام کاربری."""
    context.user_data['chat_id'] = update.effective_chat.id
    if 'client' in context.user_data:
        client = context.user_data['client']
        await update.message.reply_text(f"✅ شما از قبل با اکانت <b>{client.username}</b> وارد شده‌اید.\nبرای خروج روی /logout کلیک کنید.", parse_mode='HTML')
        return ConversationHandler.END
        
    await update.message.reply_text("👤 لطفاً نام کاربری اینستاگرام خود را وارد کنید:\n\nبرای لغو در هر مرحله، روی /cancel کلیک کنید.")
    return LOGIN_GET_USERNAME

async def login_get_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نام کاربری را دریافت و وجود session را بررسی می‌کند."""
    username = update.message.text.strip().lower()
    context.user_data['instagram_username'] = username
    session_path = get_session_path_by_username(context)

    if os.path.exists(session_path):
        keyboard = [[InlineKeyboardButton("✔️ بله، با Session وارد شو", callback_data='session_yes'), InlineKeyboardButton("✖️ خیر، با رمز عبور", callback_data='session_no')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"📂 یک Session برای کاربر '<b>{username}</b>' پیدا شد. آیا می‌خواهید با آن وارد شوید؟", reply_markup=reply_markup, parse_mode='HTML')
        return LOGIN_HANDLE_SESSION
    else:
        await update.message.reply_text("🔑 Session پیدا نشد. لطفاً رمز عبور خود را وارد کنید:\n\nبرای لغو، روی /cancel کلیک کنید.")
        return LOGIN_GET_PASSWORD

async def login_handle_session_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پاسخ کاربر برای استفاده از session را مدیریت می‌کند."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'session_yes':
        session_path = get_session_path_by_username(context)
        client = Client()
        try:
            await asyncio.to_thread(client.load_settings, session_path)
            await asyncio.to_thread(client.get_timeline_feed)
            context.user_data['client'] = client
            await asyncio.to_thread(client.dump_settings, get_session_path_by_chat_id(context))
            username = context.user_data.get('instagram_username', 'کاربر')
            await query.edit_message_text(text=f"✅ ورود با Session موفقیت آمیز بود! خوش آمدید <b>{username}</b>.", parse_mode='HTML')
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"خطا در ورود با session: {e}")
            await query.edit_message_text(text="❌ ورود با Session ناموفق بود. لطفاً رمز عبور را وارد کنید:\n\nبرای لغو، روی /cancel کلیک کنید.")
            return LOGIN_GET_PASSWORD
    else:
        await query.edit_message_text(text="🔑 بسیار خب. لطفاً رمز عبور خود را وارد کنید:\n\nبرای لغو، روی /cancel کلیک کنید.")
        return LOGIN_GET_PASSWORD

async def login_get_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """رمز عبور را دریافت و در مورد 2FA سوال می‌کند."""
    context.user_data['password'] = update.message.text
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)

    keyboard = [[InlineKeyboardButton("✔️ بله", callback_data='2fa_yes'), InlineKeyboardButton("✖️ خیر", callback_data='2fa_no')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📱 آیا حساب شما نیاز به تایید دو مرحله‌ای (2FA) دارد؟\n\nبرای لغو، روی /cancel کلیک کنید.", reply_markup=reply_markup)
    return LOGIN_HANDLE_2FA

async def login_handle_2fa_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پاسخ کاربر برای 2FA را مدیریت می‌کند."""
    query = update.callback_query
    
    if query.data == '2fa_yes':
        await query.edit_message_text(text="🔢 لطفاً کد تایید دو مرحله‌ای را وارد کنید:\n\nبرای لغو، روی /cancel کلیک کنید.")
        return LOGIN_GET_2FA_CODE
    else:
        context.user_data['verification_code'] = ''
        return await _perform_login(update, context)

async def login_get_2fa_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """کد 2FA را دریافت و تلاش برای ورود می‌کند."""
    context.user_data['verification_code'] = update.message.text
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    return await _perform_login(update, context)

@admin_only
async def request_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """درخواست تایید برای خروج از حساب را ارسال می‌کند."""
    context.user_data['chat_id'] = update.effective_chat.id
    if 'client' in context.user_data:
        keyboard = [
            [
                InlineKeyboardButton("✔️ بله، خارج شو", callback_data='confirm_logout_yes'),
                InlineKeyboardButton("✖️ خیر", callback_data='confirm_logout_no'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("⚠️ آیا مطمئن هستید که می‌خواهید از حساب خود خارج شوید؟", reply_markup=reply_markup)
    else:
        await update.message.reply_text("🤔 شما در حال حاضر وارد هیچ حسابی نشده‌اید.")

@admin_only
async def handle_logout_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پاسخ کاربر برای تایید خروج را مدیریت می‌کند."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'confirm_logout_yes':
        session_path = get_session_path_by_chat_id(context)
        if session_path and os.path.exists(session_path):
            os.remove(session_path)
        context.user_data.clear()
        await query.edit_message_text("✔️ شما با موفقیت از حساب خود خارج شدید.")
    else: # confirm_logout_no
        await query.edit_message_text("👍 عملیات خروج لغو شد.")


# --- بخش مدیریت وضعیت ---
@admin_only
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
                user_medias = await asyncio.to_thread(cl.user_medias, user.pk, amount=likes_per_user)
                if not user_medias:
                    context.user_data['last_like_status'] = f"ℹ️ اطلاعات: کاربر {user.username} پستی برای لایک نداشت."
                    continue

                for media in user_medias:
                    if media.has_liked:
                        context.user_data['already_liked_count'] += 1
                        context.user_data['last_like_status'] = f"🟡 قبلاً لایک شده: پست کاربر {user.username}"
                        logger.info(f"پست کاربر {user.username} ({media.code}) قبلاً لایک شده بود.")
                        continue

                    await asyncio.to_thread(cl.media_like, media.pk)
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

@admin_only
async def liking_setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع مکالمه برای تنظیمات لایک."""
    if context.user_data.get('is_liking', False):
        await update.message.reply_text("⏳ یک فرآیند لایک دیگر در حال اجراست.")
        return ConversationHandler.END

    if 'client' not in context.user_data:
        await update.message.reply_text("🔒 شما هنوز وارد حساب اینستاگرام خود نشده‌اید. لطفاً ابتدا از دستور /login استفاده کنید.")
        return ConversationHandler.END

    urls = [url.strip() for url in update.message.text.split(',')]
    valid_urls = []
    insta_pattern = re.compile(r'https?://www\.instagram\.com/(p(ost)?|reel)/.*')
    for url in urls:
        if insta_pattern.match(url):
            valid_urls.append(url)

    if not valid_urls:
        return
    
    context.user_data['post_urls'] = valid_urls
    await update.message.reply_text(
        f"✅ <b>{len(valid_urls)}</b> لینک معتبر شناسایی شد.\n\n"
        "⚙️ لطفاً تنظیمات زیر را مشخص کنید:\n\n"
        "<b>مرحله ۱ از ۳:</b>\n"
        "⏱️ محدوده تاخیر بین درخواست‌ها را وارد کنید (مثال: <code>5,10</code>).\n"
        "<i>این تنظیم برای جلوگیری از بلاک شدن مهم است.</i>\n\n"
        "برای لغو، روی /cancel کلیک کنید.",
        parse_mode='HTML'
    )
    return LIKING_GET_DELAY

async def liking_get_delay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """محدوده تاخیر را دریافت می‌کند."""
    try:
        parts = [int(p.strip()) for p in update.message.text.split(',')]
        if len(parts) != 2: raise ValueError
        context.user_data['delay_range'] = [min(parts), max(parts)]
        await update.message.reply_text(
            "👍 بسیار خب.\n\n"
            "<b>مرحله ۲ از ۳:</b>\n"
            "🔢 حالا تعداد پست‌هایی که از هر کاربر لایک شود را وارد کنید (مثال: <code>1</code>):\n\n"
            "برای لغو، روی /cancel کلیک کنید.",
            parse_mode='HTML'
        )
        return LIKING_GET_COUNT
    except (ValueError, IndexError):
        await update.message.reply_text("❌ ورودی نامعتبر است. لطفاً دو عدد را با کاما جدا کنید (مثال: <code>5,10</code>).", parse_mode='HTML')
        return LIKING_GET_DELAY

async def liking_get_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تعداد لایک برای هر کاربر را دریافت می‌کند."""
    try:
        count = int(update.message.text.strip())
        if count <= 0: raise ValueError
        context.user_data['likes_per_user'] = count
        await update.message.reply_text(
            "👍 عالی!\n\n"
            "<b>مرحله ۳ از ۳:</b>\n"
            "😴 در آخر، محدوده زمان انتظار (به ثانیه) بعد از هر لایک را وارد کنید (مثال: <code>10,20</code>):\n\n"
            "برای لغو، روی /cancel کلیک کنید.",
            parse_mode='HTML'
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
        await update.message.reply_text("❌ ورودی نامعتبر است. لطفاً دو عدد را با کاما جدا کنید (مثال: <code>10,20</code>).", parse_mode='HTML')
        return LIKING_GET_SLEEP

    chat_id = update.effective_chat.id
    cl = context.user_data['client']
    urls = context.user_data['post_urls']
    msg = await context.bot.send_message(chat_id, f"⏳ در حال پردازش <b>{len(urls)}</b> لینک... لطفاً صبر کنید.", parse_mode='HTML')
    
    try:
        all_likers = {}
        for i, url in enumerate(urls):
            await msg.edit_text(f"📄 در حال دریافت لایک‌کنندگان از لینک <b>{i+1}</b> از <b>{len(urls)}</b>...", parse_mode='HTML')
            media_pk = await asyncio.to_thread(cl.media_pk_from_url, url)
            likers = await asyncio.to_thread(cl.media_likers, media_pk)
            for liker in likers:
                all_likers[liker.pk] = liker

        await msg.edit_text(f"👥 تعداد کل لایک‌کنندگان منحصر به فرد: <b>{len(all_likers)}</b>. در حال فیلتر کردن اکانت‌های عمومی...", parse_mode='HTML')

        public_users = [user for user in all_likers.values() if not user.is_private]
        
        context.user_data['is_liking'] = True
        context.user_data['total_public_users'] = len(public_users)
        context.user_data['processed_users'] = 0
        context.user_data['total_likes_done'] = 0
        context.user_data['already_liked_count'] = 0
        context.user_data['errors_encountered'] = 0
        context.user_data['start_time'] = time.monotonic()
        context.user_data['last_like_status'] = "فرآیند هنوز شروع نشده است."
        context.user_data['public_users'] = public_users

        await msg.edit_text(f"🚀 تعداد کاربران عمومی: <b>{len(public_users)}</b>. شروع فرآیند لایک...\nبرای لغو از /cancel_liking استفاده کنید.", parse_mode='HTML')
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

@admin_only
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    مکالمه فعلی (ورود یا تنظیمات) را لغو می کند.
    """
    await update.message.reply_text("🛑 عملیات فعلی لغو شد.", reply_markup=ReplyKeyboardRemove())
    
    # پاکسازی داده‌های موقت برای جلوگیری از تداخل
    for key in ['instagram_username', 'password', 'verification_code', 'post_urls', 'delay_range', 'likes_per_user', 'sleep_range']:
        if key in context.user_data:
            del context.user_data[key]
            
    return ConversationHandler.END

@admin_only
async def request_cancel_liking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """درخواست تایید برای لغو فرآیند لایک را ارسال می‌کند."""
    if context.user_data.get('is_liking', False):
        keyboard = [
            [
                InlineKeyboardButton("✔️ بله، متوقف کن", callback_data='confirm_cancel_yes'),
                InlineKeyboardButton("✖️ خیر، ادامه بده", callback_data='confirm_cancel_no'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("⚠️ آیا مطمئن هستید که می‌خواهید فرآیند لایک را متوقف کنید؟", reply_markup=reply_markup)
    else:
        await update.message.reply_text("🤔 هیچ فرآیند لایکی برای لغو کردن در حال اجرا نیست.")

@admin_only
async def handle_cancel_liking_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پاسخ کاربر برای تایید لغو فرآیند لایک را مدیریت می‌کند."""
    query = update.callback_query
    await query.answer()

    if query.data == 'confirm_cancel_yes':
        if context.user_data.get('is_liking', False):
            context.user_data['is_liking'] = False
            await query.edit_message_text("✋ فرآیند لایک در حال اجرا لغو شد.")
        else:
            await query.edit_message_text("🤔 فرآیند قبلاً متوقف شده بود.")
    else: # confirm_cancel_no
        await query.edit_message_text("👍 بسیار خب. عملیات ادامه پیدا می‌کند.")


def main() -> None:
    """ربات را راه‌اندازی و اجرا می‌کند."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # دستور لغو یکپارچه برای هر دو مکالمه
    cancel_conv_handler = CommandHandler('cancel', cancel_conversation)

    login_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states={
            LOGIN_GET_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_get_username)],
            LOGIN_HANDLE_SESSION: [CallbackQueryHandler(login_handle_session_choice)],
            LOGIN_GET_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_get_password)],
            LOGIN_HANDLE_2FA: [CallbackQueryHandler(login_handle_2fa_choice)],
            LOGIN_GET_2FA_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_get_2fa_code)],
        },
        fallbacks=[cancel_conv_handler],
        per_message=False,
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )

    liking_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r'instagram.com'), liking_setup_start)],
        states={
            LIKING_GET_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, liking_get_delay)],
            LIKING_GET_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, liking_get_count)],
            LIKING_GET_SLEEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, liking_get_sleep_and_start)],
        },
        fallbacks=[cancel_conv_handler],
        per_message=False,
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("logout", request_logout))
    application.add_handler(CallbackQueryHandler(handle_logout_confirmation, pattern=r'^confirm_logout_'))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("cancel_liking", request_cancel_liking))
    application.add_handler(CallbackQueryHandler(handle_cancel_liking_confirmation, pattern=r'^confirm_cancel_'))
    application.add_handler(login_handler)
    application.add_handler(liking_handler)


    application.run_polling()
    print("Bot is now running. Press Ctrl+C to stop.")

if __name__ == '__main__':
    main()
