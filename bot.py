import logging
import sqlite3
import feedparser
import asyncio
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os



# Конфигурация
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RSS_FEED_URLS = [
    "https://spbnews78.ru/rss.xml",
    #"https://lenta.ru/rss",
    #"https://ria.ru/export/rss2/archive/index.xml",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.woman.ru/rss-feeds/rss.xml"
]

CHECK_INTERVAL = 3800  # секунд
CLEANUP_INTERVAL = 86400  # очистка каждые 24 часа
DATABASE_FILE = "news_bot.db"
NEWS_RETENTION_DAYS = 20

CHANNELS = {
    "спорт": {
        "link": "https://t.me/spgnovosti",
        "chat_id": "@spgnovosti"
    },
    "экономика": {
        "link": "https://t.me/ekon_cnh",
        "chat_id": "@ekon_cnh"
    },
    "технологии": {
        "link": "https://t.me/th_hanel",
        "chat_id": "@th_hanel"
    },
    "политика": {
        "link": "https://t.me/pol_cnh",
        "chat_id": "@pol_cnh"
    },
    "разное": {
        "link": "https://t.me/kras_cht",
        "chat_id": '@kras_cht'
    }
}

SYNONYMS = {
    "спорт": ["спорт", "футбол", "хоккей", " теннис", "баскетбол", "олимпиада", "лыжи", "мяч",
              "шайба", "бокс"],
    "экономика": ["экономика", "финансы", "бизнес", "рынок", "инвестиции", "деньги", "банк"],
    "технологии": ["технологии", "гаджеты", "it", "программирование", "искусственный интеллект", "ai"],
    "политика": ["политика", "правительство", "президент", "выборы", "парламент", "власть", "губернатор"],
    "разное": ["красота", "косметика", "артисты", "кино", "шоу", "театр", "здоровье", "жена",
                "измена", "рецепты", "макияж", "крем", "укладка", "морщины", "прыщи", "глаза", "муж", "нос"]
}

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Глобальный словарь для временного хранения выбранных категорий
user_selections = {}


def clean_html(text):
    """Очистка текста от HTML тегов"""
    if not text:
        return ""

    soup = BeautifulSoup(text, 'html.parser')
    clean_text = soup.get_text()
    clean_text = re.sub(r'&[a-z]+;', '', clean_text)
    clean_text = re.sub(r'<[^>]+>', '', clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    return clean_text


def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            filters TEXT,
            subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE,
            title TEXT,
            published_at TIMESTAMP,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_count INTEGER DEFAULT 0,
            last_check TIMESTAMP,
            subscribers_count INTEGER DEFAULT 0,
            last_cleanup TIMESTAMP
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM stats")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO stats (news_count, last_check, subscribers_count, last_cleanup) VALUES (0, CURRENT_TIMESTAMP, 0, CURRENT_TIMESTAMP)")

    conn.commit()
    conn.close()


async def add_subscriber(chat_id, username, first_name, last_name, filters):
    """Добавление или обновление подписчика"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO subscribers 
            (chat_id, username, first_name, last_name, filters, is_active) 
            VALUES (?, ?, ?, ?, ?, 1)
        """, (chat_id, username, first_name, last_name, " ".join(filters)))
        conn.commit()
        logger.info(f"Добавлен подписчик: {chat_id} с фильтрами: {filters}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении подписчика: {e}")
    finally:
        conn.close()


async def remove_subscriber(chat_id):
    """Удаление подписчика"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE subscribers SET is_active = 0 WHERE chat_id = ?", (chat_id,))
        conn.commit()
        logger.info(f"Удален подписчик: {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка при удалении подписчика: {e}")
    finally:
        conn.close()


def get_subscriber_filters(chat_id):
    """Получение фильтров подписчика"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT filters FROM subscribers WHERE chat_id = ? AND is_active = 1", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0]:
        return result[0].split()
    return []


def get_active_subscribers():
    """Получение активных подписчиков"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, filters FROM subscribers WHERE is_active = 1")
    subscribers = cursor.fetchall()
    conn.close()
    return subscribers


def is_news_sent(link):
    """Проверка, была ли новость уже отправлена"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sent_news WHERE link = ?", (link,))
    result = cursor.fetchone()[0] > 0
    conn.close()
    return result


def mark_news_as_sent(link, title, published_at):
    """Пометить новость как отправленную"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO sent_news (link, title, published_at)
            VALUES (?, ?, ?)
        """, (link, title, published_at))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при сохранении отправленной новости: {e}")
    finally:
        conn.close()


