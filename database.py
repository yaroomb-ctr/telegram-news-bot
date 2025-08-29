import aiosqlite
import os
from datetime import datetime

DATABASE_PATH = "bot_database.db"


async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                subscribe_date TEXT,
                filters TEXT
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS sent_entries (
                entry_id TEXT PRIMARY KEY,
                title TEXT,
                link TEXT,
                sent_date TEXT
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY,
                sent_count INTEGER DEFAULT 0,
                last_update TEXT
            )
        ''')

        # Инициализация статистики
        await db.execute('''
            INSERT OR IGNORE INTO statistics (id, sent_count, last_update)
            VALUES (1, 0, ?)
        ''', (datetime.now().isoformat(),))

        await db.commit()


async def add_subscriber(chat_id: int, username: str = None, first_name: str = None,
                         last_name: str = None, filters: str = None) -> bool:
    """Добавление подписчика"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO subscribers 
                (chat_id, username, first_name, last_name, subscribe_date, filters)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ''', (chat_id, username, first_name, last_name, filters))
            await db.commit()
            return True
    except Exception as e:
        print(f"Ошибка добавления подписчика: {e}")
        return False


async def remove_subscriber(chat_id: int) -> bool:
    """Удаление подписчика"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute('DELETE FROM subscribers WHERE chat_id = ?', (chat_id,))
            await db.commit()
            return True
    except Exception:
        return False


async def get_all_subscribers():
    """Получение всех подписчиков с фильтрами"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute('SELECT chat_id, username, first_name, last_name, filters FROM subscribers')
            return await cursor.fetchall()
    except Exception:
        return []


async def is_subscriber(chat_id: int) -> bool:
    """Проверка, является ли пользователь подписчиком"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute('SELECT 1 FROM subscribers WHERE chat_id = ?', (chat_id,))
            result = await cursor.fetchone()
            return result is not None
    except Exception:
        return False


async def mark_entry_as_sent(entry_id: str, title: str, link: str) -> bool:
    """Пометить запись как отправленную"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute('''
                INSERT OR IGNORE INTO sent_entries (entry_id, title, link, sent_date)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (entry_id, title, link))
            await db.commit()
            return True
    except Exception:
        return False


async def is_entry_sent(entry_id: str) -> bool:
    """Проверить, была ли запись отправлена"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute('SELECT 1 FROM sent_entries WHERE entry_id = ?', (entry_id,))
            result = await cursor.fetchone()
            return result is not None
    except Exception:
        return False


async def increment_sent_count(count: int = 1):
    """Увеличить счетчик отправленных новостей"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute('''
                UPDATE statistics 
                SET sent_count = sent_count + ?, last_update = CURRENT_TIMESTAMP
                WHERE id = 1
            ''', (count,))
            await db.commit()
    except Exception:
        pass


async def get_statistics():
    """Получить статистику"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute('SELECT sent_count, last_update FROM statistics WHERE id = 1')
            result = await cursor.fetchone()
            return result if result else (0, None)
    except Exception:
        return (0, None)
