import schedule
import time
import logging
import sys
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Добавляем корень проекта в PYTHONPATH
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.insert(0, project_root)

from .config import get_notification_config
from .notification_manager import NotificationManager
from shared.config_loader import get_database_url

logger = logging.getLogger(__name__)

class NotificationScheduler:
    def __init__(self):
        self.config = get_notification_config()
        self.db_engine = create_engine(get_database_url())
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.db_engine)
        self.is_running = False
    
    def get_db(self) -> Session:
        """Получить сессию базы данных"""
        return self.SessionLocal()
    
    def setup_schedule(self):
        """Настроить расписание уведомлений"""
        
        # Напоминания в указанное время
        for reminder_time in self.config.reminder_times:
            schedule.every().day.at(reminder_time).do(self.run_reminders)
            logger.info(f"Scheduled reminders at {reminder_time}")
        
        # schedule.every(1).minutes.do(self.run_reminders)
        # logger.info("Тестовый режим: уведомления каждые 5 минут")
        
        # Итоговое уведомление в конце дня
        schedule.every().day.at(self.config.daily_summary_time).do(self.run_daily_summaries)
        logger.info(f"Scheduled daily summaries at {self.config.daily_summary_time}")
        
        # Периодическая проверка (для отладки и гибкости)
        schedule.every(self.config.check_interval_minutes).minutes.do(self.run_reminders)
        logger.info(f"Scheduled periodic checks every {self.config.check_interval_minutes} minutes")
    
    def run_reminders(self):
        """Запустить обработку напоминаний"""
        logger.info("Running reminder notifications...")
        db = self.get_db()
        try:
            manager = NotificationManager(db)
            results = manager.process_reminders()
            logger.info(f"Reminder execution completed: {len(results)} users processed")
        except Exception as e:
            logger.error(f"Error in reminder execution: {e}")
        finally:
            db.close()
    
    def run_daily_summaries(self):
        """Запустить обработку итоговых уведомлений"""
        logger.info("Running daily summary notifications...")
        db = self.get_db()
        try:
            manager = NotificationManager(db)
            results = manager.process_daily_summaries()
            logger.info(f"Daily summary execution completed: {len(results)} users processed")
        except Exception as e:
            logger.error(f"Error in daily summary execution: {e}")
        finally:
            db.close()
    
    def start(self):
        """Запустить планировщик"""
        logger.info("Starting notification scheduler...")
        self.setup_schedule()
        self.is_running = True
        
        try:
            while self.is_running:
                schedule.run_pending()
                time.sleep(60)  # Проверяем каждую минуту
        except KeyboardInterrupt:
            logger.info("Notification scheduler stopped by user")
        except Exception as e:
            logger.error(f"Notification scheduler error: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """Остановить планировщик"""
        logger.info("Stopping notification scheduler...")
        self.is_running = False
        schedule.clear()

def run_scheduler():
    """Запустить планировщик уведомлений"""
    scheduler = NotificationScheduler()
    scheduler.start()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run_scheduler()