import os
import json
import uuid
import logging
import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import motor.motor_asyncio
from pymongo import MongoClient
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    LabeledPrice, PreCheckoutQuery, Message
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, PreCheckoutQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGODB_URL = os.getenv('MONGODB_URL', 'mongodb+srv://Nischay999:Nischay999@cluster0.5kufo.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0)
DATABASE_NAME = os.getenv('DATABASE_NAME', 'filebot')
PORT = int(os.getenv('PORT', 8080))

class MongoDBManager:
    def __init__(self):
        self.client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URL)
        self.db = self.client[DATABASE_NAME]
        self.files_collection = self.db.files
        self.users_collection = self.db.users
        self.redeem_codes_collection = self.db.redeem_codes
        
    async def ensure_indexes(self):
        """Create indexes for better performance"""
        await self.files_collection.create_index("file_id", unique=True)
        await self.files_collection.create_index("owner_id")
        await self.users_collection.create_index("user_id", unique=True)
        await self.redeem_codes_collection.create_index("code", unique=True)
        await self.redeem_codes_collection.create_index(
            "created_at", 
            expireAfterSeconds=86400*30  # Expire after 30 days
        )
    
    async def save_file(self, file_data: dict):
        """Save file data to MongoDB"""
        await self.files_collection.replace_one(
            {"file_id": file_data["file_id"]},
            file_data,
            upsert=True
        )
    
    async def get_file(self, file_id: str) -> Optional[dict]:
        """Get file data from MongoDB"""
        return await self.files_collection.find_one({"file_id": file_id})
    
    async def get_user_files(self, user_id: int, limit: int = 50) -> List[dict]:
        """Get user's files"""
        cursor = self.files_collection.find(
            {"owner_id": user_id}
        ).sort("upload_date", -1).limit(limit)
        return await cursor.to_list(length=limit)
    
    async def save_user(self, user_data: dict):
        """Save user data to MongoDB"""
        await self.users_collection.replace_one(
            {"user_id": user_data["user_id"]},
            user_data,
            upsert=True
        )
    
    async def get_user(self, user_id: int) -> Optional[dict]:
        """Get user data from MongoDB"""
        return await self.users_collection.find_one({"user_id": user_id})
    
    async def update_user_credits(self, user_id: int, credits_delta: int):
        """Update user credits"""
        await self.users_collection.update_one(
            {"user_id": user_id},
            {
                "$inc": {"credits": credits_delta},
                "$setOnInsert": {
                    "user_id": user_id,
                    "joined_date": datetime.utcnow(),
                    "credits": 0
                }
            },
            upsert=True
        )
    
    async def get_user_credits(self, user_id: int) -> int:
        """Get user credits"""
        user = await self.get_user(user_id)
        return user.get("credits", 0) if user else 0
    
    async def spend_credits(self, user_id: int, amount: int) -> bool:
        """Spend user credits"""
        result = await self.users_collection.update_one(
            {"user_id": user_id, "credits": {"$gte": amount}},
            {"$inc": {"credits": -amount}}
        )
        return result.modified_count > 0
    
    async def save_redeem_code(self, code: str, file_id: str):
        """Save redeem code"""
        await self.redeem_codes_collection.replace_one(
            {"code": code},
            {
                "code": code,
                "file_id": file_id,
                "created_at": datetime.utcnow()
            },
            upsert=True
        )
    
    async def get_redeem_code(self, code: str) -> Optional[dict]:
        """Get redeem code data"""
        return await self.redeem_codes_collection.find_one({"code": code})
    
    async def delete_redeem_code(self, code: str):
        """Delete used redeem code"""
        await self.redeem_codes_collection.delete_one({"code": code})
    
    async def increment_file_access(self, file_id: str):
        """Increment file access count"""
        await self.files_collection.update_one(
            {"file_id": file_id},
            {"$inc": {"access_count": 1}}
        )

