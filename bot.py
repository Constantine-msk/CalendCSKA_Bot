import logging
import requests
import csv
import sqlite3
import os
import pytz
import aiohttp
from io import StringIO
from datetime import datetime, time as dtime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ========== ТОКЕН ==========
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 125741486

# ========== НАСТРОЙКИ ==========
CSV_URL = "https://raw.githubusercontent.com/Constantine-msk/CalendCSKA_Bot/main/schedule.csv"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SPORT_NAMES = {
    "MF": "⚽ ПФК ЦСКА",
    "JF": "⚽ ЖФК ЦСКА",
    "MF2": "⚽ ПФК ЦСКА-М",
    "HK": "🏒 ПХК ЦСКА",
    "VHL": "🏒 Звезда",
    "MHL": "🏒 Красная Армия",
    "BG": "🏀 ПБК ЦСКА",
    "BG2": "🏀 БК ЦСКА-2",
    "VB": "🏐 ПВК ЦСКА",
    "MG": "🤾 ПГК ЦСКА",
    "ZHG": "🤾 ЖГК ЦСКА",
    "PF": "⚽ МФК ЦСКА",
    "BF": "🏖️ КПФ ЦСКА",
}

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect("/app/bot.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER,
            sport_code TEXT,
            PRIMARY KEY (user_id, sport_code)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_reminders (
            reminder_id TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

def get_user_subs(user_id):
    conn = sqlite3.connect("/app/bot.db")
    rows = conn.execute(
        "SELECT sport_code FROM subscriptions WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    return set(r[0] for r in rows)

def toggle_sub(user_id, sport_code):
    conn = sqlite3.connect("/app/bot.db")
    exists = conn.execute(
        "SELECT 1 FROM subscriptions WHERE user_id=? AND sport_code=?",
        (user_id, sport_code)
    ).fetchone()
    if exists:
        conn.execute(
            "DELETE FROM subscriptions WHERE user_id=? AND sport_code=?",
            (user_id, sport_code)
        )
        action = "отписался от"
    else:
        conn.execute(
            "INSERT INTO subscriptions VALUES (?,?)",
            (user_id, sport_code)
        )
        action = "подписался на"
    conn.commit()
    conn.close()
    return action

def clear_user_subs(user_id):
    conn = sqlite3.connect("/app/bot.db")
    conn.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_subscriptions():
    conn = sqlite3.connect("/app/bot.db")
    rows = conn.execute("SELECT user_id, sport_code FROM subscriptions").fetchall()
    conn.close()
    result = {}
    for user_id, sport_code in rows:
        result.setdefault(user_id, set()).add(sport_code)
    return result

def is_reminder_sent(reminder_id):
    conn = sqlite3.connect("/app/bot.db")
    exists = conn.execute(
        "SELECT 1 FROM sent_reminders WHERE reminder_id=?", (reminder_id,)
    ).fetchone()
    conn.close()
    return bool(exists)

def mark_reminder_sent(reminder_id):
    conn = sqlite3.connect("/app/bot.db")
    conn.execute("INSERT OR IGNORE INTO sent_reminders VALUES (?)", (reminder_id,))
    conn.commit()
    conn.close()


# ========== ГОРОДА И КООРДИНАТЫ ==========
CITY_COORDS = {
    "Москва": (55.75, 37.62),
    "Санкт-Петербург": (59.93, 30.32),
    "Казань": (55.79, 49.12),
    "Краснодар": (45.04, 38.98),
    "Екатеринбург": (56.84, 60.60),
    "Уфа": (54.74, 55.97),
    "Самара": (53.20, 50.15),
    "Ростов-на-Дону": (47.23, 39.72),
    "Нижний Новгород": (56.33, 44.00),
    "Новосибирск": (54.99, 82.90),
}

WMO_CODES = {
    0: "☀️ Ясно",
    1: "🌤 Преимущественно ясно",
    2: "⛅️ Переменная облачность",
    3: "☁️ Пасмурно",
    45: "🌫 Туман",
    48: "🌫 Туман с изморозью",
    51: "🌦 Лёгкая морось",
    53: "🌦 Морось",
    55: "🌧 Сильная морось",
    61: "🌧 Лёгкий дождь",
    63: "🌧 Дождь",
    65: "🌧 Сильный дождь",
    71: "🌨 Лёгкий снег",
    73: "🌨 Снег",
    75: "❄️ Сильный снег",
    80: "🌦 Ливень",
    81: "🌧 Сильный ливень",
    95: "⛈ Гроза",
    96: "⛈ Гроза с градом",
    99: "⛈ Сильная гроза с градом",
}

# ========== ЗАГРУЗКА РАСПИСАНИЯ ==========
def get_manual_matches():
    """Загружает матчи добавленные вручную через /add_match"""
    matches = []
    now = datetime.now()
    try:
        conn = sqlite3.connect("/app/bot.db")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport_code TEXT, team_name TEXT, opponent TEXT,
                date TEXT, time TEXT, location_type TEXT,
                city TEXT, stadium TEXT, tournament TEXT,
                boycott TEXT, notes TEXT
            )
        """)
        rows = conn.execute("SELECT * FROM manual_matches").fetchall()
        conn.close()

        for row in rows:
            _, sport_code, team_name, opponent, date_str, time_str, location_type, city, stadium, tournament, boycott, notes = row
            try:
                match_date = datetime.strptime(date_str, "%Y-%m-%d")
                if time_str and time_str.strip():
                    try:
                        match_date = match_date.replace(
                            hour=int(time_str[:2]), minute=int(time_str[3:5])
                        )
                    except Exception:
                        pass
                if match_date < now:
                    continue
                matches.append({
                    "match_id": f"manual_{date_str}_{opponent}_{location_type}",
                    "sport_code": sport_code,
                    "team_name": team_name,
                    "opponent": opponent,
                    "date": match_date,
                    "location_type": location_type,
                    "city": city or "",
                    "stadium": stadium or "",
                    "tournament": tournament or "",
                    "boycott": boycott or "none",
                    "notes": notes or "",
                })
            except Exception as e:
                logger.warning(f"Ошибка в ручном матче: {e}")
    except Exception as e:
        logger.error(f"Ошибка загрузки ручных матчей: {e}")
    return matches


def get_group_subs(chat_id):
    conn = sqlite3.connect("/app/bot.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_subscriptions (
            chat_id INTEGER,
            sport_code TEXT,
            PRIMARY KEY (chat_id, sport_code)
        )
    """)
    rows = conn.execute(
        "SELECT sport_code FROM group_subscriptions WHERE chat_id=?", (chat_id,)
    ).fetchall()
    conn.close()
    return set(r[0] for r in rows)

def toggle_group_sub(chat_id, sport_code):
    conn = sqlite3.connect("/app/bot.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_subscriptions (
            chat_id INTEGER,
            sport_code TEXT,
            PRIMARY KEY (chat_id, sport_code)
        )
    """)
    exists = conn.execute(
        "SELECT 1 FROM group_subscriptions WHERE chat_id=? AND sport_code=?",
        (chat_id, sport_code)
    ).fetchone()
    if exists:
        conn.execute(
            "DELETE FROM group_subscriptions WHERE chat_id=? AND sport_code=?",
            (chat_id, sport_code)
        )
        action = "отписалась от"
    else:
        conn.execute(
            "INSERT INTO group_subscriptions VALUES (?,?)",
            (chat_id, sport_code)
        )
        action = "подписалась на"
    conn.commit()
    conn.close()
    return action

def clear_group_subs(chat_id):
    conn = sqlite3.connect("/app/bot.db")
    conn.execute("DELETE FROM group_subscriptions WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()

def get_all_group_subscriptions():
    conn = sqlite3.connect("/app/bot.db")
    try:
        rows = conn.execute("SELECT chat_id, sport_code FROM group_subscriptions").fetchall()
    except Exception:
        rows = []
    conn.close()
    result = {}
    for chat_id, sport_code in rows:
        result.setdefault(chat_id, set()).add(sport_code)
    return result

def get_matches():
    matches = []
    now = datetime.now()

    try:
        response = requests.get(CSV_URL)
        response.raise_for_status()
        response.encoding = "utf-8"

        csv_content = StringIO(response.text)
        reader = csv.DictReader(csv_content)

        for row in reader:
            if row.get("active", "").upper() != "TRUE":
                continue

            date_str = row.get("date")
            if not date_str:
                continue

            try:
                match_date = datetime.strptime(date_str, "%Y-%m-%d")

                time_str = row.get("time", "")
                if time_str and str(time_str).strip():
                    try:
                        time_part = str(time_str).split(".")[0][:5]
                        match_date = match_date.replace(
                            hour=int(time_part[:2]),
                            minute=int(time_part[3:5])
                        )
                    except Exception:
                        pass

                if match_date < now:
                    continue

                matches.append({
                    "match_id": (
                        f"{match_date.strftime('%Y%m%d')}"
                        f"_{row.get('opponent', '')}"
                        f"_{row.get('location_type', '')}"
                    ),
                    "sport_code": row.get("sport_code", ""),
                    "team_name": row.get("team_name", ""),
                    "opponent": row.get("opponent", ""),
                    "date": match_date,
                    "location_type": row.get("location_type", ""),
                    "city": row.get("city", ""),
                    "stadium": row.get("stadium", ""),
                    "tournament": row.get("tournament", ""),
                    "boycott": row.get("boycott", "none"),
                    "notes": row.get("notes", ""),
                })

            except Exception as e:
                logger.warning(f"Ошибка в строке: {e}")
                continue

        logger.info(f"Загружено {len(matches)} матчей из CSV")

    except Exception as e:
        logger.error(f"Ошибка загрузки CSV: {e}")

    # Добавляем ручные матчи
    manual = get_manual_matches()
    logger.info(f"Загружено {len(manual)} ручных матчей")
    matches.extend(manual)

    return sorted(matches, key=lambda x: x["date"])


async def get_weather(city: str, date) -> str:
    """Получает погоду для города на указанную дату (асинхронно)"""
    try:
        coords = None
        for city_name, c in CITY_COORDS.items():
            if city_name.lower() in city.lower():
                coords = c
                break

        if not coords:
            return ""

        lat, lon = coords
        target_date = date.strftime("%Y-%m-%d")

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
            "timezone": "Europe/Moscow",
            "start_date": target_date,
            "end_date": target_date,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()

        temp_max = round(data["daily"]["temperature_2m_max"][0])
        temp_min = round(data["daily"]["temperature_2m_min"][0])
        precip = data["daily"]["precipitation_sum"][0]
        wcode = data["daily"]["weathercode"][0]

        weather_desc = WMO_CODES.get(wcode, "Переменная облачность")
        rain_str = f"🌂 Осадки: {precip} мм" if precip > 0.5 else "☂️ Осадков не ожидается"

        return f"🌡 Погода: {temp_min}°..{temp_max}°C, {weather_desc}\n{rain_str}"
    except Exception as e:
        logger.warning(f"Погода недоступна: {e}")
        return ""

async def format_match(match):
    import locale
    try:
        locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")
    except Exception:
        pass

    now = datetime.now()
    days = (match["date"] - now).days
    home_away = "🏠 ДОМА" if match["location_type"] == "home" else "✈️ В ГОСТЯХ"
    sport_name = SPORT_NAMES.get(match["sport_code"], match["sport_code"])

    time_str = match["date"].strftime("%H:%M")
    if time_str == "00:00":
        time_str = "Время уточняется"
    else:
        time_str += " (МСК)"

    location_parts = []
    if match.get("city"):
        location_parts.append(match["city"])
    if match.get("stadium"):
        location_parts.append(match["stadium"])
    location_str = ", ".join(location_parts) if location_parts else "Место уточняется"

    if match["boycott"] == "full":
        status = "❌ ПОЛНЫЙ БОЙКОТ! (не идем, не смотрим)"
    elif match["boycott"] == "partial":
        status = "📺 Частичный бойкот — смотрим по ТВ"
    else:
        status = "✅ Идем на стадион!"

    if days == 0:
        days_str = "Сегодня! 🔥"
    elif days == 1:
        days_str = "1 день"
    elif days in [2, 3, 4]:
        days_str = f"{days} дня"
    else:
        days_str = f"{days} дней"

    text = (
        f"{home_away}\n"
        f"{match['team_name']}\n"
        f"🏆 {match['tournament']}\n"
        f"🆚 {match['opponent']}\n"
        f"📅 {match['date'].strftime('%d.%m.%Y, %A')}\n"
        f"⏰ {time_str}\n"
        f"📍 {location_str}\n\n"
        f"⏰ До матча: {days_str}\n"
        f"{status}"
    )
    # Погода только для домашних матчей или если есть город
    weather = ""
    if match.get("city"):
        weather = await get_weather(match["city"], match["date"])
    if weather:
        text += f"\n\n{weather}"
    if match.get("notes"):
        text += f"\n📝 {match['notes']}"
    return text

SBP_URL = "https://www.tinkoff.ru/rm/r_xRlEDFlILw.UuuGAqCeAk/0JWTr91979"
BOT_URL = "https://t.me/CalendCSKA_Bot"

# ========== КЛАВИАТУРА ==========
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Ближайшие матчи", callback_data="next_all")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="my_subs")],
        [InlineKeyboardButton("➕ Подписаться на команду", callback_data="subscribe_menu")],
        [InlineKeyboardButton("📊 Турнирные таблицы", callback_data="tables")],
        [InlineKeyboardButton("📅 Экспорт в календарь", callback_data="export_cal")],
        [InlineKeyboardButton("📤 Поделиться ботом", url="https://t.me/share/url?url=https://t.me/CalendCSKA_Bot&text=Бот+с+расписанием+матчей+ЦСКА+%F0%9F%94%B4%F0%9F%94%B5")],
        [InlineKeyboardButton("❤️ Поддержать бота", url=SBP_URL)],
    ])

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔴🔵 *Слава ЦСКА*\n\n"
        "Здесь расписание матчей всех армейских команд.\n"
        "Подпишись — получишь напоминание вовремя, "
        "чтобы успеть на стадион или к экрану.\n\n"
        "Выбери действие 👇",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def my_matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subs = get_user_subs(user_id)

    if not subs:
        await update.message.reply_text("❌ Вы ни на что не подписаны. Нажмите /start")
        return

    all_matches = get_matches()
    my_matches = [m for m in all_matches if m["sport_code"] in subs]

    if not my_matches:
        await update.message.reply_text("❌ Нет ближайших матчей по вашим подпискам")
        return

    text = "📅 *Ваши ближайшие матчи:*\n\n"
    for match in my_matches[:10]:
        emoji = "❌" if match["boycott"] == "full" else "📺" if match["boycott"] == "partial" else "✅"
        home_away = "🏠" if match["location_type"] == "home" else "✈️"
        time_str = match["date"].strftime("%H:%M")
        if time_str == "00:00":
            time_str = ""
        text += f"{home_away} {match['date'].strftime('%d.%m')} {time_str} {match['team_name']} vs {match['opponent']} {emoji}\n"

    keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== КНОПКИ ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "main_menu":
        await query.edit_message_text(
            "🥅 *Главное меню*\n\nВыберите действие:",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )

    elif data == "subscribe_menu":
        subs = get_user_subs(user_id)
        keyboard = []
        for code, name in SPORT_NAMES.items():
            label = f"✅ {name}" if code in subs else name
            keyboard.append([InlineKeyboardButton(label, callback_data=f"toggle_{code}")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])

        await query.edit_message_text(
            "📋 *Выбери команду:*\n\n"
            "✅ — уже подписан\n"
            "Нажми на команду, чтобы подписаться или отписаться.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("toggle_"):
        sport_code = data.split("_", 1)[1]
        action = toggle_sub(user_id, sport_code)
        subs = get_user_subs(user_id)

        keyboard = []
        for code, name in SPORT_NAMES.items():
            label = f"✅ {name}" if code in subs else name
            keyboard.append([InlineKeyboardButton(label, callback_data=f"toggle_{code}")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])

        subs_text = "\n".join(f"• {SPORT_NAMES[code]}" for code in subs) if subs else "Пока нет"

        await query.edit_message_text(
            f"✅ Вы {action} {SPORT_NAMES[sport_code]}\n\n"
            f"*Ваши подписки:*\n{subs_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "my_subs":
        subs = get_user_subs(user_id)
        if subs:
            text = "📋 *Ваши подписки:*\n\n" + "\n".join(f"• {SPORT_NAMES[code]}" for code in subs)
            text += "\n\nЧтобы отписаться, нажмите «Подписаться на команду» и выберите команду с ✅"
        else:
            text = "❌ Вы пока ни на что не подписаны."

        keyboard = [
            [InlineKeyboardButton("➕ Подписаться", callback_data="subscribe_menu")],
            [InlineKeyboardButton("🗑 Отписаться от всех", callback_data="unsubscribe_all")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
        ]
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "unsubscribe_all":
        clear_user_subs(user_id)
        await query.edit_message_text(
            "🗑 Вы отписались от *всех* команд.\n\n"
            "Чтобы снова подписаться, нажмите «Подписаться на команду».",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Подписаться", callback_data="subscribe_menu")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
            ])
        )

    elif data == "export_cal":
        user_id = update.effective_user.id
        subs = get_user_subs(user_id)

        if not subs:
            await query.edit_message_text(
                "❌ Вы ни на что не подписаны.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]])
            )
            return

        matches = get_matches()
        my_matches = [m for m in matches if m["sport_code"] in subs]

        if not my_matches:
            await query.edit_message_text(
                "❌ Нет ближайших матчей по вашим подпискам.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]])
            )
            return

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Calend.CSKA Bot//RU",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "X-WR-CALNAME:ЦСКА Матчи",
            "X-WR-TIMEZONE:Europe/Moscow",
        ]

        for match in my_matches:
            match_id = match["match_id"]
            team = match["team_name"]
            opponent = match["opponent"]
            tournament = match["tournament"]
            loc_type = "ДОМА" if match["location_type"] == "home" else "В ГОСТЯХ"
            city = match.get("city", "")
            stadium = match.get("stadium", "")
            notes = match.get("notes", "")
            dtstart = match["date"].strftime("%Y%m%dT%H%M%S")
            dtend = match["date"].replace(hour=min(match["date"].hour + 2, 23)).strftime("%Y%m%dT%H%M%S")
            location = ", ".join(filter(None, [city, stadium]))
            description = f"{tournament}. {loc_type}"
            if notes:
                description += f". {notes}"

            lines += [
                "BEGIN:VEVENT",
                f"UID:{match_id}@calendcska",
                f"DTSTART;TZID=Europe/Moscow:{dtstart}",
                f"DTEND;TZID=Europe/Moscow:{dtend}",
                f"SUMMARY:{team} vs {opponent}",
                f"LOCATION:{location}",
                f"DESCRIPTION:{description}",
                "END:VEVENT",
            ]

        lines.append("END:VCALENDAR")
        ics_content = "\r\n".join(lines)

        import io
        ics_file = io.BytesIO(ics_content.encode("utf-8"))
        ics_file.name = "cska_matches.ics"

        await query.message.reply_document(
            document=ics_file,
            filename="cska_matches.ics",
            caption=(
                f"📅 *Календарь матчей ЦСКА*\n\n"
                f"В файле {len(my_matches)} матчей по вашим подпискам.\n\n"
                "Откройте файл на телефоне — матчи добавятся в ваш календарь!"
            ),
            parse_mode="Markdown"
        )

    elif data == "tables":
        keyboard = [
            [InlineKeyboardButton("⚽ ПФК ЦСКА — РПЛ", url="https://premierliga.ru/tournament-table/")],
            [InlineKeyboardButton("⚽ ЖФК ЦСКА — Суперлига", url="https://wfl.ru/tournaments/superleague/table/")],
            [InlineKeyboardButton("⚽ МФК ЦСКА — Суперлига", url="https://amfr.ru/competitions/superleague/")],
            [InlineKeyboardButton("🏒 ПХК ЦСКА — КХЛ", url="https://www.khl.ru/standings/")],
            [InlineKeyboardButton("🏒 Звезда — ВХЛ", url="https://allhockey.ru/stat/vhl/table")],
            [InlineKeyboardButton("🏒 Красная Армия — МХЛ", url="https://mhl.khl.ru/standings/regular/")],
            [InlineKeyboardButton("🏀 ПБК ЦСКА — ЕЛ ВТБ", url="https://www.vtbleague.ru/ru/standings")],
            [InlineKeyboardButton("🏀 ПБК ЦСКА-2 — ЕМЛ ВТБ", url="https://www.vtbleague.ru/ru/standings")],
            [InlineKeyboardButton("🏐 ПВК ЦСКА — Суперлига", url="https://www.volley.ru/competitions/superleague/table/")],
            [InlineKeyboardButton("🤾 ПГК ЦСКА — Суперлига", url="https://handball.ru/tournaments/superleague/")],
            [InlineKeyboardButton("🤾 ЖГК ЦСКА — Суперлига", url="https://handball.ru/tournaments/superleague-women/")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
        ]
        await query.edit_message_text(
            "📊 *Турнирные таблицы*\n\nВыбери лигу — откроется официальный сайт:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "share":
        share_text = (
            "📤 *Поделиться ботом*\n\n"
            "Отправь эту ссылку друзьям-болельщикам ЦСКА:\n\n"
            f"👉 {BOT_URL}\n\n"
            "Бот пришлёт напоминания о матчах всех армейских команд — "
            "футбол, хоккей, баскетбол, волейбол и другие."
        )
        keyboard = [
            [InlineKeyboardButton("📤 Поделиться", url=f"https://t.me/share/url?url={BOT_URL}&text=Бот+с+расписанием+матчей+ЦСКА")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
        ]
        await query.edit_message_text(share_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


    elif data.startswith("gtoggle_"):
        parts = data.split("_")
        chat_id = int(parts[1])
        sport_code = parts[2]
        user = update.effective_user
        member = await context.bot.get_chat_member(chat_id, user.id)
        if member.status not in ["administrator", "creator"]:
            await query.answer("⛔ Только администраторы могут менять подписки.", show_alert=True)
            return
        action = toggle_group_sub(chat_id, sport_code)
        subs = get_group_subs(chat_id)
        keyboard = []
        for code, name in SPORT_NAMES.items():
            label = f"✅ {name}" if code in subs else name
            keyboard.append([InlineKeyboardButton(label, callback_data=f"gtoggle_{chat_id}_{code}")])
        keyboard.append([InlineKeyboardButton("🔙 Закрыть", callback_data=f"gclose_{chat_id}")])
        subs_text = "\n".join(f"• {SPORT_NAMES[c]}" for c in subs) if subs else "Пока нет"
        await query.edit_message_text(
            f"✅ Группа {action} {SPORT_NAMES[sport_code]}\n\n*Подписки:*\n{subs_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("gclose_"):
        await query.delete_message()

    elif data == "next_all":
        matches = get_matches()
        if not matches:
            text = "❌ Нет ближайших матчей"
        else:
            text = "📅 *Ближайшие матчи:*\n\n"
            for match in matches[:10]:
                emoji = "❌" if match["boycott"] == "full" else "📺" if match["boycott"] == "partial" else "✅"
                home_away = "🏠" if match["location_type"] == "home" else "✈️"
                time_str = match["date"].strftime("%H:%M")
                if time_str == "00:00":
                    time_str = ""
                text += f"{home_away} {match['date'].strftime('%d.%m')} {time_str} {match['team_name']} vs {match['opponent']} {emoji}\n"

        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ========== НАПОМИНАНИЯ ==========
async def _send_to_subscribers(context, match, remind_type, subscriptions):
    """Отправляет напоминание всем подписчикам матча"""
    reminder_id = f"{match['match_id']}_{remind_type}"
    if is_reminder_sent(reminder_id):
        return
    for user_id, subs in subscriptions.items():
        if match["sport_code"] in subs:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=await format_match(match),
                    parse_mode="Markdown"
                )
                logger.info(f"Отправлено {user_id}: {reminder_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки {user_id}: {e}")
    mark_reminder_sent(reminder_id)

async def send_reminders_daily(context: ContextTypes.DEFAULT_TYPE):
    """Запускается в 10:00 МСК — напоминания за 14/7/1 день и в день матча"""
    logger.info("Ежедневная проверка напоминаний...")
    matches = get_matches()
    subscriptions = get_all_subscriptions()

    for match in matches:
        now = datetime.now()
        diff = match["date"] - now
        if diff.total_seconds() < 0:
            continue
        if match["boycott"] == "full":
            continue

        days = diff.days
        hours_left = diff.total_seconds() / 3600

        if match["date"].date() == now.date() and hours_left > 3:
            await _send_to_subscribers(context, match, "day0", subscriptions)
        elif days == 1:
            await _send_to_subscribers(context, match, "day1", subscriptions)
        elif match["location_type"] == "home" and days == 7:
            await _send_to_subscribers(context, match, "day7", subscriptions)
        elif match["location_type"] == "away" and days == 14:
            await _send_to_subscribers(context, match, "day14", subscriptions)


    # Рассылка в группы
    group_subscriptions = get_all_group_subscriptions()
    for chat_id, subs in group_subscriptions.items():
        if match["sport_code"] in subs:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=await format_match(match),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Group send error {chat_id}: {e}")
async def group_subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подписать группу на уведомления. Только для админов группы."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда только для групп.")
        return

    # Проверяем что пользователь — админ группы
    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ["administrator", "creator"]:
        await update.message.reply_text("⛔ Только администраторы группы могут управлять подписками.")
        return

    subs = get_group_subs(chat.id)
    keyboard = []
    for code, name in SPORT_NAMES.items():
        label = f"✅ {name}" if code in subs else name
        keyboard.append([InlineKeyboardButton(label, callback_data=f"gtoggle_{chat.id}_{code}")])
    keyboard.append([InlineKeyboardButton("🔙 Закрыть", callback_data=f"gclose_{chat.id}")])

    await update.message.reply_text(
        "📋 *Подписки группы на уведомления ЦСКА:*\n\n"
        "✅ — уже подписана\n"
        "Нажми на команду чтобы подписаться или отписаться.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def group_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущие подписки группы."""
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("Эта команда только для групп.")
        return

    subs = get_group_subs(chat.id)
    if subs:
        text = "📋 *Подписки этой группы:*\n\n" + "\n".join(f"• {SPORT_NAMES[c]}" for c in subs)
    else:
        text = "❌ Группа ни на что не подписана.\nИспользуй /group_subscribe чтобы подписаться."
    await update.message.reply_text(text, parse_mode="Markdown")


async def tables_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ссылки на турнирные таблицы"""
    keyboard = [
        [InlineKeyboardButton("⚽ РПЛ (мужской футбол)", url="https://premierliga.ru/tournament-table/")],
        [InlineKeyboardButton("⚽ Суперлига (женский футбол)", url="https://wfl.ru/tournaments/superleague/table/")],
        [InlineKeyboardButton("🏒 КХЛ", url="https://www.khl.ru/standings/")],
        [InlineKeyboardButton("🏀 Единая лига ВТБ", url="https://www.vtbleague.ru/ru/standings")],
        [InlineKeyboardButton("🏐 Суперлига (волейбол муж.)", url="https://www.volley.ru/competitions/superleague/table/")],
        [InlineKeyboardButton("🤾 Суперлига (гандбол муж.)", url="https://handball.ru/tournaments/superleague/")],
        [InlineKeyboardButton("🤾 Суперлига (гандбол жен.)", url="https://handball.ru/tournaments/superleague-women/")],
        [InlineKeyboardButton("⚽ Суперлига (мини-футбол)", url="https://amfr.ru/competitions/superleague/")],
    ]
    await update.message.reply_text(
        "📊 *Турнирные таблицы*\n\n"
        "Выбери лигу — откроется официальный сайт:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def export_calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерирует .ics файл с матчами по подпискам пользователя"""
    user_id = update.effective_user.id
    subs = get_user_subs(user_id)

    if not subs:
        await update.message.reply_text("❌ Вы ни на что не подписаны. Нажмите /start")
        return

    matches = get_matches()
    my_matches = [m for m in matches if m["sport_code"] in subs]

    if not my_matches:
        await update.message.reply_text("❌ Нет ближайших матчей по вашим подпискам")
        return

    # Генерируем .ics
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Calend.CSKA Bot//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ЦСКА Матчи",
        "X-WR-TIMEZONE:Europe/Moscow",
    ]

    for match in my_matches:
        uid = f"{match['match_id']}@calendcska"
        dtstart = match["date"].strftime("%Y%m%dT%H%M%S")
        dtend = (match["date"].replace(hour=match["date"].hour + 2)).strftime("%Y%m%dT%H%M%S")
        summary = f"{match['team_name']} vs {match['opponent']}"
        location = ", ".join(filter(None, [match.get("city", ""), match.get("stadium", "")]))
        description = f"{match['tournament']}. {'ДОМА' if match['location_type'] == 'home' else 'В ГОСТЯХ'}"
        if match.get("notes"):
            description += f". {match['notes']}"
        if match["boycott"] == "full":
            description += ". ❌ ПОЛНЫЙ БОЙКОТ"
        elif match["boycott"] == "partial":
            description += ". 📺 Частичный бойкот"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART;TZID=Europe/Moscow:{dtstart}",
            f"DTEND;TZID=Europe/Moscow:{dtend}",
            f"SUMMARY:{summary}",
            f"LOCATION:{location}",
            f"DESCRIPTION:{description}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    ics_content = "\r\n".join(lines)

    # Отправляем файл
    import io
    ics_bytes = ics_content.encode("utf-8")
    ics_file = io.BytesIO(ics_bytes)
    ics_file.name = "cska_matches.ics"

    await update.message.reply_document(
        document=ics_file,
        filename="cska_matches.ics",
        caption=(
            "📅 *Календарь матчей ЦСКА*\n\n"
            f"В файле {len(my_matches)} матчей по вашим подпискам.\n\n"
            "Откройте файл на телефоне — матчи добавятся в ваш календарь!"
        ),
        parse_mode="Markdown"
    )


