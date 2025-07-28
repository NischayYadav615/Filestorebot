import os
import logging
import hashlib
import random
import string
import json
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, PreCheckoutQueryHandler, filters, ContextTypes
from typing import Dict, List, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

# In-memory storage (for demo - use database in production)
class DataStore:
    def __init__(self):
        self.files = {}  # file_id: {owner_id, filename, file_data, price, access_count, created_at}
        self.redeem_codes = {}  # code: file_id
        self.user_files = {}  # user_id: [file_ids]
        self.user_stars = {}  # user_id: star_balance
        self.access_history = {}  # user_id: [file_ids_accessed]

    def add_file(self, file_id: str, owner_id: int, filename: str, file_data: dict, price: int = 0):
        self.files[file_id] = {
            'owner_id': owner_id,
            'filename': filename,
            'file_data': file_data,
            'price': price,
            'access_count': 0,
            'created_at': datetime.now().isoformat(),
            'redeem_codes': []
        }
        
        if owner_id not in self.user_files:
            self.user_files[owner_id] = []
        self.user_files[owner_id].append(file_id)
        
        return file_id

    def generate_redeem_code(self, file_id: str) -> str:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        self.redeem_codes[code] = file_id
        if file_id in self.files:
            self.files[file_id]['redeem_codes'].append(code)
        return code

    def get_file_by_code(self, code: str) -> Optional[str]:
        return self.redeem_codes.get(code)

    def add_stars(self, user_id: int, amount: int):
        if user_id not in self.user_stars:
            self.user_stars[user_id] = 0
        self.user_stars[user_id] += amount

    def deduct_stars(self, user_id: int, amount: int) -> bool:
        if user_id not in self.user_stars:
            self.user_stars[user_id] = 0
        
        if self.user_stars[user_id] >= amount:
            self.user_stars[user_id] -= amount
            return True
        return False

    def get_user_stars(self, user_id: int) -> int:
        return self.user_stars.get(user_id, 0)

# Initialize data store
data_store = DataStore()

