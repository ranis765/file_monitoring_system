# Эта файл оставлен как fallback для polling, но не используется по умолчанию на сервере
import os
import time
import threading
from datetime import datetime
from shared.logger import setup_logger
from shared.config_loader import get_monitoring_config
from .event_handler import EventHandler
from .background_checker import BackgroundSessionChecker

class FileMonitor:
    def __init__(self, monitoring_config=None):
        if monitoring_config is None:
            self.config = get_monitoring_config()
        else:
            self.config = monitoring_config
            
        self.logger = setup_logger(__name__)
        
        # Инициализация компонентов
        self.event_handler = EventHandler(monitoring_config=self.config)
        
        # Создаем фоновый проверщик сессий
        check_interval = self.config.get('background_check_interval', 15)
        self.background_checker = BackgroundSessionChecker(
            self.event_handler, 
            check_interval=check_interval
        )
        
        # Флаг работы
        self._running = False
        self._monitor_thread = None
        
        # Настройки сканирования
        self.scan_interval = self.config.get('poll_interval', 5)  # Увеличено до 5 сек
        
        # watch_paths теперь локальные
        self.watch_paths = self.config.get('watch_paths', ['C:\\SharedFolder'])
        
        # Трекер состояния файлов
        self.file_states = {}  # file_path -> (mtime, size)
        
        self.logger.info(f"🎯 FileMonitor initialized with process-based scanning every {self.scan_interval}s (fallback mode)")

    def start(self):
        """Запускает мониторинг"""
        if self._running:
            self.logger.warning("Monitor is already running")
            return
            
        self._running = True
        
        # Проверяем соединение с API
        if not self.event_handler.api_client.test_connection():
            self.logger.error("❌ Cannot connect to API server")
            return
        
        # Запускаем фоновую проверку
        self.background_checker.start()
        
        # Запускаем основной цикл мониторинга
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        
        self.logger.info("✅ File monitoring started successfully")
        
        # Основной цикл (только для статистики)
        try:
            stats_interval = 30
            while self._running:
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

    def _monitor_loop(self):
        """Основной цикл мониторинга файлов"""
        self.logger.info("🔄 Starting file monitoring loop...")
        
        # Первоначальное сканирование для отслеживания состояния
        self._initial_scan()
        
        while self._running:
            try:
                self._scan_files()
                time.sleep(self.scan_interval)
            except Exception as e:
                self.logger.error(f"Error in monitor loop: {e}")
                time.sleep(10)  # Пауза при ошибке

    def _initial_scan(self):
        """Первоначальное сканирование файлов для установки базового состояния"""
        self.logger.info("🔍 Performing initial file scan for state tracking...")
        
        for watch_path in self.watch_paths:
            if not os.path.exists(watch_path):
                self.logger.warning(f"Watch path does not exist: {watch_path}")
                continue
                
            for root, dirs, files in os.walk(watch_path):
                # Пропускаем игнорируемые директории
                dirs[:] = [d for d in dirs if not self._should_ignore_dir(os.path.join(root, d))]
                
                for file in files:
                    file_path = os.path.join(root, file)
                    if self.event_handler.file_validator.should_monitor_file(file_path):
                        try:
                            stat = os.stat(file_path)
                            self.file_states[file_path] = (stat.st_mtime, stat.st_size)
                            self.logger.debug(f"📁 Tracked existing file: {file_path}")
                        except (OSError, PermissionError) as e:
                            self.logger.debug(f"Could not access file {file_path}: {e}")

    def _scan_files(self):
        """Сканирует файлы на изменения"""
        current_files = set()
        
        for watch_path in self.watch_paths:
            if not os.path.exists(watch_path):
                continue
                
            for root, dirs, files in os.walk(watch_path):
                # Пропускаем игнорируемые директории
                dirs[:] = [d for d in dirs if not self._should_ignore_dir(os.path.join(root, d))]
                
                for file in files:
                    file_path = os.path.join(root, file)
                    
                    if not self.event_handler.file_validator.should_monitor_file(file_path):
                        continue
                        
                    current_files.add(file_path)
                    self._check_file_changes(file_path)
        
        # Проверяем удаленные файлы
        self._check_deleted_files(current_files)

    def _check_file_changes(self, file_path):
        """Проверяет изменения файла"""
        try:
            if not os.path.exists(file_path):
                return
                
            current_stat = os.stat(file_path)
            current_mtime = current_stat.st_mtime
            current_size = current_stat.st_size
            
            previous_state = self.file_states.get(file_path)
            
            if previous_state is None:
                # Новый файл
                self.logger.info(f"🆕 New file detected: {file_path}")
                self._process_file_event(file_path, 'created', current_mtime, current_size)
            else:
                prev_mtime, prev_size = previous_state
                
                if current_mtime != prev_mtime:
                    if current_size != prev_size:
                        # Файл изменен
                        self.logger.debug(f"📝 File modified: {file_path}")
                        self._process_file_event(file_path, 'modified', current_mtime, current_size)
                    else:
                        # Изменены только метаданные
                        self.file_states[file_path] = (current_mtime, current_size)
                        
        except (OSError, PermissionError) as e:
            self.logger.debug(f"Could not access file {file_path}: {e}")

    def _check_deleted_files(self, current_files):
        """Проверяет удаленные файлы"""
        deleted_files = set(self.file_states.keys()) - current_files
        
        for file_path in deleted_files:
            if self.event_handler.file_validator.should_monitor_file_by_name(file_path):
                self.logger.info(f"🗑️ File deleted: {file_path}")
                self._process_file_event(file_path, 'deleted')
            # Удаляем из состояния в любом случае
            if file_path in self.file_states:
                del self.file_states[file_path]

    def _process_file_event(self, file_path, event_type, mtime=None, size=None):
        """Обрабатывает событие файла"""
        try:
            # Обрабатываем событие через event_handler
            success = self.event_handler.handle_file_event(event_type, file_path)
            
            if success and event_type != 'deleted':
                # Обновляем состояние только для существующих файлов
                if mtime is None or size is None:
                    stat = os.stat(file_path)
                    mtime, size = stat.st_mtime, stat.st_size
                
                self.file_states[file_path] = (mtime, size)
                
        except Exception as e:
            self.logger.error(f"Error processing {event_type} event for {file_path}: {e}")

    def _should_ignore_dir(self, dir_path):
        """Проверяет нужно ли игнорировать директорию"""
        dir_name = os.path.basename(dir_path)
        ignore_dirs = self.config.get('ignore_dirs', [])
        
        if dir_name in ignore_dirs:
            return True
            
        # Проверяем паттерны
        ignore_patterns = self.config.get('ignore_patterns', [])
        for pattern in ignore_patterns:
            if pattern.startswith('*') and dir_name.endswith(pattern[1:]):
                return True
            elif pattern.endswith('*') and dir_name.startswith(pattern[:-1]):
                return True
            elif pattern in dir_name:
                return True
                
        return False

    def stop(self):
        """Останавливает мониторинг"""
        self.logger.info("🛑 Stopping file monitor...")
        self._running = False
        
        if self.background_checker:
            self.background_checker.stop()
            
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
            
        # Очищаем ресурсы
        self.event_handler.cleanup()
        
        self.logger.info("✅ File monitoring stopped")