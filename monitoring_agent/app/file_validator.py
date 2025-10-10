import os
import fnmatch
import re
from typing import List
from shared.logger import setup_logger

class FileValidator:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logger(__name__)
        # Компилируем паттерны для лучшей производительности
        self.ignore_patterns = self.config.get('ignore_patterns', [])
        self.ignore_extensions = self.config.get('ignore_extensions', [])
        self.ignore_dirs = self.config.get('ignore_dirs', [])
    
    def should_monitor_file(self, file_path: str) -> bool:
        """Определяет нужно ли отслеживать файл"""
        if not os.path.isfile(file_path):
            return False
        
        filename = os.path.basename(file_path)
        file_ext = os.path.splitext(filename)[1].lower()
        
        # Проверяем игнорируемые паттерны (с улучшенной логикой)
        if self._matches_ignore_patterns(filename, file_path):
            self.logger.debug(f"Ignoring file due to pattern: {filename}")
            return False
        
        # Проверяем игнорируемые расширения
        if file_ext in self.ignore_extensions:
            self.logger.debug(f"Ignoring file due to extension: {filename}")
            return False
        
        # Проверяем разрешенные расширения
        allowed_extensions = self.config.get('file_extensions', [])
        if allowed_extensions and file_ext not in allowed_extensions:
            self.logger.debug(f"Ignoring file - extension not in allowed list: {filename}")
            return False
        
        # Проверяем игнорируемые директории в пути
        if self._contains_ignore_dirs(file_path):
            self.logger.debug(f"Ignoring file in excluded directory: {file_path}")
            return False
        
        return True
    
    def should_monitor_file_by_name(self, file_path: str) -> bool:
        """Определяет нужно ли отслеживать файл только по имени (когда файл уже удален)"""
        filename = os.path.basename(file_path)
        
        # Проверяем игнорируемые паттерны
        if self._matches_ignore_patterns(filename, file_path):
            self.logger.debug(f"Ignoring deleted file due to pattern: {filename}")
            return False
        
        # Проверяем игнорируемые расширения
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext in self.ignore_extensions:
            self.logger.debug(f"Ignoring deleted file due to extension: {filename}")
            return False
        
        # Проверяем разрешенные расширения
        allowed_extensions = self.config.get('file_extensions', [])
        if allowed_extensions and file_ext not in allowed_extensions:
            self.logger.debug(f"Ignoring deleted file - extension not in allowed list: {filename}")
            return False
        
        # Проверяем игнорируемые директории в пути
        if self._contains_ignore_dirs(file_path):
            self.logger.debug(f"Ignoring deleted file in excluded directory: {file_path}")
            return False
        
        return True
    
    def _matches_ignore_patterns(self, filename: str, file_path: str) -> bool:
        """Проверяет совпадение с игнорируемыми паттернами"""
        for pattern in self.ignore_patterns:
            # Для паттернов, начинающихся с * (например, "*.tmp")
            if pattern.startswith('*') and filename.endswith(pattern[1:]):
                return True
            # Для паттернов, заканчивающихся на * (например, "~*")
            elif pattern.endswith('*') and filename.startswith(pattern[:-1]):
                return True
            # Для паттернов с * в середине
            elif '*' in pattern:
                if fnmatch.fnmatch(filename, pattern):
                    return True
            # Точное совпадение
            elif filename == pattern:
                return True
            # Проверка по регулярному выражению для сложных паттернов
            elif self._is_regex_match(filename, pattern):
                return True
        
        return False
    
    def _is_regex_match(self, filename: str, pattern: str) -> bool:
        """Проверяет совпадение с использованием упрощенных regex-паттернов"""
        try:
            # Преобразуем fnmatch-паттерн в regex
            regex_pattern = fnmatch.translate(pattern)
            return re.match(regex_pattern, filename) is not None
        except re.error:
            return False
    
    def _contains_ignore_dirs(self, file_path: str) -> bool:
        """Проверяет содержит ли путь игнорируемые директории"""
        if not self.ignore_dirs:
            return False
        
        # Нормализуем путь для кроссплатформенности
        normalized_path = os.path.normpath(file_path).lower()
        path_parts = normalized_path.split(os.sep)
        
        for ignore_dir in self.ignore_dirs:
            ignore_dir_normalized = os.path.normpath(ignore_dir).lower()
            # Проверяем, содержится ли игнорируемая директория в пути
            if ignore_dir_normalized in path_parts:
                return True
        
        return False
    
    def get_monitorable_files(self, directory: str) -> List[str]:
        """Возвращает список файлов для мониторинга в директории"""
        monitorable_files = []
        
        try:
            for root, dirs, files in os.walk(directory):
                # Исключаем игнорируемые директории из дальнейшего обхода
                dirs[:] = [d for d in dirs if not self._should_ignore_dir(os.path.join(root, d))]
                
                for file in files:
                    file_path = os.path.join(root, file)
                    if self.should_monitor_file(file_path):
                        monitorable_files.append(file_path)
                        
        except Exception as e:
            self.logger.error(f"Error scanning directory {directory}: {e}")
        
        return monitorable_files
    
    def _should_ignore_dir(self, dir_path: str) -> bool:
        """Определяет нужно ли игнорировать директорию"""
        dir_name = os.path.basename(dir_path)
        
        # Проверяем паттерны имен директорий
        for pattern in self.ignore_patterns:
            if self._matches_ignore_patterns(dir_name, dir_path):
                return True
        
        # Проверяем конкретные директории из ignore_dirs
        if self.ignore_dirs:
            normalized_path = os.path.normpath(dir_path).lower()
            
            for ignore_dir in self.ignore_dirs:
                ignore_dir_normalized = os.path.normpath(ignore_dir).lower()
                # Проверяем точное совпадение имени директории
                if ignore_dir_normalized == dir_name.lower():
                    return True
                # Проверяем вхождение в путь
                if ignore_dir_normalized in normalized_path.split(os.sep):
                    return True
        
        return False