import sqlite3, time, threading, requests, io, os, logging, telebot, textwrap, html, json
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from secrets import choice
from collections import Counter
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

pathh = '' # "/data/"
BOT_TOKEN = '8878267491:AAEZ0FeKe8sxELvdd1nXmoBpjyZLDMj03Qo'
ADMIN_ID = 8431984238
LOG_GROUP_ID = ADMIN_ID #-1002276939424
DUMP_CHAT_ID = -1004329704422 #-1003236275508
EDIT_LOG_GROUP_ID = -1004401223207 #-1003898318342
API_KEY_FILE = f'{pathh}api_key.txt'
V2RAY_PROXY_FILE = f'{pathh}v2ray_proxy.txt'
GROQ_API_KEY = ""
DEFAULT_API_URL = "https://track.mipoh.ru"
RESOURCE_DIR = pathh
DB_PATH = f'{pathh}users.db'
AI_DB_PATH = f'{pathh}ai_config.db'
AI_DELAY = 5
OWNER_TIMEOUT = 0

CHROME_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
]

V2RAY_PROXY = None
v2ray_proxy_lock = threading.Lock()

pending_chats = {}
pending_chats_lock = threading.Lock()
bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(level=logging.INFO)

sc_start_times = {}
sc_start_times_lock = threading.Lock()
last_owner_activity = 0
last_owner_activity_lock = threading.Lock()
current_track_state = None
current_proxy_index = 0
current_proxy_index_lock = threading.Lock()
chat_history_cache = {}
cache_lock = threading.Lock()
CACHE_TTL = 300
stolen_file_ids = set()
stolen_file_ids_lock = threading.Lock()
pending_stolen_messages = {}
pending_stolen_lock = threading.Lock()

ALLOWED_TIMER_COLUMNS = {'ai_delay', 'owner_timeout'}


def load_api_key_from_file():
    global GROQ_API_KEY
    try:
        with open(API_KEY_FILE, 'r') as f:
            key = f.read().strip()
            if key:
                GROQ_API_KEY = key
            else:
                save_api_key_to_file(GROQ_API_KEY)
                log_to_group(f"⚠️ Файл {API_KEY_FILE} был пуст. Записан ключ по умолчанию.")
    except FileNotFoundError:
        save_api_key_to_file(GROQ_API_KEY)
        log_to_group(f"⚠️ Файл {API_KEY_FILE} не найден. Создан новый с ключом по умолчанию.")


def save_api_key_to_file(new_key):
    try:
        with open(API_KEY_FILE, 'w') as f:
            f.write(new_key)
    except Exception as e:
        log_to_group(f"❌ Не удалось сохранить ключ API в файл: {e}")


def extract_proxy_from_v2ray_json(raw_json: str):
    try:
        cfg = json.loads(raw_json)
        inbounds = cfg.get('inbounds', [])
        for proto, prefix in [('http', 'http'), ('socks', 'socks5')]:
            for inb in inbounds:
                if inb.get('protocol') == proto:
                    host = inb.get('listen', '127.0.0.1') or '127.0.0.1'
                    port = inb.get('port')
                    if port:
                        return f"{prefix}://{host}:{port}"
        return None
    except Exception as e:
        return None


def save_v2ray_proxy_to_file(proxy_url: str):
    try:
        with open(V2RAY_PROXY_FILE, 'w') as f:
            f.write(proxy_url)
    except Exception as e:
        log_to_group(f"❌ Не удалось сохранить V2Ray прокси: {e}")


def load_v2ray_proxy_from_file():
    global V2RAY_PROXY
    try:
        with open(V2RAY_PROXY_FILE, 'r') as f:
            val = f.read().strip()
            if val:
                with v2ray_proxy_lock:
                    V2RAY_PROXY = val
                log_to_group(f"🔌 V2Ray прокси загружен: <code>{val}</code>")
    except FileNotFoundError:
        pass
    except Exception as e:
        log_to_group(f"❌ Ошибка загрузки V2Ray прокси: {e}")


def db_op(query, params=(), fetchone=False, fetchall=False):
    try:
        with sqlite3.connect(DB_PATH, timeout=15) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetchone: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            conn.commit()
    except Exception as e:
        log_to_group(f"Ошибка БД: {str(e)}")
        return None


def ai_db_op(query, params=(), fetchone=False, fetchall=False):
    try:
        with sqlite3.connect(AI_DB_PATH, timeout=15) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetchone: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            conn.commit()
    except Exception as e:
        log_to_group(f"Ошибка AI БД: {str(e)}")
        return None


