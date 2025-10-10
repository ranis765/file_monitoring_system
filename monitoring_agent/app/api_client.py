import requests
import time
import json
import os
from typing import Optional, Dict, Any
from shared.logger import setup_logger
from shared.config_loader import get_api_client_config

class APIClient:
    def __init__(self):
        self.config = get_api_client_config()
        self.base_url = self.config.get('base_url', 'http://localhost:8000')
        self.events_endpoint = self.config.get('events_endpoint', '/api/events')
        self.timeout = self.config.get('timeout', 10)
        self.retry_attempts = self.config.get('retry_attempts', 3)
        self.event_cache_file = self.config.get('event_cache_file', 'event_cache.json')
        self.logger = setup_logger(__name__)
        self.event_cache = self._load_event_cache()
        
        self.logger.info(f"API Client configured for: {self.base_url}")
    
    def _load_event_cache(self) -> list:
        """Загружает кэш событий из файла"""
        try:
            if os.path.exists(self.event_cache_file):
                with open(self.event_cache_file, 'r') as f:
                    return json.load(f)
            return []
        except Exception as e:
            self.logger.error(f"Failed to load event cache: {e}")
            return []
    
    def _save_event_cache(self):
        """Сохраняет кэш событий в файл"""
        try:
            with open(self.event_cache_file, 'w') as f:
                json.dump(self.event_cache, f)
        except Exception as e:
            self.logger.error(f"Failed to save event cache: {e}")
    
    def send_event(self, event_data: Dict[str, Any]) -> bool:
        """Отправляет событие на сервер с повторными попытками"""
        full_url = f"{self.base_url}{self.events_endpoint}"
        self.logger.debug(f"Sending event to {full_url}: {event_data}")
        
        for attempt in range(self.retry_attempts):
            try:
                response = requests.post(
                    full_url,
                    json=event_data,
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    self.logger.debug(f"Event sent successfully: {event_data.get('event_type')} for {event_data.get('file_path')}")
                    # Try sending cached events
                    self._retry_cached_events()
                    return True
                else:
                    self.logger.warning(f"API returned {response.status_code}: {response.text} for event: {event_data}")
                    
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Attempt {attempt + 1} failed: {e} for event: {event_data}")
                
            if attempt < self.retry_attempts - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
        
        self.logger.error(f"Failed to send event after {self.retry_attempts} attempts to {full_url}: {event_data}")
        # Cache the event
        self.event_cache.append(event_data)
        self._save_event_cache()
        return False
    
    def _retry_cached_events(self):
        """Пытается отправить кэшированные события"""
        if not self.event_cache:
            return
        
        full_url = f"{self.base_url}{self.events_endpoint}"
        remaining_events = []
        for event_data in self.event_cache:
            try:
                response = requests.post(
                    full_url,
                    json=event_data,
                    timeout=self.timeout
                )
                if response.status_code == 200:
                    self.logger.info(f"Successfully sent cached event: {event_data.get('event_type')} for {event_data.get('file_path')}")
                else:
                    self.logger.warning(f"Failed to send cached event: {response.status_code} {response.text}")
                    remaining_events.append(event_data)
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Failed to send cached event: {e}")
                remaining_events.append(event_data)
        
        self.event_cache = remaining_events
        self._save_event_cache()
    
    def create_file_session(self, session_data: Dict[str, Any]) -> Optional[str]:
        """Создает сессию на сервере и возвращает session_id"""
        full_url = f"{self.base_url}/api/sessions"
        try:
            response = requests.post(
                full_url,
                json=session_data,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get('id')
            else:
                self.logger.error(f"Failed to create session: {response.status_code} {response.text} for URL: {full_url}")
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error creating session: {e} for URL: {full_url}")
        
        return None
    
    def test_connection(self) -> bool:
        """Проверяет соединение с API"""
        full_url = f"{self.base_url}/health"
        try:
            response = requests.get(full_url, timeout=5)
            if response.status_code == 200:
                self.logger.info(f"API connection successful: {full_url}")
                # Try sending cached events on successful connection
                self._retry_cached_events()
                return True
            else:
                self.logger.error(f"API connection failed: {response.status_code} {response.text} for URL: {full_url}")
                return False
        except requests.exceptions.RequestException as e:
            self.logger.error(f"API connection failed: {e} for URL: {full_url}")
            return False