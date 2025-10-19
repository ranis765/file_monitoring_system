import os
import fnmatch
import re
from typing import List
from shared.logger import setup_logger

class FileValidator:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logger(__name__)

        # ОГРАНИЧЕНИЕ РАЗМЕРА КЭША
        self._category_cache = {}
        self._max_cache_size = 1000  # Максимальный размер кэша
        self._cache_hits = 0
        self._cache_misses = 0
        
        # УНИВЕРСАЛЬНАЯ КЛАССИФИКАЦИЯ ФАЙЛОВ - РАСШИРЕННАЯ ВЕРСИЯ
        self.FILE_CATEGORIES = {
            # ОСНОВНЫЕ ФАЙЛЫ - отслеживаем сессии
            'MAIN': [
                # Office
                '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.rtf',
                # PDF и текстовые
                '.pdf', '.txt', '.md',
                # OpenDocument
                '.odt', '.ods', '.odp',
                # CAD системы
                '.dwg', '.dxf', '.dgn', '.rvt', '.rfa', '.rte', '.sat', '.ipt', '.iam', '.prt', '.asm', '.sldprt', '.sldasm',
                '.3dm', '.skp', '.max', '.blend', '.mb', '.ma',
                # Credo и геодезические системы
                '.crproj', '.credoproj', '.gpx', '.kml', '.kmz',
                # Архивы и образы
                '.zip', '.rar', '.7z', '.iso',
                # Медиа (для полноты)
                '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'
            ],
            
            # ВРЕМЕННЫЕ ФАЙЛЫ - игнорируем для сессий, но отслеживаем для контекста
            'TEMPORARY': [
                # Общие временные
                '.tmp', '.temp', '.crdownload', '.part',
                # Office временные
                '~$', '~wr', '~wrd', '~wrl', '~rf',
                # CAD временные и backup
                '.bak', '.dwl', '.dwl2', '.sv$', '.autosave',
                # Системные временные
                '.lock', '.lck'
            ],
            
            # ПОЛНОСТЬЮ ИГНОРИРУЕМЫЕ ФАЙЛЫ - даже не обрабатываем события
            'IGNORE': [
                '.log', '.cache', '.DS_Store', '.thumb', '.thumbs',
                'desktop.ini', '.tmp.metadata'
            ]
        }
        
        # ДОБАВЛЕНО: Стандартные имена Office файлов для специальной обработки
        self.OFFICE_DEFAULT_NAMES = [
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
        
        # Компилируем паттерны для лучшей производительности
        self.ignore_patterns = self.config.get('ignore_patterns', [])
        self.ignore_extensions = self.config.get('ignore_extensions', [])
        self.ignore_dirs = self.config.get('ignore_dirs', [])
        
        # ДОБАВЛЕНО: Кэш для быстрого определения категорий
        self._category_cache = {}
        
        # ДОБАВЛЕНО: Расширенные паттерны для временных файлов
        self._extended_temp_patterns = [
            # Файлы без расширения с HEX-именами (типичные временные файлы)
            r'^[0-9A-F]{4,16}$',
            r'^[0-9A-F]{4,16}\.tmp$',
            r'^[0-9A-F]{4,16}\.temp$',
            # Файлы с короткими именами (часто временные)
            r'^[A-Z0-9]{4,8}$',
            r'^[A-Z0-9]{4,8}\.tmp$',
        ]
        
        self.logger.info(f"✅ FileValidator initialized with {len(self.FILE_CATEGORIES['MAIN'])} main formats, "
                        f"{len(self.FILE_CATEGORIES['TEMPORARY'])} temporary patterns, "
                        f"{len(self.FILE_CATEGORIES['IGNORE'])} ignored patterns")

    def get_file_category(self, file_path: str) -> str:
        """Определяет категорию файла"""
        filename = os.path.basename(file_path)
        
        # ОЧИСТКА КЭША ПРИ ПРЕВЫШЕНИИ ЛИМИТА
        if len(self._category_cache) > self._max_cache_size:
            self._category_cache.clear()
            self.logger.debug("🧹 Cleared category cache (size limit exceeded)")
        
        if file_path in self._category_cache:
            self._cache_hits += 1
            return self._category_cache[file_path]
    
        self._cache_misses += 1
        
        
        # 1. Проверяем полностью игнорируемые файлы
        if self._is_ignored_file(filename):
            self._category_cache[file_path] = 'IGNORE'
            return 'IGNORE'
        
        # 2. Проверяем временные файлы (включая расширенные паттерны)
        if self._is_temporary_file(filename):
            self._category_cache[file_path] = 'TEMPORARY'
            return 'TEMPORARY'
        
        # 3. Проверяем основные файлы
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext in self.FILE_CATEGORIES['MAIN']:
            self._category_cache[file_path] = 'MAIN'
            return 'MAIN'
        
        # 4. Файл не подходит ни под одну категорию - считаем IGNORE
        self._category_cache[file_path] = 'IGNORE'
        return 'IGNORE'

    def is_office_default_name(self, file_path: str) -> bool:
        """Проверяет является ли файл стандартным именем Office"""
        filename = os.path.basename(file_path).lower()
        return filename in self.OFFICE_DEFAULT_NAMES

    def should_monitor_file(self, file_path: str) -> bool:
        """Определяет нужно ли отслеживать файл"""
        if not os.path.isfile(file_path):
            return False
        
        # Используем универсальную классификацию
        file_category = self.get_file_category(file_path)
        
        if file_category == 'IGNORE':
            self.logger.debug(f"🚫 Ignoring file (category: IGNORE): {os.path.basename(file_path)}")
            return False
        
        if file_category == 'TEMPORARY':
            self.logger.debug(f"⏰ Temporary file, monitoring for context: {os.path.basename(file_path)}")
            return True  # Отслеживаем для контекста, но не создаем сессии
        
        if file_category == 'MAIN':
            # Дополнительные проверки для основных файлов
            if not self._passes_additional_checks(file_path):
                return False
            return True
        
        return False

    def should_monitor_file_by_name(self, file_path: str) -> bool:
        """Определяет нужно ли отслеживать файл только по имени (когда файл уже удален)"""
        filename = os.path.basename(file_path)
        
        # Используем ту же логику, но без проверки существования файла
        file_category = self.get_file_category(file_path)
        
        if file_category == 'IGNORE':
            self.logger.debug(f"🚫 Ignoring deleted file (category: IGNORE): {filename}")
            return False
        
        if file_category == 'TEMPORARY':
            self.logger.debug(f"⏰ Deleted temporary file: {filename}")
            return True
        
        if file_category == 'MAIN':
            return True
        
        return False

    def _is_ignored_file(self, filename: str) -> bool:
        """Проверяет является ли файл полностью игнорируемым"""
        filename_lower = filename.lower()
        
        # Проверяем паттерны IGNORE
        for pattern in self.FILE_CATEGORIES['IGNORE']:
            if self._matches_pattern(filename_lower, pattern):
                return True
        
        # Проверяем пользовательские ignore_patterns
        for pattern in self.ignore_patterns:
            if self._matches_pattern(filename_lower, pattern):
                return True
        
        return False

    def _is_temporary_file(self, filename: str) -> bool:
        """Проверяет является ли файл временным - РАСШИРЕННАЯ ВЕРСИЯ"""
        filename_lower = filename.lower()
        
        # Проверяем базовые паттерны TEMPORARY
        for pattern in self.FILE_CATEGORIES['TEMPORARY']:
            if self._matches_pattern(filename_lower, pattern):
                return True
        
        # Проверяем расширенные паттерны для временных файлов
        for pattern in self._extended_temp_patterns:
            if re.match(pattern, filename, re.IGNORECASE):
                return True
        
        # Специфические паттерны для разных приложений
        specific_temp_patterns = [
            r'~wrl\d+\.tmp',      # Word
            r'~wrd\d+\.tmp',      # Word  
            r'~rf.*\.tmp',        # Excel
            r'.*\.tmp\..*',       # Файлы с двойными расширениями
            r'^~\$.*',            # Автосохранение Office
            # Дополнительные паттерны для Excel/Word временных файлов
            r'^[A-F0-9]{8}\.tmp$',  # E3327DC9.tmp и подобные
            r'^[A-F0-9]{8}$',       # C1EE4200 и подобные (без расширения)
        ]
        
        for pattern in specific_temp_patterns:
            if re.match(pattern, filename_lower):
                return True
        
        # Проверяем файлы без расширения с короткими именами (часто временные)
        name_without_ext = os.path.splitext(filename)[0]
        if '.' not in filename and len(name_without_ext) <= 8 and name_without_ext.isalnum():
            if name_without_ext.isupper() or all(c in '0123456789ABCDEF' for c in name_without_ext.upper()):
                return True
        
        return False

    def _matches_pattern(self, filename: str, pattern: str) -> bool:
        """Проверяет совпадение с паттерном"""
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
        # Проверка по регулярному выражению
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

    def _passes_additional_checks(self, file_path: str) -> bool:
        """Дополнительные проверки для основных файлов"""
        filename = os.path.basename(file_path)
        
        # Проверяем игнорируемые расширения из конфига
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext in self.ignore_extensions:
            self.logger.debug(f"Ignoring file due to extension: {filename}")
            return False
        
        # Проверяем игнорируемые директории в пути
        if self._contains_ignore_dirs(file_path):
            self.logger.debug(f"Ignoring file in excluded directory: {file_path}")
            return False
        
        # Проверяем размер файла (игнорируем слишком маленькие файлы)
        try:
            file_size = os.path.getsize(file_path)
            if file_size < 10:  # Игнорируем файлы меньше 10 байт
                self.logger.debug(f"Ignoring too small file: {filename} ({file_size} bytes)")
                return False
        except (OSError, ValueError):
            pass
        
        return True

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
            if self._matches_pattern(dir_name, dir_path):
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

    def clear_cache(self):
        """Очищает кэш категорий"""
        self._category_cache.clear()