# ========== ТРИГГЕРЫ В ГРУППАХ ==========
TRIGGERS = {
    "цска": "Всегда будет первым! 🔴🔵",
    "я никогда не устану повторять": "Е5, Спартак, е5! 🐷",
    "мы цска": "Мы победим! ✊🔴🔵",
}

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Реагирует на ключевые слова в группах"""
    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        return

    message = update.message
    if not message or not message.text:
        return

    text_lower = message.text.lower().strip()

    for trigger, response in TRIGGERS.items():
        if trigger in text_lower:
            await message.reply_text(response)
            return

# ========== АДМИН ==========
def admin_only(func):
    """Декоратор — только для админа"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Нет доступа.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def result_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправить результат матча подписчикам. /result код|соперник|счёт|комментарий"""
    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "`/result код|соперник|счёт|комментарий`\n\n"
            "Пример:\n"
            "`/result MF|Локомотив|2:1|Победа в дерби!`\n\n"
            "Коды: MF, JF, HK, BG, VB, MG, ZHG, PF, BF",
            parse_mode="Markdown"
        )
        return

    raw = " ".join(context.args)
    parts = raw.split("|")
    if len(parts) < 3:
        await update.message.reply_text("❌ Нужно минимум 3 поля: код|соперник|счёт")
        return

    sport_code = parts[0].strip()
    opponent = parts[1].strip()
    score = parts[2].strip()
    comment = parts[3].strip() if len(parts) > 3 else ""
    sport_name = SPORT_NAMES.get(sport_code, sport_code)

    text = (
        f"🏁 *Матч завершён!*\n\n"
        f"{sport_name}\n"
        f"🆚 ЦСКА — {opponent}\n"
        f"🔢 Счёт: *{score}*"
    )
    if comment:
        text += f"\n\n💬 {comment}"

    subscriptions = get_all_subscriptions()
    sent = 0
    failed = 0
    for user_id, subs in subscriptions.items():
        if sport_code in subs:
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                sent += 1
            except Exception as e:
                logger.error(f"Result error {user_id}: {e}")
                failed += 1

    await update.message.reply_text(
        f"✅ Результат отправлен!\n\nПолучили: {sent}\nОшибок: {failed}"
    )

