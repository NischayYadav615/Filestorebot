#!/usr/bin/env python3
"""
Simple startup script for the Telegram File Bot
This helps with deployment on platforms like Render and Koyeb
"""

import os
import sys
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_environment():
    """Check if all required environment variables are set"""
    required_vars = ['BOT_TOKEN']
    missing_vars = []
    
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please set the following environment variables:")
        for var in missing_vars:
            logger.error(f"  {var}=your_{var.lower()}_here")
        return False
    
    return True

def main():
    """Main startup function"""
    logger.info("🤖 Starting Telegram File Bot...")
    
    # Check environment
    if not check_environment():
        logger.error("❌ Environment check failed!")
        return 1
    
    # Set the port for platforms like Render
    port = os.getenv('PORT', '8080')
    logger.info(f"🌐 Port configured: {port}")
    
    # Import and run the main bot
    try:
        from main import main as bot_main
        logger.info("✅ Bot module imported successfully")
        
        # Run the bot
        return bot_main()
        
    except ImportError as e:
        logger.error(f"❌ Failed to import bot module: {e}")
        return 1
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
