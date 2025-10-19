import os
import time
import threading
from datetime import datetime
from shared.logger import setup_logger

class BackgroundSessionChecker:
    def __init__(self, event_handler, check_interval=10):
        self.event_handler = event_handler
        self.check_interval = check_interval
        self.adaptive_interval = check_interval  # Адаптивный интервал
        self.logger = setup_logger(__name__)
        self._stop_event = threading.Event()
        self._thread = None
        self.last_session_count = 0
        
    def start(self):
        """Запускает фоновую проверку сессий"""
        if self._thread and self._thread.is_alive():
            self.logger.warning("Background checker is already running")
            return
            
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.logger.info(f"🎯 Background session checker started (interval: {self.check_interval}s)")
        
    def stop(self):
        """Останавливает фоновую проверку"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.logger.info("Background session checker stopped")
        
    def _run(self):
        """Основной цикл фоновой проверки"""
        while not self._stop_event.is_set():
            try:
                self._check_sessions()
                self._adjust_check_interval()
            except Exception as e:
                self.logger.error(f"Error in background session checker: {e}")
                
            # Ждем указанный интервал
            self._stop_event.wait(self.check_interval)
            
    def _adjust_check_interval(self):
        """Адаптивно настраивает интервал проверок based on нагрузки"""
        current_sessions = len(self.event_handler.session_manager.active_sessions)
    
        if current_sessions == 0 and self.last_session_count == 0:
            # Нет активных сессий - увеличиваем интервал
            self.adaptive_interval = min(60, self.adaptive_interval * 1.5)  # макс 60 сек
        elif current_sessions > 10:
            # Много сессий - уменьшаем интервал
            self.adaptive_interval = max(5, self.check_interval)  # мин 5 сек
        else:
            # Нормальная нагрузка - используем стандартный интервал
            self.adaptive_interval = self.check_interval
        
        self.last_session_count = current_sessions
    
    def _check_sessions(self):
        """Проверяет и обрабатывает expired сессии - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        try:
            self.logger.debug("🔍 Starting background session check...")
            
            # Получаем статистику ДО проверки
            active_before = len(self.event_handler.session_manager.active_sessions)
            
            # ВЫЗЫВАЕМ ПРОВЕРКУ ИСТЕКШИХ СЕССИЙ ПЕРВОЙ
            expired_count = self._check_expired_sessions_aggressive()
            
            # Проверяем открытые файлы
            self.event_handler.check_open_files()
            
            # Очищаем orphaned сессии
            self.event_handler.cleanup_orphaned_sessions()
            
            # Получаем статистику ПОСЛЕ проверки
            active_after = len(self.event_handler.session_manager.active_sessions)
            
            # Логируем результат
            if expired_count > 0:
                self.logger.info(f"✅ Background check: closed {expired_count} expired sessions")
            elif active_before > 0:
                self.logger.debug(f"📊 Background check: {active_after}/{active_before} sessions still active")
            else:
                self.logger.debug("💤 Background check: no active sessions")
                
        except Exception as e:
            self.logger.error(f"❌ Error in background session check: {e}")
    
    def _check_expired_sessions_aggressive(self):
        """Агрессивно проверяет и закрывает просроченные сессии - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        try:
            # Используем прямой вызов менеджера сессий
            expired_sessions = self.event_handler.session_manager.check_and_close_expired_sessions()
            closed_count = 0
            
            for session_data in expired_sessions:
                file_path = session_data['file_path']
                username = session_data['username']
                
                # ПРОВЕРЯЕМ ЧТО ended_at УСТАНОВЛЕНО
                if 'ended_at' not in session_data or session_data['ended_at'] is None:
                    self.logger.error(f"❌ Session closed but ended_at is None for: {file_path}")
                    continue
                
                # Вычисляем финальный хеш если файл существует
                file_hash = None
                if self.event_handler.config.get('hashing', {}).get('enabled', True):
                    file_hash = self.event_handler.hash_calculator.calculate_file_hash_with_retry(file_path)
                
                # Отправляем событие closed для expired сессии
                event_data = {
                    'file_path': file_path,
                    'file_name': session_data.get('file_name', os.path.basename(file_path)),
                    'event_type': 'closed',
                    'file_hash': file_hash,
                    'user_id': username,
                    'session_id': session_data['session_id'],
                    'resume_count': session_data.get('resume_count', 0),
                    'session_duration': (session_data['ended_at'] - session_data['started_at']).total_seconds(),
                    'event_timestamp': session_data['ended_at'].isoformat()  # ИСПОЛЬЗУЕМ ВРЕМЯ ЗАКРЫТИЯ СЕССИИ
                }
                
                success = self.event_handler.api_client.send_event(event_data)
                if success:
                    self.logger.info(f"🕒 Closed expired session: {file_path} (ended_at: {session_data['ended_at']})")
                    closed_count += 1
                else:
                    self.logger.error(f"❌ Failed to send closed event for: {file_path}")
            
            return closed_count
            
        except Exception as e:
            self.logger.error(f"❌ Error in aggressive session check: {e}")
            return 0
        
    # В background_checker.py добавим синхронизацию состояния

    def _check_expired_sessions_aggressive(self):
        """Агрессивно проверяет и закрывает просроченные сессии - ОБНОВЛЕННАЯ ВЕРСИЯ"""
        try:
            # Используем прямой вызов менеджера сессий
            expired_sessions = self.event_handler.session_manager.check_and_close_expired_sessions()
            closed_count = 0
        
            for session_data in expired_sessions:
                file_path = session_data['file_path']
                username = session_data['username']
            
                # ПРОВЕРЯЕМ ЧТО ended_at УСТАНОВЛЕНО
                if 'ended_at' not in session_data or session_data['ended_at'] is None:
                    self.logger.error(f"❌ Session closed but ended_at is None for: {file_path}")
                    continue
            
                # Проверяем не прокомментирован ли файл на сервере
                if self.event_handler.is_file_commented(file_path):
                    self.logger.debug(f"💬 Skipping expired session for commented file: {file_path}")
                    continue
            
                #    Вычисляем финальный хеш если файл существует
                file_hash = None
                if self.event_handler.config.get('hashing', {}).get('enabled', True):
                    file_hash = self.event_handler.hash_calculator.calculate_file_hash_with_retry(file_path)
            
                # Отправляем событие closed для expired сессии
                event_data = {
                    'file_path': file_path,
                    'file_name': session_data.get('file_name', os.path.basename(file_path)),
                    'event_type': 'closed',
                    'file_hash': file_hash,
                    'user_id': username,
                    'session_id': session_data['session_id'],
                    'resume_count': session_data.get('resume_count', 0),
                    'session_duration': (session_data['ended_at'] - session_data['started_at']).total_seconds(),
                    'event_timestamp': session_data['ended_at'].isoformat()  # ИСПОЛЬЗУЕМ ВРЕМЯ ЗАКРЫТИЯ СЕССИИ
                }
            
                success = self.event_handler.api_client.send_event(event_data)
                if success:
                    self.logger.info(f"🕒 Closed expired session: {file_path} (ended_at: {session_data['ended_at']})")
                    closed_count += 1
                else:
                    self.logger.error(f"❌ Failed to send closed event for: {file_path}")
        
            return closed_count
        
        except Exception as e:
            self.logger.error(f"❌ Error in aggressive session check: {e}")
            return 0
        
    def _check_expired_sessions_aggressive(self):
        """Агрессивно проверяет и закрывает просроченные сессии - ОБНОВЛЕННАЯ ВЕРСИЯ"""
        try:
            # Используем прямой вызов менеджера сессий
            expired_sessions = self.event_handler.session_manager.check_and_close_expired_sessions()
            closed_count = 0
    
            for session_data in expired_sessions:
                file_path = session_data['file_path']
                username = session_data['username']
        
                # ПРОВЕРЯЕМ ЧТО ended_at УСТАНОВЛЕНО
                if 'ended_at' not in session_data or session_data['ended_at'] is None:
                    self.logger.error(f"❌ Session closed but ended_at is None for: {file_path}")
                    # Принудительно устанавливаем ended_at
                    session_data['ended_at'] = datetime.now()
                    self.logger.info(f"✅ Manually set ended_at for: {file_path}")
            
                # Проверяем не прокомментирован ли файл на сервере
                if self.event_handler.is_file_commented(file_path):
                    self.logger.debug(f"💬 Skipping expired session for commented file: {file_path}")
                    continue
            
                # Вычисляем финальный хеш если файл существует
                file_hash = None
                if self.event_handler.config.get('hashing', {}).get('enabled', True):
                    file_hash = self.event_handler.hash_calculator.calculate_file_hash_with_retry(file_path)
            
                # Отправляем событие closed для expired сессии
                event_data = {
                    'file_path': file_path,
                    'file_name': session_data.get('file_name', os.path.basename(file_path)),
                    'event_type': 'closed',
                    'file_hash': file_hash,
                    'user_id': username,
                    'session_id': session_data['session_id'],
                    'resume_count': session_data.get('resume_count', 0),
                    'session_duration': (session_data['ended_at'] - session_data['started_at']).total_seconds(),
                    'event_timestamp': session_data['ended_at'].isoformat()  # ИСПОЛЬЗУЕМ ВРЕМЯ ЗАКРЫТИЯ СЕССИИ
                }
            
                success = self.event_handler.api_client.send_event(event_data)
                if success:
                    self.logger.info(f"🕒 Closed expired session: {file_path} (ended_at: {session_data['ended_at']})")
                    closed_count += 1
                else:
                    self.logger.error(f"❌ Failed to send closed event for: {file_path}")
    
            return closed_count
    
        except Exception as e:
            self.logger.error(f"❌ Error in aggressive session check: {e}")
            return 0