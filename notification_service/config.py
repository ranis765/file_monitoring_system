import os
from typing import List, Dict, Any
from pydantic_settings import BaseSettings
from shared.config_loader import load_config

class NotificationConfig(BaseSettings):
    """Конфигурация сервиса уведомлений"""
    
    # Настройки SMTP
    smtp_host: str = "smtp.mail.ru"
    smtp_port: int = 587
    smtp_username: str = "mr.ranis02@mail.ru"
    smtp_password: str = "ssO1IIaqPxCEOYp2AYjH"
    smtp_use_tls: bool = True
    

    # Настройки отправителя
    sender_name: str = "File Monitoring System"
    sender_email: str = "mr.ranis02@mail.ru"
    
    # Настройки времени уведомлений
    reminder_times: List[str] = ["09:00", "13:00", "17:00"]  # Время напоминаний
    daily_summary_time: str = "18:00"  # Время итогового уведомления
    check_interval_minutes: int = 5  # Интервал проверки новых сессий
    
    # Настройки агрегации
    aggregation_window_hours: int = 24  # Окно агрегации уведомлений
    min_sessions_for_aggregation: int = 2  # Минимальное количество сессий для агрегации
    
    # Настройки шаблонов
    template_dir: str = "notifications/templates"
    
    class Config:
        env_file = ".env"

def get_notification_config() -> NotificationConfig:
    """Получить конфигурацию уведомлений"""
    config_data = load_config()
    
    # Извлекаем настройки уведомлений из основного конфига
    notification_settings = config_data.get('notifications', {})
    
    return NotificationConfig(
        smtp_host=notification_settings.get('smtp_host', 'smtp.gmail.com'),
        smtp_port=notification_settings.get('smtp_port', 587),
        smtp_username=notification_settings.get('smtp_username', ''),
        smtp_password=notification_settings.get('smtp_password', ''),
        smtp_use_tls=notification_settings.get('smtp_use_tls', True),
        sender_name=notification_settings.get('sender_name', 'File Monitoring System'),
        sender_email=notification_settings.get('sender_email', 'noreply@filemonitor.com'),
        reminder_times=notification_settings.get('reminder_times', ['09:00', '13:00', '17:00']),
        daily_summary_time=notification_settings.get('daily_summary_time', '18:00'),
        check_interval_minutes=notification_settings.get('check_interval_minutes', 5),
        aggregation_window_hours=notification_settings.get('aggregation_window_hours', 24),
        min_sessions_for_aggregation=notification_settings.get('min_sessions_for_aggregation', 2)
    )