def generate_file_id():
    """Generate unique file ID"""
    return hashlib.md5(f"{datetime.now().isoformat()}{random.randint(1000, 9999)}".encode()).hexdigest()[:12]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    welcome_text = f"""
🎉 Welcome to File Share Bot, {user.first_name}!

📁 **Features:**
• Upload files and get shareable links
• Set Stars price for file access
• Generate redeem codes for free access
• Manage your uploaded files
• Buy Stars to access premium files

💫 **Your Stars Balance:** {data_store.get_user_stars(user.id)} ⭐

**Commands:**
/upload - Upload a new file
/myfiles - View your uploaded files
/buystars - Purchase Stars
/redeem - Use redeem code
/help - Show help

🚀 Start by uploading a file or browsing available content!
    """
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data="upload")],
        [InlineKeyboardButton("📂 My Files", callback_data="myfiles"),
         InlineKeyboardButton("💫 Buy Stars", callback_data="buystars")],
        [InlineKeyboardButton("🎫 Redeem Code", callback_data="redeem"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    help_text = """
📖 **How to use File Share Bot:**

**📤 Uploading Files:**
1. Click "Upload File" or use /upload
2. Send any file (document, photo, video, etc.)
3. Set a price in Stars (0 for free)
4. Get a shareable link

**💰 Setting Prices:**
• 0 Stars = Free access
• 1-100 Stars = Premium access
• Users pay Stars to download

**🎫 Redeem Codes:**
• Generate codes for free access to paid files
• Share codes with specific users
• Each code can be used once

**💫 Stars System:**
• Users buy Stars to access premium files
• File owners earn Stars from downloads
• Stars can be purchased via Telegram payments

**🔗 Sharing Files:**
• Each file gets a unique link
• Click link → Opens bot → Pay/Redeem → Download

**Commands:**
/start - Main menu
/upload - Upload new file
/myfiles - Manage your files
/buystars - Purchase Stars
/redeem - Enter redeem code
/help - This help message

Need more help? Contact @NY_BOTS
    """
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upload command handler"""
    text = """
📤 **Upload a File**

Send me any file (document, photo, video, audio, etc.) and I'll create a shareable link for it!

After uploading, you can:
• Set a price in Stars
• Generate redeem codes
• Track downloads
• Manage access

🚀 **Send your file now!**
    """
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads"""
    user = update.effective_user
    message = update.message
    
    # Get file info based on message type
    file_obj = None
    filename = None
    file_type = None
    
    if message.document:
        file_obj = message.document
        filename = file_obj.file_name or "document"
        file_type = "document"
    elif message.photo:
        file_obj = message.photo[-1]  # Get highest resolution
        filename = f"photo_{file_obj.file_id[:8]}.jpg"
        file_type = "photo"
    elif message.video:
        file_obj = message.video
        filename = file_obj.file_name or f"video_{file_obj.file_id[:8]}.mp4"
        file_type = "video"
    elif message.audio:
        file_obj = message.audio
        filename = file_obj.file_name or f"audio_{file_obj.file_id[:8]}.mp3"
        file_type = "audio"
    elif message.voice:
        file_obj = message.voice
        filename = f"voice_{file_obj.file_id[:8]}.ogg"
        file_type = "voice"
    elif message.video_note:
        file_obj = message.video_note
        filename = f"video_note_{file_obj.file_id[:8]}.mp4"
        file_type = "video_note"
    elif message.sticker:
        file_obj = message.sticker
        filename = f"sticker_{file_obj.file_id[:8]}.webp"
        file_type = "sticker"
    else:
        await message.reply_text("❌ Please send a valid file (document, photo, video, audio, etc.)")
        return
    
    # Store file data
    file_data = {
        'file_id': file_obj.file_id,
        'file_type': file_type,
        'file_size': getattr(file_obj, 'file_size', 0),
        'mime_type': getattr(file_obj, 'mime_type', 'unknown')
    }
    
    # Generate unique file ID
    unique_file_id = generate_file_id()
    
    # Add file to storage
    data_store.add_file(unique_file_id, user.id, filename, file_data)
    
    # Ask for price
    text = f"""
✅ **File Uploaded Successfully!**

📁 **File:** `{filename}`
🆔 **File ID:** `{unique_file_id}`
📊 **Size:** {file_data['file_size']} bytes

💰 **Set Price (in Stars):**
Choose how many Stars users need to pay to access this file:
    """
    
    keyboard = [
        [InlineKeyboardButton("🆓 Free (0 Stars)", callback_data=f"setprice_{unique_file_id}_0")],
        [InlineKeyboardButton("⭐ 1 Star", callback_data=f"setprice_{unique_file_id}_1"),
         InlineKeyboardButton("⭐ 5 Stars", callback_data=f"setprice_{unique_file_id}_5")],
        [InlineKeyboardButton("⭐ 10 Stars", callback_data=f"setprice_{unique_file_id}_10"),
         InlineKeyboardButton("⭐ 25 Stars", callback_data=f"setprice_{unique_file_id}_25")],
        [InlineKeyboardButton("⭐ 50 Stars", callback_data=f"setprice_{unique_file_id}_50"),
         InlineKeyboardButton("⭐ 100 Stars", callback_data=f"setprice_{unique_file_id}_100")],
        [InlineKeyboardButton("✏️ Custom Price", callback_data=f"customprice_{unique_file_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def set_file_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set file price handler"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    file_id = data[1]
    price = int(data[2])
    
    # Update file price
    if file_id in data_store.files:
        data_store.files[file_id]['price'] = price
        
        # Generate shareable link
        bot_username = context.bot.username
        share_link = f"https://t.me/{bot_username}?start=file_{file_id}"
        
        text = f"""
🎉 **File Setup Complete!**

📁 **File:** `{data_store.files[file_id]['filename']}`
💰 **Price:** {price} Stars {'(Free)' if price == 0 else ''}
🔗 **Share Link:** `{share_link}`

**What you can do now:**
        """
        
        keyboard = [
            [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data=f"gencode_{file_id}")],
            [InlineKeyboardButton("📊 View Stats", callback_data=f"stats_{file_id}"),
             InlineKeyboardButton("✏️ Edit Price", callback_data=f"editprice_{file_id}")],
            [InlineKeyboardButton("📋 Copy Link", url=share_link)],
            [InlineKeyboardButton("📂 My Files", callback_data="myfiles"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await query.edit_message_text("❌ File not found!")

async def generate_redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate redeem code for file"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split('_')[1]
    
    if file_id in data_store.files:
        file_info = data_store.files[file_id]
        
        # Check if user owns the file
        if file_info['owner_id'] != query.from_user.id:
            await query.edit_message_text("❌ You can only generate redeem codes for your own files!")
            return
        
        # Generate redeem code
        redeem_code = data_store.generate_redeem_code(file_id)
        
        text = f"""
🎫 **Redeem Code Generated!**

📁 **File:** `{file_info['filename']}`
🎫 **Redeem Code:** `{redeem_code}`

**Instructions for users:**
1. Click: /redeem
2. Enter code: `{redeem_code}`
3. Get free access to your file!

⚠️ **Note:** Each code can only be used once.
        """
        
        keyboard = [
            [InlineKeyboardButton("🎫 Generate Another Code", callback_data=f"gencode_{file_id}")],
            [InlineKeyboardButton("📊 View All Codes", callback_data=f"viewcodes_{file_id}")],
            [InlineKeyboardButton("🔙 Back to File", callback_data=f"fileinfo_{file_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await query.edit_message_text("❌ File not found!")

async def my_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's files"""
    user_id = update.effective_user.id
    
    if user_id not in data_store.user_files or not data_store.user_files[user_id]:
        text = """
📂 **My Files**

You haven't uploaded any files yet!

🚀 Start by uploading your first file.
        """
        
        keyboard = [
            [InlineKeyboardButton("📤 Upload File", callback_data="upload")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
    else:
        files = data_store.user_files[user_id]
        text = f"📂 **My Files** ({len(files)} files)\n\n"
        
        keyboard = []
        for file_id in files[-10:]:  # Show last 10 files
            if file_id in data_store.files:
                file_info = data_store.files[file_id]
                filename = file_info['filename'][:25] + ('...' if len(file_info['filename']) > 25 else '')
                price_text = f"({file_info['price']}⭐)" if file_info['price'] > 0 else "(Free)"
                
                button_text = f"📁 {filename} {price_text}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"fileinfo_{file_id}")])
        
        keyboard.append([InlineKeyboardButton("📤 Upload New File", callback_data="upload")])
        keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="start")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def file_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed file information"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split('_')[1]
    
    if file_id in data_store.files:
        file_info = data_store.files[file_id]
        
        # Check if user owns the file
        if file_info['owner_id'] != query.from_user.id:
            await query.edit_message_text("❌ You can only view details of your own files!")
            return
        
        bot_username = context.bot.username
        share_link = f"https://t.me/{bot_username}?start=file_{file_id}"
        
        created_date = datetime.fromisoformat(file_info['created_at']).strftime("%Y-%m-%d %H:%M")
        
        text = f"""
📁 **File Details**

**📋 Info:**
• Name: `{file_info['filename']}`
• Price: {file_info['price']} Stars {'(Free)' if file_info['price'] == 0 else ''}
• Downloads: {file_info['access_count']}
• Created: {created_date}

**🎫 Redeem Codes:** {len(file_info.get('redeem_codes', []))}

**🔗 Share Link:**
`{share_link}`
        """
        
        keyboard = [
            [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data=f"gencode_{file_id}"),
             InlineKeyboardButton("✏️ Edit Price", callback_data=f"editprice_{file_id}")],
            [InlineKeyboardButton("📊 View Codes", callback_data=f"viewcodes_{file_id}"),
             InlineKeyboardButton("🗑️ Delete File", callback_data=f"delete_{file_id}")],
            [InlineKeyboardButton("📋 Copy Link", url=share_link)],
            [InlineKeyboardButton("🔙 My Files", callback_data="myfiles")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await query.edit_message_text("❌ File not found!")

async def buy_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buy Stars handler"""
    text = """
💫 **Buy Stars**

Stars are used to access premium files. Choose a package:

⭐ **Star Packages:**
• 10 Stars = $0.99
• 50 Stars = $4.99  
• 100 Stars = $9.99
• 500 Stars = $39.99

💡 **How it works:**
1. Purchase Stars with Telegram Stars payment
2. Use Stars to access premium files
3. File owners earn Stars from downloads

💰 **Your Balance:** {data_store.get_user_stars(update.effective_user.id)} ⭐
    """
    
    keyboard = [
        [InlineKeyboardButton("⭐ 10 Stars - $0.99", callback_data="buypack_10_99")],
        [InlineKeyboardButton("⭐ 50 Stars - $4.99", callback_data="buypack_50_499")],
        [InlineKeyboardButton("⭐ 100 Stars - $9.99", callback_data="buypack_100_999")],
        [InlineKeyboardButton("⭐ 500 Stars - $39.99", callback_data="buypack_500_3999")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_star_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Stars purchase"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    stars = int(data[1])
    price_cents = int(data[2])
    
    # Create payment invoice
    title = f"{stars} Telegram Stars"
    description = f"Purchase {stars} Stars for accessing premium files"
    payload = f"stars_{stars}_{query.from_user.id}"
    currency = "XTR"  # Telegram Stars currency
    prices = [LabeledPrice(label=f"{stars} Stars", amount=stars)]
    
    try:
        await context.bot.send_invoice(
            chat_id=query.from_user.id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # Empty for Telegram Stars
            currency=currency,
            prices=prices,
            start_parameter="stars_purchase"
        )
        
        await query.edit_message_text(
            f"💫 **Payment Invoice Sent!**\n\nCheck your DM for the payment invoice to purchase {stars} Stars.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="buystars")]])
        )
    except Exception as e:
        logger.error(f"Error sending invoice: {e}")
        await query.edit_message_text(
            "❌ **Payment Error**\n\nUnable to process payment at the moment. Please try again later.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="buystars")]])
        )

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pre-checkout queries"""
    query = update.pre_checkout_query
    
    # Always approve the payment
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle successful payments"""
    payment = update.message.successful_payment
    payload_data = payment.invoice_payload.split('_')
    
    if len(payload_data) >= 3 and payload_data[0] == "stars":
        stars_amount = int(payload_data[1])
        user_id = int(payload_data[2])
        
        # Add stars to user account
        data_store.add_stars(user_id, stars_amount)
        
        await update.message.reply_text(
            f"🎉 **Payment Successful!**\n\n"
            f"You received {stars_amount} ⭐ Stars!\n"
            f"Total Balance: {data_store.get_user_stars(user_id)} ⭐\n\n"
            f"You can now access premium files!"
        )

async def redeem_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem code command"""
    text = """
🎫 **Redeem Code**

Enter your redeem code to get free access to premium files!

💡 **How to use:**
1. Get a redeem code from file owner
2. Enter the code below
3. Get instant access to the file

✏️ **Enter your redeem code:**
    """
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        context.user_data['waiting_for_redeem_code'] = True
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        context.user_data['waiting_for_redeem_code'] = True

async def handle_redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle redeem code input"""
    if not context.user_data.get('waiting_for_redeem_code'):
        return
    
    code = update.message.text.strip().upper()
    user_id = update.effective_user.id
    
    # Clear waiting state
    context.user_data['waiting_for_redeem_code'] = False
    
    # Check if code exists
    file_id = data_store.get_file_by_code(code)
    
    if not file_id:
        await update.message.reply_text(
            "❌ **Invalid Redeem Code**\n\n"
            "The code you entered is not valid or has already been used.\n"
            "Please check the code and try again."
        )
        return
    
    if file_id not in data_store.files:
        await update.message.reply_text("❌ File not found!")
        return
    
    file_info = data_store.files[file_id]
    
    # Remove used code
    if code in data_store.redeem_codes:
        del data_store.redeem_codes[code]
    
    if 'redeem_codes' in file_info and code in file_info['redeem_codes']:
        file_info['redeem_codes'].remove(code)
    
    # Send file to user
    await send_file_to_user(update, context, file_id, free_access=True)

async def handle_file_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file access from start parameter"""
    if not context.args or len(context.args) == 0:
        await start(update, context)
        return
    
    param = context.args[0]
    
    if param.startswith('file_'):
        file_id = param[5:]  # Remove 'file_' prefix
        
        if file_id in data_store.files:
            file_info = data_store.files[file_id]
            user_id = update.effective_user.id
            
            # Check if file is free
            if file_info['price'] == 0:
                await send_file_to_user(update, context, file_id, free_access=True)
                return
            
            # Check if user has enough stars
            user_stars = data_store.get_user_stars(user_id)
            
            text = f"""
📁 **File Access Required**

**File:** `{file_info['filename']}`
**Price:** {file_info['price']} ⭐ Stars
**Your Balance:** {user_stars} ⭐ Stars

{'✅ You have enough Stars!' if user_stars >= file_info['price'] else '❌ Insufficient Stars!'}
            """
            
            keyboard = []
            
            if user_stars >= file_info['price']:
                keyboard.append([InlineKeyboardButton(f"💫 Pay {file_info['price']} Stars & Download", 
                                                     callback_data=f"payfile_{file_id}")])
            else:
                needed_stars = file_info['price'] - user_stars
                keyboard.append([InlineKeyboardButton(f"💫 Buy {needed_stars} More Stars", 
                                                     callback_data="buystars")])
            
            keyboard.extend([
                [InlineKeyboardButton("🎫 Use Redeem Code", callback_data="redeem")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="start")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ File not found or no longer available!")

async def pay_for_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file payment"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split('_')[1]
    user_id = query.from_user.id
    
    if file_id not in data_store.files:
        await query.edit_message_text("❌ File not found!")
        return
    
    file_info = data_store.files[file_id]
    price = file_info['price']
    
    # Check and deduct stars
    if data_store.deduct_stars(user_id, price):
        # Add stars to file owner
        data_store.add_stars(file_info['owner_id'], price)
        
        # Send file
        await send_file_to_user(update, context, file_id, paid_access=True)
    else:
        await query.edit_message_text(
            f"❌ **Insufficient Stars!**\n\n"
            f"You need {price} ⭐ Stars but only have {data_store.get_user_stars(user_id)} ⭐\n\n"
            f"Please buy more Stars to access this file.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💫 Buy Stars", callback_data="buystars")]])
        )

async def send_file_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str, free_access: bool = False, paid_access: bool = False):
    """Send file to user"""
    if file_id not in data_store.files:
        if update.message:
            await update.message.reply_text("❌ File not found!")
        else:
            await update.callback_query.edit_message_text("❌ File not found!")
        return
    
    file_info = data_store.files[file_id]
    file_data = file_info['file_data']
    user_id = update.effective_user.id
    
    # Increment access count
    data_store.files[file_id]['access_count'] += 1
    
    # Add to user's access history
    if user_id not in data_store.access_history:
        data_store.access_history[user_id] = []
    data_store.access_history[user_id].append(file_id)
    
    # Prepare success message
    access_type = "FREE ACCESS" if free_access else ("PAID ACCESS" if paid_access else "ACCESS")
    success_text = f"""
✅ **{access_type} GRANTED**

📁 **File:** `{file_info['filename']}`
💫 **Cost:** {file_info['price']} Stars {'(Free)' if free_access else ''}
🔄 **Downloads:** {file_info['access_count']}

📥 **Your file is being sent...**
    """
    
    try:
        # Send the file based on type
        file_type = file_data['file_type']
        telegram_file_id = file_data['file_id']
        
        if update.message:
            chat_id = update.message.chat_id
        else:
            chat_id = update.callback_query.message.chat_id
            await update.callback_query.edit_message_text(success_text, parse_mode='Markdown')
        
        # Send file based on type
        if file_type == 'document':
            await context.bot.send_document(chat_id=chat_id, document=telegram_file_id, 
                                          caption=f"📁 {file_info['filename']}")
        elif file_type == 'photo':
            await context.bot.send_photo(chat_id=chat_id, photo=telegram_file_id,
                                       caption=f"📸 {file_info['filename']}")
        elif file_type == 'video':
            await context.bot.send_video(chat_id=chat_id, video=telegram_file_id,
                                       caption=f"🎥 {file_info['filename']}")
        elif file_type == 'audio':
            await context.bot.send_audio(chat_id=chat_id, audio=telegram_file_id,
                                       caption=f"🎵 {file_info['filename']}")
        elif file_type == 'voice':
            await context.bot.send_voice(chat_id=chat_id, voice=telegram_file_id)
        elif file_type == 'video_note':
            await context.bot.send_video_note(chat_id=chat_id, video_note=telegram_file_id)
        elif file_type == 'sticker':
            await context.bot.send_sticker(chat_id=chat_id, sticker=telegram_file_id)
        
        if update.message:
            await update.message.reply_text(success_text, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        error_text = "❌ **Error sending file**\n\nThe file may no longer be available. Please contact the file owner."
        
        if update.message:
            await update.message.reply_text(error_text)
        else:
            await update.callback_query.edit_message_text(error_text)

async def view_redeem_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all redeem codes for a file"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split('_')[1]
    
    if file_id not in data_store.files:
        await query.edit_message_text("❌ File not found!")
        return
    
    file_info = data_store.files[file_id]
    
    if file_info['owner_id'] != query.from_user.id:
        await query.edit_message_text("❌ You can only view codes for your own files!")
        return
    
    codes = file_info.get('redeem_codes', [])
    
    if not codes:
        text = f"""
🎫 **Redeem Codes**

📁 **File:** `{file_info['filename']}`

No redeem codes generated yet.
        """
        keyboard = [
            [InlineKeyboardButton("🎫 Generate First Code", callback_data=f"gencode_{file_id}")],
            [InlineKeyboardButton("🔙 Back to File", callback_data=f"fileinfo_{file_id}")]
        ]
    else:
        text = f"""
🎫 **Redeem Codes** ({len(codes)} active)

📁 **File:** `{file_info['filename']}`

**Active Codes:**
        """
        
        for i, code in enumerate(codes[-10:], 1):  # Show last 10 codes
            text += f"\n`{code}`"
        
        if len(codes) > 10:
            text += f"\n\n... and {len(codes) - 10} more codes"
        
        keyboard = [
            [InlineKeyboardButton("🎫 Generate New Code", callback_data=f"gencode_{file_id}")],
            [InlineKeyboardButton("🔙 Back to File", callback_data=f"fileinfo_{file_id}")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a file"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split('_')[1]
    
    if file_id not in data_store.files:
        await query.edit_message_text("❌ File not found!")
        return
    
    file_info = data_store.files[file_id]
    
    if file_info['owner_id'] != query.from_user.id:
        await query.edit_message_text("❌ You can only delete your own files!")
        return
    
    text = f"""
🗑️ **Delete File**

📁 **File:** `{file_info['filename']}`
💰 **Price:** {file_info['price']} Stars
📊 **Downloads:** {file_info['access_count']}

⚠️ **Warning:** This action cannot be undone!
All redeem codes for this file will also be deleted.

Are you sure you want to delete this file?
    """
    
    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data=f"fileinfo_{file_id}"),
         InlineKeyboardButton("🗑️ Delete", callback_data=f"confirmdelete_{file_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def confirm_delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm file deletion"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split('_')[1]
    user_id = query.from_user.id
    
    if file_id not in data_store.files:
        await query.edit_message_text("❌ File not found!")
        return
    
    file_info = data_store.files[file_id]
    
    if file_info['owner_id'] != user_id:
        await query.edit_message_text("❌ You can only delete your own files!")
        return
    
    filename = file_info['filename']
    
    # Remove redeem codes
    codes_to_remove = file_info.get('redeem_codes', [])
    for code in codes_to_remove:
        if code in data_store.redeem_codes:
            del data_store.redeem_codes[code]
    
    # Remove file from storage
    del data_store.files[file_id]
    
    # Remove from user's file list
    if user_id in data_store.user_files and file_id in data_store.user_files[user_id]:
        data_store.user_files[user_id].remove(file_id)
    
    text = f"""
✅ **File Deleted Successfully**

📁 **File:** `{filename}`

The file and all its redeem codes have been permanently deleted.
    """
    
    keyboard = [
        [InlineKeyboardButton("📂 My Files", callback_data="myfiles"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def edit_file_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit file price"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split('_')[1]
    
    if file_id not in data_store.files:
        await query.edit_message_text("❌ File not found!")
        return
    
    file_info = data_store.files[file_id]
    
    if file_info['owner_id'] != query.from_user.id:
        await query.edit_message_text("❌ You can only edit your own files!")
        return
    
    text = f"""
✏️ **Edit File Price**

📁 **File:** `{file_info['filename']}`
💰 **Current Price:** {file_info['price']} Stars

Select new price:
    """
    
    keyboard = [
        [InlineKeyboardButton("🆓 Free (0 Stars)", callback_data=f"setprice_{file_id}_0")],
        [InlineKeyboardButton("⭐ 1 Star", callback_data=f"setprice_{file_id}_1"),
         InlineKeyboardButton("⭐ 5 Stars", callback_data=f"setprice_{file_id}_5")],
        [InlineKeyboardButton("⭐ 10 Stars", callback_data=f"setprice_{file_id}_10"),
         InlineKeyboardButton("⭐ 25 Stars", callback_data=f"setprice_{file_id}_25")],
        [InlineKeyboardButton("⭐ 50 Stars", callback_data=f"setprice_{file_id}_50"),
         InlineKeyboardButton("⭐ 100 Stars", callback_data=f"setprice_{file_id}_100")],
        [InlineKeyboardButton("🔙 Cancel", callback_data=f"fileinfo_{file_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    query = update.callback_query
    
    if query.data == "start":
        await start(update, context)
    elif query.data == "upload":
        await upload_command(update, context)
    elif query.data == "myfiles":
        await my_files(update, context)
    elif query.data == "buystars":
        await buy_stars(update, context)
    elif query.data == "redeem":
        await redeem_code_command(update, context)
    elif query.data == "help":
        await help_command(update, context)
    elif query.data.startswith("setprice_"):
        await set_file_price(update, context)
    elif query.data.startswith("gencode_"):
        await generate_redeem_code(update, context)
    elif query.data.startswith("fileinfo_"):
        await file_info(update, context)
    elif query.data.startswith("viewcodes_"):
        await view_redeem_codes(update, context)
    elif query.data.startswith("delete_"):
        await delete_file(update, context)
    elif query.data.startswith("confirmdelete_"):
        await confirm_delete_file(update, context)
    elif query.data.startswith("editprice_"):
        await edit_file_price(update, context)
    elif query.data.startswith("buypack_"):
        await handle_star_purchase(update, context)
    elif query.data.startswith("payfile_"):
        await pay_for_file(update, context)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    if context.user_data.get('waiting_for_redeem_code'):
        await handle_redeem_code(update, context)
        return
    
    # If no specific handler, show help
    await update.message.reply_text(
        "ℹ️ Use /start to see the main menu or /help for assistance.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="start")]])
    )

class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for health checks"""
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        response = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Telegram File Bot</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background: #f0f2f5; }
                .container { max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                .header { text-align: center; color: #1d72b8; margin-bottom: 30px; }
                .status { background: #d4edda; color: #155724; padding: 15px; border-radius: 5px; margin: 20px 0; }
                .info { background: #e2e3e5; padding: 15px; border-radius: 5px; margin: 10px 0; }
                .feature { margin: 10px 0; padding: 10px; background: #f8f9fa; border-left: 4px solid #007bff; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🤖 Telegram File Bot</h1>
                    <p>File Sharing with Telegram Stars Payment</p>
                </div>
                
                <div class="status">
                    <strong>✅ Bot Status:</strong> Online and Running
                </div>
                
                <div class="info">
                    <strong>🚀 Features:</strong>
                </div>
                
                <div class="feature">📤 Upload any file type and get shareable links</div>
                <div class="feature">💫 Telegram Stars payment integration</div>
                <div class="feature">🎫 Generate redeem codes for free access</div>
                <div class="feature">📊 File management and analytics</div>
                <div class="feature">👥 Public bot for all users</div>
                
                <div class="info">
                    <strong>📞 Contact:</strong> @NY_BOTS<br>
                    <strong>🕒 Server Time:</strong> {time}<br>
                    <strong>🌐 Host:</strong> 0.0.0.0:8080
                </div>
                
                <div style="text-align: center; margin-top: 30px; color: #6c757d;">
                    <p>Bot is healthy and ready to serve! 🎉</p>
                </div>
            </div>
        </body>
        </html>
        """.format(time=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"))
        
        self.wfile.write(response.encode())
    
    def log_message(self, format, *args):
        # Suppress HTTP server logs
        pass

def start_http_server(port):
    """Start HTTP server for health checks"""
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logger.info(f"HTTP server started on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start HTTP server: {e}")

def main():
    """Start the bot"""
    try:
        # Create application with proper configuration
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .build()
        )
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("upload", upload_command))
        application.add_handler(CommandHandler("myfiles", my_files))
        application.add_handler(CommandHandler("buystars", buy_stars))
        application.add_handler(CommandHandler("redeem", redeem_code_command))
        application.add_handler(CommandHandler("help", help_command))
        
        # File upload handlers
        application.add_handler(MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VIDEO | 
            filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | filters.Sticker.ALL,
            handle_file_upload
        ))
        
        # Text message handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
        
        # Callback query handler
        application.add_handler(CallbackQueryHandler(callback_query_handler))
        
        # Payment handlers
        application.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
        
        # Get port from environment variable or default to 8080
        PORT = int(os.environ.get('PORT', 8080))
        HOST = '0.0.0.0'
        
        logger.info(f"Starting Telegram File Bot on {HOST}:{PORT}...")
        logger.info("Bot features: File sharing, Stars payment, Redeem codes, Multi-file support")
        
        # Start HTTP server in a separate thread for health checks
        http_thread = threading.Thread(target=start_http_server, args=(PORT,), daemon=True)
        http_thread.start()
        
        # Start the bot with polling
        logger.info("Starting Telegram bot polling...")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=10
        )
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        raise

if __name__ == '__main__':
    main()
