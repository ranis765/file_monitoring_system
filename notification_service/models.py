from sqlalchemy import Column, String, DateTime, Boolean, Text, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
import uuid
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)

class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    email_notifications = Column(Boolean, default=True)
    daily_summary = Column(Boolean, default=True)
    session_reminders = Column(Boolean, default=True)
    aggregation_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class SentNotification(Base):
    __tablename__ = "sent_notifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    notification_type = Column(String(50), nullable=False)  # 'reminder', 'daily_summary', 'aggregated'
    session_ids = Column(Text)  # JSON список ID сессий
    subject = Column(String(255), nullable=False)
    sent_at = Column(DateTime, default=datetime.now)
    delivered = Column(Boolean, default=True)
    
    def get_session_ids(self):
        import json
        if self.session_ids:
            return json.loads(self.session_ids)
        return []
    
    def set_session_ids(self, session_ids):
        import json
        self.session_ids = json.dumps(session_ids)