def cleanup_old_news():
    """Очистка старых новостей из базы данных"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    try:
        cutoff_date = (datetime.now() - timedelta(days=NEWS_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM sent_news WHERE published_at < ? OR sent_at < ?",
                       (cutoff_date, cutoff_date))
        deleted_count = cursor.rowcount
        conn.commit()
        logger.info(f"🧹 Очистка базы: удалено {deleted_count} старых новостей")
        cursor.execute("UPDATE stats SET last_cleanup = CURRENT_TIMESTAMP")
        conn.commit()
        return deleted_count
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке старых новостей: {e}")
        return 0
    finally:
        conn.close()


def update_stats(news_count=0, subscribers_count=0):
    """Обновление статистики"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        if news_count > 0:
            cursor.execute("UPDATE stats SET news_count = news_count + ?, last_check = CURRENT_TIMESTAMP",
                           (news_count,))
        if subscribers_count > 0:
            cursor.execute("UPDATE stats SET subscribers_count = ?", (subscribers_count,))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при обновлении статистики: {e}")
    finally:
        conn.close()


def get_stats():
    """Получение статистики"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT news_count, last_check, subscribers_count, last_cleanup FROM stats")
    stats = cursor.fetchall()
    conn.close()
    if stats:
        return stats[0]
    return (0, None, 0, None)


def create_subscription_keyboard(current_filters=None):
    """Создание клавиатуры для подписки"""
    if current_filters is None:
        current_filters = []

    keyboard = []
    for category in CHANNELS.keys():
        emoji = "✅" if category in current_filters else "◻️"
        keyboard.append([InlineKeyboardButton(f"{emoji} {category.capitalize()}", callback_data=f"toggle_{category}")])

    keyboard.append([InlineKeyboardButton("🚀 Подписаться", callback_data="subscribe_confirm")])
    keyboard.append([InlineKeyboardButton("🗑️ Отписаться от всех", callback_data="unsubscribe_all")])

    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы"""
    welcome_message = (
        "👋 Добро пожаловать в бот новостей!\n\n"
        "📌 Выберите интересующие темы и нажмите 'Подписаться':\n\n"
        "Доступные категории:"
    )

    chat_id = update.effective_chat.id
    current_filters = get_subscriber_filters(chat_id)

    # Сохраняем текущий выбор пользователя
    user_selections[chat_id] = current_filters.copy()

    keyboard = create_subscription_keyboard(current_filters)

    await update.message.reply_text(welcome_message, reply_markup=keyboard, parse_mode="HTML")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback-кнопок"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data

    # Инициализируем выбор пользователя, если его еще нет
    if chat_id not in user_selections:
        user_selections[chat_id] = get_subscriber_filters(chat_id).copy()

    if data.startswith("toggle_"):
        # Переключение категории
        category = data.replace("toggle_", "")

        if category in user_selections[chat_id]:
            user_selections[chat_id].remove(category)
        else:
            user_selections[chat_id].append(category)

        # Обновляем сообщение с новым состоянием кнопок
        keyboard = create_subscription_keyboard(user_selections[chat_id])
        await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data == "subscribe_confirm":
        # Подтверждение подписки
        current_filters = user_selections.get(chat_id, [])

        if not current_filters:
            await query.message.reply_text("❌ Пожалуйста, выберите хотя бы одну категорию!")
            return

        user = query.from_user
        await add_subscriber(
            chat_id,
            user.username,
            user.first_name,
            user.last_name,
            current_filters
        )

        links_msg = "\n".join([
            f"🔹 {filter_name.capitalize()}: {CHANNELS[filter_name]['link']}"
            for filter_name in current_filters
        ])

        response_message = (
            f"✅ Вы успешно подписались на новости по темам: {', '.join(current_filters)}\n\n"
            f"📰 Читайте новости в следующих каналах:\n{links_msg}\n\n"
            "🔔 Новые новости будут публиковаться в этих каналах автоматически!"
        )

        await query.message.reply_text(response_message)
        update_stats(subscribers_count=len(get_active_subscribers()))

        # Очищаем временные данные
        if chat_id in user_selections:
            del user_selections[chat_id]

    elif data == "unsubscribe_all":
        # Отписка от всех категорий
        await remove_subscriber(chat_id)
        await query.message.reply_text(
            "❌ Вы отписались от всех новостей.\n"
            "Чтобы подписаться снова, используйте /start"
        )
        update_stats(subscribers_count=len(get_active_subscribers()))

        # Очищаем временные данные
        if chat_id in user_selections:
            del user_selections[chat_id]


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда подписки (альтернатива через команду)"""
    chat_id = update.effective_chat.id
    current_filters = get_subscriber_filters(chat_id)

    # Сохраняем текущий выбор пользователя
    user_selections[chat_id] = current_filters.copy()

    keyboard = create_subscription_keyboard(current_filters)

    await update.message.reply_text(
        "📋 Выберите интересующие категории:",
        reply_markup=keyboard
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда отписки"""
    chat_id = update.effective_chat.id
    await remove_subscriber(chat_id)
    await update.message.reply_text(
        "❌ Вы отписались от всех новостей.\n"
        "Чтобы подписаться снова, используйте /start"
    )
    update_stats(subscribers_count=len(get_active_subscribers()))

    # Очищаем временные данные
    if chat_id in user_selections:
        del user_selections[chat_id]


