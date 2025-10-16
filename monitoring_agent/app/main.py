#!/usr/bin/env python3
import sys
import os
import time

# Добавляем корень проекта в PYTHONPATH
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.insert(0, project_root)

from shared.config_loader import load_config, get_monitoring_config
from shared.logger import setup_logger
from monitoring_agent.app.file_monitor import FileMonitor

def main():
    """Главная функция запуска агента мониторинга"""
    logger = setup_logger(__name__)
    
    try:
        # Загружаем общую конфигурацию
        config = load_config()
        logger.info(f"Starting File Monitoring Agent in {config.get('environment', 'development')} mode...")
        
        # Получаем конфигурацию мониторинга
        monitoring_config = get_monitoring_config()
        
        # Логируем настройки фильтрации для отладки
        logger.info(f"Ignore patterns: {monitoring_config.get('ignore_patterns', [])}")
        logger.info(f"Ignore extensions: {monitoring_config.get('ignore_extensions', [])}")
        logger.info(f"Ignore directories: {monitoring_config.get('ignore_dirs', [])}")
        
        # Проверяем существование путей для мониторинга
        watch_paths = monitoring_config.get('watch_paths', ['./monitor'])
        for path in watch_paths:
            if not os.path.exists(path):
                logger.warning(f"Watch path does not exist: {path}")
                os.makedirs(path, exist_ok=True)
                logger.info(f"Created watch path: {path}")
        
        # Создаем и запускаем монитор
        monitor = FileMonitor(monitoring_config=monitoring_config)
        monitor.start()
        
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.error(f"Failed to start monitoring: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()