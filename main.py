import os
import logging
import asyncio
import uuid
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
import json

# Telegram imports
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    LabeledPrice, PreCheckoutQuery, SuccessfulPayment,
    Bot, InputFile, CallbackQuery
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, PreCheckoutQueryHandler,
    ContextTypes, filters
)
from telegram.error import TelegramError

# MongoDB imports
try:
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    print("MongoDB not available, using in-memory storage")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class FileStorage:
    """Handle file storage operations"""
    
    def __init__(self):
        self.files = {}  # In-memory storage fallback
        self.users = {}
        self.redeem_codes = {}
        self.user_stars = {}
        
        # Initialize MongoDB if available
        if MONGODB_AVAILABLE:
            try:
                mongo_url = os.getenv('MONGODB_URL', 'mongodb+srv://Nischay999:Nischay999@cluster0.5kufo.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
                self.client = MongoClient(mongo_url)
                self.db = self.client.file_bot
                self.files_collection = self.db.files
                self.users_collection = self.db.users
                self.codes_collection = self.db.redeem_codes
                self.stars_collection = self.db.user_stars
                logger.info("Connected to MongoDB")
            except Exception as e:
                logger.error(f"MongoDB connection failed: {e}")
                self.client = None
        else:
            self.client = None
    
    def save_file(self, file_id: str, file_data: dict) -> str:
        """Save file data and return unique link"""
        unique_id = str(uuid.uuid4())
        file_data['unique_id'] = unique_id
        file_data['created_at'] = datetime.now()
        
        if self.client:
            try:
                self.files_collection.insert_one({
                    'unique_id': unique_id,
                    **file_data
                })
            except Exception as e:
                logger.error(f"MongoDB save failed: {e}")
                self.files[unique_id] = file_data
        else:
            self.files[unique_id] = file_data
        
        return unique_id
    
    def get_file(self, unique_id: str) -> Optional[dict]:
        """Get file data by unique ID"""
        if self.client:
            try:
                return self.files_collection.find_one({'unique_id': unique_id})
            except Exception as e:
                logger.error(f"MongoDB get failed: {e}")
                return self.files.get(unique_id)
        else:
            return self.files.get(unique_id)
    
    def generate_redeem_code(self, unique_id: str) -> str:
        """Generate redeem code for a file"""
        code = hashlib.md5(f"{unique_id}{datetime.now()}".encode()).hexdigest()[:8].upper()
        
        redeem_data = {
            'code': code,
            'file_id': unique_id,
            'created_at': datetime.now(),
            'used': False
        }
        
        if self.client:
            try:
                self.codes_collection.insert_one(redeem_data)
            except Exception as e:
                logger.error(f"MongoDB redeem save failed: {e}")
                self.redeem_codes[code] = redeem_data
        else:
            self.redeem_codes[code] = redeem_data
        
        return code
    
    def use_redeem_code(self, code: str, user_id: int) -> Optional[str]:
        """Use redeem code and return file unique_id"""
        if self.client:
            try:
                redeem_data = self.codes_collection.find_one({'code': code, 'used': False})
                if redeem_data:
                    self.codes_collection.update_one(
                        {'code': code},
                        {'$set': {'used': True, 'used_by': user_id, 'used_at': datetime.now()}}
                    )
                    return redeem_data['file_id']
            except Exception as e:
                logger.error(f"MongoDB redeem use failed: {e}")
                redeem_data = self.redeem_codes.get(code)
                if redeem_data and not redeem_data['used']:
                    redeem_data['used'] = True
                    redeem_data['used_by'] = user_id
                    return redeem_data['file_id']
        else:
            redeem_data = self.redeem_codes.get(code)
            if redeem_data and not redeem_data['used']:
                redeem_data['used'] = True
                redeem_data['used_by'] = user_id
                return redeem_data['file_id']
        
        return None
    
    def get_user_stars(self, user_id: int) -> int:
        """Get user's star balance"""
        if self.client:
            try:
                user_data = self.stars_collection.find_one({'user_id': user_id})
                return user_data['stars'] if user_data else 0
            except Exception as e:
                logger.error(f"MongoDB stars get failed: {e}")
                return self.user_stars.get(user_id, 0)
        else:
            return self.user_stars.get(user_id, 0)
    
    def add_user_stars(self, user_id: int, stars: int):
        """Add stars to user balance"""
        current_stars = self.get_user_stars(user_id)
        new_balance = current_stars + stars
        
        if self.client:
            try:
                self.stars_collection.update_one(
                    {'user_id': user_id},
                    {'$set': {'stars': new_balance, 'updated_at': datetime.now()}},
                    upsert=True
                )
            except Exception as e:
                logger.error(f"MongoDB stars add failed: {e}")
                self.user_stars[user_id] = new_balance
        else:
            self.user_stars[user_id] = new_balance
    
    def spend_user_stars(self, user_id: int, stars: int) -> bool:
        """Spend user stars if balance is sufficient"""
        current_stars = self.get_user_stars(user_id)
        if current_stars >= stars:
            self.add_user_stars(user_id, -stars)
            return True
        return False

class TelegramFileBot:
    """Main bot class"""
    
    def __init__(self, token: str):
        self.token = token
        self.storage = FileStorage()
        self.bot = Bot(token)
        
        # Build application
        self.application = Application.builder().token(token).build()
        
        # Add handlers
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup all command and message handlers"""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("balance", self.balance_command))
        self.application.add_handler(CommandHandler("redeem", self.redeem_command))
        
        # Message handlers
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.application.add_handler(MessageHandler(filters.VIDEO, self.handle_video))
        self.application.add_handler(MessageHandler(filters.AUDIO, self.handle_audio))
        
        # Callback query handler
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Payment handlers
        self.application.add_handler(PreCheckoutQueryHandler(self.precheckout_callback))
        self.application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self.successful_payment_callback))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        args = context.args
        user = update.effective_user
        
        if args and len(args) > 0:
            # Handle file access link
            unique_id = args[0]
            file_data = self.storage.get_file(unique_id)
            
            if not file_data:
                await update.message.reply_text("❌ File not found or expired!")
                return
            
            await self.show_file_access(update, file_data, user.id)
        else:
            welcome_text = (
                "🌟 Welcome to File Sharing Bot! 🌟\n\n"
                "📁 Send me any file and I'll create a shareable link\n"
                "⭐ Set star prices for premium access\n"
                "🎫 Generate redeem codes for free access\n"
                "💰 Earn stars from file purchases\n\n"
                "Commands:\n"
                "/help - Show help\n"
                "/balance - Check your stars\n"
                "/redeem <code> - Use redeem code\n\n"
                "Just send me a file to get started! ✨"
            )
            await update.message.reply_text(welcome_text)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = (
            "🤖 **File Sharing Bot Help**\n\n"
            "**How to use:**\n"
            "1️⃣ Send any file (document, photo, video, audio)\n"
            "2️⃣ Set pricing (free or stars required)\n"
            "3️⃣ Share the generated link\n"
            "4️⃣ Generate redeem codes for free access\n\n"
            "**Commands:**\n"
            "• `/start` - Start the bot\n"
            "• `/balance` - Check your star balance\n"
            "• `/redeem <code>` - Use a redeem code\n\n"
            "**Features:**\n"
            "⭐ Star-based payments\n"
            "🎫 Redeem codes\n"
            "📊 File analytics\n"
            "🔗 Public sharing links\n\n"
            "Created by @NY_BOTS ✨"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        user_id = update.effective_user.id
        balance = self.storage.get_user_stars(user_id)
        
        await update.message.reply_text(
            f"⭐ Your current balance: **{balance} stars**",
            parse_mode='Markdown'
        )
    
    async def redeem_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /redeem command"""
        if not context.args:
            await update.message.reply_text(
                "Usage: `/redeem <code>`\n"
                "Example: `/redeem ABC12345`",
                parse_mode='Markdown'
            )
            return
        
        code = context.args[0].upper()
        user_id = update.effective_user.id
        
        file_id = self.storage.use_redeem_code(code, user_id)
        
        if file_id:
            file_data = self.storage.get_file(file_id)
            if file_data:
                await self.send_file_to_user(update, file_data)
                await update.message.reply_text("✅ Redeem code used successfully!")
            else:
                await update.message.reply_text("❌ File not found!")
        else:
            await update.message.reply_text("❌ Invalid or already used redeem code!")
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle document uploads"""
        document = update.message.document
        await self.process_file(update, 'document', document.file_id, document.file_name, document.file_size)
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo uploads"""
        photo = update.message.photo[-1]  # Get highest resolution
        await self.process_file(update, 'photo', photo.file_id, 'photo.jpg', photo.file_size)
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle video uploads"""
        video = update.message.video
        filename = video.file_name or 'video.mp4'
        await self.process_file(update, 'video', video.file_id, filename, video.file_size)
    
    async def handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle audio uploads"""
        audio = update.message.audio
        filename = audio.file_name or 'audio.mp3'
        await self.process_file(update, 'audio', audio.file_id, filename, audio.file_size)
    
    async def process_file(self, update: Update, file_type: str, file_id: str, filename: str, file_size: int):
        """Process uploaded file"""
        user = update.effective_user
        
        file_data = {
            'file_type': file_type,
            'file_id': file_id,
            'filename': filename,
            'file_size': file_size,
            'owner_id': user.id,
            'owner_username': user.username,
            'upload_date': datetime.now(),
            'downloads': 0,
            'stars_earned': 0
        }
        
        unique_id = self.storage.save_file(file_id, file_data)
        
        # Create pricing keyboard
        keyboard = [
            [
                InlineKeyboardButton("🆓 Free Access", callback_data=f"price_free_{unique_id}"),
                InlineKeyboardButton("⭐ 1 Star", callback_data=f"price_1_{unique_id}")
            ],
            [
                InlineKeyboardButton("⭐ 5 Stars", callback_data=f"price_5_{unique_id}"),
                InlineKeyboardButton("⭐ 10 Stars", callback_data=f"price_10_{unique_id}")
            ],
            [
                InlineKeyboardButton("⭐ Custom Price", callback_data=f"price_custom_{unique_id}")
            ]
        ]
        
        await update.message.reply_text(
            f"📁 File uploaded: **{filename}**\n"
            f"📊 Size: {self.format_file_size(file_size)}\n\n"
            "Please select the access price:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data.startswith('price_'):
            await self.handle_price_selection(query, data)
        elif data.startswith('buy_'):
            await self.handle_buy_file(query, data)
        elif data.startswith('generate_code_'):
            await self.handle_generate_code(query, data)
        elif data.startswith('view_stats_'):
            await self.handle_view_stats(query, data)
    
    async def handle_price_selection(self, query, data):
        """Handle price selection for uploaded file"""
        parts = data.split('_')
        price_type = parts[1]
        unique_id = parts[2]
        
        file_data = self.storage.get_file(unique_id)
        if not file_data:
            await query.edit_message_text("❌ File not found!")
            return
        
        if price_type == 'custom':
            await query.edit_message_text(
                "Please reply with your custom price (number of stars):"
            )
            return
        
        price = 0 if price_type == 'free' else int(price_type)
        
        # Update file with price
        if self.storage.client:
            try:
                self.storage.files_collection.update_one(
                    {'unique_id': unique_id},
                    {'$set': {'price': price}}
                )
            except Exception as e:
                logger.error(f"Price update failed: {e}")
                self.storage.files[unique_id]['price'] = price
        else:
            self.storage.files[unique_id]['price'] = price
        
        bot_username = (await self.bot.get_me()).username
        share_link = f"https://t.me/{bot_username}?start={unique_id}"
        
        price_text = "🆓 FREE" if price == 0 else f"⭐ {price} stars"
        
        keyboard = [
            [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data=f"generate_code_{unique_id}")],
            [InlineKeyboardButton("📊 View Stats", callback_data=f"view_stats_{unique_id}")]
        ]
        
        await query.edit_message_text(
            f"✅ File configured successfully!\n\n"
            f"📁 **{file_data['filename']}**\n"
            f"💰 Price: {price_text}\n"
            f"🔗 **Share Link:**\n`{share_link}`\n\n"
            "Copy and share this link with others!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def handle_buy_file(self, query, data):
        """Handle file purchase with stars"""
        unique_id = data.split('_')[1]
        user_id = query.from_user.id
        
        file_data = self.storage.get_file(unique_id)
        if not file_data:
            await query.edit_message_text("❌ File not found!")
            return
        
        price = file_data.get('price', 0)
        user_balance = self.storage.get_user_stars(user_id)
        
        if user_balance >= price:
            # Process purchase
            self.storage.spend_user_stars(user_id, price)
            self.storage.add_user_stars(file_data['owner_id'], price)
            
            # Update file stats
            if self.storage.client:
                try:
                    self.storage.files_collection.update_one(
                        {'unique_id': unique_id},
                        {
                            '$inc': {
                                'downloads': 1,
                                'stars_earned': price
                            }
                        }
                    )
                except Exception as e:
                    logger.error(f"Stats update failed: {e}")
            
            await self.send_file_to_user(query, file_data)
            await query.edit_message_text(
                f"✅ Purchase successful!\n"
                f"💸 Spent: {price} stars\n"
                f"💰 Remaining balance: {self.storage.get_user_stars(user_id)} stars"
            )
        else:
            needed_stars = price - user_balance
            keyboard = [
                [InlineKeyboardButton("💳 Buy Stars", callback_data=f"buy_stars_{needed_stars}")]
            ]
            
            await query.edit_message_text(
                f"❌ Insufficient stars!\n"
                f"💰 Your balance: {user_balance} stars\n"
                f"💸 Required: {price} stars\n"
                f"📈 Need: {needed_stars} more stars",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    async def handle_generate_code(self, query, data):
        """Generate redeem code for file"""
        unique_id = data.split('_')[2]
        user_id = query.from_user.id
        
        file_data = self.storage.get_file(unique_id)
        if not file_data or file_data['owner_id'] != user_id:
            await query.answer("❌ Access denied!", show_alert=True)
            return
        
        redeem_code = self.storage.generate_redeem_code(unique_id)
        
        await query.edit_message_text(
            f"🎫 **Redeem Code Generated!**\n\n"
            f"📁 File: {file_data['filename']}\n"
            f"🔑 Code: `{redeem_code}`\n\n"
            f"Share this code with users for free access!\n"
            f"Usage: `/redeem {redeem_code}`",
            parse_mode='Markdown'
        )
    
    async def handle_view_stats(self, query, data):
        """View file statistics"""
        unique_id = data.split('_')[2]
        user_id = query.from_user.id
        
        file_data = self.storage.get_file(unique_id)
        if not file_data or file_data['owner_id'] != user_id:
            await query.answer("❌ Access denied!", show_alert=True)
            return
        
        downloads = file_data.get('downloads', 0)
        stars_earned = file_data.get('stars_earned', 0)
        price = file_data.get('price', 0)
        upload_date = file_data.get('upload_date', datetime.now())
        
        stats_text = (
            f"📊 **File Statistics**\n\n"
            f"📁 **{file_data['filename']}**\n"
            f"📅 Uploaded: {upload_date.strftime('%Y-%m-%d %H:%M')}\n"
            f"💰 Price: {'🆓 FREE' if price == 0 else f'⭐ {price} stars'}\n"
            f"⬇️ Downloads: {downloads}\n"
            f"💎 Stars Earned: {stars_earned}\n"
            f"📊 File Size: {self.format_file_size(file_data['file_size'])}"
        )
        
        keyboard = [
            [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data=f"generate_code_{unique_id}")]
        ]
        
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def show_file_access(self, update, file_data: dict, user_id: int):
        """Show file access options"""
        price = file_data.get('price', 0)
        filename = file_data['filename']
        file_size = self.format_file_size(file_data['file_size'])
        
        if price == 0:
            # Free file
            await self.send_file_to_user(update, file_data)
            
            if hasattr(update, 'message'):
                await update.message.reply_text("✅ Free file downloaded!")
            else:
                await update.callback_query.message.reply_text("✅ Free file downloaded!")
        else:
            # Paid file
            user_balance = self.storage.get_user_stars(user_id)
            
            keyboard = [
                [InlineKeyboardButton(f"💳 Buy for {price} ⭐", callback_data=f"buy_{file_data['unique_id']}")]
            ]
            
            access_text = (
                f"📁 **{filename}**\n"
                f"📊 Size: {file_size}\n"
                f"💰 Price: {price} ⭐\n"
                f"💎 Your balance: {user_balance} ⭐\n\n"
            )
            
            if user_balance >= price:
                access_text += "✅ You can purchase this file!"
            else:
                needed = price - user_balance
                access_text += f"❌ You need {needed} more stars to purchase this file."
                keyboard.append([
                    InlineKeyboardButton("💳 Buy Stars", callback_data=f"buy_stars_{needed}")
                ])
            
            if hasattr(update, 'message'):
                await update.message.reply_text(
                    access_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                await update.callback_query.edit_message_text(
                    access_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
    
    async def send_file_to_user(self, update, file_data: dict):
        """Send file to user"""
        file_type = file_data['file_type']
        file_id = file_data['file_id']
        filename = file_data['filename']
        
        try:
            if hasattr(update, 'message'):
                chat_id = update.message.chat_id
            else:
                chat_id = update.callback_query.message.chat_id
            
            if file_type == 'document':
                await self.bot.send_document(chat_id=chat_id, document=file_id, caption=f"📁 {filename}")
            elif file_type == 'photo':
                await self.bot.send_photo(chat_id=chat_id, photo=file_id, caption=f"🖼️ {filename}")
            elif file_type == 'video':
                await self.bot.send_video(chat_id=chat_id, video=file_id, caption=f"🎥 {filename}")
            elif file_type == 'audio':
                await self.bot.send_audio(chat_id=chat_id, audio=file_id, caption=f"🎵 {filename}")
        
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            if hasattr(update, 'message'):
                await update.message.reply_text("❌ Failed to send file. It may have expired.")
            else:
                await update.callback_query.message.reply_text("❌ Failed to send file. It may have expired.")
    
    async def precheckout_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle pre-checkout for star purchases"""
        query = update.pre_checkout_query
        await query.answer(ok=True)
    
    async def successful_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle successful star purchases"""
        payment = update.message.successful_payment
        user_id = update.effective_user.id
        
        # Add stars to user balance
        stars_purchased = payment.total_amount  # Assuming 1:1 ratio
        self.storage.add_user_stars(user_id, stars_purchased)
        
        await update.message.reply_text(
            f"✅ Payment successful!\n"
            f"⭐ Added {stars_purchased} stars to your account\n"
            f"💰 New balance: {self.storage.get_user_stars(user_id)} stars"
        )
    
    @staticmethod
    def format_file_size(size_bytes: int) -> str:
        """Format file size in human readable format"""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024
            i += 1
        
        return f"{size_bytes:.1f} {size_names[i]}"
    
    async def run(self):
        """Run the bot"""
        try:
            logger.info("Starting bot...")
            
            # Initialize application
            await self.application.initialize()
            await self.application.start()
            
            # Start polling with better error handling
            updater = self.application.updater
            await updater.start_polling(
                poll_interval=2.0,
                timeout=20,
                bootstrap_retries=3,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30
            )
            
            logger.info("Bot is running and polling...")
            
            # Run until stopped
            try:
                await updater.idle()
            except KeyboardInterrupt:
                logger.info("Received interrupt signal...")
                
        except Exception as e:
            logger.error(f"Error running bot: {e}")
            raise
        finally:
            logger.info("Shutting down bot...")
            try:
                if hasattr(self.application, 'updater') and self.application.updater.running:
                    await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")

def main():
    """Main function"""
    # Get bot token from environment
    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        logger.error("BOT_TOKEN environment variable is required!")
        logger.error("Please set BOT_TOKEN in your environment variables")
        return 1
    
    logger.info("Bot token found, initializing...")
    
    # Create bot instance
    try:
        bot = TelegramFileBot(bot_token)
        logger.info("Bot instance created successfully")
    except Exception as e:
        logger.error(f"Failed to create bot instance: {e}")
        return 1
    
    # Run the bot
    try:
        logger.info("Starting bot main loop...")
        asyncio.run(bot.run())
        return 0
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code if exit_code is not None else 0)
