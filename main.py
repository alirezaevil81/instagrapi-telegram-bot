import logging
import asyncio
import signal
import random
import re
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from instagrapi import Client
from instagrapi.exceptions import ClientError, RateLimitError

# تنظیم لاگ‌گذاری برای دیباگ دقیق
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تابع برای استخراج media_id از لینک اینستاگرام
def extract_media_id(url):
    """استخراج media_id از لینک اینستاگرام"""
    try:
        pattern = r"https://www\.instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)/"
        match = re.search(pattern, url)
        if match:
            shortcode = match.group(1)
            cl = Client()
            return cl.media_pk_from_code(shortcode)
        else:
            raise ValueError("لینک اینستاگرام معتبر نیست")
    except Exception as e:
        logger.error(f"خطا در استخراج media_id: {e}")
        return None

# تابع دستور /start
async def start(update, context):
    """پاسخ به دستور /start"""
    logger.info(f"دستور /start از کاربر {update.effective_user.id} دریافت شد")
    await update.message.reply_text("به ربات خوش آمدید! لطفاً نام کاربری اینستاگرام خود را ارسال کنید یا یک لینک پست اینستاگرام بفرستید.")

# تابع دستور /status
async def status(update, context):
    """بررسی وضعیت ربات"""
    logger.info(f"دستور /status از کاربر {update.effective_user.id} دریافت شد")
    active_tasks = len([task for task in asyncio.all_tasks() if not task.done()])
    await update.message.reply_text(f"ربات فعال است. تعداد وظایف در حال اجرا: {active_tasks}")

# تابع دستور /stop
async def stop(update, context):
    """توقف فرآیندهای لایک کردن"""
    logger.info(f"دستور /stop از کاربر {update.effective_user.id} دریافت شد")
    like_tasks = context.bot_data.get("like_tasks", [])
    
    if not like_tasks:
        await update.message.reply_text("هیچ فرآیند لایک کردنی در حال اجرا نیست.")
        return
    
    cancelled_tasks = 0
    for task in like_tasks:
        if not task.done():
            task.cancel()
            cancelled_tasks += 1
    
    # پاکسازی لیست وظایف
    context.bot_data["like_tasks"] = [task for task in like_tasks if not task.done()]
    logger.info(f"{cancelled_tasks} وظیفه لایک کردن لغو شد")
    await update.message.reply_text(f"{cancelled_tasks} فرآیند لایک کردن متوقف شد. ربات آماده دریافت دستورات جدید است.")

# تابع مدیریت خطاها
async def error_handler(update, context):
    """مدیریت خطاهای ربات تلگرام"""
    logger.error(f"به‌روزرسانی {update} باعث خطای {context.error} شد")
    if update and update.message:
        await update.message.reply_text("خطایی رخ داد. لطفاً دوباره تلاش کنید.")

# تابع پردازش نام کاربری اینستاگرام
async def handle_username(update, context):
    """پردازش نام کاربری اینستاگرام و ورود به حساب"""
    username = update.message.text.strip()
    logger.info(f"کاربر {update.effective_user.id} نام کاربری {username} را ارسال کرد")
    
    try:
        cl = Client()
        session_path = f"sessions/{username}.json"
        try:
            cl.load_settings(session_path)
            cl.get_timeline_feed()  # تست ورود
            logger.info(f"ورود با سشن برای {username} موفق بود")
        except Exception as e:
            logger.error(f"خطا در بارگذاری سشن {username}: {e}")
            await update.message.reply_text("خطا در بارگذاری سشن. لطفاً نام کاربری معتبر ارسال کنید.")
            return
        
        context.bot_data["client"] = cl
        context.bot_data["username"] = username
        context.bot_data["like_tasks"] = []  # مقداردهی اولیه لیست وظایف
        await update.message.reply_text(f"ورود با نام کاربری {username} موفق بود. حالا یک لینک اینستاگرام بفرستید.")
    except Exception as e:
        logger.error(f"خطا در پردازش نام کاربری {username}: {e}")
        await update.message.reply_text("خطا در ورود. لطفاً دوباره تلاش کنید.")

