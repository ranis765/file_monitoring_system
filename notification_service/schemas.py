from pydantic import BaseModel, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

class NotificationPreferenceBase(BaseModel):
    email_notifications: bool = True
    daily_summary: bool = True
    session_reminders: bool = True
    aggregation_enabled: bool = True

class NotificationPreferenceCreate(NotificationPreferenceBase):
    user_id: uuid.UUID

class NotificationPreference(NotificationPreferenceBase):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class SentNotificationBase(BaseModel):
    user_id: uuid.UUID
    notification_type: str
    session_ids: List[uuid.UUID]
    subject: str

class SentNotification(SentNotificationBase):
    id: uuid.UUID
    sent_at: datetime
    delivered: bool
    
    class Config:
        from_attributes = True

class UserSessionInfo(BaseModel):
    user_id: uuid.UUID 
    username: str
    email: str
    pending_sessions: List[Dict[str, Any]]
    total_pending: int

class EmailMessage(BaseModel):
    to_email: EmailStr
    to_name: str
    subject: str
    html_content: str
    text_content: Optional[str] = None