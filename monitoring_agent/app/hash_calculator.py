# hash_calculator.py
import hashlib
import os
import time  # For retry
from typing import Optional
from shared.logger import setup_logger

class HashCalculator:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logger(__name__)
    
    def calculate_file_hash_with_retry(self, file_path: str, max_retries=3, delay=1) -> Optional[str]:
        """Вычисляет хеш файла с retry для locked файлов"""
        for attempt in range(max_retries):
            try:
                return self.calculate_file_hash(file_path)
            except Exception as e:
                if 'locked' in str(e) or 'permission' in str(e).lower():  # Rough check for locked
                    self.logger.warning(f"File {file_path} locked, retry {attempt+1}/{max_retries}")
                    time.sleep(delay)
                else:
                    raise
        return None
    
    def calculate_file_hash(self, file_path: str) -> Optional[str]:
        """Вычисляет хеш файла с учетом ограничений по размеру"""
        try:
            if not os.path.exists(file_path):
                return None
            
            file_size = os.path.getsize(file_path)
            max_size_mb = self.config.get('max_file_size_mb', 50)
            
            if file_size > max_size_mb * 1024 * 1024:
                # Для больших файлов используем частичное хеширование
                return self._calculate_partial_hash(file_path)
            else:
                # Для маленьких файлов - полное хеширование
                return self._calculate_full_hash(file_path)
                
        except Exception as e:
            self.logger.error(f"Error calculating hash for {file_path}: {e}")
            raise
    
    def _calculate_full_hash(self, file_path: str) -> str:
        """Вычисляет полный хеш файла"""
        hash_method = self.config.get('method', 'sha256')
        hasher = hashlib.new(hash_method)
        
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        
        return hasher.hexdigest()
    
    def _calculate_partial_hash(self, file_path: str) -> str:
        """Вычисляет частичный хеш для больших файлов"""
        hash_method = self.config.get('method', 'sha256')
        hasher = hashlib.new(hash_method)
        file_size = os.path.getsize(file_path)
        
        with open(file_path, 'rb') as f:
            # Хешируем начало файла (первые 64KB)
            hasher.update(f.read(65536))
            
            # Хешируем конец файла (последние 64KB)
            if file_size > 131072:
                f.seek(-65536, 2)  # Перемещаемся к концу файла
                hasher.update(f.read(65536))
            
            # Хешируем середину файла (64KB из середины)
            if file_size > 262144:
                f.seek(file_size // 2)
                hasher.update(f.read(65536))
        
        return hasher.hexdigest()