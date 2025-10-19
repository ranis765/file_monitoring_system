from sqlalchemy.orm import Session
from . import models, schemas
import uuid
from datetime import datetime, timedelta

# User CRUD
def get_user(db: Session, user_id: uuid.UUID):
    return db.query(models.User).filter(models.User.id == user_id).first()

def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()

def create_user(db: Session, user: schemas.UserCreate):
    db_user = models.User(**user.model_dump())
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# File CRUD
def get_file(db: Session, file_id: uuid.UUID):
    return db.query(models.File).filter(models.File.id == file_id).first()

def get_file_by_path(db: Session, file_path: str):
    return db.query(models.File).filter(models.File.file_path == file_path).first()

def create_file(db: Session, file: schemas.FileCreate):
    # Проверяем, существует ли файл
    db_file = get_file_by_path(db, file.file_path)
    if db_file:
        return db_file
    
    db_file = models.File(**file.model_dump())
    db.add(db_file)
    db.commit()
    db.refresh(db_file)
    return db_file

# File Session CRUD
def create_file_session(db: Session, session: schemas.FileSessionCreate):
    db_session = models.FileSession(**session.model_dump())
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session

def get_file_session(db: Session, session_id: uuid.UUID):
    return db.query(models.FileSession).filter(models.FileSession.id == session_id).first()

def update_file_session_activity(db: Session, session_id: uuid.UUID):
    db_session = get_file_session(db, session_id)
    if db_session:
        db_session.last_activity = datetime.now()
        db.commit()
        db.refresh(db_session)
    return db_session

def get_active_session_by_user_and_file(db: Session, user_id: uuid.UUID, file_id: uuid.UUID):
    """Получает активную сессию для пользователя и файла (только незакрытые)"""
    return db.query(models.FileSession).filter(
        models.FileSession.user_id == user_id,
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).first()

def get_any_active_session_for_file(db: Session, file_id: uuid.UUID):
    """Получает любую активную сессию для файла (любого пользователя)"""
    return db.query(models.FileSession).filter(
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).first()

def close_all_active_sessions_for_file(db: Session, file_id: uuid.UUID, ended_at: datetime = None):
    """Закрывает все активные сессии для файла"""
    active_sessions = db.query(models.FileSession).filter(
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).all()
    
    for session in active_sessions:
        session.ended_at = ended_at or datetime.now()
    
    db.commit()
    return len(active_sessions)

def close_user_sessions_for_file(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, ended_at: datetime = None):
    """Закрывает все активные сессии пользователя для файла"""
    active_sessions = db.query(models.FileSession).filter(
        models.FileSession.user_id == user_id,
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).all()
    
    for session in active_sessions:
        session.ended_at = ended_at or datetime.now()
    
    db.commit()
    return len(active_sessions)

def close_session(db: Session, session_id: uuid.UUID, ended_at: datetime = None):
    """Закрывает сессию с указанным временем окончания"""
    session = get_file_session(db, session_id)
    if session:
        session.ended_at = ended_at or datetime.now()
        db.commit()
        db.refresh(session)
    return session

def get_recent_closed_session(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, hours: int = 1):
    """Получает недавно закрытую сессию для возможного возобновления (только не прокомментированные)"""
    cutoff_time = datetime.now() - timedelta(hours=hours)
    return db.query(models.FileSession).filter(
        models.FileSession.user_id == user_id,
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at >= cutoff_time,
        models.FileSession.is_commented == False  
    ).order_by(models.FileSession.ended_at.desc()).first()

# File Event CRUD
def create_file_event(db: Session, event: schemas.FileEventCreate):
    db_event = models.FileEvent(**event.model_dump())
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event

# Comment CRUD
def create_comment(db: Session, comment: schemas.CommentCreate):
    db_comment = models.Comment(**comment.model_dump())
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    
    # Обновляем флаг комментирования в сессии
    db_session = get_file_session(db, comment.session_id)
    if db_session:
        db_session.is_commented = True
        db.commit()
    
    return db_comment