@admin_only
async def reset_reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбросить все отправленные напоминания"""
    conn = sqlite3.connect("/app/bot.db")
    count = conn.execute("SELECT COUNT(*) FROM sent_reminders").fetchone()[0]
    conn.execute("DELETE FROM sent_reminders")
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"✅ Сброшено {count} напоминаний.\n\n"
        f"Завтра в 10:00 все актуальные напоминания отправятся заново."
    )

@admin_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика пользователей"""
    conn = sqlite3.connect("/app/bot.db")
    total_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM subscriptions").fetchone()[0]
    rows = conn.execute(
        "SELECT sport_code, COUNT(*) as cnt FROM subscriptions GROUP BY sport_code ORDER BY cnt DESC"
    ).fetchall()
    conn.close()

    text = f"📊 *Статистика*\n\nВсего пользователей с подписками: *{total_users}*\n\n*По командам:*\n"
    for sport_code, cnt in rows:
        name = SPORT_NAMES.get(sport_code, sport_code)
        text += f"• {name}: {cnt}\n"

    await update.message.reply_text(text, parse_mode="Markdown")

@admin_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка всем пользователям. Использование: /broadcast Текст сообщения"""
    if not context.args:
        await update.message.reply_text(
            "Использование: `/broadcast Текст сообщения`\n\nПример:\n`/broadcast Сегодня хоккей в 19:00, приходите!`",
            parse_mode="Markdown"
        )
        return

    text = " ".join(context.args)
    conn = sqlite3.connect("/app/bot.db")
    user_ids = [row[0] for row in conn.execute("SELECT DISTINCT user_id FROM subscriptions").fetchall()]
    conn.close()

    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"📢 {text}")
            sent += 1
        except Exception as e:
            logger.error(f"Broadcast error {user_id}: {e}")
            failed += 1

    await update.message.reply_text(
        f"✅ Рассылка завершена\n\nОтправлено: {sent}\nОшибок: {failed}"
    )

@admin_only
async def add_match_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Добавить матч вручную.
    Использование: /add_match sport_code|opponent|date(YYYY-MM-DD)|time(HH:MM)|home/away|city|stadium|tournament|boycott|notes
    Пример: /add_match HK|Динамо|2025-06-01|19:00|home|Москва|ЦСКА Арена|КХЛ|none|
    """
    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "`/add_match код|соперник|дата|время|дом/гость|город|стадион|турнир|бойкот|заметки`\n\n"
            "Пример:\n"
            "`/add_match HK|Динамо|2025-06-01|19:00|home|Москва|ЦСКА Арена|КХЛ|none|`\n\n"
            "Коды видов спорта: MF, JF, HK, BG, VB, MG, ZHG, PF, BF\n"
            "Бойкот: none / partial / full",
            parse_mode="Markdown"
        )
        return

    raw = " ".join(context.args)
    parts = raw.split("|")
    if len(parts) < 9:
        await update.message.reply_text("❌ Неверный формат. Нужно минимум 9 полей через |")
        return

    try:
        sport_code = parts[0].strip()
        opponent = parts[1].strip()
        date_str = parts[2].strip()
        time_str = parts[3].strip()
        location_type = parts[4].strip()
        city = parts[5].strip()
        stadium = parts[6].strip()
        tournament = parts[7].strip()
        boycott = parts[8].strip()
        notes = parts[9].strip() if len(parts) > 9 else ""

        team_name = SPORT_NAMES.get(sport_code, sport_code)

        conn = sqlite3.connect("/app/bot.db")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport_code TEXT,
                team_name TEXT,
                opponent TEXT,
                date TEXT,
                time TEXT,
                location_type TEXT,
                city TEXT,
                stadium TEXT,
                tournament TEXT,
                boycott TEXT,
                notes TEXT
            )
        """)
        conn.execute(
            "INSERT INTO manual_matches VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)",
            (sport_code, team_name, opponent, date_str, time_str, location_type, city, stadium, tournament, boycott, notes)
        )
        conn.commit()
        conn.close()

        await update.message.reply_text(
            f"✅ Матч добавлен!\n\n"
            f"🏷 {team_name}\n"
            f"🆚 {opponent}\n"
            f"📅 {date_str} {time_str}\n"
            f"📍 {city}, {stadium}\n"
            f"🏆 {tournament}\n"
            f"{'🏠 Дома' if location_type == 'home' else '✈️ В гостях'}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ========== ЗАПУСК ==========
async def set_bot_commands(app: Application):
    commands = [
        BotCommand("start", "🚀 Главное меню"),
        BotCommand("my_matches", "📅 Мои ближайшие матчи"),
        BotCommand("tables", "📊 Турнирные таблицы"),
        BotCommand("export", "📅 Экспорт матчей в календарь"),
    ]
    await app.bot.set_my_commands(commands)

    # Отдельные команды для админа
    admin_commands = commands + [
        BotCommand("stats", "📊 Статистика пользователей"),
        BotCommand("broadcast", "📢 Рассылка всем"),
        BotCommand("add_match", "➕ Добавить матч вручную"),
        BotCommand("reset_reminders", "🔄 Сбросить отправленные напоминания"),
        BotCommand("result", "🏁 Отправить результат матча"),
    ]
    from telegram import BotCommandScopeChat
    await app.bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my_matches", my_matches_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("add_match", add_match_command))
    app.add_handler(CommandHandler("reset_reminders", reset_reminders_command))
    app.add_handler(CommandHandler("result", result_command))
    app.add_handler(CommandHandler("tables", tables_command))
    app.add_handler(CommandHandler("export", export_calendar_command))
    app.add_handler(CommandHandler("group_subscribe", group_subscribe_command))
    app.add_handler(CommandHandler("group_status", group_status_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, group_message_handler))

    # Два планировщика:
    # 1. В 10:00 МСК — напоминания за 7/14/1 день и в день матча
    moscow = pytz.timezone("Europe/Moscow")
    app.job_queue.run_daily(
        send_reminders_daily,
        time=dtime(10, 0, tzinfo=moscow)
    )

    app.post_init = set_bot_commands

    logger.info("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
