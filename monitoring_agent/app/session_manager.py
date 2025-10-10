import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from shared.logger import setup_logger

class SessionManager:
    def __init__(self):
        self.active_sessions: Dict[str, Dict] = {}  # file_path:username -> session_data
        self.closed_sessions: Dict[str, List[Dict]] = {}  # История закрытых сессий
        self.logger = setup_logger(__name__)
        self.config = {}
    
    def set_config(self, config: dict):
        """Устанавливает конфигурацию"""
        self.config = config
        timeout = self.config.get('session_timeout_minutes', 1)
        self.logger.info(f"⚙️ Session config: timeout={timeout}min, max_age={self.config.get('max_session_hours', 3)}h")
    
    def _get_session_key(self, file_path: str, username: str) -> str:
        """Генерирует ключ сессии"""
        return f"{file_path}:{username}"
    
    def _find_recently_closed(self, session_key: str, hours: int = 1) -> Optional[Dict]:
        """Находит недавно закрытую сессию для возможного возобновления"""
        if session_key not in self.closed_sessions:
            return None
        
        closed_sessions = self.closed_sessions[session_key]
        if not closed_sessions:
            return None
        
        # Берем последнюю закрытую сессию
        last_session = closed_sessions[-1]
        
        # Проверяем, закрыта ли она в пределах указанного времени
        if 'ended_at' in last_session and last_session['ended_at']:
            time_since_close = datetime.now() - last_session['ended_at']
            if time_since_close <= timedelta(hours=hours):
                return last_session
        
        return None
    
    def _resume_session(self, session_data: Dict, file_hash: str = None) -> Dict:
        """Возобновляет существующую сессию"""
        session_key = self._get_session_key(session_data['file_path'], session_data['username'])
        
        # Обновляем данные сессии
        resumed_session = session_data.copy()
        resumed_session['last_activity'] = datetime.now()
        resumed_session['resumed_at'] = datetime.now()
        resumed_session['resume_count'] = resumed_session.get('resume_count', 0) + 1
        resumed_session['hash_before'] = file_hash
        
        # Убираем поля окончания, т.к. сессия снова активна
        resumed_session.pop('ended_at', None)
        resumed_session.pop('hash_after', None)
        
        # Возвращаем в активные сессии
        self.active_sessions[session_key] = resumed_session
        
        # Удаляем из истории закрытых, если она там есть
        if session_key in self.closed_sessions and session_data in self.closed_sessions[session_key]:
            self.closed_sessions[session_key].remove(session_data)
        
        self.logger.info(f"🔄 Resumed session for {resumed_session['file_path']}")
        
        return resumed_session
    
    def get_active_session(self, file_path: str, username: str) -> Optional[Dict]:
        """Возвращает активную сессию для файла и пользователя"""
        session_key = self._get_session_key(file_path, username)
        session_data = self.active_sessions.get(session_key)
        
        if session_data:
            # Проверяем не истекла ли сессия
            if self._is_session_expired(session_data):
                # Сессия истекла - закрываем ее
                self.logger.info(f"🕒 Session expired, closing: {file_path}")
                self.close_session(file_path, username)
                return None
            
            # Обновляем время последней активности
            session_data['last_activity'] = datetime.now()
        
        return session_data
    
    def _is_session_expired(self, session_data: Dict) -> bool:
        """Проверяет истекла ли сессия"""
        timeout_minutes = self.config.get('session_timeout_minutes', 1)
        max_age_hours = self.config.get('max_session_hours', 3)
        
        last_activity = session_data['last_activity']
        session_age = datetime.now() - session_data['started_at']
        
        time_since_activity = datetime.now() - last_activity
        timeout_seconds = timeout_minutes * 60
        
        # Проверяем таймаут активности
        if time_since_activity.total_seconds() > timeout_seconds:
            self.logger.info(f"🕒 Session expired by timeout: {session_data['file_path']}, inactive for {time_since_activity.total_seconds():.1f}s > {timeout_seconds}s")
            return True
        
        # Проверяем максимальный возраст сессии
        if session_age.total_seconds() > (max_age_hours * 3600):
            self.logger.info(f"📅 Session expired by max age: {session_data['file_path']}, age: {session_age.total_seconds()/3600:.1f}h")
            return True
        
        return False
    
    def check_and_close_expired_sessions(self) -> List[Dict]:
        """Проверяет и закрывает все просроченные сессии - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        expired_sessions = []
        
        total_sessions = len(self.active_sessions)
        if total_sessions == 0:
            return expired_sessions
            
        self.logger.info(f"🔍 Checking {total_sessions} active sessions for expiration...")
        
        # Создаем копию списка для безопасной итерации
        sessions_to_check = list(self.active_sessions.items())
        
        for session_key, session_data in sessions_to_check:
            try:
                if self._is_session_expired(session_data):
                    file_path = session_data['file_path']
                    username = session_data['username']
                    
                    time_since_activity = datetime.now() - session_data['last_activity']
                    self.logger.info(f"🔒 Closing expired session: {file_path} (inactive: {time_since_activity.total_seconds():.1f}s)")
                    
                    # Закрываем сессию и получаем данные с ended_at
                    closed_data = self.close_session(file_path, username)
                    if closed_data:
                        expired_sessions.append(closed_data)
                        self.logger.info(f"✅ Session closed with ended_at: {closed_data['ended_at']}")
                        
            except Exception as e:
                self.logger.error(f"❌ Error checking session {session_key}: {e}")
        
        if expired_sessions:
            self.logger.info(f"✅ Closed {len(expired_sessions)} expired sessions")
        else:
            self.logger.debug(f"📊 All {total_sessions} sessions are active")
        
        return expired_sessions
    
    def smart_create_session(self, file_path: str, username: str, file_hash: str = None, resume_window_hours: int = 1) -> Dict:
        """Умное создание сессии с возможностью возобновления недавно закрытой сессии"""
        # Сначала проверяем активную сессию
        active_session = self.get_active_session(file_path, username)
        if active_session:
            return active_session
        
        # Пытаемся найти недавно закрытую сессию для возобновления
        session_key = self._get_session_key(file_path, username)
        recently_closed = self._find_recently_closed(session_key, resume_window_hours)
        
        if recently_closed:
            return self._resume_session(recently_closed, file_hash)
        else:
            return self.create_session(file_path, username, file_hash)
    
    def create_session(self, file_path: str, username: str, file_hash: str = None) -> Dict:
        """Создает новую сессию"""
        session_key = self._get_session_key(file_path, username)
        
        session_data = {
            'session_id': str(uuid.uuid4()),
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'username': username,
            'started_at': datetime.now(),
            'last_activity': datetime.now(),
            'hash_before': file_hash,
            'events': [],
            'resume_count': 0
        }
        
        self.active_sessions[session_key] = session_data
        self.logger.info(f"✅ Created session for {file_path}")
        
        return session_data
    
    def update_session(self, file_path: str, username: str, file_hash: str = None) -> Dict:
        """Обновляет существующую сессию или создает новую"""
        session_data = self.get_active_session(file_path, username)
        
        if session_data:
            # Обновляем существующую сессию
            if file_hash:
                session_data['hash_after'] = file_hash
            session_data['last_activity'] = datetime.now()
            self.logger.debug(f"📝 Updated session for {file_path}")
        else:
            # Создаем новую сессию
            session_data = self.create_session(file_path, username, file_hash)
        
        return session_data
    
    def close_session(self, file_path: str, username: str, file_hash: str = None) -> Optional[Dict]:
        """Закрывает сессию - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        session_key = self._get_session_key(file_path, username)
        session_data = self.active_sessions.pop(session_key, None)
        
        if session_data:
            # Устанавливаем время окончания - ВАЖНО: ДОБАВЛЕНО ВРЕМЯ ЗАКРЫТИЯ
            ended_at = datetime.now()
            session_data['ended_at'] = ended_at
            
            if file_hash:
                session_data['hash_after'] = file_hash
            
            # Сохраняем в историю закрытых сессий
            if session_key not in self.closed_sessions:
                self.closed_sessions[session_key] = []
            self.closed_sessions[session_key].append(session_data)
            
            # Ограничиваем историю
            if len(self.closed_sessions[session_key]) > 10:
                self.closed_sessions[session_key] = self.closed_sessions[session_key][-10:]
            
            # Логируем с информацией о времени сессии
            session_duration = ended_at - session_data['started_at']
            self.logger.info(f"🔒 Closed session for {file_path} (duration: {session_duration.total_seconds():.1f}s, ended_at: {ended_at})")
            
            return session_data
        else:
            self.logger.debug(f"❌ No active session found for: {file_path} (user: {username})")
            return None
    
    def close_all_sessions_for_file(self, file_path: str) -> List[Dict]:
        """Принудительно закрывает все сессии для указанного файла"""
        closed_sessions = []
        
        sessions_to_close = []
        for session_key, session_data in list(self.active_sessions.items()):
            if session_data['file_path'] == file_path:
                sessions_to_close.append((session_data['file_path'], session_data['username']))
        
        self.logger.info(f"🔍 Found {len(sessions_to_close)} sessions to close for: {file_path}")
        
        for file_path, username in sessions_to_close:
            session_data = self.close_session(file_path, username)
            if session_data:
                closed_sessions.append(session_data)
        
        return closed_sessions
    
    def cleanup_expired_sessions(self, event_handler) -> list:
        """Очищает просроченные сессии"""
        return self.check_and_close_expired_sessions()
    
    def get_session_stats(self) -> Dict:
        """Возвращает статистику по сессиям"""
        total_resumes = sum(session.get('resume_count', 0) for session in self.active_sessions.values())
        
        return {
            'active_sessions': len(self.active_sessions),
            'session_keys': list(self.active_sessions.keys()),
            'total_resumes': total_resumes,
            'closed_sessions_count': sum(len(sessions) for sessions in self.closed_sessions.values())
        }
    
    def get_session_history(self, file_path: str, username: str) -> List[Dict]:
        """Возвращает историю сессий для файла и пользователя"""
        session_key = self._get_session_key(file_path, username)
        return self.closed_sessions.get(session_key, [])