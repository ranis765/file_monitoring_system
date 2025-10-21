import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
import jinja2
import os
import sys
from sqlalchemy import text
import uuid
# Добавляем корень проекта в PYTHONPATH для абсолютных импортов
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.insert(0, project_root)

from notification_service.config import get_notification_config
from notification_service.email_sender import EmailSender
from notification_service.schemas import EmailMessage, UserSessionInfo
from notification_service.models import NotificationPreference, SentNotification

logger = logging.getLogger(__name__)

class NotificationManager:
    def __init__(self, db: Session):
        self.db = db
        self.config = get_notification_config()
        self.email_sender = EmailSender()
        self.template_env = self._setup_templates()
    
    def _setup_templates(self) -> jinja2.Environment:
        """Настроить окружение для шаблонов"""
        template_dir = self.config.template_dir
        if not os.path.exists(template_dir):
            template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        
        return jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_dir),
            autoescape=jinja2.select_autoescape(['html', 'xml'])
        )
    
    def get_users_with_pending_sessions(self) -> List[UserSessionInfo]:
        """Получить пользователей с незакомментированными сессиями"""
        query = text("""
        SELECT 
            u.id as user_id,
            u.username,
            u.email,
            COUNT(fs.id) as pending_count,
            JSON_AGG(
                JSON_BUILD_OBJECT(
                    'session_id', fs.id,
                    'file_name', f.file_name,
                    'file_path', f.file_path,
                    'ended_at', fs.ended_at,
                    'duration', EXTRACT(EPOCH FROM (fs.ended_at - fs.started_at))
                )
            ) as sessions
        FROM users u
        JOIN file_sessions fs ON u.id = fs.user_id
        JOIN files f ON fs.file_id = f.id
        WHERE fs.ended_at IS NOT NULL 
        AND fs.is_commented = FALSE
        AND fs.ended_at >= NOW() - INTERVAL '7 days'
        GROUP BY u.id, u.username, u.email
        HAVING COUNT(fs.id) > 0
        """)
    
        result = self.db.execute(query)
        users_info = []
    
        for row in result:
            users_info.append(UserSessionInfo(
                user_id=row.user_id,
                username=row.username,
                email=row.email or f"{row.username}@example.com",
                pending_sessions=row.sessions,
                total_pending=row.pending_count
            ))
    
        return users_info
    
    def should_send_reminder(self, user_id: uuid.UUID, notification_type: str) -> bool:
        """Проверить, нужно ли отправлять уведомление пользователю"""
        # Проверяем настройки пользователя
        preference = self.db.query(NotificationPreference).filter(
            NotificationPreference.user_id == user_id
        ).first()
        
        if not preference:
            # Создаем настройки по умолчанию
            preference = NotificationPreference(
                user_id=user_id,
                email_notifications=True,
                daily_summary=True,
                session_reminders=True,
                aggregation_enabled=True
            )
            self.db.add(preference)
            self.db.commit()
        
        if not preference.email_notifications:
            return False
        
        if notification_type == 'reminder' and not preference.session_reminders:
            return False
        
        if notification_type == 'daily_summary' and not preference.daily_summary:
            return False
        
        # Проверяем, не отправляли ли мы уже уведомление за последние N часов
        if preference.aggregation_enabled:
            aggregation_window = datetime.now() - timedelta(hours=self.config.aggregation_window_hours)
            recent_notification = self.db.query(SentNotification).filter(
                SentNotification.user_id == user_id,
                SentNotification.notification_type == notification_type,
                SentNotification.sent_at >= aggregation_window
            ).first()
            
            if recent_notification:
                logger.info(f"Notification {notification_type} already sent to user {user_id} recently")
                return False
        
        return True
    
    def send_reminder_notification(self, user_info: UserSessionInfo) -> bool:
        """Отправить уведомление-напоминание"""
        if not self.should_send_reminder(user_info.user_id, 'reminder'):
            return False
        
        template = self.template_env.get_template('pending_sessions.html')
        html_content = template.render(
            user=user_info,
            config=self.config,
            current_time=datetime.now()
        )
        
        email_message = EmailMessage(
            to_email=user_info.email,
            to_name=user_info.username,
            subject=f"Напоминание: у вас {user_info.total_pending} незакомментированных сессий",
            html_content=html_content
        )
        
        success = self.email_sender.send_email(email_message)
        
        if success:
            # Сохраняем информацию об отправленном уведомлении
            notification = SentNotification(
                user_id=user_info.user_id,  # В реальности нужно использовать user_id
                notification_type='reminder',
                session_ids=[session['session_id'] for session in user_info.pending_sessions],
                subject=email_message.subject
            )
            self.db.add(notification)
            self.db.commit()
        
        return success
    
    def send_aggregated_reminder(self, users_info: List[UserSessionInfo]) -> List[bool]:
        """Отправить агрегированные уведомления"""
        results = []
        
        for user_info in users_info:
            if user_info.total_pending >= self.config.min_sessions_for_aggregation:
                if not self.should_send_reminder(user_info.user_id, 'aggregated'):
                    results.append(False)
                    continue
                
                template = self.template_env.get_template('aggregated_reminder.html')
                html_content = template.render(
                    user=user_info,
                    config=self.config,
                    current_time=datetime.now()
                )
                
                email_message = EmailMessage(
                    to_email=user_info.email,
                    to_name=user_info.username,
                    subject=f"Сводка: {user_info.total_pending} сессий требуют комментариев",
                    html_content=html_content
                )
                
                success = self.email_sender.send_email(email_message)
                results.append(success)
                
                if success:
                    notification = SentNotification(
                        user_id=user_info.user_id,
                        notification_type='aggregated',
                        session_ids=[session['session_id'] for session in user_info.pending_sessions],
                        subject=email_message.subject
                    )
                    self.db.add(notification)
                    self.db.commit()
            else:
                results.append(False)
        
        return results
    
    def send_daily_summary(self, users_info: List[UserSessionInfo]) -> List[bool]:
        """Отправить итоговые уведомления за день"""
        results = []
        
        for user_info in users_info:
            if not self.should_send_reminder(user_info.user_id, 'daily_summary'):
                results.append(False)
                continue
            
            template = self.template_env.get_template('daily_summary.html')
            html_content = template.render(
                user=user_info,
                config=self.config,
                current_time=datetime.now()
            )
            
            email_message = EmailMessage(
                to_email=user_info.email,
                to_name=user_info.username,
                subject=f"Итоги дня: {user_info.total_pending} сессий для комментариев",
                html_content=html_content
            )
            
            success = self.email_sender.send_email(email_message)
            results.append(success)
            
            if success:
                notification = SentNotification(
                    user_id=user_info.user_id,
                    notification_type='daily_summary',
                    session_ids=[session['session_id'] for session in user_info.pending_sessions],
                    subject=email_message.subject
                )
                self.db.add(notification)
                self.db.commit()
        
        return results
    
    def process_reminders(self):
        """Обработать все напоминания"""
        logger.info("Processing reminder notifications...")
        
        users_info = self.get_users_with_pending_sessions()
        results = []
        
        for user_info in users_info:
            if user_info.total_pending >= self.config.min_sessions_for_aggregation:
                # Отправляем агрегированное уведомление
                result = self.send_aggregated_reminder([user_info])[0]
            else:
                # Отправляем индивидуальные уведомления
                result = self.send_reminder_notification(user_info)
            
            results.append((user_info.username, result))
        
        successful = sum(1 for _, result in results if result)
        logger.info(f"Reminder processing completed: {successful}/{len(results)} successful")
        return results
    
    def process_daily_summaries(self):
        """Обработать итоговые уведомления за день"""
        logger.info("Processing daily summary notifications...")
        
        users_info = self.get_users_with_pending_sessions()
        results = self.send_daily_summary(users_info)
        
        successful = sum(1 for result in results if result)
        logger.info(f"Daily summary processing completed: {successful}/{len(results)} successful")
        return results
    
    