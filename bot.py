import logging
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.async_ import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import os
import json

# ========== ТОКЕН БОТА ==========
BOT_TOKEN = "ТВОЙ_ТОКЕН_СЮДА"

# ========== НАСТРОЙКИ ==========
SHEET_ID = "19aEpcf-lnOxHCjhYPDNfhWRprssK2nNgKYPH92S95IA"
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Названия для кнопок
SPORT_NAMES = {
    "MF": "⚽ Мужской футбол (ПФК)", "JF": "⚽ Женский футбол (ЖФК)",
    "HK": "🏒 Хоккей (ПХК)", "BG": "🏀 Баскетбол (ПБК)",
    "VB": "🏐 Волейбол (ПВК)", "MG": "🤾 Мужской гандбол (ПГК)",
    "ZHG": "🤾 Женский гандбол (ЖГК)", "PF": "⚽ Мини-футбол (МФК)",
    "BF": "🏖️ Пляжный футбол",
}

# ========== ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS ==========
def get_sheet():
    try:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        if creds_json:
            creds_dict = json.loads(creds_json)
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
        client = gspread.authorize(creds)
        return client.open_by_key(SHEET_ID).sheet1
    except Exception as e:
        logger.error(f"Ошибка подключения: {e}")
        return None

def get_matches():
    sheet = get_sheet()
    if not sheet:
        return []
    
    records = sheet.get_all_records()
    matches = []
    today = datetime.now()
    
    for row in records:
        if str(row.get("active", "")).upper() != "TRUE":
            continue
        
        date_str = row.get("date")
        if not date_str:
            continue
        
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d")
            if match_date < today.replace(hour=0, minute=0, second=0):
                continue
            
            time_str = row.get("time")
            if time_str and str(time_str).strip():
                try:
                    match_date = match_date.replace(hour=int(str(time_str)[:2]), minute=int(str(time_str)[3:5]))
                except:
                    pass
        except Exception as e:
            logger.warning(f"Ошибка даты {date_str}: {e}")
            continue
        
        matches.append({
            "match_id": f"{match_date.strftime('%Y%m%d')}_{row.get('opponent', '')}_{row.get('location_type', '')}",
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
    
    return sorted(matches, key=lambda x: x["date"])

def format_match(match):
    days = (match["date"] - datetime.now()).days
    home_away = "🏠 ДОМА" if match["location_type"] == "home" else "✈️ В ГОСТЯХ"
    sport_name = SPORT_NAMES.get(match["sport_code"], match["sport_code"])
    
    time_str = match["date"].strftime("%H:%M")
    if time_str == "00:00":
        time_str = "Время уточняется"
    
    location_parts = [p for p in [match.get("city"), match.get("stadium")] if p]
    location_str = ", ".join(location_parts) if location_parts else "Место уточняется"
    
    if match["boycott"] == "full":
        status = "❌ ПОЛНЫЙ БОЙКОТ!"
    elif match["boycott"] == "partial":
        status = "📺 Частичный бойкот — смотрим по ТВ"
    else:
        status = "✅ Идем на стадион!"
    
    text = (
        f"{home_away}\n{sport_name}\n🏆 {match['tournament']}\n🆚 {match['opponent']}\n"
        f"📅 {match['date'].strftime('%d.%m.%Y, %A')}\n⏰ {time_str}\n📍 {location_str}\n\n"
        f"⏰ До матча: {days} {'день' if days == 1 else 'дня' if days in [2,3,4] else 'дней'}\n{status}"
    )
    if match.get("notes"):
        text += f"\n📝 {match['notes']}"
    return text

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(name, callback_data=f"sub_{code}")] for code, name in SPORT_NAMES.items()]
    keyboard.append([InlineKeyboardButton("📋 Мои подписки", callback_data="my_subs")])
    keyboard.append([InlineKeyboardButton("📅 Ближайшие матчи", callback_data="next_all")])
    await update.message.reply_text("🥅 **ЦСКА Бот**\n\nВыбери команду:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data.startswith("sub_"):
        sport_code = data.split("_")[1]
        if "subscriptions" not in context.bot_data:
            context.bot_data["subscriptions"] = {}
        if user_id not in context.bot_data["subscriptions"]:
            context.bot_data["subscriptions"][user_id] = set()
        context.bot_data["subscriptions"][user_id].add(sport_code)
        await query.edit_message_text(f"✅ Вы подписались на {SPORT_NAMES[sport_code]}")
    
    elif data == "my_subs":
        subs = context.bot_data.get("subscriptions", {}).get(user_id, set())
        text = "📋 **Ваши подписки:**\n" + "\n".join(f"• {SPORT_NAMES[code]}" for code in subs) if subs else "❌ Вы ни на что не подписаны."
        await query.edit_message_text(text, parse_mode="Markdown")
    
    elif data == "next_all":
        matches = get_matches()
        if not matches:
            await query.edit_message_text("❌ Нет ближайших матчей")
            return
        text = "📅 **Ближайшие матчи:**\n\n"
        for match in matches[:10]:
            days = (match["date"] - datetime.now()).days
            emoji = "❌" if match["boycott"] == "full" else "📺" if match["boycott"] == "partial" else "✅"
            home_away = "🏠" if match["location_type"] == "home" else "✈️"
            text += f"{home_away} {match['date'].strftime('%d.%m')} {match['date'].strftime('%H:%M')} {match['team_name']} vs {match['opponent']} {emoji}\n"
        await query.edit_message_text(text, parse_mode="Markdown")

async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Проверка напоминаний...")
    matches = get_matches()
    subscriptions = context.bot_data.get("subscriptions", {})
    
    if "sent_reminders" not in context.bot_data:
        context.bot_data["sent_reminders"] = set()
    
    for match in matches:
        days = (match["date"] - datetime.now()).days
        
        # Определяем, нужно ли напоминать
        need_remind = False
        remind_type = ""
        
        if match["boycott"] == "full":
            continue
        
        if days == 0:  # В ДЕНЬ МАТЧА
            need_remind = True
            remind_type = "day0"
        elif days == 1:  # ЗА 1 ДЕНЬ
            need_remind = True
            remind_type = "day1"
        elif match["location_type"] == "home" and days == 7:
            need_remind = True
            remind_type = "day7"
        elif match["location_type"] == "away" and days == 14:
            need_remind = True
            remind_type = "day14"
        
        if not need_remind:
            continue
        
        # Уникальный ID для этого напоминания
        reminder_id = f"{match['match_id']}_{remind_type}"
        if reminder_id in context.bot_data["sent_reminders"]:
            continue
        
        # Отправляем подписчикам
        for user_id, subs in subscriptions.items():
            if match["sport_code"] in subs:
                try:
                    await context.bot.send_message(chat_id=user_id, text=format_match(match), parse_mode="Markdown")
                    logger.info(f"Отправлено {user_id}: {reminder_id}")
                except Exception as e:
                    logger.error(f"Ошибка {user_id}: {e}")
        
        context.bot_data["sent_reminders"].add(reminder_id)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.bot_data["subscriptions"] = {}
    app.bot_data["sent_reminders"] = set()
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, CronTrigger(hour=10, minute=0), args=[app])
    scheduler.start()
    
    logger.info("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
