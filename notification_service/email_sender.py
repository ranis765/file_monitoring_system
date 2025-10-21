import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import  MIMEMultipart 
from typing import List, Optional
import logging
import sys
import os
from datetime import datetime

# Добавляем корень проекта в PYTHONPATH
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.insert(0, project_root)

from notification_service.config import get_notification_config
from notification_service.schemas import EmailMessage

logger = logging.getLogger(__name__)

class EmailSender:
    def __init__(self):
        self.config = get_notification_config()
        self.smtp_server = None
        
    def connect(self):
        """Установить соединение с SMTP сервером"""
        try:
            self.smtp_server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port)
            if self.config.smtp_use_tls:
                self.smtp_server.starttls()
            if self.config.smtp_username and self.config.smtp_password:
                self.smtp_server.login(self.config.smtp_username, self.config.smtp_password)
            logger.info("SMTP connection established")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to SMTP server: {e}")
            return False
    
    def disconnect(self):
        """Закрыть соединение с SMTP сервером"""
        if self.smtp_server:
            try:
                self.smtp_server.quit()
            except:
                self.smtp_server.close()
            self.smtp_server = None
    
    def send_email(self, email_message: EmailMessage) -> bool:
        """Отправить email сообщение"""
        try:
            if not self.smtp_server:
                if not self.connect():
                    return False
        
            # Создаем сообщение с правильными заголовками для Mail.ru
            msg = MIMEMultipart('alternative')
            msg['Subject'] = email_message.subject
            msg['From'] = f"{self.config.sender_name} <{self.config.sender_email}>"
            msg['To'] = email_message.to_email  # Только email, без имени
            msg['Reply-To'] = self.config.sender_email
        
            # Добавляем обязательные заголовки для Mail.ru
            msg['Date'] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
            msg['Message-ID'] = f"<{datetime.now().strftime('%Y%m%d%H%M%S')}@{self.config.smtp_host}>"
            msg['MIME-Version'] = '1.0'
            msg['Content-Type'] = 'multipart/alternative; boundary="{}"'.format(msg.get_boundary())
        
            # Добавляем текстовую версию (обязательно для Mail.ru)
            if email_message.text_content:
                text_part = MIMEText(email_message.text_content, 'plain', 'utf-8')
            else:
                # Создаем простую текстовую версию из HTML
                import re
                text_content = re.sub('<[^<]+?>', '', email_message.html_content)
                text_part = MIMEText(text_content, 'plain', 'utf-8')
        
            msg.attach(text_part)
        
            # Добавляем HTML версию
            html_part = MIMEText(email_message.html_content, 'html', 'utf-8')
            msg.attach(html_part)
        
            # Отправляем
            self.smtp_server.sendmail(
                self.config.sender_email,  # from_addr
                [email_message.to_email],  # to_addrs
                msg.as_string()            # msg
            )
            
            logger.info(f"Email sent to {email_message.to_email}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to send email to {email_message.to_email}: {e}")
            return False
    def send_batch_emails(self, email_messages: List[EmailMessage]) -> List[bool]:
        """Отправить несколько email сообщений"""
        results = []
        for message in email_messages:
            result = self.send_email(message)
            results.append(result)
        return results