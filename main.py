import os
import logging
import asyncio
import secrets
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    Application
)
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import motor.motor_asyncio

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://Nischay999:Nischay999@cluster0.5kufo.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DATABASE_NAME = "telegram_file_bot"
PORT = int(os.getenv("PORT", 8080))

# MongoDB setup
client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = client[DATABASE_NAME]

# Collections
users_collection = db.users
files_collection = db.files
transactions_collection = db.transactions
redeem_codes_collection = db.redeem_codes

class FileBot:
    def __init__(self):
        self.bot_username = None
        self.admin_users = set()
        
    async def initialize(self, bot):
        """Initialize bot data"""
        try:
            bot_info = await bot.get_me()
            self.bot_username = bot_info.username
            logger.info(f"Bot initialized: @{self.bot_username}")
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")

    def generate_code(self, length=8):
        """Generate random code"""
        chars = string.ascii_uppercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(length))

    async def ensure_user_exists(self, user_id: int, username: str = None, first_name: str = None):
        """Ensure user exists in database"""
        user_data = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "stars_balance": 0,
            "files_uploaded": 0,
            "joined_date": datetime.now(),
            "last_active": datetime.now()
        }
        
        await users_collection.update_one(
            {"user_id": user_id},
            {"$setOnInsert": user_data, "$set": {"last_active": datetime.now()}},
            upsert=True
        )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        await self.ensure_user_exists(user.id, user.username, user.first_name)
        
        # Check if it's a file access link
        if context.args:
            file_id = context.args[0]
            await self.handle_file_access(update, context, file_id)
            return
            
        welcome_text = f"""
🎉 **Welcome to File Storage Bot!** 

📁 **Features:**
• Upload and store files securely
• Generate public access links
• Set star prices for file access
• Create redeem codes for free access
• Earn stars from file sales

💫 **Your Stats:**
• Stars Balance: {await self.get_user_stars(user.id)} ⭐
• Files Uploaded: {await self.get_user_files_count(user.id)}

🚀 **Get Started:**
Just send me any file to upload!
        """
        
        keyboard = [
            [InlineKeyboardButton("📁 My Files", callback_data="my_files")],
            [InlineKeyboardButton("💫 Buy Stars", callback_data="buy_stars"),
             InlineKeyboardButton("🎫 Redeem Code", callback_data="redeem_code")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def handle_file_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle file uploads"""
        user = update.effective_user
        await self.ensure_user_exists(user.id, user.username, user.first_name)
        
        # Get file info
        file_obj = None
        file_name = None
        file_size = 0
        
        if update.message.document:
            file_obj = update.message.document
            file_name = file_obj.file_name
            file_size = file_obj.file_size
        elif update.message.photo:
            file_obj = update.message.photo[-1]  # Get highest quality
            file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            file_size = file_obj.file_size
        elif update.message.video:
            file_obj = update.message.video
            file_name = file_obj.file_name or f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            file_size = file_obj.file_size
        elif update.message.audio:
            file_obj = update.message.audio
            file_name = file_obj.file_name or f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
            file_size = file_obj.file_size
        else:
            await update.message.reply_text("❌ Unsupported file type!")
            return
            
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            await update.message.reply_text("❌ File too large! Maximum size is 50MB.")
            return
            
        # Generate unique file ID
        file_id = self.generate_code(12)
        
        # Store file info in database
        file_data = {
            "file_id": file_id,
            "telegram_file_id": file_obj.file_id,
            "file_name": file_name,
            "file_size": file_size,
            "owner_id": user.id,
            "owner_username": user.username,
            "upload_date": datetime.now(),
            "access_count": 0,
            "star_price": 0,  # Default free
            "is_public": True,
            "description": ""
        }
        
        try:
            await files_collection.insert_one(file_data)
            
            # Update user stats
            await users_collection.update_one(
                {"user_id": user.id},
                {"$inc": {"files_uploaded": 1}}
            )
            
            # Create access link
            bot_username = self.bot_username or "your_bot"
            access_link = f"https://t.me/{bot_username}?start={file_id}"
            
            keyboard = [
                [InlineKeyboardButton("⚙️ Set Price", callback_data=f"set_price_{file_id}")],
                [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data=f"gen_redeem_{file_id}")],
                [InlineKeyboardButton("📊 File Stats", callback_data=f"file_stats_{file_id}")],
                [InlineKeyboardButton("🔗 Share Link", url=access_link)]
            ]
            
            success_text = f"""
✅ **File Uploaded Successfully!**

📁 **File:** {file_name}
📏 **Size:** {self.format_file_size(file_size)}
🔗 **Access Link:** `{access_link}`
💰 **Current Price:** Free

You can set a star price or generate redeem codes using the buttons below.
            """
            
            await update.message.reply_text(
                success_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error storing file: {e}")
            await update.message.reply_text("❌ Error uploading file. Please try again.")

    async def handle_file_access(self, update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str):
        """Handle file access via link"""
        user = update.effective_user
        await self.ensure_user_exists(user.id, user.username, user.first_name)
        
        # Get file info
        file_data = await files_collection.find_one({"file_id": file_id})
        if not file_data:
            await update.message.reply_text("❌ File not found or has been removed.")
            return
            
        # Check if user is owner
        if file_data["owner_id"] == user.id:
            await self.send_file_to_user(update, file_data)
            return
            
        # Check if file is free
        if file_data["star_price"] == 0:
            await self.send_file_to_user(update, file_data)
            await files_collection.update_one(
                {"file_id": file_id},
                {"$inc": {"access_count": 1}}
            )
            return
            
        # File requires stars
        user_stars = await self.get_user_stars(user.id)
        required_stars = file_data["star_price"]
        
        if user_stars >= required_stars:
            keyboard = [
                [InlineKeyboardButton(f"💫 Pay {required_stars} Stars", callback_data=f"pay_stars_{file_id}")],
                [InlineKeyboardButton("🎫 Enter Redeem Code", callback_data=f"redeem_file_{file_id}")],
                [InlineKeyboardButton("💫 Buy More Stars", callback_data="buy_stars")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("🎫 Enter Redeem Code", callback_data=f"redeem_file_{file_id}")],
                [InlineKeyboardButton("💫 Buy Stars", callback_data="buy_stars")]
            ]
            
        access_text = f"""
📁 **{file_data['file_name']}**
📏 **Size:** {self.format_file_size(file_data['file_size'])}
👤 **Owner:** @{file_data['owner_username'] or 'Anonymous'}

💰 **Price:** {required_stars} ⭐
💫 **Your Stars:** {user_stars} ⭐

{f"✅ You can afford this file!" if user_stars >= required_stars else "❌ You need more stars!"}
        """
        
        await update.message.reply_text(
            access_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def send_file_to_user(self, update: Update, file_data: dict):
        """Send file to user"""
        try:
            caption = f"📁 {file_data['file_name']}\n👤 Shared by @{file_data['owner_username'] or 'Anonymous'}"
            
            if file_data['file_name'].lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                await update.effective_chat.send_photo(
                    photo=file_data['telegram_file_id'],
                    caption=caption
                )
            elif file_data['file_name'].lower().endswith(('.mp4', '.avi', '.mov')):
                await update.effective_chat.send_video(
                    video=file_data['telegram_file_id'],
                    caption=caption
                )
            elif file_data['file_name'].lower().endswith(('.mp3', '.wav', '.ogg')):
                await update.effective_chat.send_audio(
                    audio=file_data['telegram_file_id'],
                    caption=caption
                )
            else:
                await update.effective_chat.send_document(
                    document=file_data['telegram_file_id'],
                    caption=caption
                )
                
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await update.effective_chat.send_message("❌ Error accessing file. File may have been removed.")

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user = update.effective_user
        
        if data == "my_files":
            await self.show_user_files(update, context)
        elif data == "buy_stars":
            await self.show_buy_stars(update, context)
        elif data == "redeem_code":
            await self.show_redeem_code_input(update, context)
        elif data == "help":
            await self.show_help(update, context)
        elif data.startswith("set_price_"):
            file_id = data.split("_", 2)[2]
            await self.show_set_price(update, context, file_id)
        elif data.startswith("gen_redeem_"):
            file_id = data.split("_", 2)[2]
            await self.generate_redeem_code(update, context, file_id)
        elif data.startswith("pay_stars_"):
            file_id = data.split("_", 2)[2]
            await self.process_star_payment(update, context, file_id)
        elif data.startswith("redeem_file_"):
            file_id = data.split("_", 2)[2]
            context.user_data["redeem_file_id"] = file_id
            await query.edit_message_text("🎫 Please enter your redeem code:")
        elif data.startswith("buy_stars_"):
            amount = int(data.split("_")[2])
            await self.create_star_invoice(update, context, amount)

    async def show_user_files(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's uploaded files"""
        user = update.effective_user
        
        files = await files_collection.find({"owner_id": user.id}).sort("upload_date", -1).limit(10).to_list(None)
        
        if not files:
            await update.callback_query.edit_message_text("📁 You haven't uploaded any files yet.")
            return
            
        text = "📁 **Your Files:**\n\n"
        keyboard = []
        
        for file_data in files:
            text += f"• {file_data['file_name']}\n"
            text += f"  💰 Price: {file_data['star_price']} ⭐\n"
            text += f"  👁 Views: {file_data['access_count']}\n\n"
            
            keyboard.append([InlineKeyboardButton(
                f"⚙️ {file_data['file_name'][:20]}...",
                callback_data=f"file_stats_{file_data['file_id']}"
            )])
            
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start")])
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def show_buy_stars(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show star purchase options"""
        text = """
💫 **Buy Telegram Stars**

Choose how many stars you want to purchase:
        """
        
        keyboard = [
            [InlineKeyboardButton("⭐ 10 Stars", callback_data="buy_stars_10")],
            [InlineKeyboardButton("⭐ 25 Stars", callback_data="buy_stars_25")],
            [InlineKeyboardButton("⭐ 50 Stars", callback_data="buy_stars_50")],
            [InlineKeyboardButton("⭐ 100 Stars", callback_data="buy_stars_100")],
            [InlineKeyboardButton("🔙 Back", callback_data="start")]
        ]
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def create_star_invoice(self, update: Update, context: ContextTypes.DEFAULT_TYPE, stars: int):
        """Create a star payment invoice"""
        try:
            # Create invoice for star payment
            await context.bot.send_invoice(
                chat_id=update.effective_chat.id,
                title=f"Buy {stars} Telegram Stars",
                description=f"Purchase {stars} stars for accessing premium files",
                payload=f"stars_{stars}_{update.effective_user.id}",
                provider_token="",  # Empty for stars
                currency="XTR",  # Telegram Stars currency
                prices=[{"label": f"{stars} Stars", "amount": stars}],
                max_tip_amount=0,
                suggested_tip_amounts=[],
                photo_url=None,
                photo_size=None,
                photo_width=None,
                photo_height=None,
                need_name=False,
                need_phone_number=False,
                need_email=False,
                need_shipping_address=False,
                send_phone_number_to_provider=False,
                send_email_to_provider=False,
                is_flexible=False
            )
        except Exception as e:
            logger.error(f"Error creating star invoice: {e}")
            await update.callback_query.edit_message_text(
                "❌ Unable to create payment. This feature requires proper bot configuration."
            )

    async def show_set_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str):
        """Show price setting options"""
        user = update.effective_user
        
        # Verify file ownership
        file_data = await files_collection.find_one({"file_id": file_id, "owner_id": user.id})
        if not file_data:
            await update.callback_query.edit_message_text("❌ File not found or you don't own this file.")
            return
        
        text = f"""
⚙️ **Set Price for File**

📁 **File:** {file_data['file_name']}
💰 **Current Price:** {file_data['star_price']} ⭐

Choose a new price:
        """
        
        keyboard = [
            [InlineKeyboardButton("🆓 Free", callback_data=f"price_0_{file_id}")],
            [InlineKeyboardButton("⭐ 1 Star", callback_data=f"price_1_{file_id}"),
             InlineKeyboardButton("⭐ 2 Stars", callback_data=f"price_2_{file_id}")],
            [InlineKeyboardButton("⭐ 5 Stars", callback_data=f"price_5_{file_id}"),
             InlineKeyboardButton("⭐ 10 Stars", callback_data=f"price_10_{file_id}")],
            [InlineKeyboardButton("⭐ 25 Stars", callback_data=f"price_25_{file_id}"),
             InlineKeyboardButton("⭐ 50 Stars", callback_data=f"price_50_{file_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="my_files")]
        ]
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def process_star_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str):
        """Process star payment for file access"""
        user = update.effective_user
        
        # Get file info
        file_data = await files_collection.find_one({"file_id": file_id})
        if not file_data:
            await update.callback_query.edit_message_text("❌ File not found.")
            return
            
        required_stars = file_data["star_price"]
        user_stars = await self.get_user_stars(user.id)
        
        if user_stars < required_stars:
            await update.callback_query.edit_message_text("❌ Insufficient stars!")
            return
            
        # Deduct stars from user
        await users_collection.update_one(
            {"user_id": user.id},
            {"$inc": {"stars_balance": -required_stars}}
        )
        
        # Add stars to file owner
        await users_collection.update_one(
            {"user_id": file_data["owner_id"]},
            {"$inc": {"stars_balance": required_stars}}
        )
        
        # Record transaction
        transaction_data = {
            "buyer_id": user.id,
            "seller_id": file_data["owner_id"],
            "file_id": file_id,
            "amount": required_stars,
            "date": datetime.now()
        }
        await transactions_collection.insert_one(transaction_data)
        
        # Update file access count
        await files_collection.update_one(
            {"file_id": file_id},
            {"$inc": {"access_count": 1}}
        )
        
        # Send file to user
        await self.send_file_to_user(update, file_data)
        await update.callback_query.edit_message_text(
            f"✅ Payment successful! {required_stars} ⭐ stars deducted."
        )

    async def show_redeem_code_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show redeem code input"""
        context.user_data["awaiting_redeem_code"] = True
        await update.callback_query.edit_message_text(
            "🎫 **Enter Redeem Code**\n\nPlease type your redeem code:"
        )

    async def handle_redeem_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle redeem code input"""
        if not context.user_data.get("awaiting_redeem_code") and not context.user_data.get("redeem_file_id"):
            return
            
        code = update.message.text.strip().upper()
        user = update.effective_user
        
        # Find redeem code
        redeem_data = await redeem_codes_collection.find_one({"code": code, "is_used": False})
        
        if not redeem_data:
            await update.message.reply_text("❌ Invalid or expired redeem code!")
            return
            
        # Check if it's for a specific file
        if context.user_data.get("redeem_file_id"):
            file_id = context.user_data["redeem_file_id"]
            if redeem_data.get("file_id") and redeem_data["file_id"] != file_id:
                await update.message.reply_text("❌ This redeem code is not valid for this file!")
                return
                
        # Process redemption
        if redeem_data.get("file_id"):
            # File access redeem code
            file_data = await files_collection.find_one({"file_id": redeem_data["file_id"]})
            if file_data:
                await self.send_file_to_user(update, file_data)
                await update.message.reply_text("✅ File accessed successfully!")
                
                # Mark code as used
                await redeem_codes_collection.update_one(
                    {"code": code},
                    {"$set": {"is_used": True, "used_by": user.id, "used_date": datetime.now()}}
                )
        elif redeem_data.get("stars"):
            # Stars redeem code
            stars = redeem_data["stars"]
            await users_collection.update_one(
                {"user_id": user.id},
                {"$inc": {"stars_balance": stars}}
            )
            
            await update.message.reply_text(f"✅ Redeemed {stars} ⭐ stars successfully!")
            
            # Mark code as used
            await redeem_codes_collection.update_one(
                {"code": code},
                {"$set": {"is_used": True, "used_by": user.id, "used_date": datetime.now()}}
            )
            
        # Clear user data
        context.user_data.pop("awaiting_redeem_code", None)
        context.user_data.pop("redeem_file_id", None)

    async def generate_redeem_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str):
        """Generate redeem code for file"""
        user = update.effective_user
        
        # Verify file ownership
        file_data = await files_collection.find_one({"file_id": file_id, "owner_id": user.id})
        if not file_data:
            await update.callback_query.edit_message_text("❌ File not found or you don't own this file.")
            return
            
        # Generate redeem code
        code = self.generate_code(8)
        
        redeem_data = {
            "code": code,
            "file_id": file_id,
            "created_by": user.id,
            "created_date": datetime.now(),
            "is_used": False,
            "expires_date": datetime.now() + timedelta(days=30)  # 30 days validity
        }
        
        try:
            await redeem_codes_collection.insert_one(redeem_data)
            
            text = f"""
✅ **Redeem Code Generated!**

🎫 **Code:** `{code}`
📁 **File:** {file_data['file_name']}
⏰ **Valid Until:** {redeem_data['expires_date'].strftime('%Y-%m-%d')}

Share this code with others to give them free access to your file!
            """
            
            await update.callback_query.edit_message_text(text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error generating redeem code: {e}")
            await update.callback_query.edit_message_text("❌ Error generating redeem code.")

    async def get_user_stars(self, user_id: int) -> int:
        """Get user's star balance"""
        user_data = await users_collection.find_one({"user_id": user_id})
        return user_data.get("stars_balance", 0) if user_data else 0

    async def get_user_files_count(self, user_id: int) -> int:
        """Get user's uploaded files count"""
        return await files_collection.count_documents({"owner_id": user_id})

    def format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format"""
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.1f}{size_names[i]}"

    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help information"""
        help_text = """
🤖 **File Storage Bot Help**

**📤 Uploading Files:**
• Send any file (document, photo, video, audio)
• Get a shareable link instantly
• Set star prices for premium access

**💰 Earning Stars:**
• Set prices on your files
• Earn stars when people buy access
• Generate free redeem codes for friends

**💫 Using Stars:**
• Buy stars to access premium files
• Use redeem codes for free access
• Check your balance anytime

**🎫 Redeem Codes:**
• Get free access to premium files
• Valid for 30 days after creation
• One-time use only

**Commands:**
• /start - Main menu
• Send file - Upload new file
• Send redeem code - Redeem code

Need more help? Contact @NY_BOTS
        """
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
        
        await update.callback_query.edit_message_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

file_bot = FileBot()

async def main():
    """Start the bot using simple polling"""
    try:
        # Create bot instance
        bot = telegram.Bot(token=BOT_TOKEN)
        
        # Initialize file bot
        await file_bot.initialize(bot)
        
        # Create application with simple setup
        app = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        app.add_handler(CommandHandler("start", file_bot.start_command))
        app.add_handler(CallbackQueryHandler(file_bot.handle_callback_query))
        app.add_handler(MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO,
            file_bot.handle_file_upload
        ))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, file_bot.handle_redeem_code))
        
        # Add callback handlers for price setting
        async def handle_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.callback_query
            await query.answer()
            
            if query.data.startswith("price_"):
                parts = query.data.split("_")
                price = int(parts[1])
                file_id = parts[2]
                
                # Update file price
                result = await files_collection.update_one(
                    {"file_id": file_id, "owner_id": update.effective_user.id},
                    {"$set": {"star_price": price}}
                )
                
                if result.modified_count > 0:
                    await query.edit_message_text(f"✅ Price updated to {price} ⭐ stars!")
                else:
                    await query.edit_message_text("❌ Error updating price.")
        
        app.add_handler(CallbackQueryHandler(handle_price_callback, pattern="^price_"))
        
        logger.info("Starting bot with polling...")
        
        # Use run_polling instead of the problematic updater
        await app.run_polling(
            poll_interval=1.0,
            timeout=10,
            bootstrap_retries=-1,
            read_timeout=10,
            write_timeout=10,
            connect_timeout=10,
            pool_timeout=10,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        raise

if __name__ == "__main__":
    # Run the bot
    # Ensure event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
