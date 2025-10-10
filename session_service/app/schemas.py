from pydantic import BaseModel, ConfigDict
from datetime import datetime, date
from typing import Optional, List
import uuid

# User Schemas
class UserBase(BaseModel):
    username: str
    email: Optional[str] = None

class UserCreate(UserBase):
    pass

class User(UserBase):
    id: uuid.UUID
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

# File Schemas
class FileBase(BaseModel):
    file_path: str
    file_name: str

class FileCreate(FileBase):
    pass

class File(FileBase):
    id: uuid.UUID
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

# File Session Schemas
class FileSessionBase(BaseModel):
    user_id: uuid.UUID
    file_id: uuid.UUID
    started_at: datetime
    last_activity: datetime
    hash_before: Optional[str] = None
    hash_after: Optional[str] = None
    resume_count: int = 0

class FileSessionCreate(FileSessionBase):
    pass

class FileSession(FileSessionBase):
    id: uuid.UUID
    ended_at: Optional[datetime] = None
    is_commented: bool = False
    
    model_config = ConfigDict(from_attributes=True)

# File Event Schemas
class FileEventBase(BaseModel):
    session_id: uuid.UUID
    event_type: str
    file_hash: Optional[str] = None
    event_timestamp: datetime

class FileEventCreate(FileEventBase):
    pass

class FileEvent(FileEventBase):
    id: uuid.UUID
    
    model_config = ConfigDict(from_attributes=True)

# Comment Schemas (ОБНОВЛЕНЫ - добавлен change_type)
class CommentBase(BaseModel):
    session_id: uuid.UUID
    user_id: uuid.UUID
    content: str
    change_type: str = "other"  # НОВОЕ ПОЛЕ

class CommentCreate(CommentBase):
    pass

class Comment(CommentBase):
    id: uuid.UUID
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

# Report Schemas
class ReportBase(BaseModel):
    report_date: date
    report_type: str = 'daily'
    file_format: str
    file_path: Optional[str] = None

class ReportCreate(ReportBase):
    pass

class Report(ReportBase):
    id: uuid.UUID
    generated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

# Дополнительные схемы для API (ДОБАВЬТЕ ЭТИ КЛАССЫ)
class CommentWithUser(BaseModel):
    id: uuid.UUID
    content: str
    change_type: str
    created_at: datetime
    username: str
    
    model_config = ConfigDict(from_attributes=True)

class SessionWithDetails(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: Optional[datetime] = None
    last_activity: datetime
    resume_count: int
    file_path: str
    file_name: str
    username: str
    comment: Optional[CommentWithUser] = None
    
    model_config = ConfigDict(from_attributes=True)

# Схемы для ответов API
class CommentResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID
    content: str
    change_type: str
    created_at: datetime
    username: str
    
    model_config = ConfigDict(from_attributes=True)

class SessionCommentResponse(BaseModel):
    session: FileSession
    comment: Optional[Comment] = None
    file: File
    user: User
    
    model_config = ConfigDict(from_attributes=True)