def get_comment_by_session(db: Session, session_id: uuid.UUID):
    return db.query(models.Comment).filter(models.Comment.session_id == session_id).first()

def get_comments_by_user(db: Session, user_id: uuid.UUID):
    """Получает все комментарии пользователя"""
    return db.query(models.Comment).filter(models.Comment.user_id == user_id).all()

def get_comments_by_change_type(db: Session, change_type: str):
    """Получает комментарии по типу изменения"""
    return db.query(models.Comment).filter(models.Comment.change_type == change_type).all()

def get_comment_with_user(db: Session, session_id: uuid.UUID):
    """Получает комментарий с информацией о пользователе"""
    return db.query(
        models.Comment,
        models.User.username
    ).join(
        models.User, models.Comment.user_id == models.User.id
    ).filter(
        models.Comment.session_id == session_id
    ).first()

# Новые функции для работы с сессиями и комментариями
def get_session_with_details(db: Session, session_id: uuid.UUID):
    """Получает сессию с детальной информацией о файле, пользователе и комментарии"""
    return db.query(
        models.FileSession,
        models.File.file_path,
        models.File.file_name,
        models.User.username,
        models.Comment
    ).join(
        models.File, models.FileSession.file_id == models.File.id
    ).join(
        models.User, models.FileSession.user_id == models.User.id
    ).outerjoin(
        models.Comment, models.FileSession.id == models.Comment.session_id
    ).filter(
        models.FileSession.id == session_id
    ).first()

def get_sessions_with_comments(db: Session, skip: int = 0, limit: int = 100):
    """Получает сессии с комментариями"""
    return db.query(
        models.FileSession,
        models.File.file_path,
        models.File.file_name,
        models.User.username,
        models.Comment
    ).join(
        models.File, models.FileSession.file_id == models.File.id
    ).join(
        models.User, models.FileSession.user_id == models.User.id
    ).outerjoin(
        models.Comment, models.FileSession.id == models.Comment.session_id
    ).filter(
        models.FileSession.is_commented == True
    ).offset(skip).limit(limit).all()

def create_file_session_with_id(db: Session, session: schemas.FileSessionCreate):
    """Создает сессию с возможностью указания ID"""
    # Если в сессии указан ID, используем его
    session_dict = session.model_dump()
    
    if 'id' in session_dict and session_dict['id'] is not None:
        db_session = models.FileSession(**session_dict)
    else:
        # Иначе создаем сессию с автоматически сгенерированным ID
        db_session = models.FileSession(**session_dict)
    
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session

def get_active_sessions_by_user_and_file(db: Session, user_id: uuid.UUID, file_id: uuid.UUID):
    """Получает активную сессию для пользователя и файла (только незакрытые)"""
    return db.query(models.FileSession).filter(
        models.FileSession.user_id == user_id,
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).first()

def get_any_active_session_for_file(db: Session, file_id: uuid.UUID):
    """Получает любую активную сессию для файла (любого пользователя)"""
    return db.query(models.FileSession).filter(
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).first()

def close_all_active_sessions_for_file(db: Session, file_id: uuid.UUID, ended_at: datetime = None):
    """Закрывает все активные сессии для файла"""
    active_sessions = db.query(models.FileSession).filter(
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).all()
    
    for session in active_sessions:
        session.ended_at = ended_at or datetime.now()
    
    db.commit()
    return len(active_sessions)

def close_user_sessions_for_file(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, ended_at: datetime = None):
    """Закрывает все активные сессии пользователя для файла"""
    active_sessions = db.query(models.FileSession).filter(
        models.FileSession.user_id == user_id,
        models.FileSession.file_id == file_id,
        models.FileSession.ended_at.is_(None)
    ).all()
    
    for session in active_sessions:
        session.ended_at = ended_at or datetime.now()
    
    db.commit()
    return len(active_sessions)