import os
import getpass
import platform
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from shared.logger import setup_logger
from shared.config_loader import get_monitoring_config, get_api_client_config

try:
    import win32security  # For Windows user
except ImportError:
    win32security = None
try:
    import psutil  # For tracking open files
except ImportError:
    psutil = None

from .hash_calculator import HashCalculator
from .session_manager import SessionManager
from .api_client import APIClient
from .file_validator import FileValidator

class EventHandler:
    def __init__(self, monitoring_config=None):
        if monitoring_config is None:
            self.config = get_monitoring_config()
        else:
            self.config = monitoring_config
            
        self.logger = setup_logger(__name__)
        
        # Инициализация компонентов
        self.hash_calculator = HashCalculator(self.config.get('hashing', {}))
        self.session_manager = SessionManager()
        
        # ПЕРЕДАЕМ КОНФИГУРАЦИЮ СЕССИЙ ПРАВИЛЬНО
        session_config = self.config.get('sessions', {})
        self.session_manager.set_config(session_config)
        
        self.api_client = APIClient()
        self.file_validator = FileValidator(self.config)
        
        # Статистика
        self.stats = {
            'events_processed': 0,
            'events_failed': 0,
            'sessions_created': 0,
            'sessions_resumed': 0,
            'files_closed': 0,
            'files_deleted': 0,
            'expired_sessions': 0
        }
        
        # Отслеживание открытых файлов
        self.open_files = {}  # file_path -> {username, processes, last_activity}
        
        # Трекер перемещений файлов
        self.file_renames = {}
        
        # Время последней проверки открытых файлов
        self.last_open_files_check = datetime.now()
        
        self.logger.info("EventHandler initialized with centralized config")
    
    def _normalize_username(self, username: str) -> str:
        """Нормализует имя пользователя к единому формату"""
        if not username:
            return getpass.getuser()
        
        # Если имя содержит домен (формат DOMAIN\\username), извлекаем только username
        if '\\' in username:
            # Разделяем по обратному слешу и берем последнюю часть
            parts = username.split('\\')
            normalized = parts[-1]  # Берем последнюю часть после \\
            self.logger.debug(f"Normalized username: {username} -> {normalized}")
            return normalized
        
        # Если имя уже в правильном формате, возвращаем как есть
        return username
    
    def handle_file_event(self, event_type: str, file_path: str, dest_path: str = None) -> bool:
        """Обрабатывает событие файла"""
        try:
            self.stats['events_processed'] += 1
            
            self.logger.debug(f"Raw event: {event_type} - {file_path} -> {dest_path}")
            
            # Обрабатываем перемещение как специальный случай
            if event_type == 'moved' and dest_path:
                return self._handle_file_moved(file_path, dest_path)
            
            # Проверяем нужно ли отслеживать файл
            if not self._should_process_file(file_path, event_type):
                self.logger.debug(f"Ignoring file: {file_path} (event: {event_type})")
                return True
            
            # Получаем пользователя ОС и НОРМАЛИЗУЕМ его имя
            username = self._get_file_modifier_safe(file_path, event_type)
            normalized_username = self._normalize_username(username)
            
            self.logger.debug(f"User: {username} -> normalized: {normalized_username}")
            
            # Обновляем отслеживание открытых файлов
            if event_type in ('created', 'modified'):
                self._update_open_file_tracking(file_path, normalized_username, event_type)
            elif event_type == 'deleted':
                # Удаляем из отслеживания открытых файлов
                if file_path in self.open_files:
                    del self.open_files[file_path]
            
            # Вычисляем хеш если нужно (только для существующих файлов)
            file_hash = None
            if (event_type != 'deleted' and 
                self.config.get('hashing', {}).get('enabled', True) and 
                not self._is_temporary_file(file_path) and
                os.path.exists(file_path)):
                file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
            
            # Обрабатываем в зависимости от типа события
            if event_type == 'created':
                return self._handle_file_created(file_path, normalized_username, file_hash)
            elif event_type == 'modified':
                return self._handle_file_modified(file_path, normalized_username, file_hash)
            elif event_type == 'deleted':
                return self._handle_file_deleted(file_path, normalized_username)
            else:
                self.logger.warning(f"Unknown event type: {event_type}")
                return False
                
        except Exception as e:
            self.stats['events_failed'] += 1
            self.logger.error(f"Error handling {event_type} event for {file_path}: {e}")
            return False
    
    def check_expired_sessions(self):
        """Проверяет и закрывает просроченные сессии"""
        try:
            self.logger.debug("🔍 Starting expired sessions check...")
            expired_sessions = self.session_manager.check_and_close_expired_sessions()
            closed_count = 0
            
            for session_data in expired_sessions:
                file_path = session_data['file_path']
                username = session_data['username']
                self.stats['expired_sessions'] += 1
                closed_count += 1
                
                # ПРОВЕРЯЕМ ЧТО ended_at УСТАНОВЛЕНО
                if 'ended_at' not in session_data or session_data['ended_at'] is None:
                    self.logger.error(f"❌ Session closed but ended_at is None for: {file_path}")
                    continue
                
                # Вычисляем финальный хеш если файл существует
                file_hash = None
                if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                    file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
                
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
                
                success = self.api_client.send_event(event_data)
                if success:
                    self.logger.info(f"✅ Closed expired session: {file_path} (ended_at: {session_data['ended_at']})")
                else:
                    self.logger.error(f"❌ Failed to send closed event for: {file_path}")
            
            return closed_count
            
        except Exception as e:
            self.logger.error(f"❌ Error checking expired sessions: {e}")
            return 0
    
    def _update_open_file_tracking(self, file_path: str, username: str, event_type: str):
        """Обновляет информацию об открытых файлах"""
        if not psutil:
            return
            
        try:
            current_processes = self._get_processes_using_file(file_path)
            current_time = datetime.now()
            
            if current_processes:
                # Файл открыт - обновляем информацию
                self.open_files[file_path] = {
                    'username': username,
                    'processes': current_processes,
                    'last_activity': current_time,
                    'last_checked': current_time,
                    'event_type': event_type
                }
                self.logger.debug(f"File {file_path} is open in {len(current_processes)} processes")
            else:
                # Файл больше не открыт - проверяем нужно ли закрыть сессию
                if file_path in self.open_files:
                    file_info = self.open_files[file_path]
                    time_since_last_activity = current_time - file_info['last_activity']
                    
                    # Закрываем сессию только если прошло достаточно времени с последней активности
                    if time_since_last_activity > timedelta(seconds=5):
                        self.logger.info(f"File {file_path} is no longer open, closing session")
                        
                        # Вычисляем финальный хеш если файл существует
                        file_hash = None
                        if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                            file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
                        
                        # Закрываем сессию
                        self._handle_file_closed(file_path, file_info['username'], file_hash)
                        del self.open_files[file_path]
                        self.stats['files_closed'] += 1
                    else:
                        # Обновляем время проверки, но не закрываем сессию
                        self.open_files[file_path]['last_checked'] = current_time
                        
        except Exception as e:
            self.logger.error(f"Error updating open file tracking for {file_path}: {e}")
    
    def _get_processes_using_file(self, file_path: str) -> list:
        """Возвращает список процессов, использующих файл"""
        if not psutil:
            return []
            
        processes = []
        try:
            for proc in psutil.process_iter(['pid', 'name', 'username', 'open_files']):
                try:
                    open_files = proc.info.get('open_files')
                    if open_files is None:
                        continue
                        
                    for file in open_files:
                        if file.path.lower() == file_path.lower():
                            # НОРМАЛИЗУЕМ имя пользователя процесса
                            process_username = self._normalize_username(proc.info.get('username', 'unknown'))
                            processes.append({
                                'pid': proc.pid,
                                'name': proc.info['name'],
                                'username': process_username
                            })
                            break
                            
                except (psutil.NoSuchProcess, psutil.AccessDenied, FileNotFoundError):
                    continue
                    
        except Exception as e:
            self.logger.debug(f"Error getting processes for {file_path}: {e}")
            
        return processes
    
    def check_open_files(self):
        """Периодически проверяет состояние открытых файлов"""
        if not psutil:
            return
            
        try:
            current_time = datetime.now()
            check_interval = timedelta(seconds=30)
            
            if current_time - self.last_open_files_check < check_interval:
                return
                
            self.last_open_files_check = current_time
            
            files_to_close = []
            
            for file_path, file_info in list(self.open_files.items()):
                # Проверяем, открыт ли файл все еще
                current_processes = self._get_processes_using_file(file_path)
                
                if not current_processes:
                    # Файл больше не открыт - проверяем время с последней активности
                    time_since_last_activity = current_time - file_info['last_activity']
                    
                    # Закрываем сессию только если прошло достаточно времени
                    if time_since_last_activity > timedelta(seconds=5):
                        files_to_close.append((file_path, file_info))
                    else:
                        # Обновляем время проверки
                        file_info['last_checked'] = current_time
                else:
                    # Файл все еще открыт - обновляем информацию
                    file_info['processes'] = current_processes
                    file_info['last_checked'] = current_time
            
            # Закрываем сессии для файлов, которые больше не открыты
            for file_path, file_info in files_to_close:
                self.logger.info(f"Detected file closure: {file_path}")
                
                # Вычисляем финальный хеш если файл существует
                file_hash = None
                if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                    file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
                
                # Закрываем сессию
                self._handle_file_closed(file_path, file_info['username'], file_hash)
                del self.open_files[file_path]
                self.stats['files_closed'] += 1
                
        except Exception as e:
            self.logger.error(f"Error checking open files: {e}")
    
    def _handle_file_closed(self, file_path: str, username: str, file_hash: str) -> bool:
        """Обрабатывает закрытие файла - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        self.logger.info(f"File closed: {file_path} by {username}")
        
        # Закрываем сессию в SessionManager
        session_data = self.session_manager.close_session(file_path, username, file_hash)
        
        if session_data:
            # ВАЖНО: проверяем что ended_at установлен
            if 'ended_at' not in session_data or session_data['ended_at'] is None:
                self.logger.error(f"❌ Session closed but ended_at is not set for {file_path}")
                # Устанавливаем ended_at если его нет
                session_data['ended_at'] = datetime.now()
                self.logger.info(f"✅ Manually set ended_at to: {session_data['ended_at']}")
            
            session_duration = (session_data['ended_at'] - session_data['started_at']).total_seconds()
            
            event_data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'event_type': 'closed',
                'file_hash': file_hash,
                'user_id': username,
                'session_id': session_data['session_id'],
                'resume_count': session_data.get('resume_count', 0),
                'session_duration': session_duration,
                'event_timestamp': session_data['ended_at'].isoformat()  # ИСПОЛЬЗУЕМ ВРЕМЯ ЗАКРЫТИЯ СЕССИИ
            }
            
            success = self.api_client.send_event(event_data)
            if success:
                self.logger.info(f"✅ Successfully closed session for {file_path} (duration: {session_duration:.1f}s, ended_at: {session_data['ended_at']})")
            else:
                self.logger.error(f"❌ Failed to send closed event for {file_path}")
            return success
        else:
            self.logger.warning(f"No active session found for closed file: {file_path}")
            return True

    def _should_process_file(self, file_path: str, event_type: str) -> bool:
        """Определяет нужно ли обрабатывать файл"""
        if event_type in ('deleted', 'closed') and not os.path.exists(file_path):
            return self.file_validator.should_monitor_file_by_name(file_path)
        return self.file_validator.should_monitor_file(file_path)
    
    def _get_file_modifier_safe(self, file_path: str, event_type: str) -> str:
        """Безопасное получение модификатора файла"""
        try:
            if not os.path.exists(file_path) or self._is_temporary_file(file_path):
                return getpass.getuser()
            return self._get_file_modifier(file_path)
        except Exception as e:
            self.logger.warning(f"Failed to get file modifier for {file_path}, using current user: {e}")
            return getpass.getuser()
    
    def _is_temporary_file(self, file_path: str) -> bool:
        """Проверяет является ли файл временным"""
        filename = os.path.basename(file_path).lower()
        temp_patterns = ['~wr', '~$', '.tmp', '.temp']
        return any(pattern in filename for pattern in temp_patterns)
    
    def _is_temporary_operation(self, src_path: str, dest_path: str) -> bool:
        """Определяет является ли операция временной"""
        src_name = os.path.basename(src_path).lower()
        dest_name = os.path.basename(dest_path).lower()
        temp_patterns = ['~wr', '~$', '.tmp', '.temp']
        src_is_temp = any(pattern in src_name for pattern in temp_patterns)
        dest_is_temp = any(pattern in dest_name for pattern in temp_patterns)
        return src_is_temp or dest_is_temp
    
    def _handle_file_moved(self, src_path: str, dest_path: str) -> bool:
        """Обрабатывает перемещение/переименование файла"""
        self.logger.info(f"File moved: {src_path} -> {dest_path}")
        
        is_temp_operation = self._is_temporary_operation(src_path, dest_path)
        if is_temp_operation:
            self.logger.debug(f"Detected temporary file operation: {src_path} -> {dest_path}")
            return True
        
        username = self._get_file_modifier_safe(dest_path, 'moved')
        normalized_username = self._normalize_username(username)  # НОРМАЛИЗУЕМ имя пользователя
        self.file_renames[src_path] = dest_path
        
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(dest_path)
        
        # Обновляем сессию для нового пути файла
        old_session_key = f"{src_path}:{normalized_username}"
        if old_session_key in self.session_manager.active_sessions:
            old_session = self.session_manager.close_session(src_path, normalized_username)
            if old_session:
                self.session_manager.smart_create_session(dest_path, normalized_username, file_hash)
        
        event_data = {
            'file_path': dest_path,
            'file_name': os.path.basename(dest_path),
            'old_file_path': src_path,
            'old_file_name': os.path.basename(src_path),
            'event_type': 'moved',
            'file_hash': file_hash,
            'user_id': normalized_username,  # ИСПОЛЬЗУЕМ НОРМАЛИЗОВАННОЕ ИМЯ
            'event_timestamp': datetime.now().isoformat()
        }
        
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send moved event for {src_path} -> {dest_path}")
        return success
    
    def _get_file_modifier(self, file_path: str) -> str:
        """Получает пользователя, изменившего файл"""
        try:
            if platform.system() == 'Windows' and win32security is not None:
                sd = win32security.GetFileSecurity(file_path, win32security.OWNER_SECURITY_INFORMATION)
                owner_sid = sd.GetSecurityDescriptorOwner()
                name, domain, _ = win32security.LookupAccountSid(None, owner_sid)
                # Возвращаем полное имя с доменом (будет нормализовано позже)
                return f"{domain}\\{name}"
            else:
                return getpass.getuser()
        except Exception as e:
            self.logger.error(f"Failed to get file modifier for {file_path}: {e}")
            return getpass.getuser()
    
    def _handle_file_created(self, file_path: str, username: str, file_hash: str) -> bool:
        """Обрабатывает создание файла"""
        if file_path in self.file_renames.values():
            self.logger.debug(f"Ignoring created event for moved file: {file_path}")
            return True
            
        self.logger.info(f"File created: {file_path} by {username}")
        
        session_data = self.session_manager.smart_create_session(file_path, username, file_hash)
        
        if session_data.get('resume_count', 0) > 0:
            self.stats['sessions_resumed'] += 1
            self.logger.info(f"Session resumed for {file_path} (resume count: {session_data['resume_count']})")
        else:
            self.stats['sessions_created'] += 1
        
        event_data = {
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'event_type': 'created',
            'file_hash': file_hash,
            'user_id': username,  # УЖЕ НОРМАЛИЗОВАННОЕ ИМЯ
            'session_id': session_data['session_id'],
            'resume_count': session_data.get('resume_count', 0),
            'event_timestamp': datetime.now().isoformat()
        }
        
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send created event for {file_path}: {event_data}")
        return success
    
    def _handle_file_modified(self, file_path: str, username: str, file_hash: str) -> bool:
        """Обрабатывает изменение файла"""
        self.logger.debug(f"File modified: {file_path} by {username}")
        
        session_data = self.session_manager.smart_create_session(file_path, username, file_hash)
        
        event_data = {
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'event_type': 'modified',
            'file_hash': file_hash,
            'user_id': username,  # УЖЕ НОРМАЛИЗОВАННОЕ ИМЯ
            'session_id': session_data['session_id'],
            'resume_count': session_data.get('resume_count', 0),
            'event_timestamp': datetime.now().isoformat()
        }
        
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send modified event for {file_path}: {event_data}")
        return success
    
    def _handle_file_deleted(self, file_path: str, username: str) -> bool:
        """Обрабатывает удаление файла"""
        # Проверяем, не является ли это частью перемещения
        if file_path in self.file_renames:
            self.logger.debug(f"📦 File moved, closing session for: {file_path}")
            session_data = self.session_manager.close_session(file_path, username)
            if session_data:
                self.logger.info(f"✅ Closed session for moved file: {file_path}")
            return True
            
        self.logger.info(f"🗑️ File deleted: {file_path} by {username}")
        self.stats['files_deleted'] += 1
        
        # Удаляем из отслеживания открытых файлов
        if file_path in self.open_files:
            del self.open_files[file_path]
        
        # Пытаемся закрыть сессию обычным способом
        session_data = self.session_manager.close_session(file_path, username)
        
        # Если не нашли, используем принудительное закрытие
        if not session_data:
            self.logger.info(f"🔄 Using forced session close for: {file_path}")
            closed_sessions = self.session_manager.close_all_sessions_for_file(file_path)
            if closed_sessions:
                session_data = closed_sessions[0]
                self.logger.info(f"✅ Forced close: closed {len(closed_sessions)} sessions")
        
        if session_data:
            self.logger.info(f"✅ Successfully closed session for deleted file: {file_path}")
            event_data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'event_type': 'deleted',
                'user_id': username,  # УЖЕ НОРМАЛИЗОВАННОЕ ИМЯ
                'session_id': session_data['session_id'],
                'resume_count': session_data.get('resume_count', 0),
                'event_timestamp': datetime.now().isoformat()
            }
            
            success = self.api_client.send_event(event_data)
            if not success:
                self.logger.error(f"❌ Failed to send deleted event for: {file_path}")
            return success
        else:
            self.logger.warning(f"⚠️ No session found for deleted file: {file_path}")
            # Все равно отправляем событие удаления
            event_data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'event_type': 'deleted',
                'user_id': username,  # УЖЕ НОРМАЛИЗОВАННОЕ ИМЯ
                'event_timestamp': datetime.now().isoformat()
            }
            
            success = self.api_client.send_event(event_data)
            return success
    
    def cleanup_orphaned_sessions(self):
        """Очищает сессии для файлов, которые больше не существуют"""
        expired_sessions = []
        
        for session_key, session_data in list(self.session_manager.active_sessions.items()):
            file_path = session_data['file_path']
            username = session_data['username']
            
            if not os.path.exists(file_path):
                self.logger.info(f"Closing orphaned session for deleted file: {file_path}")
                closed_session = self.session_manager.close_session(file_path, username)
                if closed_session:
                    expired_sessions.append(closed_session)
                    event_data = {
                        'file_path': file_path,
                        'file_name': os.path.basename(file_path),
                        'event_type': 'deleted',
                        'user_id': username,
                        'session_id': closed_session['session_id'],
                        'resume_count': closed_session.get('resume_count', 0),
                        'event_timestamp': datetime.now().isoformat()
                    }
                    self.api_client.send_event(event_data)
        
        return expired_sessions
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику обработки"""
        session_stats = self.session_manager.get_session_stats()
        return {
            **self.stats, 
            **session_stats,
            'open_files_tracking': len(self.open_files)
        }
    
    def cleanup(self):
        """Очищает ресурсы"""
        # Очищаем expired сессии
        expired_sessions = self.session_manager.cleanup_expired_sessions(self)
        for session_data in expired_sessions:
            file_path = session_data['file_path']
            username = session_data['username']
            file_hash = None
            if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
            self._handle_file_closed(file_path, username, file_hash)
        
        # Проверяем открытые файлы
        self.check_open_files()
        
        # Очищаем orphaned сессии
        self.cleanup_orphaned_sessions()