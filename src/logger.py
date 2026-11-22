import sys
import logging
from logging.handlers import RotatingFileHandler

from src.paths import LOG_DIR

def setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create the root logger for our app
    logger = logging.getLogger("dictate")
    logger.setLevel(logging.DEBUG)
    
    # Avoid duplicate handlers if setup_logger is called multiple times
    if logger.hasHandlers():
        return logger
        
    # Standard format for logs
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(threadName)s - %(name)s - %(filename)s:%(lineno)d - %(message)s'
    )
    
    # Rotating File Handler (1 MB limit, up to 3 backups)
    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=1 * 1024 * 1024,  # 1 MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console Handler (useful during manual runs/debugging)
    if sys.__stderr__ is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    return logger

# Global logger instance for convenience
logger = setup_logger()
