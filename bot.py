import logging
import requests
import csv
from io import StringIO
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ========== ТОКЕН БОТА ==========
BOT_TOKEN = "8635943328:AAGWbMnRWXTcrxgF_BWuKbkJ3ZdOMYh6Qmo"

# ========== НАСТРОЙКИ ==========
CSV_URL = "https://raw.githubusercontent.com/Constantine-msk/CalendCSKA_Bot/main/schedule.csv"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
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

# ========== ЗАГРУЗКА РАСПИСАНИЯ ==========
def get_matches():
    """Скачивает CSV с GitHub и возвращает список матчей"""
    matches = []
    today = datetime.now()
    
    try:
        response = requests.get(CSV_URL)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
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
                logger.warning(f"Ошибка в строке: {e}")
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
    
    if days == 1:
        day_word = "день"
    elif days in [2, 3, 4]:
        day_word = "дня"
    else:
        day_word = "дней"
    
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

# ========== КЛАВИАТУРА ==========
def get_main_keyboard():
    """Главная клавиатура (кнопки под сообщением)"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Ближайшие матчи", callback_data="next_all")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="my_subs")],
        [InlineKeyboardButton("➕ Подписаться на команду", callback_data="subscribe_menu")],
    ])

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню"""
    await update.message.reply_text(
        "🥅 **ЦСКА Бот**\n\n"
        "Я буду присылать напоминания о матчах ЦСКА:\n"
        "• 🏠 Домашние → за 7 дней, за 1 день и в день матча\n"
        "• ✈️ Гостевые → за 14 дней, за 1 день и в день матча\n\n"
        "❌ Матчи с бойкотом отмечены особо.\n\n"
        "Выбери действие:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех кнопок"""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    # Инициализация хранилища подписок
    if "subscriptions" not in context.bot_data:
        context.bot_data["subscriptions"] = {}
    if user_id not in context.bot_data["subscriptions"]:
        context.bot_data["subscriptions"][user_id] = set()
    
    # ===== ГЛАВНОЕ МЕНЮ =====
    if data == "main_menu":
        await query.edit_message_text(
            "🥅 **Главное меню**\n\nВыберите действие:",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    
    # ===== МЕНЮ ПОДПИСКИ =====
    elif data == "subscribe_menu":
        keyboard = []
        for code, name in SPORT_NAMES.items():
            if code in context.bot_data["subscriptions"][user_id]:
                name = f"✅ {name}"
            keyboard.append([InlineKeyboardButton(name, callback_data=f"toggle_{code}")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])
        
        await query.edit_message_text(
            "📋 **Выбери команду:**\n\n"
            "✅ — уже подписан\n"
            "Нажми на команду, чтобы подписаться или отписаться.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # ===== ПОДПИСАТЬ/ОТПИСАТЬСЯ =====
    elif data.startswith("toggle_"):
        sport_code = data.split("_")[1]
        subs = context.bot_data["subscriptions"][user_id]
        
        if sport_code in subs:
            subs.remove(sport_code)
            action = "отписался от"
        else:
            subs.add(sport_code)
            action = "подписался на"
        
        keyboard = []
        for code, name in SPORT_NAMES.items():
            if code in subs:
                name = f"✅ {name}"
            keyboard.append([InlineKeyboardButton(name, callback_data=f"toggle_{code}")])
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])
        
        subs_text = "\n".join(f"• {SPORT_NAMES[code]}" for code in subs) if subs else "Пока нет"
        
        await query.edit_message_text(
            f"✅ Вы {action} {SPORT_NAMES[sport_code]}\n\n"
            f"**Ваши подписки:**\n{subs_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # ===== МОИ ПОДПИСКИ =====
    elif data == "my_subs":
        subs = context.bot_data["subscriptions"][user_id]
        if subs:
            text = "📋 **Ваши подписки:**\n\n" + "\n".join(f"• {SPORT_NAMES[code]}" for code in subs)
            text += "\n\nЧтобы отписаться, нажмите «Подписаться на команду» и выберите команду с ✅"
        else:
            text = "❌ Вы пока ни на что не подписаны."
        
        keyboard = [
            [InlineKeyboardButton("➕ Подписаться", callback_data="subscribe_menu")],
            [InlineKeyboardButton("🗑 Отписаться от всех", callback_data="unsubscribe_all")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ===== ОТПИСАТЬСЯ ОТ ВСЕХ =====
    elif data == "unsubscribe_all":
        context.bot_data["subscriptions"][user_id].clear()
        await query.edit_message_text(
            "🗑 Вы отписались от **всех** команд.\n\n"
            "Чтобы снова подписаться, нажмите «Подписаться на команду».",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Подписаться", callback_data="subscribe_menu")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
            ])
        )
    
    # ===== БЛИЖАЙШИЕ МАТЧИ =====
    elif data == "next_all":
        matches = get_matches()
        if not matches:
            text = "❌ Нет ближайших матчей"
        else:
            text = "📅 **Ближайшие матчи:**\n\n"
            for match in matches[:10]:
                days = (match["date"] - datetime.now()).days
                if match["boycott"] == "full":
                    emoji = "❌"
                elif match["boycott"] == "partial":
                    emoji = "📺"
                else:
                    emoji = "✅"
                home_away = "🏠" if match["location_type"] == "home" else "✈️"
                time_str = match["date"].strftime("%H:%M")
                if time_str == "00:00":
                    time_str = ""
                text += f"{home_away} {match['date'].strftime('%d.%m')} {time_str} {match['team_name']} vs {match['opponent']} {emoji}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ========== НАПОМИНАНИЯ ==========
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
        
        need_remind = False
        remind_type = ""
        
        if days == 0:
            need_remind = True
            remind_type = "day0"
        elif days == 1:
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
                    logger.error(f"Ошибка отправки {user_id}: {e}")
        
        context.bot_data["sent_reminders"].add(reminder_id)

# ========== ЗАПУСК ==========
async def set_bot_commands(app: Application):
    """Устанавливает команды для меню Telegram"""
    commands = [
        BotCommand("start", "🚀 Показать главное меню"),
        BotCommand("menu", "📋 Главное меню"),
        BotCommand("my_matches", "📅 Мои ближайшие матчи (по подпискам)"),
    ]
    await app.bot.set_my_commands(commands)

async def my_matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать только матчи по подпискам пользователя"""
    user_id = update.effective_user.id
    subs = context.bot_data.get("subscriptions", {}).get(user_id, set())
    
    if not subs:
        await update.message.reply_text("❌ Вы ни на что не подписаны. Нажмите /start")
        return
    
    all_matches = get_matches()
    my_matches = [m for m in all_matches if m["sport_code"] in subs]
    
    if not my_matches:
        await update.message.reply_text("❌ Нет ближайших матчей по вашим подпискам")
        return
    
    text = "📅 **Ваши ближайшие матчи:**\n\n"
    for match in my_matches[:10]:
        days = (match["date"] - datetime.now()).days
        if match["boycott"] == "full":
            emoji = "❌"
        elif match["boycott"] == "partial":
            emoji = "📺"
        else:
            emoji = "✅"
        home_away = "🏠" if match["location_type"] == "home" else "✈️"
        time_str = match["date"].strftime("%H:%M")
        if time_str == "00:00":
            time_str = ""
        text += f"{home_away} {match['date'].strftime('%d.%m')} {time_str} {match['team_name']} vs {match['opponent']} {emoji}\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /menu"""
    await start(update, context)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("my_matches", my_matches_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.bot_data["subscriptions"] = {}
    app.bot_data["sent_reminders"] = set()
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, CronTrigger(hour=10, minute=0), args=[app])
    scheduler.start()
    
    app.post_init = set_bot_commands
    
    logger.info("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
