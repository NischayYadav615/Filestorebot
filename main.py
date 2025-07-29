import os
import json
import uuid
import logging
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from flask import Flask, request, jsonify
from pymongo import MongoClient
from pyrogram import Client, types
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    LabeledPrice, Update
)
import requests

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_USERNAME = os.getenv('BOT_USERNAME', 'your_bot')
MONGODB_URL = os.getenv('MONGODB_URL', 'mongodb+srv://Nischay999:Nischay999@cluster0.5kufo.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0)'
DATABASE_NAME = os.getenv('DATABASE_NAME', 'filebot')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')  # Your server URL for webhook

class MongoDBManager:
    def __init__(self):
        self.client = MongoClient(MONGODB_URL)
        self.db = self.client[DATABASE_NAME]
        self.files_collection = self.db.files
        self.users_collection = self.db.users
        self.redeem_codes_collection = self.db.redeem_codes
        
    def ensure_indexes(self):
        """Create indexes for better performance"""
        self.files_collection.create_index("file_id", unique=True)
        self.files_collection.create_index("owner_id")
        self.users_collection.create_index("user_id", unique=True)
        self.redeem_codes_collection.create_index("code", unique=True)
        # MongoDB TTL index for redeem codes (expire after 30 days)
        self.redeem_codes_collection.create_index(
            "created_at", 
            expireAfterSeconds=86400*30
        )
    
    def save_file(self, file_data: dict):
        """Save file data to MongoDB"""
        self.files_collection.replace_one(
            {"file_id": file_data["file_id"]},
            file_data,
            upsert=True
        )
    
    def get_file(self, file_id: str) -> Optional[dict]:
        """Get file data from MongoDB"""
        return self.files_collection.find_one({"file_id": file_id})
    
    def get_user_files(self, user_id: int, limit: int = 50) -> List[dict]:
        """Get user's files"""
        return list(self.files_collection.find(
            {"owner_id": user_id}
        ).sort("upload_date", -1).limit(limit))
    
    def save_user(self, user_data: dict):
        """Save user data to MongoDB"""
        self.users_collection.replace_one(
            {"user_id": user_data["user_id"]},
            user_data,
            upsert=True
        )
    
    def get_user(self, user_id: int) -> Optional[dict]:
        """Get user data from MongoDB"""
        return self.users_collection.find_one({"user_id": user_id})
    
    def update_user_credits(self, user_id: int, credits_delta: int):
        """Update user credits"""
        self.users_collection.update_one(
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
    
    def get_user_credits(self, user_id: int) -> int:
        """Get user credits"""
        user = self.get_user(user_id)
        return user.get("credits", 0) if user else 0
    
    def spend_credits(self, user_id: int, amount: int) -> bool:
        """Spend user credits"""
        result = self.users_collection.update_one(
            {"user_id": user_id, "credits": {"$gte": amount}},
            {"$inc": {"credits": -amount}}
        )
        return result.modified_count > 0
    
    def save_redeem_code(self, code: str, file_id: str):
        """Save redeem code"""
        self.redeem_codes_collection.replace_one(
            {"code": code},
            {
                "code": code,
                "file_id": file_id,
                "created_at": datetime.utcnow()
            },
            upsert=True
        )
    
    def get_redeem_code(self, code: str) -> Optional[dict]:
        """Get redeem code data"""
        return self.redeem_codes_collection.find_one({"code": code})
    
    def delete_redeem_code(self, code: str):
        """Delete used redeem code"""
        self.redeem_codes_collection.delete_one({"code": code})
    
    def increment_file_access(self, file_id: str):
        """Increment file access count"""
        self.files_collection.update_one(
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
        base_url = f"https://t.me/{BOT_USERNAME}?start=file_{file_id}_{stars_required}"
        return base_url
    
    def initialize(self):
        """Initialize database indexes"""
        self.db.ensure_indexes()

# Initialize Flask app and bot
app = Flask(__name__)
file_bot = FileBot()

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, chat_id: int, text: str, reply_markup=None, parse_mode="Markdown"):
        """Send a message via Telegram Bot API"""
        url = f"{self.api_url}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        
        response = requests.post(url, json=data)
        return response.json()
    
    def send_document(self, chat_id: int, document: str, caption: str = None):
        """Send a document via Telegram Bot API"""
        url = f"{self.api_url}/sendDocument"
        data = {
            "chat_id": chat_id,
            "document": document,
            "caption": caption,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=data)
        return response.json()
    
    def send_photo(self, chat_id: int, photo: str, caption: str = None):
        """Send a photo via Telegram Bot API"""
        url = f"{self.api_url}/sendPhoto"
        data = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=data)
        return response.json()
    
    def send_video(self, chat_id: int, video: str, caption: str = None):
        """Send a video via Telegram Bot API"""
        url = f"{self.api_url}/sendVideo"
        data = {
            "chat_id": chat_id,
            "video": video,
            "caption": caption,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=data)
        return response.json()
    
    def answer_callback_query(self, callback_query_id: str, text: str = None):
        """Answer a callback query"""
        url = f"{self.api_url}/answerCallbackQuery"
        data = {
            "callback_query_id": callback_query_id,
            "text": text
        }
        response = requests.post(url, json=data)
        return response.json()
    
    def send_invoice(self, chat_id: int, title: str, description: str, payload: str, 
                    currency: str, prices: list, start_parameter: str = None):
        """Send an invoice"""
        url = f"{self.api_url}/sendInvoice"
        data = {
            "chat_id": chat_id,
            "title": title,
            "description": description,
            "payload": payload,
            "provider_token": "",  # Empty for Telegram Stars
            "currency": currency,
            "prices": prices
        }
        if start_parameter:
            data["start_parameter"] = start_parameter
        
        response = requests.post(url, json=data)
        return response.json()

bot = TelegramBot(BOT_TOKEN)

def create_inline_keyboard(buttons):
    """Create inline keyboard markup"""
    return {
        "inline_keyboard": buttons
    }

def handle_start_command(message):
    """Handle /start command"""
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    
    # Initialize user if not exists
    user = file_bot.db.get_user(user_id)
    if not user:
        user_data = {
            "user_id": user_id,
            "username": message['from'].get('username'),
            "first_name": message['from'].get('first_name'),
            "joined_date": datetime.utcnow(),
            "credits": 0
        }
        file_bot.db.save_user(user_data)
    
    # Handle file access from link
    text = message.get('text', '').split()
    if len(text) > 1 and text[1].startswith('file_'):
        parts = text[1].split('_')
        if len(parts) >= 3:
            file_id = parts[1]
            stars_required = int(parts[2])
            handle_file_access(chat_id, user_id, file_id, stars_required)
            return
    
    user_credits = file_bot.db.get_user_credits(user_id)
    
    welcome_text = f"""
🤖 **File Storage Bot**

Welcome! This bot allows you to:
📁 Store files and generate public links
⭐ Set Telegram Stars pricing for file access
🎫 Generate redeem codes for free access
💳 Manage your credits and files

**Commands:**
/myfiles - View your uploaded files
/credits - Check your credit balance
/redeem - Use a redeem code
/help - Show this help message

**Your Current Credits:** ⭐ {user_credits}
    """
    
    keyboard = create_inline_keyboard([
        [{"text": "📁 Upload File", "callback_data": "upload_file"}],
        [{"text": "📋 My Files", "callback_data": "my_files"}],
        [{"text": "⭐ My Credits", "callback_data": "check_credits"}],
        [{"text": "🎫 Redeem Code", "callback_data": "redeem_code"}]
    ])
    
    bot.send_message(chat_id, welcome_text, reply_markup=keyboard)

def handle_file_access(chat_id: int, user_id: int, file_id: str, stars_required: int):
    """Handle file access from public link"""
    file_data = file_bot.db.get_file(file_id)
    
    if not file_data:
        bot.send_message(chat_id, "❌ File not found or has been removed.")
        return
    
    file_name = file_data.get('name', 'Unknown File')
    file_size = file_data.get('size', 0)
    owner_id = file_data.get('owner_id')
    
    if user_id == owner_id:
        # Owner can access for free
        send_file_to_user(chat_id, file_data)
        return
    
    if stars_required == 0:
        # Free file
        send_file_to_user(chat_id, file_data)
        return
    
    # Check if user has enough credits
    user_credits = file_bot.db.get_user_credits(user_id)
    
    access_text = f"""
📁 **{file_name}**
📊 Size: {file_size} bytes
💰 Cost: ⭐ {stars_required} stars
💳 Your Credits: ⭐ {user_credits}

Choose how to access this file:
"""
    
    keyboard_buttons = []
    
    if user_credits >= stars_required:
        keyboard_buttons.append([{"text": f"💳 Use Credits (⭐ {stars_required})", "callback_data": f"use_credits_{file_id}_{stars_required}"}])
    
    keyboard_buttons.extend([
        [{"text": f"⭐ Buy with Telegram Stars (⭐ {stars_required})", "callback_data": f"buy_stars_{file_id}_{stars_required}"}],
        [{"text": "🎫 I have a redeem code", "callback_data": f"redeem_for_file_{file_id}"}],
        [{"text": "❌ Cancel", "callback_data": "cancel"}]
    ])
    
    keyboard = create_inline_keyboard(keyboard_buttons)
    bot.send_message(chat_id, access_text, reply_markup=keyboard)

def send_file_to_user(chat_id: int, file_data: dict):
    """Send file to user"""
    try:
        telegram_file_id = file_data.get('telegram_file_id')
        caption = f"📁 **{file_data.get('name')}**\n📊 Size: {file_data.get('size')} bytes"
        
        # Increment access count
        file_bot.db.increment_file_access(file_data['file_id'])
        
        file_type = file_data.get('type')
        
        if file_type == 'document':
            bot.send_document(chat_id, telegram_file_id, caption)
        elif file_type == 'photo':
            bot.send_photo(chat_id, telegram_file_id, caption)
        elif file_type == 'video':
            bot.send_video(chat_id, telegram_file_id, caption)
        else:
            bot.send_document(chat_id, telegram_file_id, caption)
        
        bot.send_message(chat_id, "✅ File sent successfully!")
            
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        bot.send_message(chat_id, "❌ Error sending file. Please try again.")

def handle_document_upload(message):
    """Handle uploaded documents"""
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    
    if 'document' in message:
        file_info = message['document']
        file_type = 'document'
        file_name = file_info.get('file_name', f'document_{uuid.uuid4().hex[:8]}')
        file_size = file_info.get('file_size', 0)
        telegram_file_id = file_info['file_id']
    elif 'photo' in message:
        file_info = message['photo'][-1]  # Get highest resolution
        file_type = 'photo'
        file_name = f'photo_{uuid.uuid4().hex[:8]}.jpg'
        file_size = file_info.get('file_size', 0)
        telegram_file_id = file_info['file_id']
    elif 'video' in message:
        file_info = message['video']
        file_type = 'video'
        file_name = file_info.get('file_name', f'video_{uuid.uuid4().hex[:8]}.mp4')
        file_size = file_info.get('file_size', 0)
        telegram_file_id = file_info['file_id']
    else:
        bot.send_message(chat_id, "❌ Unsupported file type.")
        return
    
    file_id = file_bot.generate_file_id()
    
    file_data = {
        'file_id': file_id,
        'telegram_file_id': telegram_file_id,
        'name': file_name,
        'size': file_size,
        'type': file_type,
        'owner_id': user_id,
        'upload_date': datetime.utcnow(),
        'access_count': 0,
        'price': 0  # Default free
    }
    
    file_bot.db.save_file(file_data)
    
    text = f"""
✅ **File Uploaded Successfully!**

📁 **File:** {file_data['name']}
📊 **Size:** {file_data['size']} bytes
🆔 **File ID:** `{file_id}`

Now set the pricing for your file:
"""
    
    keyboard = create_inline_keyboard([
        [{"text": "🆓 Make it Free", "callback_data": f"set_price_{file_id}_0"}],
        [{"text": "⭐ 1 Star", "callback_data": f"set_price_{file_id}_1"}],
        [{"text": "⭐ 5 Stars", "callback_data": f"set_price_{file_id}_5"}],
        [{"text": "⭐ 10 Stars", "callback_data": f"set_price_{file_id}_10"}],
        [{"text": "💰 Custom Price", "callback_data": f"custom_price_{file_id}"}]
    ])
    
    bot.send_message(chat_id, text, reply_markup=keyboard)

def handle_callback_query(callback_query):
    """Handle button callbacks"""
    query_id = callback_query['id']
    data = callback_query['data']
    user_id = callback_query['from']['id']
    chat_id = callback_query['message']['chat']['id']
    
    bot.answer_callback_query(query_id)
    
    if data == "upload_file":
        bot.send_message(
            chat_id,
            "📁 **Upload a File**\n\n"
            "Send me any file (document, photo, video) and I'll store it for you!"
        )
        
    elif data == "my_files":
        show_user_files(chat_id, user_id)
        
    elif data == "check_credits":
        credits = file_bot.db.get_user_credits(user_id)
        bot.send_message(chat_id, f"💳 **Your Credits:** ⭐ {credits}")
        
    elif data == "redeem_code":
        bot.send_message(
            chat_id,
            "🎫 **Redeem Code**\n\n"
            "Send me your redeem code to access a file for free!\n"
            "Use format: `/redeem YOUR_CODE`"
        )
        
    elif data.startswith("set_price_"):
        parts = data.split("_")
        file_id = parts[2]
        price = int(parts[3])
        set_file_price(chat_id, file_id, price)
        
    elif data.startswith("use_credits_"):
        parts = data.split("_")
        file_id = parts[2]
        stars_required = int(parts[3])
        use_credits_for_file(chat_id, user_id, file_id, stars_required)
        
    elif data.startswith("buy_stars_"):
        parts = data.split("_")
        file_id = parts[2]
        stars_required = int(parts[3])
        initiate_star_payment(chat_id, file_id, stars_required)
        
    elif data.startswith("generate_redeem_"):
        file_id = data.split("_")[2]
        generate_redeem_code(chat_id, file_id)
        
    elif data.startswith("file_details_"):
        file_id = data.split("_")[2]
        show_file_details(chat_id, file_id)

def show_user_files(chat_id: int, user_id: int):
    """Show user's uploaded files"""
    user_files = file_bot.db.get_user_files(user_id, limit=10)
    
    if not user_files:
        bot.send_message(chat_id, "📁 You haven't uploaded any files yet.")
        return
    
    text = "📋 **Your Files:**\n\n"
    keyboard_buttons = []
    
    for file_data in user_files:
        text += f"📁 {file_data['name']}\n"
        keyboard_buttons.append([{"text": f"📁 {file_data['name'][:30]}...", "callback_data": f"file_details_{file_data['file_id']}"}])
    
    keyboard = create_inline_keyboard(keyboard_buttons)
    bot.send_message(chat_id, text, reply_markup=keyboard)

def show_file_details(chat_id: int, file_id: str):
    """Show detailed file information"""
    file_data = file_bot.db.get_file(file_id)
    if not file_data:
        bot.send_message(chat_id, "❌ File not found.")
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
    
    keyboard = create_inline_keyboard([
        [{"text": "🎫 Generate Redeem Code", "callback_data": f"generate_redeem_{file_id}"}],
        [{"text": "💰 Change Price", "callback_data": f"change_price_{file_id}"}],
        [{"text": "📊 View Stats", "callback_data": f"file_stats_{file_id}"}]
    ])
    
    bot.send_message(chat_id, text, reply_markup=keyboard)

def set_file_price(chat_id: int, file_id: str, price: int):
    """Set file price"""
    file_data = file_bot.db.get_file(file_id)
    if not file_data:
        bot.send_message(chat_id, "❌ File not found.")
        return
    
    # Update price in database
    file_bot.db.files_collection.update_one(
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
    
    keyboard = create_inline_keyboard([
        [{"text": "🎫 Generate Redeem Code", "callback_data": f"generate_redeem_{file_id}"}],
        [{"text": "📋 Back to My Files", "callback_data": "my_files"}]
    ])
    
    bot.send_message(chat_id, text, reply_markup=keyboard)

def generate_redeem_code(chat_id: int, file_id: str):
    """Generate redeem code for file"""
    file_data = file_bot.db.get_file(file_id)
    if not file_data:
        bot.send_message(chat_id, "❌ File not found.")
        return
    
    redeem_code = file_bot.generate_redeem_code()
    file_bot.db.save_redeem_code(redeem_code, file_id)
    
    text = f"""
🎫 **Redeem Code Generated!**

📁 **File:** {file_data['name']}
🎫 **Code:** `{redeem_code}`

Give this code to users for free access to your file.
Users can use it with: `/redeem {redeem_code}`
"""
    
    bot.send_message(chat_id, text)

def use_credits_for_file(chat_id: int, user_id: int, file_id: str, stars_required: int):
    """Use user credits to access file"""
    if file_bot.db.spend_credits(user_id, stars_required):
        file_data = file_bot.db.get_file(file_id)
        if file_data:
            send_file_to_user(chat_id, file_data)
            
            remaining_credits = file_bot.db.get_user_credits(user_id)
            bot.send_message(
                chat_id,
                f"✅ Access granted! ⭐ {stars_required} credits used.\n"
                f"💳 Remaining credits: ⭐ {remaining_credits}"
            )
        else:
            bot.send_message(chat_id, "❌ File not found.")
    else:
        bot.send_message(chat_id, "❌ Insufficient credits.")

def initiate_star_payment(chat_id: int, file_id: str, stars_required: int):
    """Initiate Telegram Stars payment"""
    file_data = file_bot.db.get_file(file_id)
    if not file_data:
        bot.send_message(chat_id, "❌ File not found.")
        return
    
    title = f"Access to {file_data['name']}"
    description = f"Pay {stars_required} Telegram Stars to access this file"
    payload = f"file_access_{file_id}_{stars_required}"
    
    prices = [{"label": "File Access", "amount": stars_required}]
    
    try:
        bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=description,
            payload=payload,
            currency="XTR",  # Telegram Stars currency
            prices=prices,
            start_parameter=f"file_{file_id}",
        )
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        bot.send_message(chat_id, "❌ Error creating payment. Please try again.")

def handle_redeem_command(message):
    """Handle redeem code command"""
    chat_id = message['chat']['id']
    text = message.get('text', '').split()
    
    if len(text) < 2:
        bot.send_message(
            chat_id,
            "🎫 **Redeem Code**\n\n"
            "Usage: `/redeem YOUR_CODE`\n"
            "Example: `/redeem ABC123XY`"
        )
        return
    
    redeem_code = text[1].upper()
    redeem_data = file_bot.db.get_redeem_code(redeem_code)
    
    if not redeem_data:
        bot.send_message(chat_id, "❌ Invalid or expired redeem code.")
        return
    
    file_id = redeem_data['file_id']
    file_data = file_bot.db.get_file(file_id)
    
    if not file_data:
        bot.send_message(chat_id, "❌ File not found or has been removed.")
        return
    
    # Grant access
    send_file_to_user(chat_id, file_data)
    
    # Remove used redeem code
    file_bot.db.delete_redeem_code(redeem_code)
    
    bot.send_message(chat_id, f"✅ Redeem code accepted! Access granted to: {file_data['name']}")

def handle_credits_command(message):
    """Show user credits"""
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    credits = file_bot.db.get_user_credits(user_id)
    
    bot.send_message(
        chat_id,
        f"💳 **Your Credits:** ⭐ {credits}\n\n"
        "Credits can be used to access paid files without additional payment."
    )

def handle_myfiles_command(message):
    """Show user's files"""
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    show_user_files(chat_id, user_id)

def handle_help_command(message):
    """Show help message"""
    chat_id = message['chat']['id']
    help_text = """
🤖 **File Storage Bot Help**

**Commands:**
/start - Start the bot and see main menu
/myfiles - View your uploaded files
/credits - Check your credit balance
/redeem CODE - Use a redeem code
/help - Show this help message

**How to use:**
1. Upload files by sending them directly to the bot
2. Set pricing for your files (free or paid with Telegram Stars)
3. Share the generated public links
4. Generate redeem codes for free access

**Features:**
📁 File storage and sharing with MongoDB
⭐ Telegram Stars integration
💳 Credit system
🎫 Redeem codes with expiry
📊 Access statistics

**Supported file types:**
📄 Documents (PDF, DOC, etc.)
📷 Photos (JPG, PNG, etc.)
🎥 Videos (MP4, AVI, etc.)

For support: @NY_BOTS
"""
    
    bot.send_message(chat_id, help_text)

def handle_successful_payment(message):
    """Handle successful payment"""
    payment = message['successful_payment']
    payload_parts = payment['invoice_payload'].split("_")
    
    if len(payload_parts) >= 3 and payload_parts[0] == "file" and payload_parts[1] == "access":
        file_id = payload_parts[2]
        stars_paid = int(payload_parts[3])
        
        # Give user credits equal to stars paid
        user_id = message['from']['id']
        chat_id = message['chat']['id']
        file_bot.db.update_user_credits(user_id, stars_paid)
        
        # Grant access to file
        file_data = file_bot.db.get_file(file_id)
        if file_data:
            send_file_to_user(chat_id, file_data)
            
            bot.send_message(
                chat_id,
                f"✅ Payment successful! File access granted.\n"
                f"💳 {stars_paid} credits added to your account."
            )

# Flask routes
@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook updates from Telegram"""
    try:
        update = request.get_json()
        
        if 'message' in update:
            message = update['message']
            
            # Handle commands
            if 'text' in message and message['text'].startswith('/'):
                command = message['text'].split()[0]
                
                if command == '/start':
                    handle_start_command(message)
                elif command == '/help':
                    handle_help_command(message)
                elif command == '/credits':
                    handle_credits_command(message)
                elif command == '/redeem':
                    handle_redeem_command(message)
                elif command == '/myfiles':
                    handle_myfiles_command(message)
            
            # Handle file uploads
            elif any(key in message for key in ['document', 'photo', 'video']):
                handle_document_upload(message)
            
            # Handle successful payment
            elif 'successful_payment' in message:
                handle_successful_payment(message)
        
        # Handle callback queries
        elif 'callback_query' in update:
            handle_callback_query(update['callback_query'])
        
        # Handle pre-checkout query
        elif 'pre_checkout_query' in update:
            pre_checkout_query = update['pre_checkout_query']
            # Auto-approve all pre-checkout queries
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery"
            data = {
                "pre_checkout_query_id": pre_checkout_query['id'],
                "ok": True
            }
            requests.post(url, json=data)
        
        return jsonify({"status": "ok"})
    
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})

@app.route('/stats', methods=['GET'])
def get_stats():
    """Get bot statistics"""
    try:
        total_files = file_bot.db.files_collection.count_documents({})
        total_users = file_bot.db.users_collection.count_documents({})
        total_redeem_codes = file_bot.db.redeem_codes_collection.count_documents({})
        
        # Get total access count
        pipeline = [
            {"$group": {"_id": None, "total_accesses": {"$sum": "$access_count"}}}
        ]
        access_result = list(file_bot.db.files_collection.aggregate(pipeline))
        total_accesses = access_result[0]['total_accesses'] if access_result else 0
        
        return jsonify({
            "total_files": total_files,
            "total_users": total_users,
            "total_redeem_codes": total_redeem_codes,
            "total_accesses": total_accesses,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/files/<file_id>', methods=['GET'])
def get_file_info(file_id):
    """Get file information"""
    try:
        file_data = file_bot.db.get_file(file_id)
        if not file_data:
            return jsonify({"error": "File not found"}), 404
        
        # Remove sensitive data
        safe_file_data = {
            "file_id": file_data['file_id'],
            "name": file_data['name'],
            "size": file_data['size'],
            "type": file_data['type'],
            "price": file_data.get('price', 0),
            "access_count": file_data.get('access_count', 0),
            "upload_date": file_data['upload_date'].isoformat()
        }
        
        return jsonify(safe_file_data)
    except Exception as e:
        logger.error(f"Error getting file info: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/set_webhook', methods=['POST'])
def set_webhook():
    """Set webhook URL for the bot"""
    try:
        webhook_url = request.json.get('webhook_url')
        if not webhook_url:
            return jsonify({"error": "webhook_url is required"}), 400
        
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        data = {
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query", "pre_checkout_query"]
        }
        
        response = requests.post(url, json=data)
        result = response.json()
        
        if result.get('ok'):
            return jsonify({"status": "success", "message": "Webhook set successfully"})
        else:
            return jsonify({"status": "error", "message": result.get('description', 'Unknown error')}), 400
    
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/delete_webhook', methods=['POST'])
def delete_webhook():
    """Delete webhook for the bot"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
        response = requests.post(url)
        result = response.json()
        
        if result.get('ok'):
            return jsonify({"status": "success", "message": "Webhook deleted successfully"})
        else:
            return jsonify({"status": "error", "message": result.get('description', 'Unknown error')}), 400
    
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/webhook_info', methods=['GET'])
def webhook_info():
    """Get webhook information"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
        response = requests.get(url)
        result = response.json()
        
        if result.get('ok'):
            return jsonify(result['result'])
        else:
            return jsonify({"error": result.get('description', 'Unknown error')}), 400
    
    except Exception as e:
        logger.error(f"Error getting webhook info: {e}")
        return jsonify({"error": str(e)}), 500

def initialize_bot():
    """Initialize the bot and database"""
    try:
        # Initialize database
        file_bot.initialize()
        logger.info("Database initialized successfully")
        
        # Set webhook if WEBHOOK_URL is provided
        if WEBHOOK_URL:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
            data = {
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query", "pre_checkout_query"]
            }
            
            response = requests.post(url, json=data)
            result = response.json()
            
            if result.get('ok'):
                logger.info(f"Webhook set successfully to {webhook_url}")
            else:
                logger.error(f"Failed to set webhook: {result.get('description')}")
        
    except Exception as e:
        logger.error(f"Error initializing bot: {e}")
        raise

if __name__ == '__main__':
    try:
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN environment variable not set")
            exit(1)
        
        # Initialize bot
        initialize_bot()
        
        logger.info("Starting Flask server on 0.0.0.0:8080...")
        
        # Run Flask app
        app.run(
            host='0.0.0.0',
            port=8080,
            debug=False,
            threaded=True
        )
        
    except Exception as e:
        logger.error(f"Error running server: {e}")
        raise