# تابع ناهمگام برای لایک کردن پست‌ها
async def like_posts_task(cl, likers, update, max_likes=5, min_delay=12.0, max_delay=18.0):
    """وظیفه ناهمگام برای لایک کردن پست‌های کاربران غیرخصوصی"""
    try:
        non_private_users = []
        for user in likers:
            try:
                user_info = cl.user_info(user.pk)
                if not user_info.is_private:
                    non_private_users.append(user)
                await asyncio.sleep(random.uniform(1.5, 2.5))  # تأخیر سبک برای بررسی کاربران
            except Exception as e:
                logger.error(f"خطا در دریافت اطلاعات کاربر {user.username}: {e}")
                continue
        
        logger.info(f"کاربران غیرخصوصی: {len(non_private_users)} کاربر")
        await update.message.reply_text(f"کاربران غیرخصوصی: {len(non_private_users)} کاربر")
        
        for i, user in enumerate(non_private_users[:max_likes]):
            try:
                medias = cl.user_medias(user.pk, amount=1)
                if medias:
                    cl.media_like(medias[0].id)
                    logger.info(f"پست کاربر {user.username} لایک شد")
                    await update.message.reply_text(f"پست کاربر {user.username} لایک شد ({i+1}/{min(max_likes, len(non_private_users))})")
                await asyncio.sleep(random.uniform(min_delay, max_delay))
            except RateLimitError:
                logger.warning("محدودیت نرخ اینستاگرام تشخیص داده شد. توقف برای 120 ثانیه")
                await update.message.reply_text("محدودیت نرخ اینستاگرام. 120 ثانیه صبر می‌کنم...")
                await asyncio.sleep(120)
                continue
            except ClientError as e:
                logger.error(f"خطا در لایک کردن پست کاربر {user.username}: {e} - پاسخ: {cl.last_json}")
                continue
            except KeyError as e:
                logger.error(f"KeyError در پردازش کاربر {user.username}: {e} - پاسخ: {cl.last_json}")
                continue
            except Exception as e:
                logger.error(f"خطای عمومی در پردازش کاربر {user.username}: {e}")
                continue
        
        logger.info("فرآیند لایک کردن به پایان رسید")
        await update.message.reply_text("فرآیند لایک کردن به پایان رسید.")
    except asyncio.CancelledError:
        logger.info("وظیفه لایک کردن توسط کاربر لغو شد")
        await update.message.reply_text("فرآیند لایک کردن توسط شما متوقف شد.")
    except Exception as e:
        logger.error(f"خطا در وظیفه لایک کردن: {e}")
        await update.message.reply_text("خطایی در فرآیند لایک کردن رخ داد.")
    finally:
        # حذف وظیفه از لیست پس از اتمام یا لغو
        like_tasks = context.bot_data.get("like_tasks", [])
        context.bot_data["like_tasks"] = [task for task in like_tasks if not task.done()]

# تابع پردازش لینک اینستاگرام
async def handle_link(update, context):
    """پردازش لینک اینستاگرام و شروع وظیفه لایک کردن"""
    link = update.message.text.strip()
    logger.info(f"شروع پردازش لینک: {link} از کاربر {update.effective_user.id}")
    
    try:
        cl = context.bot_data.get("client")
        if not cl:
            await update.message.reply_text("لطفاً ابتدا نام کاربری اینستاگرام را ارسال کنید.")
            return
        
        media_id = extract_media_id(link)
        if not media_id:
            await update.message.reply_text("لینک اینستاگرام نامعتبر است.")
            return
        
        logger.info(f"media_id استخراج شد: {media_id}")
        likers = cl.media_likers(media_id)
        logger.info(f"لیست کاربران دریافت شد: {len(likers)} کاربر")
        await update.message.reply_text(f"لیست کاربران دریافت شد: {len(likers)} کاربر")
        
        # ایجاد وظیفه ناهمگام برای لایک کردن
        task = asyncio.create_task(like_posts_task(cl, likers, update, max_likes=5, min_delay=12.0, max_delay=18.0))
        context.bot_data.setdefault("like_tasks", []).append(task)
        await update.message.reply_text("فرآیند لایک کردن در پس‌زمینه شروع شد. می‌توانید دستورات دیگر را ارسال کنید یا با /stop آن را متوقف کنید.")
    except Exception as e:
        logger.error(f"خطا در پردازش لینک: {e}")
        await update.message.reply_text("خطایی در پردازش لینک رخ داد. لطفاً دوباره تلاش کنید.")

# تابع خاموش شدن ایمن
async def shutdown(application):
    """خاموش کردن ایمن ربات"""
    await application.stop()
    await application.shutdown()
    logger.info("ربات با موفقیت خاموش شد")

# تابع مدیریت سیگنال‌ها
def handle_shutdown(loop, application):
    """مدیریت سیگنال‌های SIGINT و SIGTERM"""
    tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
    for task in tasks:
        task.cancel()
    loop.run_until_complete(shutdown(application))
    loop.stop()
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()

# تابع اصلی
def main():
    """راه‌اندازی ربات"""
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connection_pool_size=10,
        connect_timeout=15.0,
        read_timeout=15.0
    )
    
    # تنظیم توکن ربات تلگرام
    # عبارت "YOUR_BOT_TOKEN" را با توکن واقعی ربات خود جایگزین کنید
    app = Application.builder().token("Your Token").request(request).build()
    
    # افزودن هندلرها
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Regex(r"https://www\.instagram\.com/(?:p|reel)/"), handle_link))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))
    app.add_error_handler(error_handler)
    
    # تنظیم سیگنال‌ها برای خاموشی ایمن
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown, loop, app)
    
    app.run_polling(poll_interval=3.0, timeout=30.0)

if __name__ == "__main__":
    main()