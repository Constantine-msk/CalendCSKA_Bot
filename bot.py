import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8635943328:AAGWbMnRWXTcrxgF_BWuKbkJ3ZdOMYh6Qmo"  # Замени на токен от @BotFather
SHEET_ID = "19aEpcf-lnOxHCjhYPDNfhWRprssK2nNgKYPH92S95IA"  # ID твоей таблицы

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Правила бойкота
BOYCOTT_RULES = {
    "MF": {  # Мужской футбол
        "Чемпионат России": "partial",
        "Финал Кубка России": "partial",
        "Суперкубок России": "partial",
        "default": "none"
    },
    "MG": {"default": "full"},  # Мужской гандбол — полный бойкот
    "JF": {"default": "none"},   # Женский футбол — ходим
    "ZHG": {"default": "none"},  # Женский гандбол — ходим
    "BG": {"default": "none"},   # Баскетбол — ходим
    "VB": {"default": "none"},   # Волейбол — ходим
    "HK": {"default": "none"},   # Хоккей — ходим
    "PF": {"default": "none"},   # Мини-футбол — ходим
    "BF": {"default": "none"},   # Пляжный футбол — ходим
}

# Названия видов спорта для кнопок
SPORT_NAMES = {
    "MF": "⚽ Мужской футбол (ПФК)",
    "JF": "⚽ Женский футбол (ЖФК)",
    "HK": "🏒 Хоккей (ПХК)",
    "BG": "🏀 Баскетбол (ПБК)",
    "VB": "🏐 Волейбол (ПВК)",
    "MG": "🤾 Мужской гандбол (ПГК)",
    "ZHG": "🤾 Женский гандбол (ЖГК)",
    "PF": "⚽ Мини-футбол (МФК)",
    "BF": "🏖️ Пляжный футбол"
}

SPORT_CODES = {v: k for k, v in SPORT_NAMES.items()}

# ========== РАБОТА С GOOGLE SHEETS ==========
def get_google_sheet():
    """Подключение к Google Sheets"""
    try:
        # Пытаемся получить credentials из переменных окружения (для хостинга)
        import json
        creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        if creds_json:
            creds_dict = json.loads(creds_json)
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            # Локальный режим — ищем файл credentials.json
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
        client = gspread.authorize(creds)
        return client.open_by_key(SHEET_ID).sheet1
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

def get_matches_from_sheet() -> List[Dict]:
    """Получает список активных матчей из таблицы"""
    sheet = get_google_sheet()
    if not sheet:
        return []
    
    records = sheet.get_all_records()
    matches = []
    
    for row in records:
        if str(row.get("active", "")).upper() != "TRUE":
            continue
        
        # Парсим дату
        date_str = row.get("date")
        if not date_str:
            continue
        
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            logger.warning(f"Неверный формат даты: {date_str}")
            continue
        
        # Парсим время
        time_str = row.get("time")
        if time_str and str(time_str).strip():
            try:
                # Время может быть в формате "19:30" или "19:30:00"
                time_part = str(time_str).split(".")[0][:5]
                match_date = match_date.replace(hour=int(time_part[:2]), minute=int(time_part[3:5]))
            except:
                pass
        
        # Определяем реальный бойкот (из таблицы или из правил)
        boycott = row.get("boycott", "none")
        if boycott == "none" or not boycott:
            sport_code = row.get("sport_code", "")
            tournament = row.get("tournament", "")
            rules = BOYCOTT_RULES.get(sport_code, {})
            if tournament in rules:
                boycott = rules[tournament]
            else:
                boycott = rules.get("default", "none")
        
        match = {
            "match_id": row.get("match_id"),
            "sport_code": row.get("sport_code"),
            "team_name": row.get("team_name"),
            "opponent": row.get("opponent"),
            "date": match_date,
            "location_type": row.get("location_type"),
            "city": row.get("city", ""),
            "stadium": row.get("stadium", ""),
            "tournament": row.get("tournament", ""),
            "boycott": boycott,
            "notes": row.get("notes", ""),
        }
        matches.append(match)
    
    logger.info(f"Загружено {len(matches)} активных матчей")
    return matches

# ========== ЛОГИКА НАПОМИНАНИЙ ==========
def days_until_match(match_date: datetime) -> int:
    """Возвращает количество дней до матча"""
    delta = match_date - datetime.now()
    return delta.days

def should_remind_today(match: Dict) -> bool:
    """Проверяет, нужно ли отправить напоминание сегодня"""
    if match["boycott"] == "full":
        return False
    
    days = days_until_match(match["date"])
    
    if match["location_type"] == "home":
        return days == 7
    else:  # away
        return days == 14

