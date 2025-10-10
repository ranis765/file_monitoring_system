import time
from datetime import datetime
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
from shared.logger import setup_logger
from shared.config_loader import get_monitoring_config
from .event_handler import EventHandler
from .background_checker import BackgroundSessionChecker

class FileMonitorHandler(FileSystemEventHandler):
    def __init__(self, event_handler: EventHandler):
        self.event_handler = event_handler
        self.logger = setup_logger(__name__)
    
    def on_created(self, event):
        if not event.is_directory:
            self.event_handler.handle_file_event('created', event.src_path)
    
    def on_modified(self, event):
        if not event.is_directory:
            self.event_handler.handle_file_event('modified', event.src_path)
    
    def on_deleted(self, event):
        if not event.is_directory:
            self.event_handler.handle_file_event('deleted', event.src_path)
    
    def on_moved(self, event):
        if not event.is_directory:
            self.event_handler.handle_file_event('moved', event.src_path, event.dest_path)

class FileWatcher:
    def __init__(self, monitoring_config=None):
        if monitoring_config is None:
            self.config = get_monitoring_config()
        else:
            self.config = monitoring_config
            
        self.logger = setup_logger(__name__)
        self.use_polling = self.config.get('use_polling', False)
        if self.use_polling:
            self.observer = PollingObserver()
        else:
            self.observer = Observer()
            
        # Передаем конфигурацию в EventHandler
        self.event_handler = EventHandler(monitoring_config=self.config)
        self.monitor_handler = FileMonitorHandler(self.event_handler)
        
        # Создаем фоновый проверщик сессий
        check_interval = self.config.get('background_check_interval', 15)
        self.background_checker = BackgroundSessionChecker(
            self.event_handler, 
            check_interval=check_interval
        )
        
        self.logger.info(f"🎯 FileWatcher initialized with background checking every {check_interval}s")
        
    def start(self):
        """Запускает мониторинг"""
        watch_paths = self.config.get('watch_paths', ['./monitor'])
        
        for path in watch_paths:
            try:
                self.observer.schedule(
                    self.monitor_handler,
                    path,
                    recursive=True
                )
                self.logger.info(f"📁 Started monitoring: {path}")
            except Exception as e:
                self.logger.error(f"❌ Failed to schedule watcher for {path}: {e}")
        
        self.observer.start()
        self.logger.info("✅ File monitoring started successfully")
        
        # Проверяем соединение с API
        if not self.event_handler.api_client.test_connection():
            self.logger.error("❌ Cannot connect to API server")
            return
        
        # ЗАПУСКАЕМ ФОНОВУЮ ПРОВЕРКУ
        self.background_checker.start()
        
        # Основной цикл (только для статистики)
        try:
            stats_interval = 30  # Логируем статистику каждые 30 секунд
            while True:
                time.sleep(stats_interval)
                
                # Логируем статистику
                stats = self.event_handler.get_stats()
                active_sessions = stats['active_sessions']
                
                self.logger.info(f"📊 Stats: {active_sessions} active sessions, {stats.get('expired_sessions', 0)} expired")
                
                # Детальная информация об активных сессиях
                if active_sessions > 0:
                    timeout_minutes = self.event_handler.session_manager.config.get('session_timeout_minutes', 30)
                    for session_key, session_data in self.event_handler.session_manager.active_sessions.items():
                        last_activity = session_data['last_activity']
                        time_since_activity = datetime.now() - last_activity
                        remaining = (timeout_minutes * 60) - time_since_activity.total_seconds()
                        self.logger.debug(f"⏰ {session_key}: inactive {time_since_activity.total_seconds():.1f}s (expires in {remaining:.1f}s)")
                
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            self.logger.error(f"❌ Unexpected error: {e}")
            self.stop()
    
    def stop(self):
        """Останавливает мониторинг"""
        self.background_checker.stop()
        self.observer.stop()
        self.observer.join()
        self.logger.info("🛑 File monitoring stopped")