def init_dbs():
    db_op("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        ym_token TEXT,
        sc_token TEXT,
        is_playing INTEGER DEFAULT 1,
        card_style TEXT DEFAULT 'standard',
        main_service TEXT DEFAULT 'YandexMusic',
        biz_conn_id TEXT,
        last_track_id TEXT,
        original_bio TEXT,
        bio_format TEXT DEFAULT '🎶 {track} - {artists}',
        last_track_start_time REAL DEFAULT 0,
        is_bio_reverted INTEGER DEFAULT 0,
        is_auto_detect INTEGER DEFAULT 0,
        last_active_time REAL DEFAULT 0,
        spy_mode INTEGER DEFAULT 0,
        ai_delay INTEGER DEFAULT 5,
        owner_timeout INTEGER DEFAULT 0,
        ai_enabled INTEGER DEFAULT 1
    )""")

    db_op("""CREATE TABLE IF NOT EXISTS business_messages (
        unique_id TEXT PRIMARY KEY,
        owner_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER,
        sender_id INTEGER,
        sender_name TEXT,
        text TEXT,
        timestamp INTEGER,
        dump_message_id INTEGER
    )""")

    db_op("""CREATE TABLE IF NOT EXISTS full_chat_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    db_op("CREATE INDEX IF NOT EXISTS idx_chat_timestamp ON full_chat_log(chat_id, timestamp DESC)")
    db_op("CREATE INDEX IF NOT EXISTS idx_timestamp ON full_chat_log(timestamp)")
    db_op("CREATE INDEX IF NOT EXISTS idx_biz_timestamp ON business_messages(timestamp)")

    db_op("""CREATE TABLE IF NOT EXISTS user_topics (
        user_id  INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        thread_id INTEGER NOT NULL,
        user_label TEXT,
        PRIMARY KEY (user_id, group_id)
    )""")

    ai_db_op('''CREATE TABLE IF NOT EXISTS ai_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        system_prompt TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1
    )''')

    db_op("""CREATE TABLE IF NOT EXISTS name_presets (
        user_id INTEGER NOT NULL,
        slot INTEGER NOT NULL,
        name TEXT NOT NULL,
        PRIMARY KEY (user_id, slot)
    )""")

    db_op("""CREATE TABLE IF NOT EXISTS kv_flags (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    db_op("""CREATE TABLE IF NOT EXISTS scheduled_names (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        date_from TEXT NOT NULL,
        date_to TEXT NOT NULL
    )""")

    ai_db_op('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        message TEXT,
        response TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    result = ai_db_op('SELECT COUNT(*) FROM ai_config WHERE is_active = 1', fetchone=True)
    if result and result[0] == 0:
        default_prompt = "Ты - помощник."
        ai_db_op('INSERT INTO ai_config (system_prompt, is_active) VALUES (?, 1)', (default_prompt,))


def migrate_db():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(users)")
            columns = [info[1] for info in cursor.fetchall()]

            if 'ai_enabled' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN ai_enabled INTEGER DEFAULT 1")
            if 'ai_delay' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN ai_delay INTEGER DEFAULT 5")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'ai_delay'")
            if 'owner_timeout' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN owner_timeout INTEGER DEFAULT 0")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'owner_timeout'")
            if 'spy_mode' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN spy_mode INTEGER DEFAULT 0")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'spy_mode'")
            if 'last_track_start_time' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN last_track_start_time REAL DEFAULT 0")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'last_track_start_time'")
            if 'is_bio_reverted' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN is_bio_reverted INTEGER DEFAULT 0")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'is_bio_reverted'")
            if 'is_auto_detect' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN is_auto_detect INTEGER DEFAULT 0")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'is_auto_detect'")
            if 'last_active_time' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN last_active_time REAL DEFAULT 0")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'last_active_time'")
            if 'name_format' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN name_format TEXT")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'name_format'")
            if 'original_name' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN original_name TEXT")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'original_name'")
            if 'name_auto_update' not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN name_auto_update INTEGER DEFAULT 0")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'name_auto_update'")

            cursor.execute("SELECT name_auto_update FROM users WHERE name_auto_update NOT IN (0, 1, 2)")
            if cursor.fetchone():
                cursor.execute("UPDATE users SET name_auto_update = 0 WHERE name_auto_update NOT IN (0, 1, 2)")
                log_to_group("🔧 Миграция: Сброшены некорректные значения name_auto_update")

            cursor.execute("PRAGMA table_info(business_messages)")
            biz_columns = [info[1] for info in cursor.fetchall()]
            if 'dump_message_id' not in biz_columns:
                cursor.execute("ALTER TABLE business_messages ADD COLUMN dump_message_id INTEGER")
                log_to_group("🔧 Миграция БД: Добавлена колонка 'dump_message_id' в business_messages")

            cursor.execute("""CREATE TABLE IF NOT EXISTS user_topics (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                user_label TEXT,
                PRIMARY KEY (user_id, group_id)
            )""")

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dump_topics'")
            if cursor.fetchone():
                cursor.execute("SELECT chat_id, thread_id, user_label FROM dump_topics")
                for row in cursor.fetchall():
                    cursor.execute("INSERT OR IGNORE INTO user_topics (user_id, group_id, thread_id, user_label) VALUES (?, ?, ?, ?)",
                                   (row[0], DUMP_CHAT_ID, row[1], row[2]))
                cursor.execute("DROP TABLE dump_topics")
                log_to_group("🔧 Миграция: dump_topics → user_topics")

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='edit_log_topics'")
            if cursor.fetchone():
                cursor.execute("SELECT chat_id, thread_id, user_label FROM edit_log_topics")
                for row in cursor.fetchall():
                    cursor.execute("INSERT OR IGNORE INTO user_topics (user_id, group_id, thread_id, user_label) VALUES (?, ?, ?, ?)",
                                   (row[0], EDIT_LOG_GROUP_ID, row[1], row[2]))
                cursor.execute("DROP TABLE edit_log_topics")
                log_to_group("🔧 Миграция: edit_log_topics → user_topics")

            conn.commit()
    except Exception as e:
        log_to_group(f"❌ Ошибка миграции БД: {e}")


def log_to_group(message):
    try:
        bot.send_message(LOG_GROUP_ID, f"{message}", parse_mode="HTML")
    except:
        pass


def format_name_with_time(name_format, dt=None):
    from datetime import datetime
    import pytz

    if not name_format:
        return None

    msk_tz = pytz.timezone('Asia/Yekaterinburg')
    now = dt if dt else datetime.now(msk_tz)

    formatted = name_format.replace('{time}', now.strftime('%H:%M'))
    formatted = formatted.replace('{date}', now.strftime('%d.%m'))
    formatted = formatted.replace('{year}', now.strftime('%Y'))
    formatted = formatted.replace('{day}', now.strftime('%d'))
    formatted = formatted.replace('{month}', now.strftime('%m'))

    return formatted[:64]


def log_action(user, action_name, details=""):
    msg = (f"<blockquote>👤 <b>User:</b> <a href='tg://user?id={user.id}'>{html.escape(user.first_name)}</a> (<code>{user.id}</code>)\n"
           f"⚡ <b>Action:</b> <code>{action_name}</code>\n"
           f"📝 <b>Details:</b> {details}</blockquote>")
    log_to_group(msg)


def log_business_message(owner_id, chat_id, message_id, sender_id, sender_name, text, dump_msg_id=None):
    uid = f"{owner_id}_{chat_id}_{message_id}"
    ts = int(time.time())
    db_op('INSERT OR REPLACE INTO business_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
          (uid, owner_id, chat_id, message_id, sender_id, sender_name, text, ts, dump_msg_id))


def get_logged_business_message(owner_id, chat_id, message_id):
    uid = f"{owner_id}_{chat_id}_{message_id}"
    return db_op('SELECT text, sender_name, dump_message_id, sender_id FROM business_messages WHERE unique_id = ?', (uid,), fetchone=True)


topic_creation_lock = threading.Lock()


def _build_topic_label(user_obj, fallback_id):
    username = getattr(user_obj, 'username', None)
    if username:
        return f"@{username} (id{getattr(user_obj, 'id', fallback_id)})"
    name = getattr(user_obj, 'first_name', '') or ''
    last = getattr(user_obj, 'last_name', None)
    if last:
        name += f" {last}"
    uid = getattr(user_obj, 'id', fallback_id)
    return f"{name.strip()} (id{uid})" if name.strip() else f"id{uid}"


def get_or_create_topic(group_id, user_id, user_obj):
    row = db_op(
        "SELECT thread_id FROM user_topics WHERE user_id = ? AND group_id = ?",
        (user_id, group_id), fetchone=True
    )
    if row:
        return row[0]

    with topic_creation_lock:
        row = db_op(
            "SELECT thread_id FROM user_topics WHERE user_id = ? AND group_id = ?",
            (user_id, group_id), fetchone=True
        )
        if row:
            return row[0]

        label = _build_topic_label(user_obj, user_id)
        try:
            topic = bot.create_forum_topic(group_id, label)
            thread_id = topic.message_thread_id
            db_op(
                "INSERT OR IGNORE INTO user_topics (user_id, group_id, thread_id, user_label) VALUES (?, ?, ?, ?)",
                (user_id, group_id, thread_id, label)
            )
            return thread_id
        except Exception as e:
            log_to_group(f"❌ Не удалось создать тему (группа {group_id}, юзер {user_id}): {e}")
            return None


def _recreate_topic(group_id, user_id, user_obj):
    db_op("DELETE FROM user_topics WHERE user_id = ? AND group_id = ?", (user_id, group_id))
    return get_or_create_topic(group_id, user_id, user_obj)


def safe_send_to_topic(group_id, user_id, user_obj, send_fn):
    thread_id = get_or_create_topic(group_id, user_id, user_obj)
    if thread_id is None:
        return None
    try:
        return send_fn(thread_id)
    except Exception as e:
        err = str(e).lower()
        if "message thread not found" in err or "thread_id" in err:
            log_to_group(f"⚠️ Тема удалена в группе {group_id}, пересоздаю для user_id={user_id}")
            thread_id = _recreate_topic(group_id, user_id, user_obj)
            if thread_id:
                try:
                    return send_fn(thread_id)
                except Exception as e2:
                    log_to_group(f"❌ Повторная отправка не удалась: {e2}")
        else:
            log_to_group(f"❌ Ошибка отправки в тему: {e}")
    return None


def get_or_create_dump_topic(user_id, user_obj):
    return get_or_create_topic(DUMP_CHAT_ID, user_id, user_obj)


def get_or_create_edit_log_topic(user_id, user_obj):
    return get_or_create_topic(EDIT_LOG_GROUP_ID, user_id, user_obj)


def dump_text_to_group(message_object, thread_id):
    try:
        sender = message_object.from_user
        is_owner = sender.id == ADMIN_ID
        sender_label = "👤 <b>Я:</b>" if is_owner else f"👤 <b>От:</b> {html.escape(sender.first_name)}"

        text = message_object.text or message_object.caption or "[пусто]"
        dump_text = f"{sender_label}\n💬 {html.escape(text)}"

        bot.send_message(DUMP_CHAT_ID, dump_text, parse_mode='HTML', message_thread_id=thread_id)
    except Exception as e:
        log_to_group(f"❌ Ошибка отправки текста в dump тему: {e}")


def get_active_ai_config():
    result = ai_db_op('SELECT system_prompt FROM ai_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1', fetchone=True)
    return result[0] if result else "Ты - помощник."


def save_ai_config(system_prompt):
    ai_db_op('UPDATE ai_config SET is_active = 0')
    ai_db_op('INSERT INTO ai_config (system_prompt, is_active) VALUES (?, 1)', (system_prompt,))


def save_ai_history(chat_id, user_id, message, response):
    ai_db_op('INSERT INTO chat_history (chat_id, user_id, message, response) VALUES (?, ?, ?, ?)',
             (chat_id, user_id, message, response))


def dump_media_to_group(message_object, owner_id, thread_id=None):
    file_id = None
    m_type = "Файл"

    if message_object.photo:
        file_id = message_object.photo[-1].file_id
        m_type = "Фото"
    elif message_object.video:
        file_id = message_object.video.file_id
        m_type = "Видео"
    elif message_object.voice:
        file_id = message_object.voice.file_id
        m_type = "Голосовое"
    elif message_object.video_note:
        file_id = message_object.video_note.file_id
        m_type = "Кружок"
    elif message_object.document:
        file_id = message_object.document.file_id
        m_type = "Документ"
    elif message_object.audio:
        file_id = message_object.audio.file_id
        m_type = "Аудио"
    elif message_object.sticker:
        file_id = message_object.sticker.file_id
        m_type = "Стикер"

    if not file_id:
        return None

    try:
        sender_name = html.escape(message_object.from_user.first_name)
        caption_text = message_object.caption or ""
        no_text_types = bool(message_object.voice or message_object.video_note or message_object.sticker)

        if no_text_types:
            dump_caption = (
                f"📦 <b>{m_type}</b>\n"
                f"👤 От: {sender_name} (<code>{message_object.from_user.id}</code>)"
            )
        else:
            dump_caption = (
                f"📦 <b>{m_type}</b>\n"
                f"👤 От: {sender_name} (<code>{message_object.from_user.id}</code>)\n"
                f"💬 Текст: {html.escape(caption_text) if caption_text else '<i>нет</i>'}"
            )

        if message_object.photo:
            sent = bot.send_photo(DUMP_CHAT_ID, file_id, caption=dump_caption, parse_mode='HTML', message_thread_id=thread_id)
        elif message_object.video:
            sent = bot.send_video(DUMP_CHAT_ID, file_id, caption=dump_caption, parse_mode='HTML', message_thread_id=thread_id)
        elif message_object.voice:
            sent = bot.send_voice(DUMP_CHAT_ID, file_id, caption=dump_caption, parse_mode='HTML', message_thread_id=thread_id)
        elif message_object.video_note:
            sent = bot.send_video_note(DUMP_CHAT_ID, file_id, message_thread_id=thread_id)
            bot.send_message(DUMP_CHAT_ID, dump_caption, parse_mode='HTML', reply_to_message_id=sent.message_id, message_thread_id=thread_id)
        elif message_object.document:
            sent = bot.send_document(DUMP_CHAT_ID, file_id, caption=dump_caption, parse_mode='HTML', message_thread_id=thread_id)
        elif message_object.audio:
            sent = bot.send_audio(DUMP_CHAT_ID, file_id, caption=dump_caption, parse_mode='HTML', message_thread_id=thread_id)
        elif message_object.sticker:
            sent = bot.send_sticker(DUMP_CHAT_ID, file_id, message_thread_id=thread_id)
            bot.send_message(DUMP_CHAT_ID, dump_caption, parse_mode='HTML', reply_to_message_id=sent.message_id, message_thread_id=thread_id)

        return sent.message_id
    except Exception as e:
        try:
            bot.send_message(owner_id, f"❌ Ошибка сохранения в группу: {e}", parse_mode='HTML')
        except:
            pass
        return None


def _keep_media_keyboard(msg_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Оставить", callback_data=f"keep_media:{msg_id}"))
    return kb


def _expire_stolen_media(owner_id, media_msg_id, caption_msg_id, file_id):
    with pending_stolen_lock:
        if media_msg_id not in pending_stolen_messages:
            return
        del pending_stolen_messages[media_msg_id]

    try:
        bot.forward_message(DUMP_CHAT_ID, owner_id, media_msg_id)
    except Exception as e:
        log_to_group(f"⚠️ Не удалось переслать медиа в мусор: {e}")

    for mid in {media_msg_id, caption_msg_id}:
        if mid:
            try:
                bot.delete_message(owner_id, mid)
            except:
                pass

    with stolen_file_ids_lock:
        stolen_file_ids.discard(file_id)


def _schedule_media_expiry(owner_id, media_msg_id, caption_msg_id, file_id):
    t = threading.Timer(60, _expire_stolen_media, args=(owner_id, media_msg_id, caption_msg_id, file_id))
    t.daemon = True
    with pending_stolen_lock:
        pending_stolen_messages[media_msg_id] = {'timer': t, 'owner_id': owner_id, 'caption_msg_id': caption_msg_id, 'file_id': file_id}
    t.start()


def steal_media(message_object, owner_id):
    file_id = None
    ext = "bin"
    m_type = "Файл"
    is_self_destructing = False

    if message_object.photo:
        file_id = message_object.photo[-1].file_id
        ext = "jpg"
        m_type = "Фото"
        if hasattr(message_object, 'has_media_spoiler'):
            is_self_destructing = True
    elif message_object.video:
        file_id = message_object.video.file_id
        ext = "mp4"
        m_type = "Видео"
        if hasattr(message_object, 'has_media_spoiler'):
            is_self_destructing = True
    elif message_object.voice:
        file_id = message_object.voice.file_id
        ext = "ogg"
        m_type = "Голосовое"
    elif message_object.video_note:
        file_id = message_object.video_note.file_id
        ext = "mp4"
        m_type = "Кружок"
    elif message_object.document:
        file_id = message_object.document.file_id
        ext = message_object.document.file_name.split('.')[-1] if '.' in message_object.document.file_name else "doc"
        m_type = "Документ"

    if not file_id:
        return False

    with stolen_file_ids_lock:
        if file_id in stolen_file_ids:
            return False

    status = bot.send_message(owner_id, f"🥷 <b>Краду {m_type}...</b>", parse_mode='HTML')

    if not is_self_destructing:
        try:
            caption = (
                f"🔓 <b>СКОПИРОВАНО</b>\n"
                f"👤 <b>От:</b> {html.escape(message_object.from_user.first_name)}\n"
            )

            media_msg = None
            caption_msg = None

            if message_object.photo:
                media_msg = bot.send_photo(owner_id, file_id, caption=caption, parse_mode='HTML')
            elif message_object.video:
                media_msg = bot.send_video(owner_id, file_id, caption=caption, parse_mode='HTML')
            elif message_object.voice:
                media_msg = bot.send_voice(owner_id, file_id, caption=caption, parse_mode='HTML')
            elif message_object.video_note:
                media_msg = bot.send_video_note(owner_id, file_id)
                caption_msg = bot.send_message(owner_id, caption, parse_mode='HTML')
            elif message_object.document:
                media_msg = bot.send_document(owner_id, file_id, caption=caption, parse_mode='HTML')

            if media_msg:
                kb = _keep_media_keyboard(media_msg.message_id)
                try:
                    if caption_msg:
                        bot.edit_message_reply_markup(owner_id, caption_msg.message_id, reply_markup=kb)
                    else:
                        bot.edit_message_reply_markup(owner_id, media_msg.message_id, reply_markup=kb)
                except:
                    pass

                bot.delete_message(owner_id, status.message_id)
                with stolen_file_ids_lock:
                    stolen_file_ids.add(file_id)

                _schedule_media_expiry(
                    owner_id,
                    media_msg.message_id,
                    caption_msg.message_id if caption_msg else None,
                    file_id
                )
                return True

        except Exception as fast_error:
            error_text = str(fast_error)
            if "SelfDestructing" in error_text:
                log_to_group(f"⚠️ Обнаружено одноразовое медиа, скачиваю...")
            else:
                log_to_group(f"⚠️ Быстрый метод не сработал: {fast_error}. Пробую скачать...")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            bot.edit_message_text(
                f"🥷 <b>Скачиваю {m_type}... ({attempt + 1}/{max_retries})</b>",
                owner_id,
                status.message_id,
                parse_mode='HTML'
            )

            file_info = bot.get_file(file_id)
            download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"

            response = requests.get(download_url, stream=True, timeout=(10, 10))

            if response.status_code != 200:
                raise Exception(f"API Error: {response.status_code}")

            file_data = io.BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file_data.write(chunk)

            file_data.seek(0)
            file_data.name = f"stolen_{int(time.time())}.{ext}"

            caption = (
                f"🔓 <b>СКОПИРОВАНО</b>\n"
                f"👤 <b>От:</b> {html.escape(message_object.from_user.first_name)}\n"
            )

            media_msg = bot.send_document(owner_id, file_data, caption=caption, parse_mode='HTML', timeout=90)
            try:
                bot.edit_message_reply_markup(owner_id, media_msg.message_id,
                                              reply_markup=_keep_media_keyboard(media_msg.message_id))
            except:
                pass

            bot.delete_message(owner_id, status.message_id)
            with stolen_file_ids_lock:
                stolen_file_ids.add(file_id)

            _schedule_media_expiry(owner_id, media_msg.message_id, None, file_id)
            return True

        except (requests.exceptions.Timeout, TimeoutError, ConnectionError) as e:
            if attempt < max_retries - 1:
                log_to_group(f"⚠️ Попытка {attempt + 1} не удалась: {type(e).__name__}. Повторяю...")
                time.sleep(3)
                continue
            else:
                error_msg = f"❌ Не удалось скачать {m_type} после {max_retries} попыток.\n\n💡 Попробуйте:\n• Проверить интернет\n• Повторить позже\n• Файл может быть слишком большим"
                bot.edit_message_text(error_msg, owner_id, status.message_id)
                log_to_group(f"❌ Таймаут при краже медиа: {e}")
                return False

        except Exception as e:
            error_msg = str(e)
            if len(error_msg) > 150:
                error_msg = error_msg[:150] + "..."
            bot.edit_message_text(f"❌ Ошибка: {error_msg}", owner_id, status.message_id)
            log_to_group(f"❌ Ошибка кражи медиа: {e}")
            return False

    return False


@bot.callback_query_handler(func=lambda c: c.data.startswith('keep_media:'))
def handle_keep_media(c):
    try:
        media_msg_id = int(c.data.split(':')[1])
    except:
        bot.answer_callback_query(c.id)
        return

    with pending_stolen_lock:
        entry = pending_stolen_messages.pop(media_msg_id, None)

    if entry:
        entry['timer'].cancel()
        try:
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
        except:
            pass
        try:
            old_text = c.message.caption or c.message.text or ""
            new_text = "\n".join(l for l in old_text.splitlines() if "уйдёт в мусор" not in l)
            if c.message.caption is not None:
                bot.edit_message_caption(c.message.chat.id, c.message.message_id,
                                         caption=new_text, parse_mode='HTML')
            else:
                bot.edit_message_text(new_text, c.message.chat.id, c.message.message_id, parse_mode='HTML')
        except:
            pass
        bot.answer_callback_query(c.id, "✅ Медиа оставлено!", show_alert=False)
    else:
        bot.answer_callback_query(c.id, "⚠️ Уже удалено или истекло", show_alert=True)


PROXIES_LIST = []
proxies_list_lock = threading.Lock()

PROXY_SOURCES = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&timeout=10000&country=all&format=text&limit=300",
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]

PROXY_TEST_TIMEOUT = 8
MAX_RAW_PROXIES = 300    # сколько берём на проверку (не держим 3000 в памяти)
MAX_WORKING_PROXIES = 10  # держим минимум — каждый ~200 байт, не страшно
MAX_TEST_THREADS = 10     # 10 потоков × ~5 МБ стек = ~50 МБ пик, терпимо
PROXY_REFRESH_INTERVAL = 3600  # раз в час — реже = меньше пиков RAM
_proxy_refresh_event = threading.Event()


def _fetch_raw_proxies() -> list:
    raw = []
    seen = set()
    for url in PROXY_SOURCES:
        if len(raw) >= MAX_RAW_PROXIES:
            break
        try:
            r = requests.get(url, timeout=15, verify=False)
            if r.status_code != 200 or not r.text.strip():
                print(f"[PROXY] Источник недоступен ({r.status_code}): {url}")
                continue
            count_before = len(raw)
            for line in r.text.splitlines():
                line = line.strip()
                if line and ':' in line and not line.startswith('#') and line not in seen:
                    seen.add(line)
                    raw.append(line)
                if len(raw) >= MAX_RAW_PROXIES:
                    break
            print(f"[PROXY] Источник OK: {url} -> +{len(raw) - count_before} прокси")
        except Exception as e:
            print(f"[PROXY] Ошибка источника {url}: {e}")
    return raw


def _test_proxy(proxy_addr: str) -> bool:
    proxy_url = f'http://{proxy_addr}'
    try:
        r = requests.get(
            "https://api-v2.soundcloud.com/me/play-history/tracks",
            params={'limit': '1', 'client_id': '1HxML01xkzWgtHfBreaeZfpANMe3ADjb'},
            headers={'User-Agent': 'Mozilla/5.0'},
            proxies={'http': proxy_url, 'https': proxy_url},
            timeout=PROXY_TEST_TIMEOUT,
            verify=False,
        )
        return r.status_code in (200, 401, 403)
    except Exception:
        return False


def refresh_free_proxies(notify=False):
    global current_proxy_index

    raw = _fetch_raw_proxies()
    if not raw:
        if notify:
            log_to_group("❌ Не удалось скачать прокси.")
        return 0

    working = []
    lock = threading.Lock()
    sem = threading.Semaphore(MAX_TEST_THREADS)

    def check(addr):
        with sem:
            if len(working) >= MAX_WORKING_PROXIES:
                return
            if _test_proxy(addr):
                with lock:
                    if len(working) < MAX_WORKING_PROXIES:
                        working.append(f'http://{addr}')

    threads = [threading.Thread(target=check, args=(a,), daemon=True) for a in raw]
    for t in threads:
        t.start()
    deadline = time.time() + 120
    for t in threads:
        left = deadline - time.time()
        if left <= 0:
            break
        t.join(timeout=left)

    del threads, raw

    if working:
        with proxies_list_lock:
            PROXIES_LIST.clear()
            PROXIES_LIST.extend(working)
            current_proxy_index = 0
    else:
        msg = "⚠️ Рабочих прокси не найдено."

    if notify:
        try:
            log_to_group(msg)
        except Exception:
            pass
    return len(working)


def proxy_refresher_thread():
    refresh_free_proxies(notify=True)
    while True:
        _proxy_refresh_event.wait(timeout=PROXY_REFRESH_INTERVAL)
        _proxy_refresh_event.clear()
        refresh_free_proxies(notify=True)


def get_ai_response_sync(message_text, system_prompt, conversation_history=None):
    if conversation_history is None:
        conversation_history = []

    global current_proxy_index
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    messages_payload = [{"role": "system", "content": system_prompt}]
    messages_payload.extend(conversation_history)
    messages_payload.append({"role": "user", "content": message_text})

    data = {
        "model": "llama-3.1-8b-instant",
        "messages": messages_payload,
        "temperature": 0.7,
        "max_tokens": 1024
    }

    with v2ray_proxy_lock:
        v2_proxy = V2RAY_PROXY
    if v2_proxy:
        try:
            response = requests.post(url, headers=headers, json=data, timeout=12,
                                     proxies={'http': v2_proxy, 'https': v2_proxy})
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            elif response.status_code == 429:
                return "Ошибка: слишком много запросов. Подождите минуту."
            else:
                log_to_group(f"⚠️ V2Ray прокси: ошибка API {response.status_code}. Фолбэк на обычные прокси.")
        except Exception as e:
            log_to_group(f"⚠️ V2Ray прокси недоступен ({e}). Фолбэк на обычные прокси.")

    with proxies_list_lock:
        attempts = len(PROXIES_LIST)
    if attempts == 0:
        log_to_group("⚠️ PROXIES_LIST пуст, жду обновления...")
        return None

    for _ in range(attempts):
        with proxies_list_lock:
            if not PROXIES_LIST:
                break
            proxy_address = PROXIES_LIST[current_proxy_index % len(PROXIES_LIST)]

        proxies = {
            'http': proxy_address,
            'https': proxy_address
        }

        try:
            response = requests.post(url, headers=headers, json=data, timeout=12, proxies=proxies)
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content']
            elif response.status_code == 429:
                return "Ошибка: слишком много запросов. Подождите минуту."
            else:
                with current_proxy_index_lock:
                    log_to_group(f"⚠️ Ошибка API {response.status_code} на прокси {current_proxy_index}. Меняю...")
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError):
            pass
        except Exception as e:
            log_to_group(f"‼️ Критическая ошибка: {e}")

        with current_proxy_index_lock:
            with proxies_list_lock:
                if PROXIES_LIST:
                    current_proxy_index = (current_proxy_index + 1) % len(PROXIES_LIST)

    log_to_group("❌ Все прокси сдохли.")
    return None


def load_config_from_db():
    global AI_DELAY, OWNER_TIMEOUT
    config = db_op("SELECT ai_delay, owner_timeout FROM users WHERE user_id = ?", (ADMIN_ID,), fetchone=True)
    if config:
        AI_DELAY = config[0]
        OWNER_TIMEOUT = config[1]
        log_to_group(f"⚙️ Конфигурация загружена: AI Delay={AI_DELAY}s, Owner Timeout={OWNER_TIMEOUT}s")


def auto_cleanup_old_messages():
    try:
        month_ago = time.time() - (30 * 24 * 60 * 60)
        db_op("DELETE FROM full_chat_log WHERE timestamp < datetime(?, 'unixepoch')", (month_ago,))
        db_op("DELETE FROM business_messages WHERE timestamp < ?", (int(month_ago),))
    except Exception as e:
        log_to_group(f"❌ Ошибка автоочистки: {e}")


def cleanup_sc_start_times():
    try:
        with sc_start_times_lock:
            now = time.time()
            to_delete = [uid for uid, v in sc_start_times.items() if now - v.get('last_seen', now) > 86400]
            for uid in to_delete:
                del sc_start_times[uid]
    except Exception as e:
        log_to_group(f"❌ Ошибка очистки sc_start_times: {e}")


def render_timer_settings(chat_id, msg_id=None):
    global AI_DELAY, OWNER_TIMEOUT
    text = (f"⏱️ <b>Настройки таймеров</b>\n\n"
            f"<b>AI Delay:</b> <code>{AI_DELAY}</code> секунд\n"
            f"<i>(Задержка перед ответом, если вы оффлайн)</i>\n\n"
            f"<b>Owner Timeout:</b> <code>{OWNER_TIMEOUT}</code> секунд\n"
            f"<i>(Время, в течение которого бот считает вас онлайн после вашего сообщения)</i>")

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Изменить AI Delay", callback_data="set_timer:ai_delay"),
        InlineKeyboardButton("Изменить Owner Timeout", callback_data="set_timer:owner_timeout")
    )
    kb.add(InlineKeyboardButton("⬅️ Назад в настройки", callback_data="open_settings"))

    try:
        if msg_id:
            bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb, parse_mode="HTML")
        else:
            bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        log_to_group(f"Ошибка рендера таймеров: {e}")


def render_nicks(chat_id, user_id, msg_id=None):
    import pytz as _pytz
    from datetime import datetime as dt_cls
    today = dt_cls.now(_pytz.timezone('Asia/Yekaterinburg')).strftime('%Y-%m-%d')

    presets = db_op(
        "SELECT slot, name FROM name_presets WHERE user_id = ? ORDER BY slot ASC",
        (user_id,), fetchall=True
    ) or []
    preset_map = {row[0]: row[1] for row in presets}

    active_sched = db_op(
        "SELECT name FROM scheduled_names WHERE user_id = ? AND date_from <= ? AND date_to >= ? ORDER BY id ASC LIMIT 1",
        (user_id, today, today), fetchone=True
    )
    sched_count = (db_op("SELECT COUNT(*) FROM scheduled_names WHERE user_id = ?", (user_id,), fetchone=True) or [0])[0]

    lines = ["👤 <b>Ники</b>\n"]
    lines.append("<b>Заготовки:</b>")
    for slot in range(1, 4):
        name = preset_map.get(slot)
        lines.append(f"  {slot}. {html.escape(name) if name else '<i>пусто</i>'}")
    lines.append("")
    lines.append(f"<b>Расписание:</b> {sched_count} записей")
    if active_sched:
        lines.append(f"<b>Сейчас активно:</b> {html.escape(active_sched[0])}")

    text = "\n".join(lines)

    kb = InlineKeyboardMarkup(row_width=3)
    # preset apply buttons
    slot_buttons = []
    for slot in range(1, 4):
        name = preset_map.get(slot)
        label = f"▶️ {name[:12]}" if name else f"▶️ слот {slot}"
        slot_buttons.append(InlineKeyboardButton(label, callback_data=f"np_apply:{slot}"))
    kb.add(*slot_buttons)
    # preset edit buttons
    edit_buttons = []
    for slot in range(1, 4):
        edit_buttons.append(InlineKeyboardButton(f"✏️ {slot}", callback_data=f"np_edit:{slot}"))
    kb.add(*edit_buttons)
    kb.add(InlineKeyboardButton("📅 Расписание", callback_data="open_nickname_schedule"))
    kb.add(InlineKeyboardButton("⬅️ Назад в настройки", callback_data="open_settings"))

    try:
        if msg_id:
            bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb, parse_mode="HTML")
        else:
            bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        log_to_group(f"Ошибка рендера ников: {e}")


def render_nickname_schedule(chat_id, user_id, msg_id=None):
    import pytz as _pytz
    from datetime import datetime as dt_cls
    today = dt_cls.now(_pytz.timezone('Asia/Yekaterinburg')).strftime('%Y-%m-%d')

    rows = db_op(
        "SELECT id, name, date_from, date_to FROM scheduled_names WHERE user_id = ? ORDER BY date_from ASC",
        (user_id,), fetchall=True
    ) or []

    if rows:
        lines = []
        for row_id, name, df, dt_val in rows:
            if today > dt_val:
                status = " <i>[истёк]</i>"
            elif df <= today <= dt_val:
                status = " ✅"
            else:
                status = ""
            # convert YYYY-MM-DD to DD.MM.YYYY for display
            df_disp = f"{df[8:10]}.{df[5:7]}.{df[0:4]}"
            dt_disp = f"{dt_val[8:10]}.{dt_val[5:7]}.{dt_val[0:4]}"
            lines.append(f"<code>#{row_id}</code> <b>{html.escape(name)}</b>\n    {df_disp} → {dt_disp}{status}")
        schedule_text = "\n".join(lines)
    else:
        schedule_text = "<i>Расписание пустое</i>"

    text = f"📅 <b>Расписание никнеймов</b>\n\n{schedule_text}\n\n<i>Добавить: введите имя и даты</i>"

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ Добавить никнейм", callback_data="ns_add"))
    for row_id, name, df, dt_val in rows:
        if today <= dt_val:  # only show delete for non-expired
            kb.add(InlineKeyboardButton(f"🗑 #{row_id} {name}", callback_data=f"ns_del:{row_id}"))
    kb.add(InlineKeyboardButton("⬅️ Назад к никам", callback_data="open_nicks"))

    try:
        if msg_id:
            bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb, parse_mode="HTML")
        else:
            bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        log_to_group(f"Ошибка рендера расписания никнеймов: {e}")


def process_new_timer_value(m, timer_type):
    global AI_DELAY, OWNER_TIMEOUT
    if timer_type not in ALLOWED_TIMER_COLUMNS:
        bot.send_message(m.chat.id, "❌ Недопустимый параметр.")
        return
    if m.text and m.text.isdigit():
        new_value = int(m.text)
        db_op(f"UPDATE users SET {timer_type} = ? WHERE user_id = ?", (new_value, ADMIN_ID))
        if timer_type == 'ai_delay':
            AI_DELAY = new_value
            bot.send_message(m.chat.id, f"✅ AI Delay обновлен: {new_value} секунд.")
        elif timer_type == 'owner_timeout':
            OWNER_TIMEOUT = new_value
            bot.send_message(m.chat.id, f"✅ Owner Timeout обновлен: {new_value} секунд.")
    else:
        bot.send_message(m.chat.id, "❌ Ошибка. Введите целое число.")


class YandexTrack:
    def __init__(self, data=None):
        self.service, self.service_name = 'yandex', 'YandexMusic'
        if data and data.get('track'):
            t = data['track']
            is_paused = data.get('paused', False)
            self.active = not is_paused
            self.track_id = str(t.get('track_id', ''))
            self.title = str(t.get('title', 'Unknown'))
            artists = t.get('artist', 'Unknown')
            self.artist = ", ".join(artists) if isinstance(artists, list) else str(artists)
            self.thumb = str(t.get('img', '')).replace("%%", "1000x1000")
            self.duration = int(t.get('duration', 0))
            self.progress = int(data.get('progress_ms', 0)) // 1000
            self.link = f"https://music.yandex.ru/track/{self.track_id}"
            self.song_link = f"https://song.link/ya/{self.track_id}"
        else:
            self.active = False


class SoundCloudTrack:
    def __init__(self, data=None, user_id=None):
        self.service, self.service_name = 'soundcloud', 'SoundCloud'
        self.active = False
        self.track_id = ''
        self.title = ''
        self.artist = ''
        self.thumb = ''
        self.duration = 0
        self.progress = 0
        self.link = ''
        self.song_link = ''

        if data and data.get('collection') and len(data['collection']) > 0:
            item = data['collection'][0]
            t = item.get('track')
            if t:
                self.track_id = str(t.get('id', ''))
                self.title = t.get('title', 'No title')
                meta = t.get('publisher_metadata')
                self.artist = meta.get('artist') if meta and meta.get('artist') else t.get('user', {}).get('username', 'Unknown Artist')

                art = t.get('artwork_url')
                if art:
                    self.thumb = art.replace('large', 't500x500')
                else:
                    user_avatar = t.get('user', {}).get('avatar_url', '')
                    self.thumb = user_avatar.replace('large', 't500x500') if user_avatar else ''

                if not self.thumb:
                    self.thumb = 'https://via.placeholder.com/500x500/1f1f1f/ffffff?text=SoundCloud'

                self.duration = int(t.get('duration', 0)) // 1000

                now = time.time()
                with sc_start_times_lock:
                    if user_id not in sc_start_times or sc_start_times[user_id]['id'] != self.track_id:
                        sc_start_times[user_id] = {'id': self.track_id, 'accumulated': 0, 'last_seen': now}
                        self.progress = 0
                    else:
                        entry = sc_start_times[user_id]
                        last_seen = entry.get('last_seen', now)
                        accumulated = entry.get('accumulated', 0)
                        # Добавляем только время с последней успешной проверки
                        # Ограничиваем дельту 60 сек — защита от длительного простоя прокси
                        delta = min(now - last_seen, 60)
                        accumulated += delta
                        sc_start_times[user_id]['accumulated'] = accumulated
                        sc_start_times[user_id]['last_seen'] = now
                        self.progress = int(accumulated)

                if self.progress > self.duration:
                    self.progress = self.duration

                if self.progress >= self.duration and self.duration > 0:
                    self.active = False
                else:
                    self.active = True

                self.link = t.get('permalink_url', '')
                self.song_link = f"https://song.link/{self.link}"


def get_cover_accent_color(image):
    img = image.copy()
    img = img.resize((50, 50))
    pixels = list(img.getdata())
    most_common = Counter(pixels).most_common(1)[0][0]
    return most_common


def adjust_color_for_readability(color):
    r, g, b = color[:3] if len(color) >= 3 else (color, color, color)
    return (max(0, r - 40), max(0, g - 40), max(0, b - 40))


def download_cover(url):
    _cover_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://soundcloud.com/',
    }
    with current_proxy_index_lock:
        base_index = current_proxy_index

    for attempt in range(len(PROXIES_LIST)):
        proxy_addr = PROXIES_LIST[(base_index + attempt) % len(PROXIES_LIST)]
        proxies = {'http': proxy_addr, 'https': proxy_addr}
        try:
            res = requests.get(url, timeout=15, verify=False, headers=_cover_headers, proxies=proxies)
            if res.status_code == 200:
                return Image.open(io.BytesIO(res.content)).convert("RGBA")
        except Exception as e:
            if attempt == len(PROXIES_LIST) - 1:
                log_to_group(f"⚠️ Не удалось загрузить обложку через все прокси: {e}")
        time.sleep(1)

    return Image.new("RGBA", (1000, 1000), (30, 30, 30, 255))


def create_card(track, style='standard'):
    if style == 'vertical':
        return create_vertical_card(track)
    else:
        return create_horizontal_card(track)


def create_horizontal_card(track):
    width, height = 1440, 600

    background_color = (18, 18, 18)
    title_text_color = (255, 255, 255)
    subtext_color = (180, 180, 180)

    try:
        cover = download_cover(track.thumb)

        background = cover.resize((width, height), Image.Resampling.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(radius=14))
        background = ImageEnhance.Brightness(background).enhance(0.3)

        card = Image.new('RGB', (width, height), background_color)
        card.paste(background.convert('RGB'), (0, 0))

        thumbnail = cover.resize((450, 450), Image.Resampling.LANCZOS)
        mask = Image.new('L', (450, 450), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 450, 450), 30, fill=255)
        thumbnail_rgba = thumbnail.copy()
        thumbnail_rgba.putalpha(mask)
        card.paste(thumbnail_rgba, (75, 75), thumbnail_rgba)

        draw = ImageDraw.Draw(card)

        f_bold = os.path.join(RESOURCE_DIR, "YSMusic-Bold.ttf")
        f_reg = os.path.join(RESOURCE_DIR, "YSText-Regular.ttf")
        if not os.path.exists(f_bold): f_bold = "arial.ttf"
        if not os.path.exists(f_reg): f_reg = "arial.ttf"

        title_font = ImageFont.truetype(f_bold, 60)
        artist_font = ImageFont.truetype(f_reg, 40)
        timer_font = ImageFont.truetype(f_reg, 30)

        x, y = 590, 85
        lines = textwrap.wrap(track.title, width=21)
        if len(lines) > 1:
            lines[1] = lines[1] + "..." if len(lines) > 2 else lines[1]
        lines = lines[:2]

        artists_plus_y = 70 if len(lines) > 1 else 0

        for line in lines:
            draw.text((x, y), line, font=title_font, fill=title_text_color)
            y += 70

        artists_text = " • ".join(track.artist) if isinstance(track.artist, list) else track.artist
        artists_wrapped = textwrap.wrap(artists_text, width=32)
        if len(artists_wrapped) > 1:
            if "•" in artists_wrapped[0][-2:]:
                artists_wrapped[0] = artists_wrapped[0][:artists_wrapped[0].rfind("•") - 1]
        artists_display = artists_wrapped[0] if artists_wrapped else artists_text

        draw.text((590, 170 + artists_plus_y), artists_display, subtext_color, font=artist_font)

        if track.progress and track.duration > 0:
            progress_bar_width = width - 665
            progress_bar_empty = Image.new('RGBA', (progress_bar_width, 10), (0, 0, 0, 0))
            progress_draw = ImageDraw.Draw(progress_bar_empty)

            progress_draw.rounded_rectangle(
                xy=(0, 0, progress_bar_width, 10),
                radius=7,
                fill=(*subtext_color, 60)
            )

            filled_width = int(progress_bar_width * (track.progress / track.duration))
            progress_draw.rounded_rectangle(
                xy=(0, 0, filled_width, 10),
                radius=7,
                fill=title_text_color
            )

            card.paste(progress_bar_empty, (590, 460), progress_bar_empty)

            draw.text(
                xy=(590, 490),
                text=f"{track.progress//60:02d}:{track.progress%60:02d}",
                fill=subtext_color,
                font=timer_font,
                anchor="la"
            )
            draw.text(
                xy=(1365, 490),
                text=f"{track.duration//60:02d}:{track.duration%60:02d}",
                fill=subtext_color,
                font=timer_font,
                anchor="ra"
            )
        else:
            info_font = ImageFont.truetype(f_reg, 42)
            device_font = ImageFont.truetype(f_bold, 52)

            draw.text((590, 415), "powered by Folzy", subtext_color, font=info_font, anchor="ls")
            draw.text((590, 485), track.service_name, title_text_color, font=device_font, anchor="ls")

        buf = io.BytesIO()
        card.convert("RGB").save(buf, format='JPEG', quality=95)
        buf.seek(0)
        return buf

    except Exception as e:
        log_to_group(f"❌ Ошибка создания горизонтальной карточки: {e}")
        return None


def create_vertical_card(track):
    width, height = 600, 660

    title_text_color = (255, 255, 255)
    max_title_symbols = 19
    max_subtitle_symbols = 30

    try:
        original_cover = download_cover(track.thumb)

        center_img = original_cover.copy().resize((384, 384), Image.Resampling.LANCZOS)

        background_fill = get_cover_accent_color(center_img)
        second_fill = adjust_color_for_readability(background_fill)

        card = Image.new('RGB', (width, height), background_fill)
        draw = ImageDraw.Draw(card)

        mask = Image.new('L', center_img.size, 0)
        m_draw = ImageDraw.Draw(mask)
        m_draw.rounded_rectangle((0, 0, *center_img.size), 25, fill=255)
        center_img.putalpha(mask)
        card.paste(center_img, (108, 60), center_img)

        f_bold = os.path.join(RESOURCE_DIR, "YSMusic-Bold.ttf")
        f_reg = os.path.join(RESOURCE_DIR, "YSText-Regular.ttf")
        if not os.path.exists(f_bold): f_bold = "arial.ttf"
        if not os.path.exists(f_reg): f_reg = "arial.ttf"

        title_font = ImageFont.truetype(f_bold, 40)
        artist_font = ImageFont.truetype(f_reg, 30)
        duration_font = ImageFont.truetype(f_reg, 14)

        if track.duration and track.duration > 0:
            progress_bar_color = second_fill

            draw.rounded_rectangle(
                xy=(108, 468, 492, 483),
                radius=25,
                fill=progress_bar_color
            )

            progress = track.progress / track.duration
            filled_width = 112 + max(7, int(376 * progress))
            draw.rounded_rectangle(
                xy=(112, 472, filled_width, 479),
                radius=25 if progress >= 0.1 else 5,
                fill=(255, 255, 255)
            )

            draw.text(
                xy=(108, 490),
                text=f"{track.progress//60:02d}:{track.progress%60:02d}",
                font=duration_font,
                fill=progress_bar_color,
                anchor="la"
            )
            draw.text(
                xy=(492, 490),
                text=f"{track.duration//60:02d}:{track.duration%60:02d}",
                font=duration_font,
                fill=progress_bar_color,
                anchor="ra"
            )

        title_display = track.title if len(track.title) < max_title_symbols else track.title[:max_title_symbols] + '...'
        draw.text(
            xy=(15, 550),
            text=title_display,
            font=title_font,
            fill=title_text_color,
            anchor="la"
        )

        artists = ", ".join(track.artist) if isinstance(track.artist, list) else track.artist
        artists_display = artists if len(artists) < max_subtitle_symbols else artists[:max_subtitle_symbols] + '...'
        draw.text(
            xy=(15, 605),
            text=artists_display,
            font=artist_font,
            fill=second_fill,
            anchor="la"
        )

        buf = io.BytesIO()
        card.convert("RGB").save(buf, format='JPEG', quality=95)
        buf.seek(0)
        return buf

    except Exception as e:
        log_to_group(f"❌ Ошибка создания вертикальной карточки: {e}")
        return None


@bot.message_handler(commands=['start'])
def welcome(m):
    if m.from_user.id == ADMIN_ID:
        log_action(m.from_user, "/start")
        db_op("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (m.from_user.id,))
        render_settings(m.chat.id, m.from_user.id)
    else:
        show_donation_menu(m.chat.id)


def show_donation_menu(chat_id):
    text = (
        "💝 <b>Выберите сумму пожертвования в Telegram Stars</b>\n\n"
        "Ваш вклад помогает мне развиваться и достигать новых целей!"
    )

    kb = InlineKeyboardMarkup(row_width=3)
    donations = [1, 5, 10, 15, 30, 50, 100, 250, 500]

    for i in range(0, len(donations), 3):
        row_buttons = []
        for amount in donations[i:i+3]:
            row_buttons.append(InlineKeyboardButton(f"⭐ {amount}", callback_data=f"donate_{amount}"))
        kb.add(*row_buttons)

    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_donation"))
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith('donate_'))
def handle_donation(c):
    try:
        amount = int(c.data.split('_')[1])

        try:
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except:
            pass

        bot.send_invoice(
            chat_id=c.message.chat.id,
            title="Пожертвование для развития бота",
            description=f"Спасибо за поддержку! Вы дарите {amount} Telegram Stars",
            invoice_payload=f"donation_{amount}_{c.from_user.id}_{int(time.time())}",
            provider_token='',
            currency='XTR',
            prices=[types.LabeledPrice(label=f"Пожертвование {amount} ⭐", amount=amount)],
            is_flexible=False
        )

        log_to_group(f"<blockquote>💝 <b>Создан инвойс пожертвования:</b> {amount} ⭐\n"
                     f"👤 <b>User:</b> <a href='tg://user?id={c.from_user.id}'>{html.escape(c.from_user.first_name)}</a> "
                     f"(<code>{c.from_user.id}</code>)</blockquote>")

        bot.answer_callback_query(c.id, f"Создан счет на {amount} ⭐")
    except Exception as e:
        log_to_group(f"❌ Ошибка при отправке инвойса пожертвования: {e}")
        bot.answer_callback_query(c.id, "Ошибка при формировании счета", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data == 'cancel_donation')
def cancel_donation(c):
    bot.delete_message(c.message.chat.id, c.message.message_id)
    bot.answer_callback_query(c.id)


@bot.pre_checkout_query_handler(func=lambda query: query.invoice_payload.startswith('donation_'))
def checkout_donation(pre_checkout_query):
    try:
        log_to_group(f"<blockquote>⏳ <b>Pre-checkout пожертвование:</b>\n"
                     f"👤 User ID: <code>{pre_checkout_query.from_user.id}</code>\n"
                     f"💫 Amount: {pre_checkout_query.total_amount} XTR\n"
                     f"🔗 Payload: <code>{pre_checkout_query.invoice_payload}</code></blockquote>")

        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
        log_to_group("✅ Pre-checkout query успешно подтвержден")
    except Exception as e:
        log_to_group(f"❌ Ошибка в pre-checkout: {e}")
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Ошибка обработки платежа")


@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload

    log_to_group(f"<blockquote>💳 <b>Получен successful_payment:</b>\n"
                 f"👤 User ID: <code>{user_id}</code>\n"
                 f"🔗 Payload: <code>{payload}</code>\n"
                 f"💰 Amount: {message.successful_payment.total_amount} {message.successful_payment.currency}</blockquote>")

    if payload.startswith('donation_'):
        handle_donation_payment(user_id, payload, message)
    else:
        bot.send_message(user_id, "❌ Ошибка: некорректный формат платежа.")
        log_to_group(f"❌ Неизвестный формат payload: {payload}")


def handle_donation_payment(user_id, payload, message):
    try:
        parts = payload.split('_')
        if len(parts) >= 2:
            amount = int(parts[1])

            bot.send_message(user_id,
                           f"🎉 <b>Спасибо за пожертвование!</b>\n\n"
                           f"Вы пожертвовали <code>{amount} Telegram Stars</code>.\n"
                           f"Ваш вклад очень важен для меня! 💖",
                           parse_mode="HTML")

            log_to_group(f"<blockquote>✅ <b>Успешное пожертвование:</b>\n"
                        f"👤 <a href='tg://user?id={user_id}'>{html.escape(message.from_user.first_name)}</a> "
                        f"(<code>{user_id}</code>)\n"
                        f"💫 Сумма: <code>{amount} ⭐</code></blockquote>")
    except Exception as e:
        bot.send_message(user_id, f"❌ Ошибка при обработке платежа: {e}")
        log_to_group(f"❌ Ошибка обработки пожертвования: {e}")


@bot.message_handler(commands=['admin'])
def admin_panel(m):
    if m.from_user.id != ADMIN_ID:
        return

    config = get_active_ai_config()
    u = db_op("SELECT is_playing FROM users WHERE user_id = ?", (ADMIN_ID,), fetchone=True)
    total_messages = (ai_db_op('SELECT COUNT(*) FROM chat_history', fetchone=True) or [0])[0]
    music_status = "🟢 ВКЛ" if (u and u[0]) else "🔴 ВЫКЛ"

    bot.send_message(m.chat.id,
                    f"<b>👨‍💼 Админ-панель</b>\n\n"
                    f"<b>🎵 Музыка:</b> {music_status}\n"
                    f"<b>🤖 AI:</b> активен (Задержка: {AI_DELAY} сек)\n"
                    f"<b>📊 Сообщений:</b> {total_messages}\n\n"
                    f"<b>System Prompt:</b>\n<code>{html.escape(config[:900])}...</code>",
                    parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ['.now', '/now'])
def private_now(m):
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except:
        pass
    send_now_card(m.chat.id, m.from_user.id)


def send_now_card(chat_id, user_id, biz_id=None):
    u = db_op("SELECT ym_token, sc_token, main_service, card_style, is_auto_detect FROM users WHERE user_id = ?",
              (user_id,), fetchone=True)
    if not u:
        try:
            bot.send_message(chat_id, "❌ Пользователь не найден в БД", business_connection_id=biz_id)
        except Exception as e:
            log_to_group(f"❌ /now: пользователь не найден, ошибка отправки (chat={chat_id}): {e}")
        return

    ym, sc, srv, style, auto_detect = u
    track = None
    services_to_check = []
    errors = []

    if auto_detect:
        if ym: services_to_check.append('YandexMusic')
        if sc: services_to_check.append('SoundCloud')
    elif srv:
        services_to_check.append(srv)

    if not services_to_check:
        try:
            bot.send_message(chat_id, "❌ Не подключен ни один музыкальный сервис.\nИспользуйте /settings для настройки.",
                            business_connection_id=biz_id, parse_mode="HTML")
        except Exception as e:
            log_to_group(f"❌ /now: нет сервисов, ошибка отправки (chat={chat_id}): {e}")
        return

    for service in services_to_check:
        try:
            current_track = None
            if service == 'YandexMusic' and ym:
                for attempt in range(3):
                    try:
                        r = requests.get(f"{DEFAULT_API_URL}/get_current_track_beta",
                                       headers={"ya-token": ym}, timeout=20).json()
                        current_track = YandexTrack(r)
                        if not current_track.active:
                            errors.append(f"YandexMusic: трек не активен (пауза или не играет)")
                        break
                    except requests.exceptions.Timeout:
                        if attempt == 2:
                            errors.append(f"YandexMusic: таймаут после 3 попыток")
                            log_to_group(f"⏱️ YM timeout после 3 попыток (User: {user_id})")
                            raise
                        time.sleep(1)
            elif service == 'SoundCloud' and sc:
                h = {'User-Agent': choice(CHROME_USER_AGENTS), 'Authorization': f'OAuth {sc}'}
                r = requests.get("https://api-v2.soundcloud.com/me/play-history/tracks",
                               headers=h,
                               params={'client_id': '1HxML01xkzWgtHfBreaeZfpANMe3ADjb', 'limit': '1'},
                               timeout=15).json()
                current_track = SoundCloudTrack(r, user_id=user_id)
                if not current_track.active:
                    errors.append(f"SoundCloud: трек не активен (пауза или не играет)")

            if current_track and current_track.active:
                track = current_track
                break
        except Exception as e:
            error_msg = f"{service}: {str(e)}"
            errors.append(error_msg)
            log_to_group(f"❌ Ошибка /now (User: {user_id}, Service: {service}): {e}")

    if track and track.active:
        try:
            card = create_card(track, style=style)
            if not card:
                bot.send_message(chat_id, "❌ Ошибка создания карточки", business_connection_id=biz_id)
                log_to_group(f"❌ Ошибка создания карточки для {track.title}")
                return

            cap = (f"🎶 | <b>{html.escape(track.title)}</b> — <i>{html.escape(track.artist)}</i>\n\n"
                   f"🔗 <a href='{track.link}'>{track.service_name}</a> • <a href='{track.song_link}'>song.link</a>")
            bot.send_photo(chat_id, card, caption=cap, parse_mode="HTML", business_connection_id=biz_id)
        except Exception as e:
            log_to_group(f"❌ Ошибка отправки карточки (chat={chat_id}, biz_id={biz_id}): {e}")
            try:
                bot.send_message(chat_id, f"❌ Ошибка отправки карточки", business_connection_id=biz_id)
            except Exception as e2:
                log_to_group(f"❌ Не удалось отправить сообщение об ошибке (chat={chat_id}): {e2}")
    else:
        error_text = "❌ <b>Не удалось получить текущий трек</b>\n\n"

        if errors:
            error_text += "<b>Детали:</b>\n"
            for err in errors:
                error_text += f"• {err}\n"
        else:
            error_text += "Возможные причины:\n"
            error_text += "• Музыка не играет\n"
            error_text += "• Трек на паузе\n"
            error_text += "• Проблемы с токеном\n"

        error_text += f"\n<b>Проверенные сервисы:</b> {', '.join(services_to_check)}"

        try:
            bot.send_message(chat_id, error_text, business_connection_id=biz_id, parse_mode="HTML")
        except Exception as e:
            log_to_group(f"❌ /now: не удалось отправить ошибку трека (chat={chat_id}): {e}")
        log_to_group(f"❌ /now failed for user {user_id}. Errors: {'; '.join(errors) if errors else 'No active track'}")


@bot.message_handler(commands=['settings'])
def settings_cmd(m):
    if m.from_user.id != ADMIN_ID:
        return
    render_settings(m.chat.id, m.from_user.id)


@bot.message_handler(commands=['api'])
def update_api_key_command(m):
    if m.from_user.id != ADMIN_ID:
        return
    try:
        new_key = m.text.split()[1]
        global GROQ_API_KEY
        GROQ_API_KEY = new_key
        save_api_key_to_file(new_key)
        bot.send_message(m.chat.id, "✅ API ключ Groq обновлен.")
        log_to_group("🔑 API ключ Groq был обновлен через команду /api.")
    except IndexError:
        bot.send_message(m.chat.id, "❌ Неверный формат. Используйте: /api <ключ>")
    except Exception as e:
        bot.send_message(m.chat.id, f"Произошла ошибка: {e}")


@bot.message_handler(commands=['proxies'])
def cmd_proxies(m):
    if m.from_user.id != ADMIN_ID:
        return
    with proxies_list_lock:
        count = len(PROXIES_LIST)
    msg = bot.send_message(m.chat.id,
        f"🔄 Принудительное обновление прокси...\n"
        f"Сейчас в пуле: <b>{count}</b> прокси.",
        parse_mode='HTML')
    _proxy_refresh_event.set()
    def _wait_and_report():
        _proxy_refresh_event.wait(timeout=5)
        time.sleep(90)
        with proxies_list_lock:
            new_count = len(PROXIES_LIST)
        try:
            bot.edit_message_text(
                f"✅ Прокси обновлены!\nРабочих: <b>{new_count}</b>",
                m.chat.id, msg.message_id, parse_mode='HTML')
        except Exception:
            pass
    threading.Thread(target=_wait_and_report, daemon=True).start()


@bot.message_handler(commands=['json'])
def set_v2ray_proxy_command(m):
    if m.from_user.id != ADMIN_ID:
        return
    parts = m.text.split(None, 1)
    if len(parts) > 1:
        _process_v2ray_json(m, parts[1].strip())
    else:
        msg = bot.send_message(m.chat.id,
            "📋 <b>Отправьте V2Ray/Xray JSON конфиг</b>\n"
            "Бот извлечёт из него HTTP или SOCKS прокси и будет использовать его.\n\n"
            "Чтобы <b>отключить</b> V2Ray прокси — отправьте: <code>off</code>",
            parse_mode='HTML')
        bot.register_next_step_handler(msg, _handle_v2ray_json_input)


def _handle_v2ray_json_input(m):
    if m.from_user.id != ADMIN_ID:
        return
    if m.text and m.text.strip().lower() == 'off':
        global V2RAY_PROXY
        with v2ray_proxy_lock:
            V2RAY_PROXY = None
        try:
            import os
            os.remove(V2RAY_PROXY_FILE)
        except:
            pass
        bot.send_message(m.chat.id, "🔌 V2Ray прокси <b>отключён</b>.", parse_mode='HTML')
        log_to_group("🔌 V2Ray прокси отключён через /json off")
        return
    _process_v2ray_json(m, m.text or '')


def _process_v2ray_json(m, raw_text: str):
    global V2RAY_PROXY
    proxy_url = extract_proxy_from_v2ray_json(raw_text)
    if not proxy_url:
        bot.send_message(m.chat.id,
            "❌ <b>Не удалось распознать прокси из JSON.</b>\n"
            "Убедитесь, что в конфиге есть <code>inbounds</code> с протоколом <code>http</code> или <code>socks</code>.",
            parse_mode='HTML')
        return
    with v2ray_proxy_lock:
        V2RAY_PROXY = proxy_url
    save_v2ray_proxy_to_file(proxy_url)
    bot.send_message(m.chat.id,
        f"✅ <b>V2Ray прокси установлен:</b>\n<code>{proxy_url}</code>\n\n"
        f"Теперь используется как <b>основной</b> прокси для всех запросов.\n"
        f"Чтобы отключить: /json → off",
        parse_mode='HTML')
    log_to_group(f"🔌 V2Ray прокси обновлён: <code>{proxy_url}</code>")


@bot.message_handler(commands=['promt'])
def ai_config_cmd(m):
    if m.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(m.chat.id, "⚙️ Конфигурация AI\nОтправьте .txt файл или текст.", parse_mode='HTML')
    bot.register_next_step_handler(msg, process_ai_config)


def process_ai_config(m):
    if m.from_user.id != ADMIN_ID:
        return

    new_config = None
    if m.document:
        if m.document.file_name.endswith('.txt'):
            try:
                file_info = bot.get_file(m.document.file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                content = downloaded_file.decode('utf-8')
                try:
                    json_data = json.loads(content)
                    new_config = json_data.get('system_prompt', content)
                except:
                    new_config = content
            except Exception as e:
                bot.send_message(m.chat.id, f"❌ Ошибка при чтении файла: {e}")
                return
        else:
            bot.send_message(m.chat.id, "❌ Нужен именно .txt файл")
            return
    elif m.text:
        try:
            json_data = json.loads(m.text)
            new_config = json_data.get('system_prompt', m.text)
        except:
            new_config = m.text

    if new_config:
        save_ai_config(new_config)
        bot.send_message(m.chat.id, "✅ Конфигурация обновлена")
    else:
        bot.send_message(m.chat.id, "❌ Не удалось извлечь конфиг")


@bot.message_handler(commands=['token'])
def token_command(m):
    if m.from_user.id != ADMIN_ID:
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("YandexMusic", callback_data="set_tk:yandex"),
        types.InlineKeyboardButton("Soundcloud", callback_data="set_tk:soundcloud")
    )
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="delete_msg"))
    bot.send_message(m.chat.id, "Выберите сервис для настройки токена:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith(('set_tk:', 'delete_msg')))
def tk_instr(c):
    if c.from_user.id != ADMIN_ID:
        return

    if c.data == 'delete_msg':
        bot.clear_step_handler_by_chat_id(c.message.chat.id)
        bot.delete_message(c.message.chat.id, c.message.message_id)
        return

    srv = c.data.split(':')[1]
    kb = types.InlineKeyboardMarkup(row_width=1)

    if srv == 'yandex':
        text = (
            "Для полноценного использования бота войдите в свой аккаунт Сервис.Музыки используя защищённый сайт, "
            "приложение под Android или расширение для браузеров.\n\n"
            "Для начала работы нужно привязать ваш Яндекс токен.\n\n"
            "<b>Отправьте токен сообщением в этот чат:</b>"
        )
        kb.add(
            types.InlineKeyboardButton("Войти через сайт", url="https://music-yandex-bot.ru/"),
            types.InlineKeyboardButton("Войти через Android приложение", url="https://github.com/MarshalX/yandex-music-token/releases"),
            types.InlineKeyboardButton("Войти через расширение для Chrome", url="https://chrome.google.com/webstore/detail/yandex-music-token/lcbjeookjibfhjjopieifgjnhlegmkib"),
            types.InlineKeyboardButton("Войти через расширение для Firefox", url="https://addons.mozilla.org/en-US/firefox/addon/yandex-music-token/")
        )
    else:
        text = (
            "<b>Гайд по авторизации в SoundCloud</b>\n\n"
            "📱 <b>Телефон:</b>\n"
            "1. Скачайте FireFox\n"
            "2. Скачайте расширение Cookie Editor\n"
            "3. Зайдите в аккаунт SoundCloud\n"
            "4. Перейдите в <code>oauth_token</code> через расширение\n\n"
            "💻 <b>ПК:</b>\n"
            "1. F12 -> Application -> Cookies -> <code>oauth_token</code>\n\n"
            "<b>Отправьте токен боту:</b>"
        )

    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="delete_msg"))

    msg = bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        text=text,
        parse_mode='HTML',
        reply_markup=kb,
        disable_web_page_preview=True
    )
    bot.register_next_step_handler(msg, save_tk_db, srv)


def save_tk_db(m, srv):
    if m.from_user.id != ADMIN_ID:
        return

    if m.text and not m.text.startswith('/'):
        db_op(f"UPDATE users SET {'ym_token' if srv=='yandex' else 'sc_token'} = ? WHERE user_id = ?",
              (m.text.strip(), m.from_user.id))
        bot.send_message(m.chat.id, f"✅ Токен {srv} сохранен!")
        render_settings(m.chat.id, m.from_user.id)


def render_settings(chat_id, user_id, msg_id=None):
    u = db_op("SELECT is_playing, card_style, main_service, biz_conn_id, ym_token, sc_token, bio_format, original_bio, is_auto_detect, spy_mode, ai_enabled, name_format, original_name, name_auto_update FROM users WHERE user_id = ?",
              (user_id,), fetchone=True)
    if not u:
        return

    play, style, srv, biz, ym, sc, fmt, orig, auto_detect, spy_mode, ai_enabled, name_fmt, orig_name, name_auto = u

    ai_status = '🟢 ВКЛ' if ai_enabled else '🔴 ВЫКЛ'
    ai_status_full = '🟢 Активен' if ai_enabled else '🔴 Неактивен'
    auto_detect_status = '🟢 ВКЛ' if auto_detect else '🔴 ВЫКЛ'
    spy_mode_status = '🟢 ВКЛ' if spy_mode else '🔴 ВЫКЛ'

    if name_auto == 0:
        name_auto_status = '🔴 ВЫКЛ'
    elif name_auto == 1:
        name_auto_status = '🟡 При трансляции'
    else:
        name_auto_status = '🟢 ВКЛ'

    main_service_display = f"<b>Основной сервис:</b> {srv}\n" if not auto_detect else ""

    txt = (f"⚙️ <b>Настройки</b>\n\n"
           f"<blockquote><b>Трансляция:</b> {'🟢 ВКЛ' if play else '🔴 ВЫКЛ'}\n"
           f"<b>Авто-Определение:</b> {auto_detect_status}\n"
           f"{main_service_display}"
           f"<b>Стиль карточки:</b> {style}\n"
           f"<b>Spy Mode:</b> {spy_mode_status}\n"
           f"<b>AI-Автоответчик:</b> {ai_status_full}\n"
           f"<b>ЯндексМузыка:</b> {'🟢 Подключена' if ym else '🔴 Не подключена'}\n"
           f"<b>SoundCloud:</b> {'🟢 Подключен' if sc else '🔴 Не подключен'}\n"
           f"<b>Бизнес Аккаунт:</b> {'🟢 Активен' if biz else '🔴 Ошибка'}\n"
           f"<b>Формат Bio:</b> {html.escape(fmt or '')}\n"
           f"<b>Bio по умолчанию:</b> {html.escape(orig or '')}\n"
           f"<b>Авто-обновление имени:</b> {name_auto_status}\n"
           f"<b>Формат имени:</b> {html.escape(name_fmt or 'не установлен')}\n"
           f"<b>Имя по умолчанию:</b> {html.escape(orig_name or 'не установлено')}</blockquote>")

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"Трансляция: {'🟢 ВКЛ' if play else '🔴 ВЫКЛ'}", callback_data="t_play"),
        InlineKeyboardButton(f"Авто-Определение: {auto_detect_status}", callback_data="t_auto_detect"),
        InlineKeyboardButton(f"Стиль: {style}", callback_data="t_style"),
        InlineKeyboardButton(f"Spy Mode: {spy_mode_status}", callback_data="t_spy")
    )
    kb.add(InlineKeyboardButton(f"AI Автоответчик: {ai_status}", callback_data="t_ai"))
    if not auto_detect:
        kb.add(InlineKeyboardButton(f"Основной сервис: {srv}", callback_data="ch_srv"))
    kb.add(
        InlineKeyboardButton("Формат Bio", callback_data="t_fmt"),
        InlineKeyboardButton("Bio по умолчанию", callback_data="t_orig")
    )
    kb.add(InlineKeyboardButton(f"Авто-имя: {name_auto_status}", callback_data="t_name_auto"))
    kb.add(
        InlineKeyboardButton("Формат имени", callback_data="t_name_fmt"),
        InlineKeyboardButton("Имя по умолчанию", callback_data="t_name_orig")
    )
    kb.add(InlineKeyboardButton("👤 Ники", callback_data="open_nicks"))
    kb.add(
        InlineKeyboardButton("Обновить", callback_data="open_settings"),
        InlineKeyboardButton("Таймеры ⏱️", callback_data="open_timers")
    )

    try:
        if msg_id:
            bot.edit_message_text(txt, chat_id, msg_id, reply_markup=kb, parse_mode="HTML")
        else:
            bot.send_message(chat_id, txt, reply_markup=kb, parse_mode="HTML")
    except:
        pass

def get_raw_text(msg):
    text = msg.text or ""
    entities = msg.entities or []
    if not entities:
        return text
    
    chars = list(text)
    for ent in sorted(entities, key=lambda e: e.offset, reverse=True):
        end = ent.offset + ent.length
        if ent.type == 'bold':
            chars.insert(end, '__')
            chars.insert(ent.offset, '__')
        elif ent.type == 'italic':
            chars.insert(end, '_')
            chars.insert(ent.offset, '_')
    
    return ''.join(chars)

@bot.callback_query_handler(func=lambda c: c.data in ["open_settings", "t_play", "t_style", "ch_srv", "t_fmt", "t_orig", "t_auto_detect", "t_spy", "t_ai", "open_timers", "t_name_fmt", "t_name_orig", "t_name_auto", "open_nickname_schedule", "open_nicks"] or c.data.startswith("set_timer:"))
def cfg_cb(c):
    if c.from_user.id != ADMIN_ID:
        return

    uid = c.from_user.id
    data = c.data

    if data == "open_timers":
        render_timer_settings(c.message.chat.id, c.message.message_id)
    elif data == "open_nicks":
        render_nicks(c.message.chat.id, uid, c.message.message_id)
    elif data == "open_nickname_schedule":
        render_nickname_schedule(c.message.chat.id, uid, c.message.message_id)
    elif data.startswith("set_timer:"):
        timer_type = data.split(':')[1]
        if timer_type not in ALLOWED_TIMER_COLUMNS:
            bot.answer_callback_query(c.id, "❌ Недопустимый параметр", show_alert=True)
            return
        prompt_text = "Введите новую задержку AI в секундах:" if timer_type == 'ai_delay' else "Введите новый таймаут владельца в секундах:"
        msg = bot.send_message(c.message.chat.id, prompt_text)
        bot.register_next_step_handler(msg, process_new_timer_value, timer_type)
    elif data == "open_settings":
        render_settings(c.message.chat.id, uid, c.message.message_id)
    elif data == "t_play":
        db_op("UPDATE users SET is_playing = 1 - is_playing, last_track_id = NULL WHERE user_id = ?", (uid,))
        u = db_op("SELECT is_playing, biz_conn_id, original_bio, original_name, name_auto_update FROM users WHERE user_id = ?", (uid,), fetchone=True)
        if u and u[0] == 0 and u[1]:
            try:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountBio",
                            json={"business_connection_id": u[1], "bio": (u[2] or "")[:140]}, timeout=5)
                if u[4] == 1 and u[3]:
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                                json={"business_connection_id": u[1], "name": u[3][:64]}, timeout=5)
            except:
                pass
    elif data == "t_ai":
        db_op("UPDATE users SET ai_enabled = 1 - ai_enabled WHERE user_id = ?", (uid,))
    elif data == "t_style":
        db_op("UPDATE users SET card_style = CASE WHEN card_style='standard' THEN 'vertical' ELSE 'standard' END WHERE user_id = ?", (uid,))
    elif data == "ch_srv":
        db_op("UPDATE users SET main_service = CASE WHEN main_service='YandexMusic' THEN 'SoundCloud' ELSE 'YandexMusic' END WHERE user_id = ?", (uid,))
    elif data == "t_auto_detect":
        db_op("UPDATE users SET is_auto_detect = 1 - is_auto_detect WHERE user_id = ?", (uid,))
    elif data == "t_spy":
        db_op("UPDATE users SET spy_mode = 1 - spy_mode WHERE user_id = ?", (uid,))
    elif data == "t_name_auto":
        db_op("UPDATE users SET name_auto_update = (name_auto_update + 1) % 3 WHERE user_id = ?", (uid,))
    elif data == "t_fmt":
        m = bot.send_message(c.message.chat.id, "Введите формат Bio (доступны интеграции {track}, {artists}, {service}, каждый из них отображает соответствующую информацию, к примеру {track} - название текущего трека)")
        bot.register_next_step_handler(m, lambda msg: [db_op("UPDATE users SET bio_format = ? WHERE user_id = ?", (msg.text, uid)), render_settings(msg.chat.id, uid)])
    elif data == "t_orig":
        m = bot.send_message(c.message.chat.id, "Введите свое Bio по умолчанию:")
        bot.register_next_step_handler(m, lambda msg: [db_op("UPDATE users SET original_bio = ? WHERE user_id = ?", (get_raw_text(msg), uid)),render_settings(msg.chat.id, uid)])
    elif data == "t_name_fmt":
        m = bot.send_message(c.message.chat.id, "Введите формат имени:\n\n{time} - время (ЧЧ:ММ)\n{date} - дата (ДД.ММ)\n{year} - год (ГГГГ)\n{day} - день (ДД)\n{month} - месяц (ММ)\n\nПример: MyName {time}")
        bot.register_next_step_handler(m, lambda msg: [db_op("UPDATE users SET name_format = ? WHERE user_id = ?", (msg.text, uid)), render_settings(msg.chat.id, uid)])
    elif data == "t_name_orig":
        m = bot.send_message(c.message.chat.id, "Введите свое имя по умолчанию:")
        bot.register_next_step_handler(m, lambda msg: [db_op("UPDATE users SET original_name = ? WHERE user_id = ?", (msg.text, uid)), render_settings(msg.chat.id, uid)])

    if data not in ["t_fmt", "t_orig", "t_name_fmt", "t_name_orig", "open_settings", "open_timers", "open_nickname_schedule", "open_nicks"] and not data.startswith("set_timer:"):
        render_settings(c.message.chat.id, uid, c.message.message_id)

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


@bot.message_handler(commands=['clear_history'])
def clear_history_cmd(m):
    if m.from_user.id != ADMIN_ID:
        return

    try:
        week_ago = time.time() - (7 * 24 * 60 * 60)
        db_op("DELETE FROM full_chat_log WHERE timestamp < datetime(?, 'unixepoch')", (week_ago,))
        total = (db_op("SELECT COUNT(*) FROM full_chat_log", fetchone=True) or [0])[0]

        bot.send_message(m.chat.id,
                        f"🗑️ <b>Очистка истории чатов</b>\n\n"
                        f"Удалены записи старше 7 дней\n"
                        f"📊 Осталось записей: {total}",
                        parse_mode="HTML")

        log_to_group(f"🗑️ История чатов очищена админом. Осталось записей: {total}")
    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Ошибка очистки: {e}")


@bot.business_connection_handler()
def on_biz(conn):
    if conn.is_enabled:
        db_op("UPDATE users SET biz_conn_id = ? WHERE user_id = ?", (conn.id, conn.user.id))
        log_action(conn.user, "biz_conn_on", conn.id)
    else:
        db_op("UPDATE users SET biz_conn_id = NULL WHERE user_id = ?", (conn.user.id,))
        log_action(conn.user, "biz_conn_off", conn.id)


def process_accumulated_messages(chat_id):
    with pending_chats_lock:
        if chat_id not in pending_chats:
            return

        with last_owner_activity_lock:
            owner_active = time.time() - last_owner_activity < OWNER_TIMEOUT

        if owner_active:
            pending_chats.pop(chat_id, None)
            return

        data = pending_chats.pop(chat_id)
        messages = data['msgs']
        last_m = data['meta']

    full_text = "\n".join(messages)
    system_prompt = get_active_ai_config()

    with cache_lock:
        cache_entry = chat_history_cache.get(chat_id)
        if cache_entry and (time.time() - cache_entry['timestamp'] < CACHE_TTL):
            conversation_history = cache_entry['history']
        else:
            history_records = db_op(
                "SELECT role, content FROM full_chat_log WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 50",
                (chat_id,),
                fetchall=True
            )
            conversation_history = []
            if history_records:
                for role, content in reversed(history_records):
                    conversation_history.append({"role": role, "content": content})

            chat_history_cache[chat_id] = {
                'history': conversation_history,
                'timestamp': time.time()
            }

    response_text = get_ai_response_sync(full_text, system_prompt, conversation_history=conversation_history)

    if response_text is None:
        log_to_group(f"❌ AI не смог сгенерировать ответ (все прокси недоступны) для чата {chat_id}")
        return

    error_markers = ["Ошибка API", "Ошибка соединения", "API ERROR", "CONN ERROR"]
    if any(response_text.startswith(m) for m in error_markers):
        log_to_group(f"⚠️ AI Error prevented sending to user:\n{html.escape(response_text)}")
        return

    db_op("INSERT INTO full_chat_log (chat_id, role, content) VALUES (?, ?, ?)",
          (chat_id, 'assistant', response_text))
    save_ai_history(chat_id, last_m.from_user.id, full_text, response_text)

    try:
        bot.send_message(
            chat_id=chat_id,
            text=response_text,
            business_connection_id=last_m.business_connection_id,
            parse_mode="HTML"
        )
    except Exception as e:
        log_to_group(f"Ошибка отправки (batch, с HTML): {e}")
        try:
            bot.send_message(
                chat_id=chat_id,
                text=response_text,
                business_connection_id=last_m.business_connection_id
            )
        except Exception as e_fallback:
            log_to_group(f"Ошибка отправки (batch, без HTML): {e_fallback}")

    with cache_lock:
        if chat_id in chat_history_cache:
            del chat_history_cache[chat_id]


@bot.business_message_handler(func=lambda m: m.text and m.text.lower().strip() in ['.now', '/now'])
def biz_msg(m):
    res = db_op("SELECT user_id FROM users WHERE biz_conn_id = ?", (m.business_connection_id,), fetchone=True)
    if not res:
        log_to_group(f"⚠️ /now: biz_conn_id не найден: {m.business_connection_id}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteBusinessMessages",
            json={
                'business_connection_id': str(m.business_connection_id),
                'chat_id': m.chat.id,
                'message_ids': [m.message_id]
            }, timeout=5
        )
    except:
        pass
    try:
        send_now_card(m.chat.id, res[0], biz_id=m.business_connection_id)
    except Exception as e:
        log_to_group(f"❌ Ошибка send_now_card в biz_msg (chat={m.chat.id}): {e}")


@bot.business_message_handler(content_types=['text', 'photo', 'video', 'voice', 'document', 'audio', 'sticker', 'video_note'])
def handle_business_message(m):
    global last_owner_activity

    settings = db_op("SELECT ai_enabled, spy_mode FROM users WHERE user_id = ?", (ADMIN_ID,), fetchone=True)
    ai_enabled = settings[0] if settings else 1
    spy_mode = settings[1] if settings else 0

    contact_user = m.from_user if m.from_user.id != ADMIN_ID else m.chat

    dump_msg_id = None
    if m.content_type in ['photo', 'video', 'voice', 'video_note', 'document', 'audio', 'sticker']:
        file_id = None
        m_type = "Файл"
        if m.photo: file_id, m_type = m.photo[-1].file_id, "Фото"
        elif m.video: file_id, m_type = m.video.file_id, "Видео"
        elif m.voice: file_id, m_type = m.voice.file_id, "Голосовое"
        elif m.video_note: file_id, m_type = m.video_note.file_id, "Кружок"
        elif m.document: file_id, m_type = m.document.file_id, "Документ"
        elif m.audio: file_id, m_type = m.audio.file_id, "Аудио"
        elif m.sticker: file_id, m_type = m.sticker.file_id, "Стикер"

        if file_id:
            sender_name = html.escape(m.from_user.first_name)
            caption_text = m.caption or ""
            no_text = bool(m.voice or m.video_note or m.sticker)
            if no_text:
                cap = f"📦 <b>{m_type}</b>\n👤 От: {sender_name} (<code>{m.from_user.id}</code>)"
            else:
                cap = (f"📦 <b>{m_type}</b>\n👤 От: {sender_name} (<code>{m.from_user.id}</code>)\n"
                       f"💬 Текст: {html.escape(caption_text) if caption_text else '<i>нет</i>'}")

            def _send_media(thread_id, _fid=file_id, _cap=cap, _m=m):
                if _m.photo:
                    return bot.send_photo(DUMP_CHAT_ID, _fid, caption=_cap, parse_mode='HTML', message_thread_id=thread_id)
                elif _m.video:
                    return bot.send_video(DUMP_CHAT_ID, _fid, caption=_cap, parse_mode='HTML', message_thread_id=thread_id)
                elif _m.voice:
                    return bot.send_voice(DUMP_CHAT_ID, _fid, caption=_cap, parse_mode='HTML', message_thread_id=thread_id)
                elif _m.video_note:
                    sent = bot.send_video_note(DUMP_CHAT_ID, _fid, message_thread_id=thread_id)
                    bot.send_message(DUMP_CHAT_ID, _cap, parse_mode='HTML', message_thread_id=thread_id)
                    return sent
                elif _m.document:
                    return bot.send_document(DUMP_CHAT_ID, _fid, caption=_cap, parse_mode='HTML', message_thread_id=thread_id)
                elif _m.audio:
                    return bot.send_audio(DUMP_CHAT_ID, _fid, caption=_cap, parse_mode='HTML', message_thread_id=thread_id)
                elif _m.sticker:
                    sent = bot.send_sticker(DUMP_CHAT_ID, _fid, message_thread_id=thread_id)
                    bot.send_message(DUMP_CHAT_ID, _cap, parse_mode='HTML', message_thread_id=thread_id)
                    return sent

            result = safe_send_to_topic(DUMP_CHAT_ID, m.chat.id, contact_user, _send_media)
            if result:
                dump_msg_id = result.message_id

    if m.from_user.id == ADMIN_ID:
        with last_owner_activity_lock:
            last_owner_activity = time.time()
        with pending_chats_lock:
            if m.chat.id in pending_chats:
                try:
                    pending_chats.pop(m.chat.id)['timer'].cancel()
                except:
                    pass

        if spy_mode and m.content_type in ['photo', 'video', 'voice', 'video_note', 'document', 'audio', 'sticker']:
            log_business_message(ADMIN_ID, m.chat.id, m.message_id, m.from_user.id, "ME", m.text or m.caption or "[Медиафайл]", dump_msg_id)

        if m.reply_to_message and m.reply_to_message.content_type in ['photo', 'video', 'voice', 'video_note', 'document']:
            steal_media(m.reply_to_message, ADMIN_ID)

        return

    if spy_mode:
        log_business_message(ADMIN_ID, m.chat.id, m.message_id, m.from_user.id, m.from_user.first_name, m.text or m.caption or "[Медиафайл]", dump_msg_id)

    if not ai_enabled:
        return

    if m.content_type != 'text':
        return

    db_op("INSERT INTO full_chat_log (chat_id, role, content) VALUES (?, ?, ?)",
          (m.chat.id, 'user', m.text))

    with last_owner_activity_lock:
        owner_active = time.time() - last_owner_activity < OWNER_TIMEOUT

    if owner_active:
        return

    chat_id = m.chat.id
    with pending_chats_lock:
        if chat_id in pending_chats:
            try:
                pending_chats[chat_id]['timer'].cancel()
            except:
                pass
            pending_chats[chat_id]['msgs'].append(m.text)
            pending_chats[chat_id]['meta'] = m
        else:
            pending_chats[chat_id] = {
                'msgs': [m.text],
                'meta': m,
                'timer': None
            }

        t = threading.Timer(AI_DELAY, process_accumulated_messages, args=[chat_id])
        pending_chats[chat_id]['timer'] = t
        t.start()


@bot.edited_business_message_handler(func=lambda m: True)
def on_edit_business(m):
    if m.from_user.id == ADMIN_ID:
        return

    if not (db_op("SELECT spy_mode FROM users WHERE user_id = ?", (ADMIN_ID,), True) or [0])[0]:
        return

    old_msg = get_logged_business_message(ADMIN_ID, m.chat.id, m.message_id)
    if old_msg:
        sender_name_from_db = old_msg[1]

        new_text = m.text or m.caption or "[Медиа]"
        alert = (f"✏️ <b>ИЗМЕНЕНО</b> | {html.escape(sender_name_from_db)} (id{m.from_user.id})\n\n"
                f"❌ <b>Было:</b> <blockquote>{html.escape(old_msg[0])}</blockquote>\n"
                f"✅ <b>Стало:</b> <blockquote>{html.escape(new_text)}</blockquote>")

        alert_text = alert
        safe_send_to_topic(
            EDIT_LOG_GROUP_ID, m.from_user.id, m.from_user,
            lambda tid, _a=alert_text: bot.send_message(EDIT_LOG_GROUP_ID, _a, parse_mode='HTML', message_thread_id=tid)
        )

        log_business_message(ADMIN_ID, m.chat.id, m.message_id, m.from_user.id, m.from_user.first_name, m.text or "[Медиа]")


@bot.deleted_business_messages_handler(func=lambda m: True)
def on_delete_business(m):
    if not (db_op("SELECT spy_mode FROM users WHERE user_id = ?", (ADMIN_ID,), True) or [0])[0]:
        return

    for msg_id in m.message_ids:
        old_msg = get_logged_business_message(ADMIN_ID, m.chat.id, msg_id)
        if old_msg:
            sender_name_from_db = old_msg[1]
            dump_msg_id = old_msg[2]
            sender_id = old_msg[3]

            link_text = ""
            if dump_msg_id:
                chat_id_str = str(DUMP_CHAT_ID).replace('-100', '')
                link_text = f"\n🔗 <a href='https://t.me/c/{chat_id_str}/{dump_msg_id}'>Ссылка на медиа в группе</a>"

            sender_label = f"{html.escape(sender_name_from_db)} (id{sender_id})" if sender_id else html.escape(sender_name_from_db)
            alert = (f"🗑 <b>УДАЛЕНО</b> | {sender_label}\n\n"
                    f"<blockquote>{html.escape(old_msg[0])}</blockquote>"
                    f"{link_text}")

            alert_text = alert
            contact_user_id = sender_id or m.chat.id
            safe_send_to_topic(
                EDIT_LOG_GROUP_ID, contact_user_id, m.chat,
                lambda tid, _a=alert_text: bot.send_message(
                    EDIT_LOG_GROUP_ID, _a, parse_mode='HTML',
                    message_thread_id=tid, disable_web_page_preview=False
                )
            )


def name_updater():
    import pytz
    from datetime import datetime, timedelta

    msk_tz = pytz.timezone('Asia/Yekaterinburg')

    def do_update():
        next_minute = datetime.now(msk_tz) + timedelta(minutes=1)
        try:
            today_str = datetime.now(msk_tz).strftime('%Y-%m-%d')

            # Scheduled names: apply or revert based on today's date
            scheduled = db_op(
                "SELECT id, user_id, name, date_from, date_to FROM scheduled_names ORDER BY id ASC",
                fetchall=True
            ) or []
            # fix 2: among overlapping active entries per user, only apply the first (lowest id)
            users_with_active_schedule = set()  # users that already had a schedule applied this cycle
            for sched_id, uid, sched_name, date_from, date_to in scheduled:
                u = db_op("SELECT biz_conn_id, original_name FROM users WHERE user_id = ?", (uid,), fetchone=True)
                if not u:
                    continue
                biz, orig_name = u
                if not biz:
                    continue
                flag_key = f"sched_active_{sched_id}"
                in_range = date_from <= today_str <= date_to
                if in_range:
                    # fix 2: if another entry for this user is already active this cycle, skip
                    if uid in users_with_active_schedule:
                        continue
                    users_with_active_schedule.add(uid)
                    flag = db_op("SELECT value FROM kv_flags WHERE key = ?", (flag_key,), fetchone=True)
                    if not flag:
                        try:
                            r = requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                                json={"business_connection_id": biz, "first_name": sched_name[:64]},
                                timeout=5
                            )
                            print(f"[NAME {uid}] scheduled ON -> {sched_name} | {r.status_code}")
                            if r.status_code == 200:
                                db_op("INSERT OR REPLACE INTO kv_flags (key, value) VALUES (?, ?)", (flag_key, '1'))
                                log_to_group(f"Запланированное имя активировано: <b>{html.escape(sched_name)}</b> (до {date_to})")
                        except Exception as e:
                            print(f"[NAME {uid}] Ошибка scheduled ON: {e}")
                else:
                    flag = db_op("SELECT value FROM kv_flags WHERE key = ?", (flag_key,), fetchone=True)
                    if flag:
                        # revert name
                        try:
                            revert_name = orig_name or ''
                            r = requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                                json={"business_connection_id": biz, "first_name": revert_name[:64]},
                                timeout=5
                            )
                            print(f"[NAME {uid}] scheduled OFF -> {revert_name} | {r.status_code}")
                            if r.status_code == 200:
                                db_op("DELETE FROM kv_flags WHERE key = ?", (flag_key,))
                                log_to_group(f"Запланированное имя снято, восстановлено: <b>{html.escape(revert_name)}</b>")
                        except Exception as e:
                            print(f"[NAME {uid}] Ошибка scheduled OFF: {e}")
                    # fix 4+5: auto-delete expired record once flag is cleared
                    if today_str > date_to:
                        flag_still = db_op("SELECT value FROM kv_flags WHERE key = ?", (flag_key,), fetchone=True)
                        if not flag_still:
                            db_op("DELETE FROM scheduled_names WHERE id = ?", (sched_id,))
                            print(f"[NAME {uid}] expired schedule #{sched_id} auto-deleted")

            always_on = db_op(
                "SELECT user_id, biz_conn_id, name_format FROM users "
                "WHERE name_auto_update = 2 AND biz_conn_id IS NOT NULL AND name_format IS NOT NULL",
                fetchall=True
            )
            for uid, biz, name_fmt in (always_on or []):
                # fix 1: skip if a scheduled name is currently active for this user
                if uid in users_with_active_schedule:
                    continue
                try:
                    new_name = format_name_with_time(name_fmt, next_minute)
                    if new_name:
                        r = requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                            json={"business_connection_id": biz, "first_name": new_name},
                            timeout=5
                        )
                        print(f"[NAME {uid}] always-on → {new_name} | {r.status_code}")
                except Exception as e:
                    print(f"[NAME {uid}] Ошибка (always-on): {e}")

            during_broadcast = db_op(
                "SELECT user_id, biz_conn_id, name_format FROM users "
                "WHERE name_auto_update = 1 AND biz_conn_id IS NOT NULL AND name_format IS NOT NULL "
                "AND last_track_id IS NOT NULL AND is_bio_reverted = 0",
                fetchall=True
            )
            for uid, biz, name_fmt in (during_broadcast or []):
                # fix 1: skip if a scheduled name is currently active for this user
                if uid in users_with_active_schedule:
                    continue
                try:
                    new_name = format_name_with_time(name_fmt, next_minute)
                    if new_name:
                        r = requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                            json={"business_connection_id": biz, "first_name": new_name},
                            timeout=5
                        )
                        print(f"[NAME {uid}] broadcast -> {new_name} | {r.status_code}")
                except Exception as e:
                    print(f"[NAME {uid}] Ошибка (broadcast): {e}")

        except Exception as e:
            print(f"[NAME_UPDATER] Критическая ошибка: {e}")
            log_to_group(f"Критическая ошибка в name_updater: {e}")

    print("[NAME_UPDATER] Запуск...")
    while True:
        now = datetime.now(msk_tz)
        seconds_to_next_59 = (59 - now.second) % 60
        if seconds_to_next_59 == 0:
            seconds_to_next_59 = 60
        time.sleep(seconds_to_next_59)
        print(f"[NAME_UPDATER] Запуск в {datetime.now(msk_tz).strftime('%H:%M:%S')} МСК")
        do_update()


def _get_active_scheduled_name(user_id):
    """Returns the scheduled name if one is active today for this user, else None."""
    import pytz
    from datetime import datetime
    today = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime('%Y-%m-%d')
    row = db_op(
        "SELECT name FROM scheduled_names WHERE user_id = ? AND date_from <= ? AND date_to >= ? ORDER BY id ASC LIMIT 1",
        (user_id, today, today), fetchone=True
    )
    return row[0] if row else None


def monitor():
    while True:
        try:
            users = db_op("SELECT user_id, ym_token, sc_token, main_service, biz_conn_id, last_track_id, original_bio, bio_format, is_bio_reverted, is_auto_detect, last_active_time, last_track_start_time, name_format, original_name, name_auto_update FROM users WHERE is_playing = 1", fetchall=True)

            if users:
                print(f"\n[MONITOR] Найдено активных пользователей: {len(users)}")

            for uid, ym, sc, srv, biz, last, orig, fmt, reverted, auto_detect, last_active, last_tstart, name_fmt, orig_name, name_auto in (users or []):
                try:
                    now = time.time()

                    all_tracks = {}

                    print(f"\n[DEBUG {uid}] Обработка. Текущий сервис: {srv}, AutoDetect: {auto_detect}")

                    if ym:
                        try:
                            print(f"[DEBUG {uid}] Запрос YandexMusic...")
                            for attempt in range(3):
                                try:
                                    r_raw = requests.get(f"{DEFAULT_API_URL}/get_current_track_beta",
                                                        headers={"ya-token": ym}, timeout=20)
                                    r = r_raw.json()
                                    track = YandexTrack(r)
                                    if track.active:
                                        all_tracks['YandexMusic'] = track
                                    print(f"[DEBUG {uid}] YM: Активен ({track.title})")
                                    break
                                except requests.exceptions.Timeout:
                                    print(f"[DEBUG {uid}] YM: Таймаут (попытка {attempt+1})")
                                    if attempt < 2:
                                        time.sleep(1)
                                    else:
                                        raise
                        except Exception as e:
                            print(f"[DEBUG {uid}] YM: Ошибка: {e}")

                    if sc:
                        try:
                            print(f"[DEBUG {uid}] Запрос SoundCloud...")
                            h = {'User-Agent': choice(CHROME_USER_AGENTS), 'Authorization': f'OAuth {sc}'}
                            sc_r = None
                            sc_url = "https://api-v2.soundcloud.com/me/play-history/tracks"
                            sc_params = {'limit': '1', 'client_id': '1HxML01xkzWgtHfBreaeZfpANMe3ADjb'}
                            with v2ray_proxy_lock:
                                v2_proxy = V2RAY_PROXY
                            if v2_proxy:
                                try:
                                    sc_r = requests.get(sc_url, headers=h, params=sc_params,
                                                        timeout=15, proxies={'http': v2_proxy, 'https': v2_proxy},
                                                        verify=False)
                                    if sc_r.status_code != 200:
                                        print(f"[DEBUG {uid}] SC V2Ray прокси: статус {sc_r.status_code}, фолбэк")
                                        sc_r = None
                                except Exception as sc_e:
                                    print(f"[DEBUG {uid}] SC V2Ray прокси ошибка: {sc_e}, фолбэк")
                                    sc_r = None
                            if sc_r is None:
                                with proxies_list_lock:
                                    proxy_pool = list(PROXIES_LIST)
                                with current_proxy_index_lock:
                                    base_index = current_proxy_index
                                for sc_attempt in range(len(proxy_pool)):
                                    proxy_addr = proxy_pool[(base_index + sc_attempt) % len(proxy_pool)]
                                    try:
                                        sc_r = requests.get(sc_url, headers=h, params=sc_params,
                                                            timeout=15, proxies={'http': proxy_addr, 'https': proxy_addr},
                                                            verify=False)
                                        if sc_r.status_code == 200:
                                            break
                                        sc_r = None
                                    except Exception as sc_e:
                                        print(f"[DEBUG {uid}] SC прокси {sc_attempt+1} ошибка: {sc_e}")
                                        sc_r = None
                            if sc_r and sc_r.status_code == 200:
                                r = sc_r.json()
                                track = SoundCloudTrack(r, user_id=uid)
                                if track.active:
                                    all_tracks['SoundCloud'] = track
                                print(f"[DEBUG {uid}] SC: {'Активен' if track.active else 'Не активен'} ({track.title})")
                            else:
                                print(f"[DEBUG {uid}] SC: Все прокси не ответили")
                        except Exception as e:
                            print(f"[DEBUG {uid}] SC: Ошибка: {e}")

                    selected_track = None
                    if auto_detect:
                        count = len(all_tracks)
                        print(f"[DEBUG {uid}] AutoDetect: найдено активных сервисов: {count}")

                        if count == 1:
                            selected_track = list(all_tracks.values())[0]
                        elif count > 1:
                            current_track_age = now - (last_tstart or 0)
                            print(f"[DEBUG {uid}] Несколько сервисов. Возраст текущего трека: {int(current_track_age)} сек")

                            if current_track_age > 240:
                                print(f"[DEBUG {uid}] Трек играет > 4 мин, ищем смену...")
                                for track in all_tracks.values():
                                    if track.track_id != last:
                                        selected_track = track
                                        print(f"[DEBUG {uid}] Выбран НОВЫЙ трек: {track.title}")
                                        break

                            if not selected_track:
                                for track in all_tracks.values():
                                    if track.track_id == last:
                                        selected_track = track
                                        print(f"[DEBUG {uid}] Оставляем текущий трек")
                                        break

                            if not selected_track:
                                selected_track = list(all_tracks.values())[0]
                                print(f"[DEBUG {uid}] Текущий не найден, берем первый попавшийся")
                    else:
                        selected_track = all_tracks.get(srv)
                        if selected_track:
                            print(f"[DEBUG {uid}] Выбран основной сервис {srv}: {selected_track.title}")

                    if selected_track and selected_track.active:
                        print(f"[DEBUG {uid}] ✅ ТРЕК ИГРАЕТ: {selected_track.title} (ID: {selected_track.track_id})")
                        db_op("UPDATE users SET last_active_time = ?, is_bio_reverted = 0 WHERE user_id = ?", (now, uid))

                        if selected_track.track_id != last:
                            print(f"[DEBUG {uid}] Смена трека! Обновляю Bio в Telegram...")
                            db_op("UPDATE users SET last_track_id = ?, last_track_start_time = ? WHERE user_id = ?",
                                  (selected_track.track_id, now, uid))

                            if biz and fmt:
                                try:
                                    bio_text = fmt.format(
                                        track=selected_track.title,
                                        artists=selected_track.artist,
                                        service=selected_track.service_name
                                    )[:140]
                                    res = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountBio",
                                                       json={"business_connection_id": biz, "bio": bio_text}, timeout=5)
                                    print(f"[DEBUG {uid}] Ответ Telegram: {res.status_code} {res.text}")
                                except Exception as e:
                                    log_to_group(f"Ошибка обновления Bio (User: {uid}): {e}")
                    else:
                        print(f"[DEBUG {uid}] ⏹ Трек не активен.")
                        if last and not reverted:
                            inactive_time = now - (last_active or 0)
                            print(f"[DEBUG {uid}] Время неактивности: {int(inactive_time)} сек")

                            if inactive_time > 600:
                                print(f"[DEBUG {uid}] ⏳ 10 мин прошло. Сброс Bio.")
                                db_op("UPDATE users SET last_track_id = NULL, is_bio_reverted = 1 WHERE user_id = ?", (uid,))
                                if biz:
                                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountBio",
                                                json={"business_connection_id": biz, "bio": (orig or "")[:140]}, timeout=5)
                                    # fix 4: restore scheduled name if active, not original_name
                                    sched_name = _get_active_scheduled_name(uid)
                                    revert_name = sched_name if sched_name else (orig_name or '')
                                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                                                json={"business_connection_id": biz, "first_name": revert_name[:64]}, timeout=5)
                            elif auto_detect and (now - (last_tstart or 0)) > 240:
                                print(f"[DEBUG {uid}] ⏳ 4 мин AutoDetect прошло. Сброс.")
                                db_op("UPDATE users SET last_track_id = NULL, is_bio_reverted = 1 WHERE user_id = ?", (uid,))
                                if biz:
                                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountBio",
                                                json={"business_connection_id": biz, "bio": (orig or "")[:140]}, timeout=5)
                                    # fix 4: restore scheduled name if active, not original_name
                                    sched_name = _get_active_scheduled_name(uid)
                                    revert_name = sched_name if sched_name else (orig_name or '')
                                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                                                json={"business_connection_id": biz, "first_name": revert_name[:64]}, timeout=5)

                except Exception as e:
                    print(f"[DEBUG {uid}] ❌ Ошибка в итерации: {e}")
                    log_to_group(f"Ошибка в цикле monitor (User: {uid}): {e}")

        except Exception as e:
            print(f"[CRITICAL] Ошибка монитора: {e}")
            log_to_group(f"Критическая ошибка в monitor: {e}")

        cleanup_sc_start_times()
        time.sleep(7)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ns_") or c.data.startswith("np_"))
def nickname_schedule_cb(c):
    if c.from_user.id != ADMIN_ID:
        return
    uid = c.from_user.id
    data = c.data

    if data.startswith("np_apply:"):
        slot = int(data.split(":")[1])
        row = db_op("SELECT name FROM name_presets WHERE user_id = ? AND slot = ?", (uid, slot), fetchone=True)
        if not row:
            bot.answer_callback_query(c.id, f"⚠️ Слот {slot} пустой", show_alert=True)
            return
        name = row[0]
        u = db_op("SELECT biz_conn_id FROM users WHERE user_id = ?", (uid,), fetchone=True)
        if not u or not u[0]:
            bot.answer_callback_query(c.id, "❌ Бизнес аккаунт не подключён", show_alert=True)
            return
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                json={"business_connection_id": u[0], "first_name": name[:64]},
                timeout=5
            )
            if r.status_code == 200:
                bot.answer_callback_query(c.id, f"✅ Ник: {name}", show_alert=False)
            else:
                bot.answer_callback_query(c.id, f"❌ Ошибка Telegram: {r.status_code}", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(c.id, f"❌ {e}", show_alert=True)
        return

    elif data.startswith("np_edit:"):
        slot = int(data.split(":")[1])
        bot.answer_callback_query(c.id)
        msg = bot.send_message(c.message.chat.id, f"✏️ Введите новое имя для слота {slot}:")
        bot.register_next_step_handler(msg, _process_np_edit_input, uid, slot, c.message.chat.id, c.message.message_id)
        return

    if data == "ns_add":
        bot.answer_callback_query(c.id)
        msg = bot.send_message(
            c.message.chat.id,
            "📅 <b>Добавить никнейм</b>\n\n"
            "Отправьте в формате:\n"
            "<code>Имя ДД.ММ.ГГГГ</code> — на один день\n"
            "<code>Имя ДД.ММ.ГГГГ ДД.ММ.ГГГГ</code> — диапазон\n\n"
            "Имя может содержать пробелы — даты всегда в конце.\n"
            "Пример: <code>Денис ДР 🎂 16.05.2026</code>",
            parse_mode='HTML'
        )
        bot.register_next_step_handler(msg, _process_ns_add_input, c.message.chat.id, c.message.message_id)

    elif data.startswith("ns_del:"):
        try:
            del_id = int(data.split(":")[1])
            row = db_op("SELECT user_id, name, date_from, date_to FROM scheduled_names WHERE id = ? AND user_id = ?",
                        (del_id, uid), fetchone=True)
            if row:
                r_uid, sched_name, date_from, date_to = row
                flag_key = f"sched_active_{del_id}"
                flag = db_op("SELECT value FROM kv_flags WHERE key = ?", (flag_key,), fetchone=True)
                if flag:
                    u = db_op("SELECT biz_conn_id, original_name FROM users WHERE user_id = ?", (r_uid,), fetchone=True)
                    if u and u[0]:
                        biz, orig_name = u
                        try:
                            revert_name = orig_name or ''
                            r = requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                                json={"business_connection_id": biz, "first_name": revert_name[:64]},
                                timeout=5
                            )
                            if r.status_code == 200:
                                bot.answer_callback_query(c.id, f"↩️ Имя восстановлено: {revert_name or '(пусто)'}", show_alert=False)
                        except Exception as e:
                            bot.answer_callback_query(c.id, f"⚠️ Не удалось откатить: {e}", show_alert=True)
                db_op("DELETE FROM kv_flags WHERE key = ?", (flag_key,))
                db_op("DELETE FROM scheduled_names WHERE id = ? AND user_id = ?", (del_id, uid))
            else:
                bot.answer_callback_query(c.id, "❌ Запись не найдена", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(c.id, f"❌ Ошибка: {e}", show_alert=True)
        render_nickname_schedule(c.message.chat.id, uid, c.message.message_id)


def _process_ns_add_input(m, settings_chat_id, settings_msg_id):
    if m.from_user.id != ADMIN_ID:
        return
    import re as _re
    import pytz as _pytz
    from datetime import datetime as dt_cls

    text = (m.text or "").strip()
    date_pat = _re.compile(r'\d{2}\.\d{2}\.\d{4}')
    dates_found = date_pat.findall(text)

    if not dates_found:
        bot.send_message(m.chat.id, "❌ Не найдено дат. Формат: <code>Имя ДД.ММ.ГГГГ</code>", parse_mode='HTML')
        render_nickname_schedule(settings_chat_id, m.from_user.id, settings_msg_id)
        return

    if len(dates_found) >= 2:
        d_from_str, d_to_str = dates_found[0], dates_found[1]
    else:
        d_from_str = d_to_str = dates_found[0]

    name = text[:text.index(dates_found[0])].strip()
    if not name:
        bot.send_message(m.chat.id, "❌ Имя не может быть пустым.", parse_mode='HTML')
        render_nickname_schedule(settings_chat_id, m.from_user.id, settings_msg_id)
        return

    try:
        d_from = dt_cls.strptime(d_from_str, '%d.%m.%Y').strftime('%Y-%m-%d')
        d_to = dt_cls.strptime(d_to_str, '%d.%m.%Y').strftime('%Y-%m-%d')
    except ValueError:
        bot.send_message(m.chat.id, "❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ", parse_mode='HTML')
        render_nickname_schedule(settings_chat_id, m.from_user.id, settings_msg_id)
        return

    if d_from > d_to:
        bot.send_message(m.chat.id, "❌ Дата начала позже даты конца.", parse_mode='HTML')
        render_nickname_schedule(settings_chat_id, m.from_user.id, settings_msg_id)
        return

    overlaps = db_op(
        "SELECT id, name FROM scheduled_names WHERE user_id = ? AND date_from <= ? AND date_to >= ?",
        (ADMIN_ID, d_to, d_from), fetchall=True
    ) or []

    db_op("INSERT INTO scheduled_names (user_id, name, date_from, date_to) VALUES (?, ?, ?, ?)",
          (ADMIN_ID, name, d_from, d_to))

    today_str = dt_cls.now(_pytz.timezone('Asia/Yekaterinburg')).strftime('%Y-%m-%d')
    applied_now = ""
    if d_from <= today_str <= d_to:
        u = db_op("SELECT biz_conn_id FROM users WHERE user_id = ?", (ADMIN_ID,), fetchone=True)
        if u and u[0]:
            try:
                new_id = db_op("SELECT last_insert_rowid()", fetchone=True)
                new_id = new_id[0] if new_id else None
                r = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                    json={"business_connection_id": u[0], "first_name": name[:64]},
                    timeout=5
                )
                if r.status_code == 200 and new_id:
                    db_op("INSERT OR REPLACE INTO kv_flags (key, value) VALUES (?, ?)", (f"sched_active_{new_id}", '1'))
                    applied_now = " (применено сейчас)"
            except:
                pass

    overlap_note = ""
    if overlaps:
        overlap_note = "\n⚠️ Пересечение с: " + ", ".join(f"#{r[0]} {r[1]}" for r in overlaps)

    bot.send_message(m.chat.id,
        f"✅ Добавлено: <b>{html.escape(name)}</b> {d_from_str} → {d_to_str}{applied_now}{overlap_note}",
        parse_mode='HTML')
    render_nickname_schedule(settings_chat_id, m.from_user.id, settings_msg_id)


def _process_np_edit_input(m, user_id, slot, settings_chat_id, settings_msg_id):
    if m.from_user.id != ADMIN_ID:
        return
    name = (m.text or "").strip()
    if not name:
        bot.send_message(m.chat.id, "❌ Имя не может быть пустым.")
        render_nicks(settings_chat_id, user_id, settings_msg_id)
        return
    if len(name) > 64:
        bot.send_message(m.chat.id, "❌ Имя слишком длинное (макс. 64 символа).")
        render_nicks(settings_chat_id, user_id, settings_msg_id)
        return
    db_op("INSERT OR REPLACE INTO name_presets (user_id, slot, name) VALUES (?, ?, ?)", (user_id, slot, name))
    render_nicks(settings_chat_id, user_id, settings_msg_id)


@bot.message_handler(commands=['nickname'])
def nickname_cmd(m):
    if m.from_user.id != ADMIN_ID:
        return
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        _send_nickname_help(m.chat.id)
        return
    args = parts[1].strip()

    if args == 'list':
        rows = db_op("SELECT id, name, date_from, date_to FROM scheduled_names WHERE user_id = ?", (ADMIN_ID,), fetchall=True) or []
        if not rows:
            bot.send_message(m.chat.id, "📋 Расписание никнеймов пустое.")
            return
        import pytz as _pytz
        from datetime import datetime as dt_cls
        today = dt_cls.now(_pytz.timezone('Asia/Yekaterinburg')).strftime('%Y-%m-%d')
        lines = ["📋 <b>Расписание никнеймов:</b>"]
        for row_id, name, df, dt_val in rows:
            if today > dt_val:
                status = " <i>[истёк]</i>"
            elif df <= today <= dt_val:
                status = " ✅"
            else:
                status = ""
            lines.append(f"<code>#{row_id}</code> <b>{html.escape(name)}</b> | {df} → {dt_val}{status}")
        bot.send_message(m.chat.id, "\n".join(lines), parse_mode='HTML')
        return

    if args.startswith('del '):
        try:
            del_id = int(args[4:].strip())
            # check if this record was active — if so, revert name immediately (fix 3)
            row = db_op("SELECT user_id, name, date_from, date_to FROM scheduled_names WHERE id = ? AND user_id = ?",
                        (del_id, ADMIN_ID), fetchone=True)
            if row:
                uid, sched_name, date_from, date_to = row
                flag_key = f"sched_active_{del_id}"
                flag = db_op("SELECT value FROM kv_flags WHERE key = ?", (flag_key,), fetchone=True)
                if flag:
                    u = db_op("SELECT biz_conn_id, original_name FROM users WHERE user_id = ?", (uid,), fetchone=True)
                    if u and u[0]:
                        biz, orig_name = u
                        try:
                            revert_name = orig_name or ''
                            r = requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                                json={"business_connection_id": biz, "first_name": revert_name[:64]},
                                timeout=5
                            )
                            if r.status_code == 200:
                                bot.send_message(m.chat.id, f"↩️ Имя восстановлено: <b>{html.escape(revert_name or '(пусто)')}</b>", parse_mode='HTML')
                        except Exception as e:
                            bot.send_message(m.chat.id, f"⚠️ Не удалось откатить имя: {e}")
                db_op("DELETE FROM kv_flags WHERE key = ?", (flag_key,))
                db_op("DELETE FROM scheduled_names WHERE id = ? AND user_id = ?", (del_id, ADMIN_ID))
                bot.send_message(m.chat.id, f"✅ Запись #{del_id} удалена.")
            else:
                bot.send_message(m.chat.id, f"❌ Запись #{del_id} не найдена.")
        except Exception as e:
            bot.send_message(m.chat.id, f"❌ Ошибка: {e}")
        return

    # Format: /nickname <name> <DD.MM.YYYY> [DD.MM.YYYY]
    # Dates are always at the end, name can contain spaces
    import re as _re
    date_pat = _re.compile(r'\d{2}\.\d{2}\.\d{4}')
    dates_found = date_pat.findall(args)
    if not dates_found:
        _send_nickname_help(m.chat.id)
        return
    if len(dates_found) >= 2:
        d_from_str, d_to_str = dates_found[0], dates_found[1]
        # name is everything before first date
        name = args[:args.index(dates_found[0])].strip()
    else:
        d_from_str = d_to_str = dates_found[0]
        name = args[:args.index(dates_found[0])].strip()

    if not name:
        _send_nickname_help(m.chat.id)
        return

    from datetime import datetime as dt_cls
    try:
        d_from = dt_cls.strptime(d_from_str.strip(), '%d.%m.%Y').strftime('%Y-%m-%d')
        d_to = dt_cls.strptime(d_to_str.strip(), '%d.%m.%Y').strftime('%Y-%m-%d')
    except ValueError:
        bot.send_message(m.chat.id, "❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
        return

    if d_from > d_to:
        bot.send_message(m.chat.id, "❌ Дата начала не может быть позже даты конца.")
        return

    # fix 2: warn about overlapping records
    overlaps = db_op(
        "SELECT id, name, date_from, date_to FROM scheduled_names WHERE user_id = ? "
        "AND date_from <= ? AND date_to >= ?",
        (ADMIN_ID, d_to, d_from), fetchall=True
    ) or []
    overlap_warn = ""
    if overlaps:
        overlap_lines = ", ".join(f"#{r[0]} ({html.escape(r[1])})" for r in overlaps)
        overlap_warn = f"\n\n⚠️ Пересечение с: {overlap_lines}"

    db_op("INSERT INTO scheduled_names (user_id, name, date_from, date_to) VALUES (?, ?, ?, ?)",
          (ADMIN_ID, name.strip(), d_from, d_to))

    # fix 5: apply immediately if starts today
    import pytz as _pytz
    from datetime import datetime as dt_cls2
    today_str = dt_cls2.now(_pytz.timezone('Asia/Yekaterinburg')).strftime('%Y-%m-%d')
    applied_now = ""
    if d_from <= today_str <= d_to:
        u = db_op("SELECT biz_conn_id FROM users WHERE user_id = ?", (ADMIN_ID,), fetchone=True)
        if u and u[0]:
            try:
                new_id = db_op("SELECT last_insert_rowid()", fetchone=True)
                new_id = new_id[0] if new_id else None
                r = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/setBusinessAccountName",
                    json={"business_connection_id": u[0], "first_name": name.strip()[:64]},
                    timeout=5
                )
                if r.status_code == 200 and new_id:
                    db_op("INSERT OR REPLACE INTO kv_flags (key, value) VALUES (?, ?)", (f"sched_active_{new_id}", '1'))
                    applied_now = "\n✅ Имя применено прямо сейчас."
            except Exception as e:
                applied_now = f"\n⚠️ Не удалось применить сразу: {e}"

    bot.send_message(m.chat.id,
        f"✅ Запланировано:\n<b>{html.escape(name.strip())}</b>\n📅 {d_from_str} → {d_to_str}{overlap_warn}{applied_now}",
        parse_mode='HTML')
    log_action(m.from_user, 'nickname_schedule', f"{name} | {d_from} -> {d_to}")


def _send_nickname_help(chat_id):
    bot.send_message(chat_id,
        "📅 <b>Расписание никнеймов</b>\n\n"
        "<b>Добавить:</b>\n"
        "<code>/nickname Имя 16.05.2026</code> — на один день\n"
        "<code>/nickname Имя Фамилия 16.05.2026 20.05.2026</code> — диапазон\n\n"
        "<b>Посмотреть список:</b>\n"
        "<code>/nickname list</code>\n\n"
        "<b>Удалить:</b>\n"
        "<code>/nickname del &lt;ID&gt;</code>\n\n"
        "Имя может содержать пробелы — даты всегда в конце.\n"
        "Пример: <code>/nickname Денис ДР 🎂 16.05.2026 16.05.2026</code>",
        parse_mode='HTML')


if __name__ == '__main__':
    migrate_db()
    init_dbs()
    load_config_from_db()
    load_api_key_from_file()
    load_v2ray_proxy_from_file()
    auto_cleanup_old_messages()
    log_to_group("🚀 Бот запущен")
    threading.Thread(target=proxy_refresher_thread, daemon=True).start()
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=name_updater, daemon=True).start()
    bot.infinity_polling(allowed_updates=['message', 'callback_query', 'business_connection', 'business_message', 'edited_business_message', 'deleted_business_messages'])
