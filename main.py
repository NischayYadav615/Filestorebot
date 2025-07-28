import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
import sqlite3
from pathlib import Path

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_USERNAME = os.getenv('BOT_USERNAME', 'Files_store_NY_bot')
PORT = int(os.getenv('PORT', 8080))

class FileStorageBot:
    def __init__(self):
        self.db_path = 'bot_data.db'
        self.init_database()
        
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                stars_balance INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0,
                join_date TEXT
            )
        ''')
        
        # Files table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                owner_id INTEGER,
                file_name TEXT,
                file_id TEXT,
                file_type TEXT,
                description TEXT,
                stars_price INTEGER,
                is_free BOOLEAN DEFAULT 0,
                upload_date TEXT,
                downloads INTEGER DEFAULT 0,
                public_link TEXT
            )
        ''')
        
        # Redeem codes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS redeem_codes (
                code TEXT PRIMARY KEY,
                file_id TEXT,
                created_by INTEGER,
                uses_left INTEGER,
                max_uses INTEGER,
                created_date TEXT,
                FOREIGN KEY (file_id) REFERENCES files (id)
            )
        ''')
        
        # Transactions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                file_id TEXT,
                stars_amount INTEGER,
                transaction_type TEXT,
                date TEXT
            )
        ''')
        
        # User file access table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_file_access (
                user_id INTEGER,
                file_id TEXT,
                access_date TEXT,
                access_method TEXT,
                PRIMARY KEY (user_id, file_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'user_id': result[0],
                'username': result[1],
                'stars_balance': result[2],
                'total_earned': result[3],
                'join_date': result[4]
            }
        return None
    
    def create_user(self, user_id: int, username: str = None):
        """Create new user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, join_date)
            VALUES (?, ?, ?)
        ''', (user_id, username, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def update_user_stars(self, user_id: int, stars: int, transaction_type: str = 'earned'):
        """Update user stars balance"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if transaction_type == 'earned':
            cursor.execute('''
                UPDATE users SET stars_balance = stars_balance + ?, total_earned = total_earned + ?
                WHERE user_id = ?
            ''', (stars, stars, user_id))
        else:
            cursor.execute('''
                UPDATE users SET stars_balance = stars_balance - ?
                WHERE user_id = ?
            ''', (stars, user_id))
        
        conn.commit()
        conn.close()
    
    def save_file(self, owner_id: int, file_name: str, file_id: str, file_type: str, 
                  description: str = "", stars_price: int = 0) -> str:
        """Save file to database"""
        file_uuid = str(uuid.uuid4())
        public_link = f"https://t.me/{BOT_USERNAME}?start=file_{file_uuid}"
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO files (id, owner_id, file_name, file_id, file_type, description, 
                             stars_price, is_free, upload_date, public_link)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (file_uuid, owner_id, file_name, file_id, file_type, description, 
              stars_price, stars_price == 0, datetime.now().isoformat(), public_link))
        conn.commit()
        conn.close()
        
        return file_uuid
    
    def get_file(self, file_id: str) -> Optional[Dict]:
        """Get file from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM files WHERE id = ?', (file_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'id': result[0],
                'owner_id': result[1],
                'file_name': result[2],
                'file_id': result[3],
                'file_type': result[4],
                'description': result[5],
                'stars_price': result[6],
                'is_free': result[7],
                'upload_date': result[8],
                'downloads': result[9],
                'public_link': result[10]
            }
        return None
    
    def create_redeem_code(self, file_id: str, created_by: int, max_uses: int = 1) -> str:
        """Create redeem code for file"""
        code = f"REDEEM_{uuid.uuid4().hex[:8].upper()}"
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO redeem_codes (code, file_id, created_by, uses_left, max_uses, created_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (code, file_id, created_by, max_uses, max_uses, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        return code
    
    def use_redeem_code(self, code: str, user_id: int) -> tuple[bool, str]:
        """Use redeem code"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if code exists and has uses left
        cursor.execute('SELECT * FROM redeem_codes WHERE code = ? AND uses_left > 0', (code,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return False, "Invalid or expired redeem code."
        
        file_id = result[1]
        
        # Check if user already has access
        cursor.execute('SELECT * FROM user_file_access WHERE user_id = ? AND file_id = ?', (user_id, file_id))
        if cursor.fetchone():
            conn.close()
            return False, "You already have access to this file."
        
        # Grant access and decrease uses
        cursor.execute('''
            INSERT INTO user_file_access (user_id, file_id, access_date, access_method)
            VALUES (?, ?, ?, ?)
        ''', (user_id, file_id, datetime.now().isoformat(), 'redeem_code'))
        
        cursor.execute('UPDATE redeem_codes SET uses_left = uses_left - 1 WHERE code = ?', (code,))
        
        conn.commit()
        conn.close()
        
        return True, "Redeem code used successfully! You now have access to the file."
    
    def has_file_access(self, user_id: int, file_id: str) -> bool:
        """Check if user has access to file"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if user owns the file
        cursor.execute('SELECT owner_id FROM files WHERE id = ?', (file_id,))
        result = cursor.fetchone()
        if result and result[0] == user_id:
            conn.close()
            return True
        
        # Check if user has purchased access
        cursor.execute('SELECT * FROM user_file_access WHERE user_id = ? AND file_id = ?', (user_id, file_id))
        result = cursor.fetchone()
        conn.close()
        
        return result is not None
    
    def get_user_files(self, user_id: int) -> List[Dict]:
        """Get all files owned by user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM files WHERE owner_id = ? ORDER BY upload_date DESC', (user_id,))
        results = cursor.fetchall()
        conn.close()
        
        files = []
        for result in results:
            files.append({
                'id': result[0],
                'owner_id': result[1],
                'file_name': result[2],
                'file_id': result[3],
                'file_type': result[4],
                'description': result[5],
                'stars_price': result[6],
                'is_free': result[7],
                'upload_date': result[8],
                'downloads': result[9],
                'public_link': result[10]
            })
        
        return files

# Initialize bot instance
bot_instance = FileStorageBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    bot_instance.create_user(user.id, user.username)
    
    # Check if it's a file link
    if context.args and context.args[0].startswith('file_'):
        file_id = context.args[0].replace('file_', '')
        file_data = bot_instance.get_file(file_id)
        
        if not file_data:
            await update.message.reply_text("❌ File not found.")
            return
        
        # Check if user has access
        if bot_instance.has_file_access(user.id, file_id):
            keyboard = [[InlineKeyboardButton("📥 Download File", callback_data=f"download_{file_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"📁 **{file_data['file_name']}**\n\n"
                f"📝 {file_data['description']}\n"
                f"⭐ Price: {file_data['stars_price']} stars\n"
                f"📊 Downloads: {file_data['downloads']}\n\n"
                f"✅ You have access to this file!",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            if file_data['is_free']:
                keyboard = [[InlineKeyboardButton("📥 Get Free File", callback_data=f"get_free_{file_id}")]]
            else:
                keyboard = [
                    [InlineKeyboardButton(f"⭐ Buy for {file_data['stars_price']} stars", callback_data=f"buy_{file_id}")],
                    [InlineKeyboardButton("🎟️ Use Redeem Code", callback_data=f"redeem_prompt_{file_id}")]
                ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"📁 **{file_data['file_name']}**\n\n"
                f"📝 {file_data['description']}\n"
                f"⭐ Price: {file_data['stars_price']} stars\n"
                f"📊 Downloads: {file_data['downloads']}",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data="upload_file")],
        [InlineKeyboardButton("📁 My Files", callback_data="my_files")],
        [InlineKeyboardButton("⭐ My Stars", callback_data="my_stars")],
        [InlineKeyboardButton("🎟️ Redeem Code", callback_data="redeem_code")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🤖 **Welcome to File Storage Bot!**\n\n"
        f"📤 Upload files and share them with others\n"
        f"⭐ Earn Telegram Stars from downloads\n"
        f"🎟️ Create redeem codes for free access\n"
        f"🔗 Generate public links for sharing\n\n"
        f"Choose an option below to get started:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads"""
    user = update.effective_user
    document = update.message.document
    
    bot_instance.create_user(user.id, user.username)
    
    # Store upload context
    context.user_data['pending_file'] = {
        'file_name': document.file_name,
        'file_id': document.file_id,
        'file_type': 'document'
    }
    
    keyboard = [
        [InlineKeyboardButton("💰 Set Price (Stars)", callback_data="set_price")],
        [InlineKeyboardButton("🆓 Make Free", callback_data="make_free")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📁 **File Received: {document.file_name}**\n\n"
        f"Choose pricing for your file:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads"""
    user = update.effective_user
    photo = update.message.photo[-1]  # Get highest quality
    
    bot_instance.create_user(user.id, user.username)
    
    context.user_data['pending_file'] = {
        'file_name': f"Photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg",
        'file_id': photo.file_id,
        'file_type': 'photo'
    }
    
    keyboard = [
        [InlineKeyboardButton("💰 Set Price (Stars)", callback_data="set_price")],
        [InlineKeyboardButton("🆓 Make Free", callback_data="make_free")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🖼️ **Photo Received**\n\n"
        f"Choose pricing for your photo:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video uploads"""
    user = update.effective_user
    video = update.message.video
    
    bot_instance.create_user(user.id, user.username)
    
    context.user_data['pending_file'] = {
        'file_name': video.file_name or f"Video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4",
        'file_id': video.file_id,
        'file_type': 'video'
    }
    
    keyboard = [
        [InlineKeyboardButton("💰 Set Price (Stars)", callback_data="set_price")],
        [InlineKeyboardButton("🆓 Make Free", callback_data="make_free")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎥 **Video Received: {video.file_name or 'Video'}**\n\n"
        f"Choose pricing for your video:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    await query.answer()
    
    if data == "upload_file":
        await query.edit_message_text(
            "📤 **Upload a File**\n\n"
            "Send me any file (document, photo, video) and I'll help you set it up for sharing!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "my_files":
        files = bot_instance.get_user_files(user.id)
        if not files:
            await query.edit_message_text("📁 You haven't uploaded any files yet.")
            return
        
        text = "📁 **Your Files:**\n\n"
        keyboard = []
        
        for i, file in enumerate(files[:10]):  # Show first 10 files
            text += f"{i+1}. {file['file_name']} ({'Free' if file['is_free'] else f\"{file['stars_price']} ⭐\"})\n"
            keyboard.append([InlineKeyboardButton(f"📋 Manage {file['file_name'][:15]}...", 
                                                callback_data=f"manage_{file['id']}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "my_stars":
        user_data = bot_instance.get_user(user.id)
        if not user_data:
            bot_instance.create_user(user.id, user.username)
            user_data = bot_instance.get_user(user.id)
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⭐ **Your Stars Balance**\n\n"
            f"💰 Current Balance: {user_data['stars_balance']} stars\n"
            f"📊 Total Earned: {user_data['total_earned']} stars\n"
            f"📅 Member Since: {user_data['join_date'][:10]}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "redeem_code":
        await query.edit_message_text(
            "🎟️ **Enter Redeem Code**\n\n"
            "Send me your redeem code to access a file for free!"
        )
    
    elif data == "set_price":
        await query.edit_message_text(
            "💰 **Set File Price**\n\n"
            "Send me the number of stars you want to charge for this file (e.g., 5)"
        )
        context.user_data['awaiting_price'] = True
    
    elif data == "make_free":
        pending_file = context.user_data.get('pending_file')
        if not pending_file:
            await query.edit_message_text("❌ No pending file found.")
            return
        
        file_id = bot_instance.save_file(
            user.id,
            pending_file['file_name'],
            pending_file['file_id'],
            pending_file['file_type'],
            "",
            0
        )
        
        file_data = bot_instance.get_file(file_id)
        keyboard = [
            [InlineKeyboardButton("🔗 Get Public Link", callback_data=f"get_link_{file_id}")],
            [InlineKeyboardButton("🎟️ Generate Redeem Code", callback_data=f"gen_redeem_{file_id}")],
            [InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ **File Uploaded Successfully!**\n\n"
            f"📁 Name: {file_data['file_name']}\n"
            f"⭐ Price: Free\n"
            f"🔗 Public Link: {file_data['public_link']}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
        del context.user_data['pending_file']
    
    elif data.startswith("manage_"):
        file_id = data.replace("manage_", "")
        file_data = bot_instance.get_file(file_id)
        
        if not file_data or file_data['owner_id'] != user.id:
            await query.edit_message_text("❌ File not found or access denied.")
            return
        
        keyboard = [
            [InlineKeyboardButton("🔗 Get Public Link", callback_data=f"get_link_{file_id}")],
            [InlineKeyboardButton("🎟️ Generate Redeem Code", callback_data=f"gen_redeem_{file_id}")],
            [InlineKeyboardButton("📊 View Stats", callback_data=f"stats_{file_id}")],
            [InlineKeyboardButton("🔙 Back to Files", callback_data="my_files")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📋 **Managing File**\n\n"
            f"📁 Name: {file_data['file_name']}\n"
            f"⭐ Price: {'Free' if file_data['is_free'] else f\"{file_data['stars_price']} stars\"}\n"
            f"📊 Downloads: {file_data['downloads']}\n"
            f"📅 Uploaded: {file_data['upload_date'][:10]}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data.startswith("get_link_"):
        file_id = data.replace("get_link_", "")
        file_data = bot_instance.get_file(file_id)
        
        if not file_data:
            await query.edit_message_text("❌ File not found.")
            return
        
        await query.edit_message_text(
            f"🔗 **Public Link Generated**\n\n"
            f"📁 File: {file_data['file_name']}\n"
            f"🔗 Link: {file_data['public_link']}\n\n"
            f"Share this link with anyone to let them access your file!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data.startswith("gen_redeem_"):
        file_id = data.replace("gen_redeem_", "")
        file_data = bot_instance.get_file(file_id)
        
        if not file_data or file_data['owner_id'] != user.id:
            await query.edit_message_text("❌ File not found or access denied.")
            return
        
        redeem_code = bot_instance.create_redeem_code(file_id, user.id, 10)  # 10 uses
        
        await query.edit_message_text(
            f"🎟️ **Redeem Code Generated**\n\n"
            f"📁 File: {file_data['file_name']}\n"
            f"🎟️ Code: `{redeem_code}`\n"
            f"🔢 Max Uses: 10\n\n"
            f"Share this code with users for free access!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data.startswith("buy_"):
        file_id = data.replace("buy_", "")
        file_data = bot_instance.get_file(file_id)
        user_data = bot_instance.get_user(user.id)
        
        if not file_data:
            await query.edit_message_text("❌ File not found.")
            return
        
        if user_data['stars_balance'] < file_data['stars_price']:
            await query.edit_message_text(
                f"❌ **Insufficient Stars**\n\n"
                f"You need {file_data['stars_price']} stars but only have {user_data['stars_balance']}.\n"
                f"Please buy more stars to continue."
            )
            return
        
        # Create Telegram Stars payment
        keyboard = [[InlineKeyboardButton(f"⭐ Pay {file_data['stars_price']} Stars", 
                                        callback_data=f"pay_stars_{file_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💰 **Purchase File**\n\n"
            f"📁 File: {file_data['file_name']}\n"
            f"⭐ Price: {file_data['stars_price']} stars\n"
            f"💳 Your Balance: {user_data['stars_balance']} stars\n\n"
            f"Click below to complete purchase:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data.startswith("pay_stars_"):
        file_id = data.replace("pay_stars_", "")
        file_data = bot_instance.get_file(file_id)
        user_data = bot_instance.get_user(user.id)
        
        if not file_data:
            await query.edit_message_text("❌ File not found.")
            return
        
        if user_data['stars_balance'] < file_data['stars_price']:
            await query.edit_message_text("❌ Insufficient stars balance.")
            return
        
        # Process payment
        bot_instance.update_user_stars(user.id, file_data['stars_price'], 'spent')
        bot_instance.update_user_stars(file_data['owner_id'], file_data['stars_price'], 'earned')
        
        # Grant access
        conn = sqlite3.connect(bot_instance.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO user_file_access (user_id, file_id, access_date, access_method)
            VALUES (?, ?, ?, ?)
        ''', (user.id, file_id, datetime.now().isoformat(), 'purchase'))
        
        cursor.execute('UPDATE files SET downloads = downloads + 1 WHERE id = ?', (file_id,))
        conn.commit()
        conn.close()
        
        keyboard = [[InlineKeyboardButton("📥 Download File", callback_data=f"download_{file_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ **Purchase Successful!**\n\n"
            f"📁 File: {file_data['file_name']}\n"
            f"⭐ Paid: {file_data['stars_price']} stars\n\n"
            f"You now have access to this file!",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data.startswith("get_free_"):
        file_id = data.replace("get_free_", "")
        
        # Grant access
        conn = sqlite3.connect(bot_instance.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO user_file_access (user_id, file_id, access_date, access_method)
            VALUES (?, ?, ?, ?)
        ''', (user.id, file_id, datetime.now().isoformat(), 'free'))
        
        cursor.execute('UPDATE files SET downloads = downloads + 1 WHERE id = ?', (file_id,))
        conn.commit()
        conn.close()
        
        keyboard = [[InlineKeyboardButton("📥 Download File", callback_data=f"download_{file_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ **Free File Access Granted!**\n\n"
            f"You now have access to this file!",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data.startswith("download_"):
        file_id = data.replace("download_", "")
        file_data = bot_instance.get_file(file_id)
        
        if not file_data:
            await query.edit_message_text("❌ File not found.")
            return
        
        if not bot_instance.has_file_access(user.id, file_id):
            await query.edit_message_text("❌ Access denied. Please purchase the file first.")
            return
        
        try:
            if file_data['file_type'] == 'document':
                await context.bot.send_document(
                    chat_id=user.id,
                    document=file_data['file_id'],
                    caption=f"📁 {file_data['file_name']}\n\n{file_data['description']}"
                )
            elif file_data['file_type'] == 'photo':
                await context.bot.send_photo(
                    chat_id=user.id,
                    photo=file_data['file_id'],
                    caption=f"🖼️ {file_data['file_name']}\n\n{file_data['description']}"
                )
            elif file_data['file_type'] == 'video':
                await context.bot.send_video(
                    chat_id=user.id,
                    video=file_data['file_id'],
                    caption=f"🎥 {file_data['file_name']}\n\n{file_data['description']}"
                )
            
            await query.edit_message_text(
                f"✅ **File Sent Successfully!**\n\n"
                f"📁 {file_data['file_name']} has been sent to your chat.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await query.edit_message_text("❌ Error sending file. Please try again later.")
    
    elif data == "back_to_main":
        keyboard = [
            [InlineKeyboardButton("📤 Upload File", callback_data="upload_file")],
            [InlineKeyboardButton("📁 My Files", callback_data="my_files")],
            [InlineKeyboardButton("⭐ My Stars", callback_data="my_stars")],
            [InlineKeyboardButton("🎟️ Redeem Code", callback_data="redeem_code")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🤖 **Welcome to File Storage Bot!**\n\n"
            f"📤 Upload files and share them with others\n"
            f"⭐ Earn Telegram Stars from downloads\n"
            f"🎟️ Create redeem codes for free access\n"
            f"🔗 Generate public links for sharing\n\n"
            f"Choose an option below to get started:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user = update.effective_user
    text = update.message.text
    
    # Handle price setting
    if context.user_data.get('awaiting_price'):
        try:
            price = int(text)
            if price < 0:
                await update.message.reply_text("❌ Price cannot be negative. Please enter a valid number.")
                return
            
            pending_file = context.user_data.get('pending_file')
            if not pending_file:
                await update.message.reply_text("❌ No pending file found.")
                return
            
            file_id = bot_instance.save_file(
                user.id,
                pending_file['file_name'],
                pending_file['file_id'],
                pending_file['file_type'],
                "",
                price
            )
            
            file_data = bot_instance.get_file(file_id)
            keyboard = [
                [InlineKeyboardButton("🔗 Get Public Link", callback_data=f"get_link_{file_id}")],
                [InlineKeyboardButton("🎟️ Generate Redeem Code", callback_data=f"gen_redeem_{file_id}")],
                [InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ **File Uploaded Successfully!**\n\n"
                f"📁 Name: {file_data['file_name']}\n"
                f"⭐ Price: {price} stars\n"
                f"🔗 Public Link: {file_data['public_link']}",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            del context.user_data['pending_file']
            del context.user_data['awaiting_price']
            
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number for the price.")
        return
    
    # Handle redeem code
    if text.startswith('REDEEM_'):
        success, message = bot_instance.use_redeem_code(text, user.id)
        
        if success:
            # Find the file and show download option
            conn = sqlite3.connect(bot_instance.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT file_id FROM redeem_codes WHERE code = ?', (text,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                file_id = result[0]
                keyboard = [[InlineKeyboardButton("📥 Download File", callback_data=f"download_{file_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    f"✅ {message}",
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(f"✅ {message}")
        else:
            await update.message.reply_text(f"❌ {message}")
        return
    
    # Default response for unrecognized text
    await update.message.reply_text(
        "🤖 I didn't understand that. Use /start to see available options or send me a file to upload!"
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Exception while handling an update: {context.error}")

def main():
    """Main function to run the bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required!")
        return
    
    try:
        # Create application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(MessageHandler(filters.VIDEO, handle_video))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_handler(CallbackQueryHandler(button_callback))
        
        # Add error handler
        application.add_error_handler(error_handler)
        
        # Start the bot
        logger.info("Starting bot...")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        raise

if __name__ == '__main__':
    main()
