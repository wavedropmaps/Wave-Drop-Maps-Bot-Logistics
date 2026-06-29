"""
Centralized logging setup for Wave Logistics Bot.

This module provides consistent logging configuration across the entire application.
"""

import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

class UnicodeSafeFormatter(logging.Formatter):
    """Log formatter that replaces Unicode characters that can't be encoded in console output."""
    
    def format(self, record):
        try:
            # First try normal formatting
            result = super().format(record)
            # If we're outputting to console on Windows, check encoding
            if sys.platform == 'win32' and hasattr(sys.stderr, 'encoding'):
                # Try to encode to console encoding to check if it will work
                result.encode(sys.stderr.encoding or 'cp1252', errors='strict')
            return result
        except UnicodeEncodeError:
            # If encoding fails, replace problematic Unicode characters
            result = super().format(record)
            # Replace common problematic Unicode characters with ASCII equivalents
            replacements = {
                '→': '->',
                '—': '-',
                '–': '-',
                '“': '"',
                '”': '"',
                '‘': "'",
                '’': "'",
                '…': '...',
            }
            for uni_char, ascii_char in replacements.items():
                result = result.replace(uni_char, ascii_char)
            return result

class BotLogger:
    """Centralized logger for the bot application."""
    
    def __init__(self, log_folder: str = "Logs"):
        self.log_folder = log_folder
        self._setup_done = False
        
    def setup(self, level: int = logging.INFO) -> logging.Logger:
        """
        Set up logging configuration.
        
        Args:
            level: Logging level (default: logging.INFO)
            
        Returns:
            Configured root logger
        """
        if self._setup_done:
            return logging.getLogger()
            
        # Create log directory
        os.makedirs(self.log_folder, exist_ok=True)
        
        # Clean up old log files
        self._cleanup_old_logs(days_to_keep=5)
        
        # Create log file with today's date
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_filename = os.path.join(self.log_folder, f"{today}.log")
        
        # Create handlers
        file_handler = logging.FileHandler(log_filename, mode='a', encoding="utf-8")
        console_handler = logging.StreamHandler()
        
        # Create formatters
        formatter = UnicodeSafeFormatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%d/%m/%Y %H:%M:%S"
        )
        
        # Apply formatters to handlers
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.handlers = []  # Clear existing handlers
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

        # Configure discord logger to use root handlers (avoid double logging)
        discord_logger = logging.getLogger('discord')
        discord_logger.setLevel(level)
        discord_logger.propagate = False
        discord_logger.handlers = []
        discord_logger.addHandler(file_handler)
        discord_logger.addHandler(console_handler)

        self._setup_done = True

        logger = logging.getLogger()
        logger.info(f"========== BOT STARTUP ==========")
        logger.info(f"Started at: {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M:%S')} UTC")

        return logger
    
    def _cleanup_old_logs(self, days_to_keep: int = 5):
        """Delete log files older than the specified number of days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
        
        for filename in os.listdir(self.log_folder):
            if not filename.endswith(".log"):
                continue
                
            filepath = os.path.join(self.log_folder, filename)
            file_date_str = filename.replace(".log", "")
            
            try:
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff:
                    os.remove(filepath)
                    logging.debug(f"Removed old log file: {filename}")
            except ValueError:
                # Skip files that don't match the date format
                pass
    
    def get_logger(self, name: str = "discord") -> logging.Logger:
        """
        Get a named logger instance.
        
        Args:
            name: Logger name (default: "discord" for discord.py compatibility)
            
        Returns:
            Logger instance
        """
        return logging.getLogger(name)
    
    def log_command(self, ctx, extra: Optional[str] = None):
        """Log a command execution with context."""
        logger = self.get_logger()
        extra_info = f" | {extra}" if extra else ""
        logger.info(f"CMD | {ctx.author} used '{ctx.message.content}' in #{ctx.channel} ({ctx.guild}){extra_info}")
    
    def log_error(self, ctx, error: Exception):
        """Log a command error."""
        logger = self.get_logger()
        logger.error(f"ERROR | {ctx.author} used '{ctx.message.content}' | Error: {error}")

# Global instance for easy access
bot_logger = BotLogger()

# Convenience functions
def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Set up logging and return the root logger."""
    return bot_logger.setup(level)

def get_logger(name: str = "discord") -> logging.Logger:
    """Get a named logger instance."""
    return bot_logger.get_logger(name)

def log_command(ctx, extra: Optional[str] = None):
    """Log a command execution."""
    bot_logger.log_command(ctx, extra)

def log_error(ctx, error: Exception):
    """Log a command error."""
    bot_logger.log_error(ctx, error)