import os
import logging
import re
from dotenv import load_dotenv
from urllib.parse import quote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)
from pymongo import MongoClient
from audiobookbay.search import search_audiobookbay
from magnet_scraper import get_magnet_data

# --- Load Env ---
load_dotenv()

# --- Config ---
TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))
REQUEST_GROUP = int(os.getenv("REQUEST_GROUP"))
ADMINS = list(map(int, os.getenv("ADMINS").split(',')))
MONGO_URI = os.getenv("MONGO_URI")

# --- DB Setup ---
client = MongoClient(MONGO_URI)
db = client.audiobookbot
users_collection = db.users
custom_responses = db.custom_responses
extra_links_collection = db.extra_links
settings = db.settings

# --- Logging ---
logging.basicConfig(level=logging.INFO)

# --- State Management ---
user_states = {}

# --- Helpers ---
def is_admin(user_id):
    return user_id in ADMINS

def get_keyboard(results, page):
    buttons = [[InlineKeyboardButton(r['title'], callback_data=f"select|{i}")] for i, r in enumerate(results)]
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev"))
    nav.append(InlineKeyboardButton("➡️ Next", callback_data="next"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons)

async def log_to_channel(text, context):
    try:
        await context.bot.send_message(chat_id=LOG_CHANNEL, text=text)
    except Exception as e:
        logging.warning(f"Failed to send log message: {e}")

# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = settings.find_one({"name": "welcome"})
    welcome_message = doc["message"] if doc else (
        "👋 Welcome to AudiobookBay Search Bot!\n\n"
        "🔍 Just send me the name of an audiobook, and I’ll fetch results for you.\n"
        "➡️ Use the 'Next' and 'Previous' buttons to navigate pages.\n"
        "🎧 Click on a title to get full details."
    )
    await update.message.reply_text(welcome_message)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = users_collection.count_documents({})
    await update.message.reply_text(f"👥 Total users: {total_users}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if is_admin(user_id):
        help_text = (
            "🛠️ <b>Admin Commands:</b>\n\n"
            "/start - Show welcome message\n"
            "/stats - Show total user count\n"
            "/broadcast <message> - Send message to all users\n"
            "/send <user_id|username> <message> - Send private message\n"
            "/welcome - Set custom welcome message\n"
            "/custom - Add custom response to keywords\n"
            "/attach 'Text' <link> - Attach extra link\n"
            "/remove <text> - Remove attached link\n"
            "/link - Show all attached links\n"
            "/cancel - Cancel current operation\n"
        )
    else:
        help_text = (
            "🤖 <b>User Commands:</b>\n\n"
            "/start - Show welcome message\n"
            "Type a book name to search audiobooks\n"
            "Use /request <book> to request an audiobook\n"
        )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return
    msg = update.message.text.split(" ", 1)[1]
    for user in users_collection.find():
        try:
            await context.bot.send_message(user["_id"], msg)
        except:
            continue

async def send_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return
    args = update.message.text.split(" ", 2)
    if len(args) < 3:
        return
    user_id_or_username, msg = args[1], args[2]
    user = users_collection.find_one({"username": user_id_or_username}) if not user_id_or_username.isdigit() else None
    user_id = int(user_id_or_username) if user is None else user["_id"]
    try:
        await context.bot.send_message(user_id, msg)
    except:
        await update.message.reply_text("❌ Failed to send.")

# --- Welcome ---
WELCOME_MSG, = range(1)
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return
    await update.message.reply_text("📝 Send the new welcome message:")
    return WELCOME_MSG

async def save_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings.update_one({"name": "welcome"}, {"$set": {"message": update.message.text}}, upsert=True)
    await update.message.reply_text("✅ Welcome message updated.")
    return ConversationHandler.END

# --- Custom Keywords ---
CUSTOM_KEYWORD, CUSTOM_RESPONSE = range(2)
async def custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return
    await update.message.reply_text("🔑 Send the keyword to set:")
    return CUSTOM_KEYWORD

async def get_custom_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["keyword"] = update.message.text.lower()
    await update.message.reply_text("💬 Now send the custom response for this keyword:")
    return CUSTOM_RESPONSE

async def save_custom_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = context.user_data["keyword"]
    response = update.message.text
    custom_responses.update_one({"keyword": keyword}, {"$set": {"response": response}}, upsert=True)
    await update.message.reply_text(f"✅ Custom response for keyword '{keyword}' saved.")
    return ConversationHandler.END

# --- Cancel ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# --- Extra Links ---
async def attach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return
    pattern = r"/attach\s+['\"](.+?)['\"]\s+(https?://\S+)"
    match = re.match(pattern, update.message.text)
    if not match:
        await update.message.reply_text("❌ Invalid format. Use:\n/attach 'Text for link' https://example.com")
        return
    text, link = match.groups()
    extra_links_collection.insert_one({"text": text, "link": link})
    await update.message.reply_text("✅ Extra link added.")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return
    args = update.message.text.split(" ", 1)
    if len(args) < 2:
        await update.message.reply_text("Usage: /remove 'text'")
        return
    text = args[1].strip()
    result = extra_links_collection.delete_one({"text": text})
    if result.deleted_count:
        await update.message.reply_text("✅ Link removed.")
    else:
        await update.message.reply_text("❌ No matching link found.")

async def list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return
    links = extra_links_collection.find()
    if extra_links_collection.count_documents({}) == 0:
        await update.message.reply_text("No links found.")
        return
    msg = "🔗 <b>Attached Links:</b>\n\n"
    for entry in links:
        text = entry.get('text', 'Untitled')
        link = entry.get('link', '').strip()
        if link:
            msg += f"• <b>{text}</b>: <a href=\"{link}\">{link}</a>\n"
    await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)

# --- Message Search ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    query = update.message.text.strip()
    lowered = query.lower()

    if lowered.startswith(('/request', '#request')):
        await context.bot.forward_message(
            chat_id=REQUEST_GROUP,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.id
        )
        await update.message.reply_text("✅ Your request has been forwarded.")
        return

    users_collection.update_one(
        {"_id": user_id},
        {"$set": {
            "username": update.message.from_user.username,
            "first_name": update.message.from_user.first_name
        }},
        upsert=True
    )

    custom = custom_responses.find_one({"keyword": lowered})
    if custom:
        await update.message.reply_text(custom["response"])
        return

    user_states[user_id] = {'query': query, 'page': 1}
    results = search_audiobookbay(query, 1)
    if not results:
        await update.message.reply_text("No results found.")
        await context.bot.send_message(
            chat_id=REQUEST_GROUP,
            text=f"📥 Request from <a href='tg://user?id={user_id}'>{user_id}</a>:\n<code>{query}</code>",
            parse_mode='HTML'
        )
        return

    user_states[user_id]['results'] = results
    await log_to_channel(f"🔍 Search: {query} by {user_id}", context)
    await update.message.reply_text(
        f"🔍 Search Results for '{query}' (Page 1):",
        reply_markup=get_keyboard(results, 1)
    )

# --- Callback ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_data = update.callback_query.data
    user_id = update.callback_query.from_user.id
    state = user_states.get(user_id)

    if not state:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("Session expired. Please search again.")
        return

    if query_data == "next":
        state['page'] += 1
    elif query_data == "prev" and state['page'] > 1:
        state['page'] -= 1
    elif query_data.startswith("select"):
        _, idx = query_data.split("|")
        idx = int(idx)
        result = state['results'][idx]
        data = get_magnet_data(result['link'])
        state['selected_data'] = data

        title = data.get("title", "")
        description = data.get("description", "")
        max_caption_length = 1024
        caption = f"<b>{title}</b>\n\n{description}"
        if len(caption) > max_caption_length:
            cutoff = max_caption_length - len(f"<b>{title}</b>\n\n...")
            description = description[:cutoff] + "..."
            caption = f"<b>{title}</b>\n\n{description}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Get Magnet Link", callback_data="get_magnet")]
        ])
        await update.callback_query.message.reply_photo(
            photo=data.get("image_url"),
            caption=caption,
            parse_mode='HTML',
            reply_markup=keyboard
        )
        await update.callback_query.answer()
        return

    elif query_data == "get_magnet":
        data = state.get('selected_data')
        if not data:
            await update.callback_query.answer("No magnet found.", show_alert=True)
            return
        magnet = data.get("magnet_link")
        webtor = f"https://webtor.io/{quote(magnet, safe='')}"
        extra = extra_links_collection.find_one(sort=[('_id', -1)])
        extra_button = [InlineKeyboardButton(extra['text'], url=extra['link'])] if extra else []

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Stream on Webtor", url=webtor)],
            extra_button
        ])

        await update.callback_query.message.reply_text(
            f"🔗 <b>Magnet Link:</b>\n<code>{magnet}</code>",
            parse_mode='HTML',
            reply_markup=keyboard
        )
        await update.callback_query.answer()
        return

    results = search_audiobookbay(state['query'], state['page'])
    state['results'] = results
    await update.callback_query.message.edit_text(
        f"🔍 Search Results for '{state['query']}' (Page {state['page']}):",
        reply_markup=get_keyboard(results, state['page'])
    )
    await update.callback_query.answer()

# --- Main ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("send", send_to_user))
    app.add_handler(CommandHandler("attach", attach))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("link", list_links))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("welcome", welcome)],
        states={WELCOME_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_welcome)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("custom", custom)],
        states={
            CUSTOM_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_response)],
            CUSTOM_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_custom_response)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logging.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
from aiohttp import web
import threading

# --- Health check route for Render ---
async def health(request):
    return web.Response(text="✅ Bot is alive")

def run_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    web.run_app(app, port=8080)

# --- Main Application Start ---
if __name__ == '__main__':
    # Run the web server in a background thread to keep the service alive
    threading.Thread(target=run_health_server).start()

    # Telegram bot starts here
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("send", send_to_user))
    app.add_handler(CommandHandler("attach", attach))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("link", list_links))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("welcome", welcome)],
        states={WELCOME_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_welcome)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("custom", custom)],
        states={
            CUSTOM_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_response)],
            CUSTOM_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_custom_response)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.run_polling()
