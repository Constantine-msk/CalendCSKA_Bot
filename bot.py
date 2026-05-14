import logging
import requests
import csv
from io import StringIO
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # ← ИСПРАВЛЕНО!
from apscheduler.triggers.cron import CronTrigger

# ========== ТОКЕН БОТА ==========
BOT_TOKEN = "8635943328:AAGWbMnRWXTcrxgF_BWuKbkJ3ZdOMYh6Qmo"  # ЗАМЕНИ НА РЕАЛЬНЫЙ ТОКЕН!

# ========== НАСТРОЙКИ ==========
# Ссылка на CSV-файл в твоём репозитории (RAW)
CSV_URL = "https://raw.githubusercontent.com/Constantine-msk/cskabot/main/schedule.csv"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Названия для кнопок
SPORT_NAMES = {
    "MF": "⚽ Мужской футбол (ПФК)",
    "JF": "⚽ Женский футбол (ЖФК)",
    "HK": "🏒 Хоккей (ПХК)",
    "BG": "🏀 Баскетбол (ПБК)",
    "VB": "🏐 Волейбол (ПВК)",
    "MG": "🤾 Мужской гандбол (ПГК)",
    "ZHG": "🤾 Женский гандбол (ЖГК)",
    "PF": "⚽ Мини-футбол (МФК)",
    "BF": "🏖️ Пляжный футбол",
}

# ========== ЗАГРУЗКА РАСПИСАНИЯ ИЗ CSV ==========
def get_matches():
    """Скачивает CSV с GitHub и возвращает список матчей"""
    matches = []
    today = datetime.now()
    
    try:
        response = requests.get(CSV_URL)
        response.raise_for_status()
        
        # Читаем CSV
        csv_content = StringIO(response.text)
        reader = csv.DictReader(csv_content)
        
        for row in reader:
            # Пропускаем неактивные
            if row.get("active", "").upper() != "TRUE":
                continue
            
            date_str = row.get("date")
            if not date_str:
                continue
            
            try:
                match_date = datetime.strptime(date_str, "%Y-%m-%d")
                if match_date < today.replace(hour=0, minute=0, second=0):
                    continue
                
                time_str = row.get("time", "")
                if time_str and str(time_str).strip():
                    try:
                        time_part = str(time_str).split(".")[0][:5]
                        match_date = match_date.replace(hour=int(time_part[:2]), minute=int(time_part[3:5]))
                    except:
                        pass
                
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
            except Exception as e:
                logger.warning(f"Ошибка в строке: {row} — {e}")
                continue
        
        logger.info(f"Загружено {len(matches)} матчей")
        return sorted(matches, key=lambda x: x["date"])
    
    except Exception as e:
        logger.error(f"Ошибка загрузки CSV: {e}")
        return []

def format_match(match):
    """Форматирует матч для отправки"""
    days = (match["date"] - datetime.now()).days
    home_away = "🏠 ДОМА" if match["location_type"] == "home" else "✈️ В ГОСТЯХ"
    sport_name = SPORT_NAMES.get(match["sport_code"], match["sport_code"])
    
    time_str = match["date"].strftime("%H:%M")
    if time_str == "00:00":
        time_str = "Время уточняется"
    
    location_parts = [p for p in [match.get("city"), match.get("stadium")] if p]
    location_str = ", ".join(location_parts) if location_parts else "Место уточняется"
    
    if match["boycott"] == "full":
        status = "❌ ПОЛНЫЙ БОЙКОТ! (не идем, не смотрим)"
    elif match["boycott"] == "partial":
        status = "📺 Частичный бойкот — смотрим по ТВ"
    else:
        status = "✅ Идем на стадион!"
    
    day_word = "день" if days == 1 else "дня" if days in [2,3,4] else "дней"
    
    text = (
        f"{home_away}\n"
        f"{sport_name}\n"
        f"🏆 {match['tournament']}\n"
        f"🆚 {match['opponent']}\n"
        f"📅 {match['date'].strftime('%d.%m.%Y, %A')}\n"
        f"⏰ {time_str}\n"
        f"📍 {location_str}\n\n"
        f"⏰ До матча: {days} {day_word}\n"
        f"{status}"
    )
    if match.get("notes"):
        text += f"\n📝 {match['notes']}"
    return text

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(name, callback_data=f"sub_{code}")] for code, name in SPORT_NAMES.items()]
    keyboard.append([InlineKeyboardButton("📋 Мои подписки", callback_data="my_subs")])
    keyboard.append([InlineKeyboardButton("📅 Ближайшие матчи", callback_data="next_all")])
    await update.message.reply_text(
        "🥅 **ЦСКА Бот**\n\n"
        "Выбери команду, чтобы подписаться на напоминания:\n"
        "• Дома → за 7 и 1 день, и в день матча\n"
        "• В гостях → за 14, 1 день и в день матча",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
        if subs:
            text = "📋 **Ваши подписки:**\n" + "\n".join(f"• {SPORT_NAMES[code]}" for code in subs)
        else:
            text = "❌ Вы ни на что не подписаны. Нажмите /start"
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
            time_str = match["date"].strftime("%H:%M") if match["date"].strftime("%H:%M") != "00:00" else ""
            text += f"{home_away} {match['date'].strftime('%d.%m')} {time_str} {match['team_name']} vs {match['opponent']} {emoji}\n"
        await query.edit_message_text(text, parse_mode="Markdown")

async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет и отправляет напоминания"""
    logger.info("Проверка напоминаний...")
    matches = get_matches()
    subscriptions = context.bot_data.get("subscriptions", {})
    
    if "sent_reminders" not in context.bot_data:
        context.bot_data["sent_reminders"] = set()
    
    for match in matches:
        days = (match["date"] - datetime.now()).days
        
        if match["boycott"] == "full":
            continue
        
        # Правила напоминаний
        need_remind = False
        remind_type = ""
        
        if days == 0:  # В день матча
            need_remind = True
            remind_type = "day0"
        elif days == 1:  # За 1 день
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
        
        reminder_id = f"{match['match_id']}_{remind_type}"
        if reminder_id in context.bot_data["sent_reminders"]:
            continue
        
        # Отправляем всем подписанным
        for user_id, subs in subscriptions.items():
            if match["sport_code"] in subs:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=format_match(match),
                        parse_mode="Markdown"
                    )
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
