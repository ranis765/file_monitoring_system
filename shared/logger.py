import logging
import sys
from shared.config_loader import load_config

def setup_logger(name: str) -> logging.Logger:
    """Настраивает и возвращает логгер"""
    config = load_config()
    log_config = config.get('logging', {})
    
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_config.get('level', 'INFO')))
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            log_config.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger