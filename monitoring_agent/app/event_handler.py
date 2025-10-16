import os
import getpass
import platform
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from shared.logger import setup_logger
from shared.config_loader import get_monitoring_config, get_api_client_config


try:
    import win32security
    import win32evtlog
    import win32evtlogutil
except ImportError:
    win32security = None
    win32evtlog = None
    win32evtlogutil = None

try:
    import psutil
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
        
        # Конфигурация сессий
        session_config = self.config.get('sessions', {})
        self.session_manager.set_config(session_config)
        
        self.api_client = APIClient()
        self.file_validator = FileValidator(self.config)
        
        # Настройки аудита
        self.use_auditing = self.config.get('use_auditing', False)
        self.audit_query_interval = self.config.get('audit_log_query_interval', 10)
        self.last_audit_query = datetime.now()
        self.audit_cache = {}  # Кэш для быстрого поиска пользователей
        
        # Статистика
        self.stats = {
            'events_processed': 0,
            'events_failed': 0,
            'sessions_created': 0,
            'sessions_resumed': 0,
            'files_closed': 0,
            'files_deleted': 0,
            'expired_sessions': 0,
            'main_files_processed': 0,
            'temporary_files_ignored': 0,
            'office_operations_handled': 0,
            'cad_operations_handled': 0,
            'multi_user_sessions': 0,
            'session_conflicts_resolved': 0,
            'audit_events_used': 0,
            'audit_errors': 0
        }
        
        # Отслеживание открытых файлов
        self.open_files = {}
        self.file_renames = {}
        self.file_move_chains = {}
        self.temp_to_main_map = {}
        self.main_file_tracking = {}
        
        # Трекер операций
        self.office_creation_operations = {}
        self.pending_office_operations = {}
        self.cad_temp_files = {}
        
        # Многопользовательская работа
        self.file_editors = {}
        self.user_file_locks = {}
        
        # Время последней проверки
        self.last_open_files_check = datetime.now()
        
        # Фильтр массовых событий
        self.recent_events = {}
        self.event_cooldown = 2.0
        
        # Трекер реально открытых файлов
        self.verified_open_files = set()
        
        self.logger.info(f"EventHandler initialized with auditing={self.use_auditing}")

    def handle_file_event(self, event_type: str, file_path: str, dest_path: str = None) -> bool:
        """Обрабатывает событие файла с поддержкой аудита"""
        try:
            # Фильтр частых событий
            if not self._should_process_event(file_path, event_type):
                self.logger.debug(f"⏰ Skipping frequent event: {event_type} for {file_path}")
                return True
                
            self.stats['events_processed'] += 1
            
            self.logger.debug(f"Raw event: {event_type} - {file_path} -> {dest_path}")
            
            # Определяем категорию файла ДО обработки
            file_category = self.file_validator.get_file_category(file_path)
            
            # Обрабатываем перемещение как специальный случай
            if event_type == 'moved' and dest_path:
                return self._handle_file_moved(file_path, dest_path, file_category)
            
            # Для IGNORE файлов - полностью пропускаем обработку
            if file_category == 'IGNORE':
                self.logger.debug(f"🚫 Completely ignoring event for IGNORE file: {file_path}")
                return True
            
            # Для TEMPORARY файлов - обрабатываем для контекста, но не создаем сессии
            if file_category == 'TEMPORARY':
                self.stats['temporary_files_ignored'] += 1
                self.logger.debug(f"⏰ Processing temporary file for context: {file_path}")
                return self._handle_temporary_file(event_type, file_path, dest_path)
            
            # Для MAIN файлов - полная обработка с сессиями
            if file_category == 'MAIN':
                self.stats['main_files_processed'] += 1
                return self._handle_main_file(event_type, file_path)
            
            self.logger.warning(f"Unknown file category for {file_path}: {file_category}")
            return False
                
        except Exception as e:
            self.stats['events_failed'] += 1
            self.logger.error(f"Error handling {event_type} event for {file_path}: {e}")
            return False

    def _handle_main_file(self, event_type: str, file_path: str) -> bool:
        """Обрабатывает событие для основного файла с поддержкой аудита"""
        if not self.file_validator.should_monitor_file(file_path):
            self.logger.debug(f"Ignoring main file: {file_path}")
            return True
        
        # Получаем пользователя через аудит или fallback методы
        username = self._get_file_modifier_safe(file_path, event_type)
        normalized_username = self._normalize_username(username)
        
        self.logger.debug(f"Main file event: {event_type} - {file_path} by {normalized_username}")
        
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(file_path)
        is_multi_user = len(current_editors) > 1
        
        if is_multi_user:
            self.logger.info(f"👥 Multi-user context detected for {file_path}: {current_editors}")
            self.stats['multi_user_sessions'] += 1
        
        # Проверяем является ли это частью Office операции создания
        if event_type == 'created' and self._is_office_creation_operation(file_path):
            self.logger.info(f"🔄 Detected Office file creation operation: {file_path}")
            return self._handle_office_file_creation(file_path, normalized_username)
        
        # Проверяем является ли это частью CAD операции
        if event_type == 'created' and self._is_cad_operation(file_path):
            self.logger.info(f"🔄 Detected CAD file operation: {file_path}")
            return self._handle_cad_file_operation(file_path, normalized_username, event_type)
        
        # Обрабатываем в зависимости от типа события
        if event_type == 'created':
            return self._handle_file_created(file_path, normalized_username, current_editors)
        elif event_type == 'modified':
            return self._handle_file_modified(file_path, normalized_username, current_editors)
        elif event_type == 'deleted':
            return self._handle_file_deleted(file_path, normalized_username, current_editors)
        else:
            self.logger.warning(f"Unknown event type for main file: {event_type}")
            return False

    def _get_file_modifier_safe(self, file_path: str, event_type: str) -> str:
        """Безопасное получение модификатора файла с поддержкой аудита Windows"""
        try:
            if not os.path.exists(file_path) and event_type != 'deleted':
                return getpass.getuser()
            
            # Пытаемся получить пользователя через аудит
            if self.use_auditing and win32evtlog:
                audit_user = self._get_user_from_audit_log(file_path)
                if audit_user:
                    self.stats['audit_events_used'] += 1
                    return audit_user
            
            # Fallback: получаем владельца файла
            return self._get_file_modifier(file_path)
            
        except Exception as e:
            self.logger.warning(f"Failed to get file modifier for {file_path}, using current user: {e}")
            return getpass.getuser()

    def _get_user_from_audit_log(self, file_path: str) -> Optional[str]:
        """Получает пользователя из Windows Security Event Log"""
        try:
            current_time = datetime.now()
            
            # Используем кэш для быстрого поиска
            if file_path in self.audit_cache:
                cached_data = self.audit_cache[file_path]
                if (current_time - cached_data['timestamp']).total_seconds() < 30:  # Кэш на 30 секунд
                    return cached_data['username']
            
            # Опрашиваем Event Log
            handle = win32evtlog.OpenEventLog(None, "Security")
            flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
            
            events = win32evtlog.ReadEventLog(handle, flags, 0)
            target_events = []
            
            for event in events:
                # Интересуют нас события доступа к файлам
                if event.EventID in [4663, 4656, 4670]:  # File access events
                    try:
                        # Получаем путь к файлу из события
                        obj_name = win32evtlogutil.SafeGetEventString(event, 6)  # Object Name
                        if obj_name and file_path.lower() in obj_name.lower():
                            # Получаем имя пользователя
                            user = win32evtlogutil.SafeGetEventString(event, 1)  # Subject User Name
                            if user and user != "SYSTEM":
                                target_events.append({
                                    'timestamp': event.TimeGenerated,
                                    'username': user,
                                    'event_id': event.EventID
                                })
                    except Exception as e:
                        continue
            
            win32evtlog.CloseEventLog(handle)
            
            if target_events:
                # Берем самое последнее событие
                latest_event = max(target_events, key=lambda x: x['timestamp'])
                username = latest_event['username']
                
                # Сохраняем в кэш
                self.audit_cache[file_path] = {
                    'username': username,
                    'timestamp': current_time
                }
                
                self.logger.debug(f"Found user in audit log: {username} for {file_path}")
                return username
            
        except Exception as e:
            self.stats['audit_errors'] += 1
            self.logger.error(f"Error reading audit log: {e}")
        
        return None

    def _get_file_modifier(self, file_path: str) -> str:
        """Получает владельца файла"""
        try:
            if platform.system() == 'Windows' and win32security is not None:
                sd = win32security.GetFileSecurity(file_path, win32security.OWNER_SECURITY_INFORMATION)
                owner_sid = sd.GetSecurityDescriptorOwner()
                name, domain, _ = win32security.LookupAccountSid(None, owner_sid)
                return f"{domain}\\{name}"
            else:
                return getpass.getuser()
        except Exception as e:
            self.logger.error(f"Failed to get file owner for {file_path}: {e}")
            return getpass.getuser()

    def _get_current_editors(self, file_path: str) -> List[str]:
        """Возвращает список пользователей, которые сейчас работают с файлом"""
        if not psutil:
            return []
            
        editors = set()
        try:
            processes = self._get_processes_using_file(file_path)
            for process in processes:
                editors.add(process['username'])
        except Exception as e:
            self.logger.debug(f"Error getting current editors for {file_path}: {e}")
            
        return list(editors)

    def _determine_primary_editor(self, file_path: str, current_username: str, all_editors: List[str]) -> str:
        """Определяет основного редактора файла"""
        if file_path not in self.file_editors:
            self.file_editors[file_path] = {
                'primary_editor': current_username,
                'co_editors': set(),
                'last_activity_by_user': {current_username: datetime.now()},
                'established_at': datetime.now()
            }
            self.logger.info(f"👑 {current_username} established as primary editor for {file_path}")
            return current_username
        
        editor_info = self.file_editors[file_path]
        primary_editor = editor_info['primary_editor']
        
        # Обновляем информацию о со-редакторах
        for editor in all_editors:
            if editor != primary_editor:
                editor_info['co_editors'].add(editor)
            editor_info['last_activity_by_user'][editor] = datetime.now()
        
        # Если основной редактор больше не работает с файлом, ищем нового
        if (primary_editor not in all_editors and 
            datetime.now() - editor_info['last_activity_by_user'].get(primary_editor, datetime.now()) > timedelta(minutes=5)):
            
            if all_editors:
                new_primary = max(all_editors, key=lambda u: editor_info['last_activity_by_user'].get(u, datetime.min))
                editor_info['primary_editor'] = new_primary
                self.logger.info(f"🔄 Primary editor changed from {primary_editor} to {new_primary} for {file_path}")
                self.stats['session_conflicts_resolved'] += 1
                return new_primary
        
        return primary_editor

    def _handle_file_created(self, file_path: str, username: str, current_editors: List[str]) -> bool:
        """Обрабатывает создание файла"""
        if file_path in self.file_renames.values() or file_path in self.file_move_chains.values():
            self.logger.debug(f"Ignoring created event for moved file: {file_path}")
            return True
            
        self.logger.info(f"📄 Main file created: {file_path} by {username}")
        
        # Определяем основного редактора
        primary_editor = self._determine_primary_editor(file_path, username, current_editors)
        
        # Вычисляем хеш если нужно
        file_hash = None
        if (self.config.get('hashing', {}).get('enabled', True) and
            os.path.exists(file_path)):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
        
        # Создаем сессию с основным редактором
        session_data = self.session_manager.smart_create_session(file_path, primary_editor, file_hash)
        
        # Добавляем информацию о со-редакторах в сессию
        if len(current_editors) > 1:
            session_data['co_editors'] = [editor for editor in current_editors if editor != primary_editor]
            session_data['is_multi_user'] = True
            self.logger.info(f"👥 Multi-user session created for {file_path}. Primary: {primary_editor}, Co-editors: {session_data['co_editors']}")
        
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
            'user_id': primary_editor,
            'session_id': session_data['session_id'],
            'resume_count': session_data.get('resume_count', 0),
            'is_multi_user': len(current_editors) > 1,
            'co_editors': [editor for editor in current_editors if editor != primary_editor],
            'source': 'server_agent',
            'event_timestamp': datetime.now().isoformat()
        }
        
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send created event for {file_path}: {event_data}")
        return success

    def _handle_file_modified(self, file_path: str, username: str, current_editors: List[str]) -> bool:
        """Обрабатывает изменение файла с поддержкой многопользовательской работы"""
        self.logger.debug(f"📝 Main file modified: {file_path} by {username}")
        
        # Определяем основного редактора
        primary_editor = self._determine_primary_editor(file_path, username, current_editors)
        
        # Вычисляем хеш если нужно
        file_hash = None
        if (self.config.get('hashing', {}).get('enabled', True) and
            os.path.exists(file_path)):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
        
        # Обновляем сессию с основным редактором
        session_data = self.session_manager.smart_create_session(file_path, primary_editor, file_hash)
        
        # ДОБАВЛЕНО: Обновляем информацию о со-редакторах
        if len(current_editors) > 1:
            session_data['co_editors'] = [editor for editor in current_editors if editor != primary_editor]
            session_data['is_multi_user'] = True
        
        event_data = {
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'event_type': 'modified',
            'file_hash': file_hash,
            'user_id': primary_editor,
            'session_id': session_data['session_id'],
            'resume_count': session_data.get('resume_count', 0),
            'is_multi_user': len(current_editors) > 1,
            'co_editors': [editor for editor in current_editors if editor != primary_editor],
            'source': 'server_agent',
            'event_timestamp': datetime.now().isoformat()
        }
        
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send modified event for {file_path}: {event_data}")
        return success

    def _handle_file_deleted(self, file_path: str, username: str, current_editors: List[str]) -> bool:
        """Обрабатывает удаление файла с поддержкой многопользовательской работы"""
        if file_path in self.file_renames or file_path in self.file_move_chains:
            self.logger.debug(f"📦 File moved, closing session for: {file_path}")
            
            # Определяем основного редактора для закрытия сессии
            primary_editor = self._determine_primary_editor(file_path, username, current_editors)
            session_data = self.session_manager.close_session(file_path, primary_editor)
            
            if session_data:
                self.logger.info(f"✅ Closed session for moved file: {file_path}")
            if file_path in self.file_renames:
                del self.file_renames[file_path]
            if file_path in self.file_move_chains:
                del self.file_move_chains[file_path]
            return True
            
        self.logger.info(f"🗑️ Main file deleted: {file_path} by {username}")
        self.stats['files_deleted'] += 1
        
        # Определяем основного редактора для закрытия сессии
        primary_editor = self._determine_primary_editor(file_path, username, current_editors)
        
        if file_path in self.open_files:
            del self.open_files[file_path]
        
        if file_path in self.file_renames:
            del self.file_renames[file_path]
        if file_path in self.file_move_chains:
            del self.file_move_chains[file_path]
        
        if file_path in self.verified_open_files:
            self.verified_open_files.remove(file_path)
        
        # Очищаем из temp_to_main_map
        keys_to_remove = []
        for temp_path, main_path in self.temp_to_main_map.items():
            if temp_path == file_path or main_path == file_path:
                keys_to_remove.append(temp_path)
        
        for key in keys_to_remove:
            del self.temp_to_main_map[key]
            
        # Очищаем из main_file_tracking
        if file_path in self.main_file_tracking:
            del self.main_file_tracking[file_path]
        
        # ДОБАВЛЕНО: Очищаем информацию о редакторах
        if file_path in self.file_editors:
            del self.file_editors[file_path]
        
        # Очищаем Office операции
        if file_path in self.office_creation_operations:
            del self.office_creation_operations[file_path]
        if file_path in self.cad_temp_files:
            del self.cad_temp_files[file_path]
        
        # Закрываем сессию с основным редактором
        session_data = self.session_manager.close_session(file_path, primary_editor)
        
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
                'user_id': primary_editor,
                'session_id': session_data['session_id'],
                'resume_count': session_data.get('resume_count', 0),
                'is_multi_user': len(current_editors) > 1,
                'co_editors': [editor for editor in current_editors if editor != primary_editor],
                'source': 'server_agent',
                'event_timestamp': datetime.now().isoformat()
            }
            
            success = self.api_client.send_event(event_data)
            if not success:
                self.logger.error(f"❌ Failed to send deleted event for: {file_path}")
            return success
        else:
            self.logger.warning(f"⚠️ No session found for deleted file: {file_path}")
            event_data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'event_type': 'deleted',
                'user_id': username,
                'source': 'server_agent',
                'event_timestamp': datetime.now().isoformat()
            }
            
            success = self.api_client.send_event(event_data)
            return success

    def _handle_file_moved(self, src_path: str, dest_path: str, src_category: str) -> bool:
        """Обрабатывает перемещение файла с поддержкой многопользовательской работы"""
        self.logger.info(f"🔄 File moved: {src_path} -> {dest_path} (src category: {src_category})")
        
        dest_category = self.file_validator.get_file_category(dest_path)
        self.logger.debug(f"Destination category: {dest_category}")
        
        # ДОБАВЛЕНО: Переносим информацию о редакторах
        if src_path in self.file_editors:
            self.file_editors[dest_path] = self.file_editors[src_path]
            del self.file_editors[src_path]
            self.logger.info(f"🔄 Transferred editor info from {src_path} to {dest_path}")
        
        # Специальная обработка для Office операций переименования
        if (src_category == 'MAIN' and dest_category == 'MAIN' and 
            self._is_office_creation_operation(src_path)):
            self.logger.info(f"📝 Office file rename operation: {src_path} -> {dest_path}")
            return self._handle_office_file_rename(src_path, dest_path)
        
        # Определяем тип операции
        operation_type = self._classify_move_operation(src_path, dest_path, src_category, dest_category)
        self.logger.debug(f"Move operation type: {operation_type}")
        
        if operation_type == 'TEMP_TO_TEMP':
            self.file_move_chains[src_path] = dest_path
            self.logger.debug(f"Temp-to-temp move: {src_path} -> {dest_path}")
            return True
            
        elif operation_type == 'MAIN_TO_TEMP':
            self.temp_to_main_map[dest_path] = src_path
            self.logger.debug(f"Main-to-temp move: {src_path} -> {dest_path}")
            self.main_file_tracking[src_path] = {
                'last_seen': datetime.now(),
                'temp_file': dest_path
            }
            return True
            
        elif operation_type == 'TEMP_TO_MAIN':
            return self._handle_temp_to_main_move(src_path, dest_path)
            
        elif operation_type == 'MAIN_TO_MAIN':
            return self._handle_main_to_main_move(src_path, dest_path)
            
        elif operation_type == 'TEMP_TO_IGNORE':
            self.logger.debug(f"Temp-to-ignore move (Excel operation): {src_path} -> {dest_path}")
            return True
            
        elif operation_type == 'IGNORE_TO_MAIN':
            return self._handle_ignore_to_main_move(src_path, dest_path)
        
        else:
            self.logger.warning(f"Unknown move operation type: {operation_type}")
            return self._handle_unknown_move_operation(src_path, dest_path, src_category, dest_category)

    def _handle_office_file_rename(self, src_path: str, dest_path: str) -> bool:
        """Обрабатывает переименование Office файла с поддержкой многопользовательской работы"""
        username = self._get_file_modifier_safe(dest_path, 'moved')
        normalized_username = self._normalize_username(username)
        
        # Получаем текущих редакторов для нового файла
        current_editors = self._get_current_editors(dest_path)
        primary_editor = self._determine_primary_editor(dest_path, normalized_username, current_editors)
        
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(dest_path)
        
        # Переносим существующую сессию
        old_session = self.session_manager.get_active_session(src_path, primary_editor)
        if old_session:
            transferred_session = self.session_manager.transfer_session(
                src_path, dest_path, primary_editor, file_hash
            )
            if transferred_session:
                self.logger.info(f"✅ Transferred Office session during rename: {src_path} -> {dest_path}")
                self.stats['office_operations_handled'] += 1
                
                # ДОБАВЛЕНО: Сохраняем информацию о со-редакторах
                if len(current_editors) > 1:
                    transferred_session['co_editors'] = [editor for editor in current_editors if editor != primary_editor]
                    transferred_session['is_multi_user'] = True
        else:
            # Создаем новую сессию для переименованного файла
            self.session_manager.smart_create_session(dest_path, primary_editor, file_hash)
            self.logger.info(f"✅ Created new session for renamed Office file: {dest_path}")
        
        # Сохраняем информацию о перемещении
        self.file_renames[src_path] = dest_path
        self.file_move_chains[src_path] = dest_path
        
        return self._send_moved_event(src_path, dest_path, primary_editor, file_hash, current_editors)

    def _send_moved_event(self, src_path: str, dest_path: str, username: str, file_hash: str, current_editors: List[str] = None) -> bool:
        """Отправляет событие перемещения с поддержкой многопользовательской работы"""
        if current_editors is None:
            current_editors = self._get_current_editors(dest_path)
        
        primary_editor = self._determine_primary_editor(dest_path, username, current_editors)
        is_multi_user = len(current_editors) > 1
        
        event_data = {
            'file_path': dest_path,
            'file_name': os.path.basename(dest_path),
            'old_file_path': src_path,
            'old_file_name': os.path.basename(src_path),
            'event_type': 'moved',
            'file_hash': file_hash,
            'user_id': primary_editor,
            'is_multi_user': is_multi_user,
            'co_editors': [editor for editor in current_editors if editor != primary_editor] if is_multi_user else [],
            'source': 'server_agent',
            'event_timestamp': datetime.now().isoformat()
        }
        
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send moved event for {src_path} -> {dest_path}")
        return success

    def _handle_temporary_file(self, event_type: str, file_path: str, dest_path: str = None) -> bool:
        """Обрабатывает событие для временного файла (без создания сессий)"""
        self.logger.debug(f"Temporary file event: {event_type} - {file_path}")
        
        if self._is_office_temp_file(file_path):
            self.logger.debug(f"🔍 Office temporary file detected: {file_path}")
            self._track_office_temp_file(file_path)
        
        if self._is_cad_temp_file(file_path):
            self.logger.debug(f"🔍 CAD temporary file detected: {file_path}")
            self._track_cad_temp_file(file_path)
        
        if event_type == 'moved' and dest_path:
            dest_category = self.file_validator.get_file_category(dest_path)
            self.logger.debug(f"Temporary file moved: {file_path} -> {dest_path} (dest category: {dest_category})")
            
            if dest_category == 'MAIN':
                self.logger.info(f"🔄 Temporary -> Main file operation detected: {file_path} -> {dest_path}")
                self.temp_to_main_map[file_path] = dest_path
                
                if self._is_office_temp_file(file_path):
                    return self._handle_office_temp_to_main(file_path, dest_path)
        
        return True

    def _handle_office_file_creation(self, file_path: str, username: str) -> bool:
        """Обрабатывает создание нового Office файла с поддержкой многопользовательской работы"""
        self.logger.info(f"📄 Office file creation detected: {file_path}")
        
        # Ждем немного чтобы убедиться что это не временный файл
        time.sleep(0.5)
        
        if not os.path.exists(file_path):
            self.logger.debug(f"Office creation file disappeared: {file_path}")
            return True
            
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(file_path)
        primary_editor = self._determine_primary_editor(file_path, username, current_editors)
        
        # Вычисляем хеш
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
        
        # Создаем сессию для нового Office файла
        session_data = self.session_manager.smart_create_session(file_path, primary_editor, file_hash)
        
        # ДОБАВЛЕНО: Добавляем информацию о со-редакторах
        if len(current_editors) > 1:
            session_data['co_editors'] = [editor for editor in current_editors if editor != primary_editor]
            session_data['is_multi_user'] = True
        
        self.stats['office_operations_handled'] += 1
        self.stats['sessions_created'] += 1
        
        event_data = {
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'event_type': 'created',
            'file_hash': file_hash,
            'user_id': primary_editor,
            'session_id': session_data['session_id'],
            'resume_count': session_data.get('resume_count', 0),
            'is_office_creation': True,
            'is_multi_user': len(current_editors) > 1,
            'co_editors': [editor for editor in current_editors if editor != primary_editor],
            'source': 'server_agent',
            'event_timestamp': datetime.now().isoformat()
        }
        
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send Office created event for {file_path}")
        return success

    def _handle_cad_file_operation(self, file_path: str, username: str, event_type: str) -> bool:
        """Обрабатывает операции с CAD файлами с поддержкой многопользовательской работы"""
        self.logger.info(f"🏗️ CAD file {event_type}: {file_path}")
        
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(file_path)
        primary_editor = self._determine_primary_editor(file_path, username, current_editors)
        
        # Вычисляем хеш если нужно
        file_hash = None
        if (self.config.get('hashing', {}).get('enabled', True) and
            os.path.exists(file_path)):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
        
        if event_type == 'created':
            session_data = self.session_manager.smart_create_session(file_path, primary_editor, file_hash)
            
            # ДОБАВЛЕНО: Добавляем информацию о со-редакторах
            if len(current_editors) > 1:
                session_data['co_editors'] = [editor for editor in current_editors if editor != primary_editor]
                session_data['is_multi_user'] = True
            
            self.stats['cad_operations_handled'] += 1
            self.stats['sessions_created'] += 1
            
            event_data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'event_type': 'created',
                'file_hash': file_hash,
                'user_id': primary_editor,
                'session_id': session_data['session_id'],
                'resume_count': session_data.get('resume_count', 0),
                'is_cad_file': True,
                'is_multi_user': len(current_editors) > 1,
                'co_editors': [editor for editor in current_editors if editor != primary_editor],
                'source': 'server_agent',
                'event_timestamp': datetime.now().isoformat()
            }
        elif event_type == 'modified':
            session_data = self.session_manager.smart_create_session(file_path, primary_editor, file_hash)
            
            # ДОБАВЛЕНО: Добавляем информацию о со-редакторах
            if len(current_editors) > 1:
                session_data['co_editors'] = [editor for editor in current_editors if editor != primary_editor]
                session_data['is_multi_user'] = True
            
            event_data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'event_type': 'modified',
                'file_hash': file_hash,
                'user_id': primary_editor,
                'session_id': session_data['session_id'],
                'resume_count': session_data.get('resume_count', 0),
                'is_cad_file': True,
                'is_multi_user': len(current_editors) > 1,
                'co_editors': [editor for editor in current_editors if editor != primary_editor],
                'source': 'server_agent',
                'event_timestamp': datetime.now().isoformat()
            }
        else:
            return False
            
        success = self.api_client.send_event(event_data)
        if not success:
            self.logger.error(f"Failed to send CAD {event_type} event for {file_path}")
        return success

    def _is_office_creation_operation(self, file_path: str) -> bool:
        """Определяет является ли файл частью операции создания Office документа"""
        filename = os.path.basename(file_path).lower()
        
        office_default_names = [
            'новый документ microsoft word.docx',
            'новый документ microsoft word.doc',
            'новая книга microsoft excel.xlsx', 
            'новая книга microsoft excel.xls',
            'новая презентация microsoft powerpoint.pptx',
            'новая презентация microsoft powerpoint.ppt',
            'document.docx',
            'document.doc',
            'workbook.xlsx',
            'workbook.xls',
            'presentation.pptx',
            'presentation.ppt',
            'лист microsoft excel.xlsx',
            'лист microsoft excel.xls',
            'документ microsoft word.docx',
            'документ microsoft word.doc'
        ]
        
        if filename in office_default_names:
            return True
            
        office_patterns = [
            'новый ',
            'новая ',
            'new ',
            'document',
            'workbook', 
            'presentation',
            'лист ',
            'документ '
        ]
        
        return any(pattern in filename for pattern in office_patterns)

    def _is_office_temp_file(self, file_path: str) -> bool:
        """Определяет является ли файл временным файлом Office"""
        filename = os.path.basename(file_path)
        
        office_temp_patterns = [
            '~$', '~wr', '~wrd', '~wrl', '~rf', '.tmp'
        ]
        
        name_without_ext = os.path.splitext(filename)[0]
        if len(name_without_ext) == 4 and all(c in '0123456789ABCDEF' for c in name_without_ext.upper()):
            return True
            
        if len(name_without_ext) == 8 and all(c in '0123456789ABCDEF' for c in name_without_ext.upper()):
            return True
        
        return any(pattern in filename for pattern in office_temp_patterns)

    def _is_cad_operation(self, file_path: str) -> bool:
        """Определяет является ли файл частью CAD операции"""
        file_ext = os.path.splitext(file_path)[1].lower()
        cad_extensions = ['.dwg', '.dxf', '.dgn', '.rvt', '.rfa', '.rte', '.sat', '.ipt', '.iam', '.prt', '.asm', '.sldprt', '.sldasm', '.3dm', '.skp', '.max', '.blend']
        return file_ext in cad_extensions

    def _is_cad_temp_file(self, file_path: str) -> bool:
        """Определяет является ли файл временным файлом CAD"""
        filename = os.path.basename(file_path)
        cad_temp_patterns = ['.dwl', '.dwl2', '.sv$', '.autosave', '.bak', '.lock']
        return any(pattern in filename for pattern in cad_temp_patterns)

    def _track_office_temp_file(self, file_path: str):
        """Отслеживает временный файл Office для последующей обработки"""
        self.office_creation_operations[file_path] = {
            'detected_at': datetime.now(),
            'type': 'office_temp'
        }

    def _track_cad_temp_file(self, file_path: str):
        """Отслеживает временный файл CAD"""
        dir_path = os.path.dirname(file_path)
        for file in os.listdir(dir_path):
            if self._is_cad_operation(file):
                main_file = os.path.join(dir_path, file)
                self.cad_temp_files[file_path] = main_file
                self.logger.debug(f"🔗 Linked CAD temp file {file_path} to {main_file}")
                break

    def _handle_office_temp_to_main(self, temp_path: str, main_path: str) -> bool:
        """Обрабатывает перемещение временного Office файла в основной"""
        self.logger.info(f"🔄 Office temp to main: {temp_path} -> {main_path}")
        
        username = self._get_file_modifier_safe(main_path, 'moved')
        normalized_username = self._normalize_username(username)
        
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(main_path)
        primary_editor = self._determine_primary_editor(main_path, normalized_username, current_editors)
        
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(main_path)
        
        # Переносим сессию с временного файла на основной
        old_session = self.session_manager.get_active_session(temp_path, primary_editor)
        if old_session:
            transferred_session = self.session_manager.transfer_session(
                temp_path, main_path, primary_editor, file_hash
            )
            if transferred_session:
                self.logger.info(f"✅ Transferred Office session from {temp_path} to {main_path}")
        
        # Очищаем отслеживание
        if temp_path in self.office_creation_operations:
            del self.office_creation_operations[temp_path]
        if temp_path in self.temp_to_main_map:
            del self.temp_to_main_map[temp_path]
            
        return self._send_moved_event(temp_path, main_path, primary_editor, file_hash, current_editors)

    def _classify_move_operation(self, src_path: str, dest_path: str, src_category: str, dest_category: str) -> str:
        """Классифицирует тип операции перемещения"""
        if src_category == 'TEMPORARY' and dest_category == 'TEMPORARY':
            return 'TEMP_TO_TEMP'
        elif src_category == 'MAIN' and dest_category == 'TEMPORARY':
            return 'MAIN_TO_TEMP'
        elif src_category == 'TEMPORARY' and dest_category == 'MAIN':
            return 'TEMP_TO_MAIN'
        elif src_category == 'MAIN' and dest_category == 'MAIN':
            return 'MAIN_TO_MAIN'
        elif src_category == 'TEMPORARY' and dest_category == 'IGNORE':
            return 'TEMP_TO_IGNORE'
        elif src_category == 'IGNORE' and dest_category == 'MAIN':
            return 'IGNORE_TO_MAIN'
        elif src_category == 'IGNORE' and dest_category == 'TEMPORARY':
            return 'IGNORE_TO_TEMP'
        else:
            return 'UNKNOWN'

    def _handle_temp_to_main_move(self, src_path: str, dest_path: str) -> bool:
        """Обрабатывает перемещение временного файла в основной"""
        main_file = self.temp_to_main_map.get(src_path)
        
        username = self._get_file_modifier_safe(dest_path, 'moved')
        normalized_username = self._normalize_username(username)
        
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(dest_path)
        primary_editor = self._determine_primary_editor(dest_path, normalized_username, current_editors)
        
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(dest_path)
        
        if main_file:
            old_session = self.session_manager.get_active_session(main_file, primary_editor)
            if old_session:
                transferred_session = self.session_manager.transfer_session(
                    main_file, dest_path, primary_editor, file_hash
                )
                if transferred_session:
                    self.logger.info(f"🔄 Transferred session from main file {main_file} to {dest_path}")
                    if src_path in self.temp_to_main_map:
                        del self.temp_to_main_map[src_path]
                    if main_file in self.main_file_tracking:
                        del self.main_file_tracking[main_file]
                    
                    return self._send_moved_event(src_path, dest_path, primary_editor, file_hash, current_editors)
        
        self.session_manager.smart_create_session(dest_path, primary_editor, file_hash)
        self.logger.info(f"✅ Created new session for moved file {dest_path}")
        
        if src_path in self.temp_to_main_map:
            del self.temp_to_main_map[src_path]
            
        return self._send_moved_event(src_path, dest_path, primary_editor, file_hash, current_editors)

    def _handle_main_to_main_move(self, src_path: str, dest_path: str) -> bool:
        """Обрабатывает перемещение основного файла в основной"""
        username = self._get_file_modifier_safe(dest_path, 'moved')
        normalized_username = self._normalize_username(username)
        
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(dest_path)
        primary_editor = self._determine_primary_editor(dest_path, normalized_username, current_editors)
        
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(dest_path)
        
        session_transferred = False
        old_session = self.session_manager.get_active_session(src_path, primary_editor)
        
        if old_session:
            transferred_session = self.session_manager.transfer_session(
                src_path, dest_path, primary_editor, file_hash
            )
            if transferred_session:
                session_transferred = True
                self.logger.info(f"✅ Transferred session from {src_path} to {dest_path}")
        
        if not session_transferred:
            self.session_manager.smart_create_session(dest_path, primary_editor, file_hash)
            self.logger.info(f"✅ Created new session for moved file {dest_path}")
        
        self.file_renames[src_path] = dest_path
        self.file_move_chains[src_path] = dest_path
        
        return self._send_moved_event(src_path, dest_path, primary_editor, file_hash, current_editors)

    def _handle_ignore_to_main_move(self, src_path: str, dest_path: str) -> bool:
        """Обрабатывает перемещение игнорируемого файла в основной (типично для Excel)"""
        username = self._get_file_modifier_safe(dest_path, 'moved')
        normalized_username = self._normalize_username(username)
        
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(dest_path)
        primary_editor = self._determine_primary_editor(dest_path, normalized_username, current_editors)
        
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(dest_path)
        
        main_file = self._find_related_main_file(src_path, dest_path)
        
        if main_file:
            old_session = self.session_manager.get_active_session(main_file, primary_editor)
            if old_session:
                transferred_session = self.session_manager.transfer_session(
                    main_file, dest_path, primary_editor, file_hash
                )
                if transferred_session:
                    self.logger.info(f"🔄 Transferred session from related file {main_file} to {dest_path}")
                    return self._send_moved_event(src_path, dest_path, primary_editor, file_hash, current_editors)
        
        self.session_manager.smart_create_session(dest_path, primary_editor, file_hash)
        self.logger.info(f"✅ Created new session for Excel file {dest_path}")
        
        return self._send_moved_event(src_path, dest_path, primary_editor, file_hash, current_editors)

    def _handle_unknown_move_operation(self, src_path: str, dest_path: str, src_category: str, dest_category: str) -> bool:
        """Обрабатывает неизвестные операции перемещения на основе эвристик"""
        username = self._get_file_modifier_safe(dest_path, 'moved')
        normalized_username = self._normalize_username(username)
        
        # Получаем текущих редакторов
        current_editors = self._get_current_editors(dest_path)
        primary_editor = self._determine_primary_editor(dest_path, normalized_username, current_editors)
        
        file_hash = None
        if self.config.get('hashing', {}).get('enabled', True):
            file_hash = self.hash_calculator.calculate_file_hash_with_retry(dest_path)
        
        if dest_category == 'MAIN':
            related_file = self._find_related_main_file(src_path, dest_path)
            if related_file:
                old_session = self.session_manager.get_active_session(related_file, primary_editor)
                if old_session:
                    self.session_manager.transfer_session(related_file, dest_path, primary_editor, file_hash)
                    self.logger.info(f"🔄 Transferred session from related file {related_file} to {dest_path}")
                else:
                    self.session_manager.smart_create_session(dest_path, primary_editor, file_hash)
                    self.logger.info(f"✅ Created new session for {dest_path}")
            else:
                self.session_manager.smart_create_session(dest_path, primary_editor, file_hash)
                self.logger.info(f"✅ Created new session for {dest_path}")
        
        return self._send_moved_event(src_path, dest_path, primary_editor, file_hash, current_editors)

    def _find_related_main_file(self, src_path: str, dest_path: str) -> Optional[str]:
        """Находит связанный основной файл по имени или пути"""
        src_name = os.path.basename(src_path)
        dest_name = os.path.basename(dest_path)
        
        for chain_src, chain_dest in self.file_move_chains.items():
            if chain_dest == src_path:
                chain_category = self.file_validator.get_file_category(chain_src)
                if chain_category == 'MAIN':
                    return chain_src
        
        for temp_file, main_file in self.temp_to_main_map.items():
            if temp_file == src_path:
                return main_file
        
        if src_name.isalnum() and len(src_name) == 8:
            dir_path = os.path.dirname(dest_path)
            for known_main in self.main_file_tracking.keys():
                if os.path.dirname(known_main) == dir_path:
                    return known_main
        
        return None

    def _normalize_username(self, username: str) -> str:
        """Нормализует имя пользователя к единому формату"""
        if not username:
            return getpass.getuser()
        
        if '\\' in username:
            parts = username.split('\\')
            normalized = parts[-1]
            self.logger.debug(f"Normalized username: {username} -> {normalized}")
            return normalized
        
        return username

    def _should_process_event(self, file_path: str, event_type: str) -> bool:
        """Определяет нужно ли обрабатывать событие (фильтр частых событий)"""
        current_time = datetime.now()
        event_key = f"{file_path}:{event_type}"
        
        if event_type in ('deleted', 'moved'):
            return True
            
        if event_key in self.recent_events:
            time_since_last = (current_time - self.recent_events[event_key]).total_seconds()
            if time_since_last < self.event_cooldown:
                return False
        
        self.recent_events[event_key] = current_time
        
        old_entries = []
        for key, event_time in self.recent_events.items():
            if (current_time - event_time).total_seconds() > 10:
                old_entries.append(key)
        
        for key in old_entries:
            del self.recent_events[key]
            
        return True

    def _is_file_really_opened(self, file_path: str) -> bool:
        """Проверяет, действительно ли файл открыт в каком-либо процессе"""
        if not psutil:
            self.logger.debug("psutil not available, assuming file is opened")
            return True
            
        try:
            processes = self._get_processes_using_file(file_path)
            is_opened = len(processes) > 0
            
            if is_opened:
                self.verified_open_files.add(file_path)
                self.logger.debug(f"✅ File is really opened: {file_path} by {len(processes)} processes")
            else:
                if file_path in self.verified_open_files:
                    self.verified_open_files.remove(file_path)
                    self.logger.debug(f"📁 File no longer opened: {file_path}")
            
            return is_opened
            
        except Exception as e:
            self.logger.debug(f"Error checking if file is opened: {e}")
            return True

    def _get_processes_using_file(self, file_path: str) -> list:
        """Возвращает список процессов, использующих файл"""
        if not psutil:
            return []
            
        processes = []
        try:
            normalized_path = os.path.normpath(file_path).lower()
            
            for proc in psutil.process_iter(['pid', 'name', 'username', 'open_files']):
                try:
                    open_files = proc.info.get('open_files')
                    if open_files is None:
                        continue
                        
                    for file in open_files:
                        open_file_path = os.path.normpath(file.path).lower()
                        if open_file_path == normalized_path:
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

    def _update_open_file_tracking(self, file_path: str, username: str, event_type: str):
        """Обновляет информацию об открытых файлах"""
        if not psutil:
            return
            
        try:
            current_processes = self._get_processes_using_file(file_path)
            current_time = datetime.now()
            
            if current_processes:
                self.open_files[file_path] = {
                    'username': username,
                    'processes': current_processes,
                    'last_activity': current_time,
                    'last_checked': current_time,
                    'event_type': event_type
                }
                self.logger.debug(f"File {file_path} is open in {len(current_processes)} processes")
            else:
                if file_path in self.open_files:
                    file_info = self.open_files[file_path]
                    time_since_last_activity = current_time - file_info['last_activity']
                    
                    if time_since_last_activity > timedelta(seconds=5):
                        self.logger.info(f"File {file_path} is no longer open, closing session")
                        
                        file_hash = None
                        if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                            file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
                        
                        self._handle_file_closed(file_path, file_info['username'], file_hash)
                        del self.open_files[file_path]
                        self.stats['files_closed'] += 1
                    else:
                        self.open_files[file_path]['last_checked'] = current_time
                        
        except Exception as e:
            self.logger.error(f"Error updating open file tracking for {file_path}: {e}")

    def _handle_file_closed(self, file_path: str, username: str, file_hash: str) -> bool:
        """Обрабатывает закрытие файла"""
        self.logger.info(f"File closed: {file_path} by {username}")
        
        # Получаем текущих редакторов для определения основного
        current_editors = self._get_current_editors(file_path)
        primary_editor = self._determine_primary_editor(file_path, username, current_editors)
        
        session_data = self.session_manager.close_session(file_path, primary_editor, file_hash)
        
        if session_data:
            if 'ended_at' not in session_data or session_data['ended_at'] is None:
                self.logger.error(f"❌ Session closed but ended_at is not set for {file_path}")
                session_data['ended_at'] = datetime.now()
                self.logger.info(f"✅ Manually set ended_at to: {session_data['ended_at']}")
            
            session_duration = (session_data['ended_at'] - session_data['started_at']).total_seconds()
            
            event_data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'event_type': 'closed',
                'file_hash': file_hash,
                'user_id': primary_editor,
                'session_id': session_data['session_id'],
                'resume_count': session_data.get('resume_count', 0),
                'session_duration': session_duration,
                'is_multi_user': len(current_editors) > 1,
                'co_editors': [editor for editor in current_editors if editor != primary_editor],
                'source': 'server_agent',
                'event_timestamp': session_data['ended_at'].isoformat()
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
                current_processes = self._get_processes_using_file(file_path)
                
                if not current_processes:
                    file_info = self.open_files[file_path]
                    time_since_last_activity = current_time - file_info['last_activity']
                    
                    if time_since_last_activity > timedelta(seconds=5):
                        files_to_close.append((file_path, file_info))
                    else:
                        file_info['last_checked'] = current_time
                else:
                    file_info['processes'] = current_processes
                    file_info['last_checked'] = current_time
            
            for file_path, file_info in files_to_close:
                self.logger.info(f"Detected file closure: {file_path}")
                
                file_hash = None
                if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                    file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
                
                self._handle_file_closed(file_path, file_info['username'], file_hash)
                del self.open_files[file_path]
                self.stats['files_closed'] += 1
                
        except Exception as e:
            self.logger.error(f"Error checking open files: {e}")

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
                
                if 'ended_at' not in session_data or session_data['ended_at'] is None:
                    self.logger.error(f"❌ Session closed but ended_at is None for: {file_path}")
                    continue
                
                file_hash = None
                if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                    file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
                
                # Получаем текущих редакторов для события closed
                current_editors = self._get_current_editors(file_path)
                primary_editor = self._determine_primary_editor(file_path, username, current_editors)
                
                event_data = {
                    'file_path': file_path,
                    'file_name': session_data.get('file_name', os.path.basename(file_path)),
                    'event_type': 'closed',
                    'file_hash': file_hash,
                    'user_id': primary_editor,
                    'session_id': session_data['session_id'],
                    'resume_count': session_data.get('resume_count', 0),
                    'session_duration': (session_data['ended_at'] - session_data['started_at']).total_seconds(),
                    'is_multi_user': len(current_editors) > 1,
                    'co_editors': [editor for editor in current_editors if editor != primary_editor],
                    'source': 'server_agent',
                    'event_timestamp': session_data['ended_at'].isoformat()
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
                    
                    # Получаем текущих редакторов для события deleted
                    current_editors = self._get_current_editors(file_path)
                    primary_editor = self._determine_primary_editor(file_path, username, current_editors)
                    
                    event_data = {
                        'file_path': file_path,
                        'file_name': os.path.basename(file_path),
                        'event_type': 'deleted',
                        'user_id': primary_editor,
                        'session_id': closed_session['session_id'],
                        'resume_count': closed_session.get('resume_count', 0),
                        'is_multi_user': len(current_editors) > 1,
                        'co_editors': [editor for editor in current_editors if editor != primary_editor],
                        'source': 'server_agent',
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
            'open_files_tracking': len(self.open_files),
            'file_move_chains': len(self.file_move_chains),
            'verified_open_files': len(self.verified_open_files),
            'temp_to_main_mappings': len(self.temp_to_main_map),
            'main_files_tracked': len(self.main_file_tracking),
            'office_operations': len(self.office_creation_operations),
            'cad_temp_files': len(self.cad_temp_files),
            'multi_user_files': len([f for f, editors in self.file_editors.items() if len(editors.get('co_editors', set())) > 0])
        }

    def cleanup(self):
        """Очищает ресурсы"""
        expired_sessions = self.session_manager.cleanup_expired_sessions(self)
        for session_data in expired_sessions:
            file_path = session_data['file_path']
            username = session_data['username']
            file_hash = None
            if os.path.exists(file_path) and self.config.get('hashing', {}).get('enabled', True):
                file_hash = self.hash_calculator.calculate_file_hash_with_retry(file_path)
            self._handle_file_closed(file_path, username, file_hash)
        
        self.check_open_files()
        self.cleanup_orphaned_sessions()