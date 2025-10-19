from sqlalchemy import Column, String, DateTime, Boolean, Text, Date, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
    file_sessions = relationship("FileSession", back_populates="user")
    comments = relationship("Comment", back_populates="user")

class File(Base):
    __tablename__ = "files"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_path = Column(String(1000), unique=True, nullable=False)
    file_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
    file_sessions = relationship("FileSession", back_populates="file")

class FileSession(Base):
    __tablename__ = "file_sessions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False)
    started_at = Column(DateTime, nullable=False)
    last_activity = Column(DateTime, nullable=False)
    ended_at = Column(DateTime)
    hash_before = Column(String(64))
    hash_after = Column(String(64))
    is_commented = Column(Boolean, default=False)
    resume_count = Column(Integer, default=0) 
    
    # Relationships
    user = relationship("User", back_populates="file_sessions")
    file = relationship("File", back_populates="file_sessions")
    events = relationship("FileEvent", back_populates="session")
    comment = relationship("Comment", back_populates="session", uselist=False)

class FileEvent(Base):
    __tablename__ = "file_events"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("file_sessions.id"), nullable=False)
    event_type = Column(String(20), nullable=False)
    file_hash = Column(String(64))
    event_timestamp = Column(DateTime, nullable=False)
    
    # Relationships
    session = relationship("FileSession", back_populates="events")

class Comment(Base):
    __tablename__ = "comments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("file_sessions.id"), unique=True, nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    change_type = Column(String(50), nullable=False, default='other')
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
    session = relationship("FileSession", back_populates="comment")
    user = relationship("User", back_populates="comments")

class Report(Base):
    __tablename__ = "reports"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_date = Column(Date, nullable=False)
    report_type = Column(String(20), default='daily')
    file_format = Column(String(10), nullable=False)
    file_path = Column(String(1000))
    generated_at = Column(DateTime, default=datetime.now)