def format_match_message(match: Dict) -> str:
    """Форматирует сообщение о матче"""
    days = days_until_match(match["date"])
    home_away = "🏠 ДОМА" if match["location_type"] == "home" else "✈️ В ГОСТЯХ"
    
    # Форматируем время
    time_str = match["date"].strftime("%H:%M")
    if time_str == "00:00":
        time_str = "Время уточняется"
    
    # Форматируем место
    location_parts = []
    if match["city"]:
        location_parts.append(match["city"])
    if match["stadium"]:
        location_parts.append(match["stadium"])
    location_str = ", ".join(location_parts) if location_parts else "Место уточняется"
    
    # Название спорта
    sport_name = SPORT_NAMES.get(match["sport_code"], match["sport_code"])
    
    # Статус бойкота
    if match["boycott"] == "full":
        status = "❌ ПОЛНЫЙ БОЙКОТ! (не идем, не смотрим)"
        emoji = "❌"
    elif match["boycott"] == "partial":
        status = "📺 Частичный бойкот — смотрим по ТВ"
        emoji = "📺"
    else:
        status = "✅ Идем на стадион!"
        emoji = "✅"
    
    text = (
        f"{home_away}\n"
        f"{sport_name}\n"
        f"🏆 {match['tournament']}\n"
        f"🆚 {match['opponent']}\n"
        f"📅 {match['date'].strftime('%d.%m.%Y, %A')}\n"
        f"⏰ {time_str}\n"
        f"📍 {location_str}\n"
        f"\n"
        f"⏰ До матча: {days} {'день' if days == 1 else 'дня' if days in [2,3,4] else 'дней'}\n"
        f"{emoji} {status}"
    )
    
    if match["notes"]:
        text += f"\n📝 {match['notes']}"
    
    return text

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    keyboard = [[InlineKeyboardButton(name, callback_data=f"sub_{code}")] 
                for code, name in SPORT_NAMES.items()]
    keyboard.append([InlineKeyboardButton("📋 Мои подписки", callback_data="my_subs")])
    keyboard.append([InlineKeyboardButton("📅 Календарь на месяц", callback_data="monthly_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🥅 **ЦСКА Бот**\n\n"
        "Выбери команду, чтобы подписаться на напоминания о матчах:\n\n"
        "• Домашние матчи → напоминаем за **7 дней**\n"
        "• Гостевые матчи → напоминаем за **14 дней**\n\n"
        "Бойкот-матчи отмечены особо.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    if data.startswith("sub_"):
        sport_code = data.split("_")[1]
        # Сохраняем подписку (в памяти или БД)
        if "subscriptions" not in context.bot_data:
            context.bot_data["subscriptions"] = {}
        if user_id not in context.bot_data["subscriptions"]:
            context.bot_data["subscriptions"][user_id] = set()
        context.bot_data["subscriptions"][user_id].add(sport_code)
        
        await query.edit_message_text(
            f"✅ Вы подписались на напоминания о матчах **{SPORT_NAMES[sport_code]}**",
            parse_mode="Markdown"
        )
    
    elif data == "my_subs":
        subs = context.bot_data.get("subscriptions", {}).get(user_id, set())
        if subs:
            text = "📋 **Ваши подписки:**\n" + "\n".join(f"• {SPORT_NAMES[code]}" for code in subs)
        else:
            text = "❌ Вы пока не подписаны ни на одну команду.\nИспользуйте /start, чтобы выбрать."
        await query.edit_message_text(text, parse_mode="Markdown")
    
    elif data == "monthly_menu":
        keyboard = [[InlineKeyboardButton(name, callback_data=f"monthly_{code}")] 
                    for code, name in SPORT_NAMES.items()]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📅 Выбери команду для календаря:", reply_markup=reply_markup)
    
    elif data.startswith("monthly_"):
        sport_code = data.split("_")[1]
        matches = get_matches_from_sheet()
        matches = [m for m in matches if m["sport_code"] == sport_code]
        
        # Сортируем и фильтруем на ближайший месяц
        today = datetime.now()
        next_month = today + timedelta(days=30)
        matches = [m for m in matches if today <= m["date"] <= next_month]
        matches.sort(key=lambda x: x["date"])
        
        if not matches:
            await query.edit_message_text(f"❌ Нет ближайших матчей у {SPORT_NAMES[sport_code]}")
            return
        
        text = f"📅 **Календарь {SPORT_NAMES[sport_code]} на месяц**\n\n"
        for match in matches:
            days = days_until_match(match["date"])
            home_away = "🏠" if match["location_type"] == "home" else "✈️"
            boycott_mark = " ❌" if match["boycott"] == "full" else " 📺" if match["boycott"] == "partial" else ""
            text += f"{home_away} {match['date'].strftime('%d.%m')} {match['date'].strftime('%H:%M')} vs {match['opponent']}{boycott_mark}\n"
        
        await query.edit_message_text(text, parse_mode="Markdown")

# ========== ФОНОВАЯ ЗАДАЧА ==========
async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет напоминания о матчах"""
    logger.info("Проверка напоминаний...")
    
    matches = get_matches_from_sheet()
    subscriptions = context.bot_data.get("subscriptions", {})
    
    for match in matches:
        if not should_remind_today(match):
            continue
        
        sport_code = match["sport_code"]
        match_id = match["match_id"]
        
        # Проверяем, не отправляли ли уже
        if "sent_reminders" not in context.bot_data:
            context.bot_data["sent_reminders"] = set()
        if match_id in context.bot_data["sent_reminders"]:
            continue
        
        # Отправляем всем подписанным на этот вид спорта
        for user_id, subs in subscriptions.items():
            if sport_code in subs:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=format_match_message(match),
                        parse_mode="Markdown"
                    )
                    logger.info(f"Напоминание отправлено пользователю {user_id} о матче {match_id}")
                except Exception as e:
                    logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
        
        context.bot_data["sent_reminders"].add(match_id)

# ========== ЗАПУСК ==========
def main():
    """Запуск бота"""
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Настройка планировщика (проверка каждый день в 10:00)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, CronTrigger(hour=10, minute=0), args=[app])
    scheduler.start()
    
    # Инициализируем хранилища
    app.bot_data["subscriptions"] = {}
    app.bot_data["sent_reminders"] = set()
    
    logger.info("Бот запущен и ждет команды...")
    app.run_polling()

if __name__ == "__main__":
    main()
