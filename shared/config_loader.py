import yaml
import os
from typing import Dict, Any

# Кеш для конфигурации
_config_cache = {}
_monitoring_config_cache = None
_api_config_cache = None
_api_client_config_cache = None

def get_project_root():
    """Возвращает корневую папку проекта"""
    # Определяем корень проекта по местоположению этого файла
    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    # shared/ находится в file_monitoring_system/shared/
    project_root = os.path.dirname(current_file_dir)
    return project_root

def get_config_path(config_file: str = "config.yaml") -> str:
    """Возвращает путь к файлу конфигурации"""
    project_root = get_project_root()
    config_path = os.path.join(project_root, "config", config_file)
    
    print(f"🔍 Looking for config at: {config_path}")
    print(f"📁 Config exists: {os.path.exists(config_path)}")
    
    if not os.path.exists(config_path):
        # Попробуем найти config.yaml в других местах
        possible_paths = [
            config_path,
            os.path.join(project_root, config_file),
            os.path.join(os.getcwd(), "config", config_file),
            os.path.join(os.getcwd(), config_file),
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                print(f"✅ Found config at: {path}")
                return path
        
        raise FileNotFoundError(f"Config file not found. Tried: {possible_paths}")
    
    return config_path

def load_config(config_file: str = "config.yaml") -> Dict[str, Any]:
    """Загружает конфигурацию из YAML файла с кешированием"""
    # Проверяем кеш
    if config_file in _config_cache:
        return _config_cache[config_file]
    
    config_path = get_config_path(config_file)
    
    print(f"📖 Loading config from: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)
    
    print("✅ Config loaded successfully!")
    
    # Сохраняем в кеш
    _config_cache[config_file] = config
    return config

def get_database_url() -> str:
    """Возвращает URL для подключения к базе данных"""
    config = load_config()
    db_config = config.get('database', {})
    
    if db_config.get('url'):
        return db_config['url']
    
    return f"postgresql://{db_config.get('user', 'monitor_user')}:{db_config.get('password', 'secure_password_123')}@{db_config.get('host', 'localhost')}:{db_config.get('port', 5432)}/{db_config.get('name', 'file_monitoring')}"

def get_api_config() -> Dict[str, Any]:
    """Возвращает конфигурацию API сервера"""
    global _api_config_cache
    
    if _api_config_cache is not None:
        return _api_config_cache
    
    config = load_config()
    _api_config_cache = config.get('api_server', {})
    return _api_config_cache

def get_monitoring_config() -> Dict[str, Any]:
    """Возвращает конфигурацию мониторинга"""
    global _monitoring_config_cache
    
    if _monitoring_config_cache is not None:
        return _monitoring_config_cache
    
    config = load_config()
    _monitoring_config_cache = config.get('monitoring', {})
    return _monitoring_config_cache

def get_api_client_config() -> Dict[str, Any]:
    """Возвращает конфигурацию API клиента"""
    global _api_client_config_cache
    
    if _api_client_config_cache is not None:
        return _api_client_config_cache
    
    config = load_config()
    _api_client_config_cache = config.get('api_client', {})
    return _api_client_config_cache

def clear_cache():
    """Очищает кеш конфигурации (для тестирования)"""
    global _config_cache, _monitoring_config_cache, _api_config_cache, _api_client_config_cache
    _config_cache = {}
    _monitoring_config_cache = None
    _api_config_cache = None
    _api_client_config_cache = None