class FileBot:
    def __init__(self):
        self.db = MongoDBManager()
        
    def generate_file_id(self):
        return str(uuid.uuid4())
    
    def generate_redeem_code(self):
        return hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:8].upper()
    
    def create_file_link(self, file_id: str, stars_required: int = 0):
        base_url = f"https://t.me/{os.getenv('BOT_USERNAME', 'your_bot')}?start=file_{file_id}_{stars_required}"
        return base_url
    
    async def initialize(self):
        """Initialize database indexes"""
        await self.db.ensure_indexes()

file_bot = FileBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    args = context.args
    user_id = update.effective_user.id
    
    # Initialize user if not exists
    user = await file_bot.db.get_user(user_id)
    if not user:
        user_data = {
            "user_id": user_id,
            "username": update.effective_user.username,
            "first_name": update.effective_user.first_name,
            "joined_date": datetime.utcnow(),
            "credits": 0
        }
        await file_bot.db.save_user(user_data)
    
    if args and args[0].startswith('file_'):
        # Handle file access from link
        parts = args[0].split('_')
        if len(parts) >= 3:
            file_id = parts[1]
            stars_required = int(parts[2])
            await handle_file_access(update, context, file_id, stars_required)
            return
    
    user_credits = await file_bot.db.get_user_credits(user_id)
    
    welcome_text = f"""
🤖 **File Storage Bot**

Welcome! This bot allows you to:
📁 Store files and generate public links
⭐ Set Telegram Stars pricing for file access
🎫 Generate redeem codes for free access
💳 Manage your credits and files

**Commands:**
/upload - Upload a new file
/myfiles - View your uploaded files
/credits - Check your credit balance
/redeem - Use a redeem code
/help - Show this help message

**Your Current Credits:** ⭐ {user_credits}
    """
    
    keyboard = [
        [InlineKeyboardButton("📁 Upload File", callback_data="upload_file")],
        [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
        [InlineKeyboardButton("⭐ My Credits", callback_data="check_credits")],
        [InlineKeyboardButton("🎫 Redeem Code", callback_data="redeem_code")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text, 
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def handle_file_access(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str, stars_required: int):
    """Handle file access from public link"""
    user_id = update.effective_user.id
    file_data = await file_bot.db.get_file(file_id)
    
    if not file_data:
        await update.message.reply_text("❌ File not found or has been removed.")
        return
    
    file_name = file_data.get('name', 'Unknown File')
    file_size = file_data.get('size', 0)
    owner_id = file_data.get('owner_id')
    
    if user_id == owner_id:
        # Owner can access for free
        await send_file_to_user(update, context, file_data)
        return
    
    if stars_required == 0:
        # Free file
        await send_file_to_user(update, context, file_data)
        return
    
    # Check if user has enough credits
    user_credits = await file_bot.db.get_user_credits(user_id)
    
    access_text = f"""
📁 **{file_name}**
📊 Size: {file_size} bytes
💰 Cost: ⭐ {stars_required} stars
💳 Your Credits: ⭐ {user_credits}

Choose how to access this file:
"""
    
    keyboard = []
    
    if user_credits >= stars_required:
        keyboard.append([InlineKeyboardButton(f"💳 Use Credits (⭐ {stars_required})", callback_data=f"use_credits_{file_id}_{stars_required}")])
    
    keyboard.extend([
        [InlineKeyboardButton(f"⭐ Buy with Telegram Stars (⭐ {stars_required})", callback_data=f"buy_stars_{file_id}_{stars_required}")],
        [InlineKeyboardButton("🎫 I have a redeem code", callback_data=f"redeem_for_file_{file_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(access_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def send_file_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, file_data: dict):
    """Send file to user"""
    try:
        file_id = file_data.get('telegram_file_id')
        caption = f"📁 **{file_data.get('name')}**\n📊 Size: {file_data.get('size')} bytes"
        
        # Increment access count
        await file_bot.db.increment_file_access(file_data['file_id'])
        
        if file_data.get('type') == 'document':
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )
        elif file_data.get('type') == 'photo':
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )
        elif file_data.get('type') == 'video':
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Handle both callback query and direct message
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.reply_text("✅ File sent successfully!")
        else:
            await update.message.reply_text("✅ File sent successfully!")
            
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        message_obj = update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else update.message
        await message_obj.reply_text("❌ Error sending file. Please try again.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded documents"""
    user_id = update.effective_user.id
    
    if update.message.document:
        file_info = update.message.document
        file_type = 'document'
    elif update.message.photo:
        file_info = update.message.photo[-1]  # Get highest resolution
        file_type = 'photo'
    elif update.message.video:
        file_info = update.message.video
        file_type = 'video'
    else:
        await update.message.reply_text("❌ Unsupported file type.")
        return
    
    file_id = file_bot.generate_file_id()
    
    file_data = {
        'file_id': file_id,
        'telegram_file_id': file_info.file_id,
        'name': getattr(file_info, 'file_name', f'{file_type}_{file_id}'),
        'size': getattr(file_info, 'file_size', 0),
        'type': file_type,
        'owner_id': user_id,
        'upload_date': datetime.utcnow(),
        'access_count': 0,
        'price': 0  # Default free
    }
    
    await file_bot.db.save_file(file_data)
    
    text = f"""
✅ **File Uploaded Successfully!**

📁 **File:** {file_data['name']}
📊 **Size:** {file_data['size']} bytes
🆔 **File ID:** `{file_id}`

Now set the pricing for your file:
"""
    
    keyboard = [
        [InlineKeyboardButton("🆓 Make it Free", callback_data=f"set_price_{file_id}_0")],
        [InlineKeyboardButton("⭐ 1 Star", callback_data=f"set_price_{file_id}_1")],
        [InlineKeyboardButton("⭐ 5 Stars", callback_data=f"set_price_{file_id}_5")],
        [InlineKeyboardButton("⭐ 10 Stars", callback_data=f"set_price_{file_id}_10")],
        [InlineKeyboardButton("💰 Custom Price", callback_data=f"custom_price_{file_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    if data == "upload_file":
        await query.message.reply_text(
            "📁 **Upload a File**\n\n"
            "Send me any file (document, photo, video) and I'll store it for you!",
            parse_mode=ParseMode.MARKDOWN
        )
        
    elif data == "my_files":
        await show_user_files(query, context, user_id)
        
    elif data == "check_credits":
        credits = await file_bot.db.get_user_credits(user_id)
        await query.message.reply_text(f"💳 **Your Credits:** ⭐ {credits}", parse_mode=ParseMode.MARKDOWN)
        
    elif data == "redeem_code":
        await query.message.reply_text(
            "🎫 **Redeem Code**\n\n"
            "Send me your redeem code to access a file for free!\n"
            "Use format: `/redeem YOUR_CODE`",
            parse_mode=ParseMode.MARKDOWN
        )
        
    elif data.startswith("set_price_"):
        parts = data.split("_")
        file_id = parts[2]
        price = int(parts[3])
        await set_file_price(query, context, file_id, price)
        
    elif data.startswith("use_credits_"):
        parts = data.split("_")
        file_id = parts[2]
        stars_required = int(parts[3])
        await use_credits_for_file(query, context, file_id, stars_required)
        
    elif data.startswith("buy_stars_"):
        parts = data.split("_")
        file_id = parts[2]
        stars_required = int(parts[3])
        await initiate_star_payment(query, context, file_id, stars_required)
        
    elif data.startswith("generate_redeem_"):
        file_id = data.split("_")[2]
        await generate_redeem_code(query, context, file_id)
        
    elif data.startswith("file_details_"):
        file_id = data.split("_")[2]
        await show_file_details(query, context, file_id)

async def show_user_files(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Show user's uploaded files"""
    user_files = await file_bot.db.get_user_files(user_id, limit=10)
    
    if not user_files:
        await query.message.reply_text("📁 You haven't uploaded any files yet.")
        return
    
    text = "📋 **Your Files:**\n\n"
    keyboard = []
    
    for file_data in user_files:
        text += f"📁 {file_data['name']}\n"
        keyboard.append([InlineKeyboardButton(
            f"📁 {file_data['name'][:30]}...", 
            callback_data=f"file_details_{file_data['file_id']}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def show_file_details(query, context: ContextTypes.DEFAULT_TYPE, file_id: str):
    """Show detailed file information"""
    file_data = await file_bot.db.get_file(file_id)
    if not file_data:
        await query.message.reply_text("❌ File not found.")
        return
    
    price = file_data.get('price', 0)
    link = file_bot.create_file_link(file_id, price)
    
    text = f"""
📁 **File Details**

**Name:** {file_data['name']}
**Size:** {file_data['size']} bytes
**Type:** {file_data['type']}
**Price:** ⭐ {price} stars
**Access Count:** {file_data.get('access_count', 0)}
**Upload Date:** {file_data['upload_date'].strftime('%Y-%m-%d %H:%M')}

**Public Link:**
`{link}`
"""
    
    keyboard = [
        [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data=f"generate_redeem_{file_id}")],
        [InlineKeyboardButton("💰 Change Price", callback_data=f"change_price_{file_id}")],
        [InlineKeyboardButton("📊 View Stats", callback_data=f"file_stats_{file_id}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def set_file_price(query, context: ContextTypes.DEFAULT_TYPE, file_id: str, price: int):
    """Set file price"""
    file_data = await file_bot.db.get_file(file_id)
    if not file_data:
        await query.message.reply_text("❌ File not found.")
        return
    
    # Update price in database
    await file_bot.db.files_collection.update_one(
        {"file_id": file_id},
        {"$set": {"price": price}}
    )
    
    link = file_bot.create_file_link(file_id, price)
    
    price_text = "🆓 Free" if price == 0 else f"⭐ {price} stars"
    
    text = f"""
✅ **Price Set Successfully!**

📁 **File:** {file_data['name']}
💰 **Price:** {price_text}

**Public Link:**
`{link}`

Share this link with others to let them access your file!
"""
    
    keyboard = [
        [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data=f"generate_redeem_{file_id}")],
        [InlineKeyboardButton("📋 Back to My Files", callback_data="my_files")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def generate_redeem_code(query, context: ContextTypes.DEFAULT_TYPE, file_id: str):
    """Generate redeem code for file"""
    file_data = await file_bot.db.get_file(file_id)
    if not file_data:
        await query.message.reply_text("❌ File not found.")
        return
    
    redeem_code = file_bot.generate_redeem_code()
    await file_bot.db.save_redeem_code(redeem_code, file_id)
    
    text = f"""
🎫 **Redeem Code Generated!**

📁 **File:** {file_data['name']}
🎫 **Code:** `{redeem_code}`

Give this code to users for free access to your file.
Users can use it with: `/redeem {redeem_code}`
"""
    
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def use_credits_for_file(query, context: ContextTypes.DEFAULT_TYPE, file_id: str, stars_required: int):
    """Use user credits to access file"""
    user_id = query.from_user.id
    
    if await file_bot.db.spend_credits(user_id, stars_required):
        file_data = await file_bot.db.get_file(file_id)
        if file_data:
            await send_file_to_user(query, context, file_data)
            
            remaining_credits = await file_bot.db.get_user_credits(user_id)
            await query.message.reply_text(
                f"✅ Access granted! ⭐ {stars_required} credits used.\n"
                f"💳 Remaining credits: ⭐ {remaining_credits}"
            )
        else:
            await query.message.reply_text("❌ File not found.")
    else:
        await query.message.reply_text("❌ Insufficient credits.")

async def initiate_star_payment(query, context: ContextTypes.DEFAULT_TYPE, file_id: str, stars_required: int):
    """Initiate Telegram Stars payment"""
    file_data = await file_bot.db.get_file(file_id)
    if not file_data:
        await query.message.reply_text("❌ File not found.")
        return
    
    title = f"Access to {file_data['name']}"
    description = f"Pay {stars_required} Telegram Stars to access this file"
    payload = f"file_access_{file_id}_{stars_required}"
    
    prices = [LabeledPrice("File Access", stars_required)]
    
    try:
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # Empty for Telegram Stars
            currency="XTR",  # Telegram Stars currency
            prices=prices,
            start_parameter=f"file_{file_id}",
        )
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        await query.message.reply_text("❌ Error creating payment. Please try again.")

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pre-checkout query"""
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle successful payment"""
    payment = update.message.successful_payment
    payload_parts = payment.invoice_payload.split("_")
    
    if len(payload_parts) >= 3 and payload_parts[0] == "file" and payload_parts[1] == "access":
        file_id = payload_parts[2]
        stars_paid = int(payload_parts[3])
        
        # Give user credits equal to stars paid
        user_id = update.effective_user.id
        await file_bot.db.update_user_credits(user_id, stars_paid)
        
        # Grant access to file
        file_data = await file_bot.db.get_file(file_id)
        if file_data:
            await send_file_to_user(update, context, file_data)
            
            await update.message.reply_text(
                f"✅ Payment successful! File access granted.\n"
                f"💳 {stars_paid} credits added to your account."
            )

async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle redeem code command"""
    if not context.args:
        await update.message.reply_text(
            "🎫 **Redeem Code**\n\n"
            "Usage: `/redeem YOUR_CODE`\n"
            "Example: `/redeem ABC123XY`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    redeem_code = context.args[0].upper()
    redeem_data = await file_bot.db.get_redeem_code(redeem_code)
    
    if not redeem_data:
        await update.message.reply_text("❌ Invalid or expired redeem code.")
        return
    
    file_id = redeem_data['file_id']
    file_data = await file_bot.db.get_file(file_id)
    
    if not file_data:
        await update.message.reply_text("❌ File not found or has been removed.")
        return
    
    # Grant access
    await send_file_to_user(update, context, file_data)
    
    # Remove used redeem code
    await file_bot.db.delete_redeem_code(redeem_code)
    
    await update.message.reply_text(f"✅ Redeem code accepted! Access granted to: {file_data['name']}")

async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user credits"""
    user_id = update.effective_user.id
    credits = await file_bot.db.get_user_credits(user_id)
    
    await update.message.reply_text(
        f"💳 **Your Credits:** ⭐ {credits}\n\n"
        "Credits can be used to access paid files without additional payment.",
        parse_mode=ParseMode.MARKDOWN
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    help_text = """
🤖 **File Storage Bot Help**

**Commands:**
/start - Start the bot and see main menu
/upload - Upload a new file
/myfiles - View your uploaded files
/credits - Check your credit balance
/redeem CODE - Use a redeem code
/help - Show this help message

**How to use:**
1. Upload files using the upload button or by sending files directly
2. Set pricing for your files (free or paid with Telegram Stars)
3. Share the generated public links
4. Generate redeem codes for free access

**Features:**
📁 File storage and sharing with MongoDB
⭐ Telegram Stars integration
💳 Credit system
🎫 Redeem codes with expiry
📊 Access statistics

For support: @NY_BOTS
"""
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Exception while handling an update: {context.error}")

async def main():
    """Start the bot"""
    try:
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN environment variable not set")
            return
        
        # Initialize database
        await file_bot.initialize()
        
        # Create application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("credits", credits_command))
        application.add_handler(CommandHandler("redeem", redeem_command))
        
        application.add_handler(MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VIDEO, 
            handle_document
        ))
        
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
        
        # Add error handler
        application.add_error_handler(error_handler)
        
        logger.info("Starting bot with MongoDB...")
        
        # Start the bot
        await application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main())