async def my_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущие фильтры пользователя"""
    chat_id = update.effective_chat.id
    current_filters = get_subscriber_filters(chat_id)

    if current_filters:
        filters_list = "\n".join([f"• {f.capitalize()}" for f in current_filters])
        message = f"📋 Ваши текущие подписки:\n\n{filters_list}\n\nИзменить: /subscribe"
    else:
        message = "❌ Вы не подписаны ни на одну категорию.\nИспользуйте /subscribe для выбора категорий"

    await update.message.reply_text(message)


async def filters_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда просмотра всех доступных фильтров"""
    filters_list = "\n".join([f"• {key.capitalize()}" for key in CHANNELS.keys()])
    message = (
        f"📋 Доступные категории новостей:\n\n{filters_list}\n\n"
        f"Используйте /subscribe для выбора категорий"
    )
    await update.message.reply_text(message)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда просмотра статистики"""
    stats_data = get_stats()
    if stats_data:
        news_count, last_check, subscribers_count, last_cleanup = stats_data
        last_check_str = last_check if last_check else "никогда"
        last_cleanup_str = last_cleanup if last_cleanup else "никогда"

        message = (
            "📊 Статистика бота:\n\n"
            f"📰 Отправлено новостей: {news_count}\n"
            f"👥 Активных подписчиков: {subscribers_count}\n"
            f"⏰ Последняя проверка: {last_check_str}\n"
            f"🧹 Последняя очистка: {last_cleanup_str}\n"
            f"🗑️ Новости хранятся: {NEWS_RETENTION_DAYS} дней"
        )
    else:
        message = "📊 Статистика недоступна"

    await update.message.reply_text(message)


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная команда очистки базы данных"""
    deleted_count = cleanup_old_news()
    await update.message.reply_text(
        f"🧹 Очистка базы данных завершена!\n"
        f"Удалено старых новостей: {deleted_count}\n"
        f"Новости хранятся: {NEWS_RETENTION_DAYS} дней"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда помощи"""
    help_text = (
        "🤖 Доступные команды:\n\n"
        "/start - Начало работы с ботом\n"
        "/subscribe - Выбрать категории для подписки\n"
        "/myfilters - Мои текущие подписки\n"
        "/unsubscribe - Отписаться от всех новостей\n"
        "/filters - Показать доступные категории\n"
        "/stats - Показать статистику\n"
        "/help - Показать справку\n\n"
        "📰 После подписки вы получите ссылки на тематические каналы."
    )
    await update.message.reply_text(help_text)


async def fetch_rss_entries(url):
    """Асинхронное получение RSS записей"""
    loop = asyncio.get_event_loop()
    try:
        feed = await loop.run_in_executor(None, feedparser.parse, url)
        return feed.entries
    except Exception as e:
        logger.error(f"Ошибка при получении RSS ({url}): {e}")
        return []


async def fetch_all_rss_entries(urls):
    """Получение новостей из всех RSS-каналов"""
    all_entries = []
    for url in urls:
        logger.info(f"📡 Получение новостей из: {url}")
        entries = await fetch_rss_entries(url)
        if entries:
            all_entries.extend(entries)
            logger.info(f"✅ Получено {len(entries)} новостей из {url}")
        else:
            logger.warning(f"⚠️ Не удалось получить новости из {url}")
    return all_entries


def check_filter_match(text, filter_name):
    """Проверка соответствия текста фильтру с использованием синонимов"""
    text_lower = text.lower()

    if filter_name.lower() in text_lower:
        return True

    if filter_name in SYNONYMS:
        for synonym in SYNONYMS[filter_name]:
            if synonym in text_lower:
                return True

    return False


def extract_media_from_entry(entry):
    """Извлечение медиа (фото и видео) из RSS записи"""
    photo_url = None
    video_url = None

    # Проверяем медиа контент
    if hasattr(entry, 'media_content'):
        for media in entry.media_content:
            if hasattr(media, 'type'):
                if 'image' in media.type:
                    photo_url = media.get('url')
                elif 'video' in media.type:
                    video_url = media.get('url')

    # Проверяем вложения
    if hasattr(entry, 'enclosures'):
        for enclosure in entry.enclosures:
            if hasattr(enclosure, 'type'):
                if 'image' in enclosure.type:
                    photo_url = enclosure.get('url')
                elif 'video' in enclosure.type:
                    video_url = enclosure.get('url')

    # Проверяем дополнительные поля
    if not photo_url and hasattr(entry, 'links'):
        for link in entry.links:
            if hasattr(link, 'type') and 'image' in link.type:
                photo_url = link.get('href')

    return photo_url, video_url


async def send_news_to_channels(application, entries):
    """Отправка новостей в каналы с поддержкой фото и видео"""
    sent_count = 0

    for entry in entries:
        link = entry.get('link', '')
        title = entry.get('title', '')
        summary = entry.get('summary', '')
        published = entry.get('published', '')

        if not link or not title:
            continue

        if is_news_sent(link):
            continue

        clean_title = clean_html(title)
        clean_summary = clean_html(summary)

        max_summary_length = 1500
        if len(clean_summary) > max_summary_length:
            clean_summary = clean_summary[:max_summary_length] + "..."

        message = (
            f"📰 <b>{clean_title}</b>\n\n"
            f"{clean_summary}\n\n"
            f"🔗 <a href='{link}'>Подробнее</a>"
        )

        # Извлекаем медиа (фото и видео)
        photo_url, video_url = extract_media_from_entry(entry)

        full_text = f"{clean_title} {clean_summary}".lower()

        for filter_name, channel_info in CHANNELS.items():
            if check_filter_match(full_text, filter_name):
                try:
                    if video_url:
                        # Отправляем видео с описанием
                        await application.bot.send_video(
                            chat_id=channel_info['chat_id'],
                            video=video_url,
                            caption=message,
                            parse_mode=ParseMode.HTML
                        )
                        logger.info(f"📹 Отправлено видео в канал {filter_name}")
                    elif photo_url:
                        # Отправляем фото с описанием
                        await application.bot.send_photo(
                            chat_id=channel_info['chat_id'],
                            photo=photo_url,
                            caption=message,
                            parse_mode=ParseMode.HTML
                        )
                        logger.info(f"📸 Отправлено фото в канал {filter_name}")
                    else:
                        # Отправляем просто текст
                        await application.bot.send_message(
                            chat_id=channel_info['chat_id'],
                            text=message,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True
                        )
                        logger.info(f"📝 Отправлен текст в канал {filter_name}")

                    sent_count += 1
                    await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"❌ Ошибка отправки в канал {channel_info['chat_id']}: {e}")

        if sent_count > 0:
            mark_news_as_sent(link, title, published)

    return sent_count


async def news_checker_job(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка новостей"""
    logger.info("🔍 Проверка новых новостей...")

    try:
        entries = await fetch_all_rss_entries(RSS_FEED_URLS)
        if entries:
            sent_count = await send_news_to_channels(context.application, entries)
            if sent_count > 0:
                update_stats(news_count=sent_count)
                logger.info(f"✅ Отправлено {sent_count} новых новостей")
            else:
                logger.info("ℹ️ Новых новостей не найдено")
        else:
            logger.warning("⚠️ Не удалось получить новости из RSS-каналов")

    except Exception as e:
        logger.error(f"❌ Ошибка в news_checker_job: {e}")


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая очистка старых новостей"""
    logger.info("🧹 Запуск автоматической очистка старых новостей...")
    deleted_count = cleanup_old_news()
    if deleted_count > 0:
        logger.info(f"✅ Автоматически удалено {deleted_count} старых новостей")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка при обработке обновления: {context.error}")


def main():
    """Основная функция"""
    init_db()

    application = Application.builder().token(TOKEN).build()
    application.add_error_handler(error_handler)

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("myfilters", my_filters))
    application.add_handler(CommandHandler("filters", filters_list))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("cleanup", cleanup_command))
    application.add_handler(CommandHandler("help", help_command))

    # Обработчик callback-кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))

    update_stats(subscribers_count=len(get_active_subscribers()))

    application.job_queue.run_repeating(news_checker_job, interval=CHECK_INTERVAL, first=10)
    application.job_queue.run_repeating(cleanup_job, interval=CLEANUP_INTERVAL, first=60)

    logger.info("🚀 Бот запущен и готов к работе!")
    logger.info(f"📊 Активных подписчиков: {len(get_active_subscribers())}")
    logger.info(f"📡 Мониторинг RSS-каналов: {len(RSS_FEED_URLS)}")
    logger.info(f"🧹 Очистка старых новостей каждые: {CLEANUP_INTERVAL / 3600} часов")
    logger.info(f"🗑️ Хранение новостей: {NEWS_RETENTION_DAYS} дней")

    application.run_polling()


if __name__ == "__main__":
    main()
