# main.py (сервер)
import sys
import os

# Добавляем корень проекта в PYTHONPATH
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))  # Поднимаемся на уровень выше
sys.path.insert(0, project_root)

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from shared.config_loader import get_api_config
from . import database, models, crud, schemas
from typing import List, Optional
import uuid
from datetime import datetime, timedelta

# Загружаем конфигурацию API
api_config = get_api_config()

app = FastAPI(
    title="File Monitoring API",
    description="API для отслеживания изменений файлов с системой комментариев",
    version="1.0.0"
)

# Dependency
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
async def root():
    return {
        "message": "File Monitoring System API", 
        "status": "running",
        "config_source": "YAML"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "session-service"}

@app.get("/config")
async def show_config():
    """Показывает текущую конфигурацию (без паролей)"""
    return {
        "api_host": api_config.get('host'),
        "api_port": api_config.get('port'),
        "environment": "development"
    }

# Events endpoint для мониторинга
@app.post("/api/events")
async def create_event(event_data: dict, db: Session = Depends(get_db)):
    """Эндпоинт для приема событий от мониторинга"""
    print(f"📨 Received event: {event_data}")
    
    try:
        # Обрабатываем событие в зависимости от типа
        result = await process_file_event(db, event_data)
        return {"status": "processed", "event_type": event_data.get('event_type'), "result": result}
    except Exception as e:
        print(f"❌ Error processing event: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing event: {str(e)}")

async def process_file_event(db: Session, event_data: dict):
    """Обрабатывает файловое событие и создает/обновляет сессии"""
    event_type = event_data.get('event_type')
    file_path = event_data.get('file_path')
    file_name = event_data.get('file_name')
    username = event_data.get('user_id')
    file_hash = event_data.get('file_hash')
    session_id = event_data.get('session_id')
    resume_count = event_data.get('resume_count', 0)
    
    print(f"🔧 Processing {event_type} for {file_path} (user: {username}, session: {session_id}, resume: {resume_count})")
    
    # Обрабатываем перемещение отдельно
    if event_type == 'moved':
        return await handle_file_moved(db, event_data, file_hash)
    
    # Получаем или создаем пользователя
    user = crud.get_user_by_username(db, username)
    if not user:
        user_data = schemas.UserCreate(username=username, email=f"{username}@example.com")
        user = crud.create_user(db, user_data)
    
    # Получаем или создаем файл
    file = crud.get_file_by_path(db, file_path)
    if not file:
        file_data = schemas.FileCreate(file_path=file_path, file_name=file_name)
        file = crud.create_file(db, file_data)
    
    # Обрабатываем в зависимости от типа события
    if event_type == 'created':
        return await handle_file_created(db, user.id, file.id, event_data, file_hash, resume_count)
    elif event_type == 'modified':
        return await handle_file_modified(db, user.id, file.id, event_data, file_hash, session_id, resume_count)
    elif event_type == 'deleted':
        return await handle_file_deleted(db, user.id, file.id, event_data, session_id)
    elif event_type == 'closed':
        return await handle_file_closed(db, session_id, file_hash, event_data)
    else:
        raise ValueError(f"Unknown event type: {event_type}")

async def handle_file_moved(db: Session, event_data: dict, file_hash: str = None):
    """Обрабатывает перемещение/переименование файла"""
    old_file_path = event_data.get('old_file_path')
    new_file_path = event_data.get('file_path')
    new_file_name = event_data.get('file_name')
    username = event_data.get('user_id')
    
    print(f"🔄 Processing file move: {old_file_path} -> {new_file_path}")
    
    # Получаем или создаем пользователя
    user = crud.get_user_by_username(db, username)
    if not user:
        user_data = schemas.UserCreate(username=username, email=f"{username}@example.com")
        user = crud.create_user(db, user_data)
    
    # Ищем активную сессию для старого пути
    old_file = crud.get_file_by_path(db, old_file_path)
    if old_file:
        # Находим активные сессии для этого файла
        active_sessions = crud.get_active_sessions_by_user_and_file(db, user.id, old_file.id)
        
        if active_sessions:
            # Берем последнюю активную сессию
            session = active_sessions[0]
            
            # Обновляем файл с новым путем
            old_file.file_path = new_file_path
            old_file.file_name = new_file_name
            db.commit()
            
            # Обновляем время активности сессии
            session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
            if file_hash:
                session.hash_after = file_hash
            db.commit()
            
            # Создаем событие перемещения
            event_record = schemas.FileEventCreate(
                session_id=session.id,
                event_type='moved',
                file_hash=file_hash,
                event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
            )
            crud.create_file_event(db, event_record)
            
            print(f"🔄 Moved session {session.id} from {old_file_path} to {new_file_path}")
            return {"action": "session_moved", "session_id": str(session.id)}
    
    # Если не нашли активную сессию, создаем новую
    file_data = schemas.FileCreate(file_path=new_file_path, file_name=new_file_name)
    file = crud.create_file(db, file_data)
    
    session_data = schemas.FileSessionCreate(
        user_id=user.id,
        file_id=file.id,
        started_at=datetime.fromisoformat(event_data.get('event_timestamp')),
        last_activity=datetime.fromisoformat(event_data.get('event_timestamp')),
        hash_before=file_hash,
        resume_count=0
    )
    
    session = crud.create_file_session(db, session_data)
    
    # Создаем событие перемещения
    event_record = schemas.FileEventCreate(
        session_id=session.id,
        event_type='moved',
        file_hash=file_hash,
        event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
    )
    crud.create_file_event(db, event_record)
    
    print(f"✅ Created new session {session.id} for moved file {new_file_path}")
    return {"action": "session_created", "session_id": str(session.id)}

async def handle_file_created(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, file_hash: str = None, resume_count: int = 0):
    """Обрабатывает создание файла - создает новую сессию"""
    # ПРОВЕРКА ДУБЛИРОВАНИЯ СЕССИЙ - ДОБАВЛЕНО
    active_sessions = crud.get_active_sessions_by_user_and_file(db, user_id, file_id)
    if active_sessions:
        print(f"⚠️ Active session already exists for file, updating instead of creating new")
        session = active_sessions[0]
        session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
        if file_hash:
            session.hash_before = file_hash
        session.resume_count = resume_count
        db.commit()
        db.refresh(session)
        
        # Создаем событие created
        event_record = schemas.FileEventCreate(
            session_id=session.id,
            event_type='created',
            file_hash=file_hash,
            event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
        )
        crud.create_file_event(db, event_record)
        
        print(f"🔄 Updated existing session {session.id} for created file")
        return {"action": "session_updated", "session_id": str(session.id), "resumed": False}
    
    # Проверяем, есть ли недавно закрытая сессия для возобновления
    recent_session = None
    if resume_count > 0:
        recent_session = crud.get_recent_closed_session(db, user_id, file_id)
    
    if recent_session:
        # Возобновляем существующую сессию
        recent_session.ended_at = None
        recent_session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
        recent_session.resume_count = resume_count
        recent_session.hash_before = file_hash
        db.commit()
        db.refresh(recent_session)
        
        print(f"🔄 Resumed session {recent_session.id} (resume count: {resume_count})")
        session = recent_session
    else:
        # Создаем новую сессию
        session_data = schemas.FileSessionCreate(
            user_id=user_id,
            file_id=file_id,
            started_at=datetime.fromisoformat(event_data.get('event_timestamp')),
            last_activity=datetime.fromisoformat(event_data.get('event_timestamp')),
            hash_before=file_hash,
            resume_count=resume_count
        )
        
        session = crud.create_file_session(db, session_data)
        print(f"✅ Created session {session.id} for file {event_data.get('file_path')}")
    
    # Создаем событие файла
    event_record = schemas.FileEventCreate(
        session_id=session.id,
        event_type='created',
        file_hash=file_hash,
        event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
    )
    crud.create_file_event(db, event_record)
    
    return {"action": "session_created", "session_id": str(session.id), "resumed": recent_session is not None}

async def handle_file_modified(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, file_hash: str = None, session_id: str = None, resume_count: int = 0):
    """Обрабатывает изменение файла - обновляет существующую сессию или создает новую"""
    session = None
    
    if session_id:
        # Пытаемся найти существующую сессию
        try:
            session_uuid = uuid.UUID(session_id)
            session = crud.get_file_session(db, session_uuid)
        except ValueError:
            pass  # Невалидный UUID
    
    if not session:
        # Ищем активную сессию для этого пользователя и файла
        active_sessions = crud.get_active_sessions_by_user_and_file(db, user_id, file_id)
        if active_sessions:
            session = active_sessions[0]
    
    if session:
        # Обновляем существующую сессию
        session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
        if file_hash:
            session.hash_after = file_hash
        if resume_count > session.resume_count:
            session.resume_count = resume_count
        db.commit()
        db.refresh(session)
        
        print(f"📝 Updated session {session.id} for file {event_data.get('file_path')} (resume: {resume_count})")
        action = "session_updated"
    else:
        # Создаем новую сессию
        session_data = schemas.FileSessionCreate(
            user_id=user_id,
            file_id=file_id,
            started_at=datetime.fromisoformat(event_data.get('event_timestamp')),
            last_activity=datetime.fromisoformat(event_data.get('event_timestamp')),
            hash_before=file_hash,
            resume_count=resume_count
        )
        
        session = crud.create_file_session(db, session_data)
        print(f"✅ Created new session {session.id} for modified file {event_data.get('file_path')}")
        action = "session_created"
    
    # Создаем событие файла
    event_record = schemas.FileEventCreate(
        session_id=session.id,
        event_type='modified',
        file_hash=file_hash,
        event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
    )
    crud.create_file_event(db, event_record)
    
    return {"action": action, "session_id": str(session.id)}

async def handle_file_deleted(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, session_id: str = None):
    """Обрабатывает удаление файла - закрывает сессию и создает событие deleted"""
    print(f"🗑️ Processing file deletion for file_id: {file_id}, session_id: {session_id}")
    
    session = None
    
    if session_id:
        # Пытаемся найти существующую сессию
        try:
            session_uuid = uuid.UUID(session_id)
            session = crud.get_file_session(db, session_uuid)
        except ValueError:
            pass  # Невалидный UUID
    
    if not session:
        # Ищем активную сессию для этого пользователя и файла
        active_sessions = crud.get_active_sessions_by_user_and_file(db, user_id, file_id)
        if active_sessions:
            session = active_sessions[0]
            print(f"🔍 Found active session for deletion: {session.id}")
    
    if session:
        # Закрываем сессию с временем из события
        session.ended_at = datetime.fromisoformat(event_data.get('event_timestamp'))
        db.commit()
        db.refresh(session)
        
        # Создаем событие удаления
        event_record = schemas.FileEventCreate(
            session_id=session.id,
            event_type='deleted',
            file_hash=None,  # Для удаленного файла хеша нет
            event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
        )
        crud.create_file_event(db, event_record)
        
        print(f"🗑️ Closed session {session.id} for deleted file and created deleted event")
        return {"action": "session_closed", "session_id": str(session.id)}
    else:
        print(f"⚠️ No active session found for deleted file, creating standalone deleted event")
        
        # Если сессии нет, все равно создаем событие deleted
        # Создаем временную сессию для привязки события
        session_data = schemas.FileSessionCreate(
            user_id=user_id,
            file_id=file_id,
            started_at=datetime.fromisoformat(event_data.get('event_timestamp')),
            last_activity=datetime.fromisoformat(event_data.get('event_timestamp')),
            ended_at=datetime.fromisoformat(event_data.get('event_timestamp')),  # Сразу закрываем
            hash_before=None,
            resume_count=0
        )
        
        session = crud.create_file_session(db, session_data)
        
        # Создаем событие удаления
        event_record = schemas.FileEventCreate(
            session_id=session.id,
            event_type='deleted',
            file_hash=None,
            event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
        )
        crud.create_file_event(db, event_record)
        
        print(f"🗑️ Created standalone deleted event with session {session.id}")
        return {"action": "deleted_event_created", "session_id": str(session.id)}

async def handle_file_closed(db: Session, session_id: str, file_hash: str = None, event_data: dict = None):
    """Обрабатывает закрытие файла - закрывает сессию с правильным ended_at"""
    if session_id:
        try:
            session_uuid = uuid.UUID(session_id)
            session = crud.get_file_session(db, session_uuid)
            if session:
                # ОБНОВЛЯЕМ СЕССИЮ С ПРАВИЛЬНЫМ ended_at
                if event_data and 'event_timestamp' in event_data:
                    # Используем время из события закрытия
                    ended_at = datetime.fromisoformat(event_data['event_timestamp'])
                else:
                    # Используем текущее время
                    ended_at = datetime.now()
                
                session.ended_at = ended_at
                
                if file_hash:
                    session.hash_after = file_hash
                db.commit()
                
                # Создаем событие закрытия
                event_timestamp = ended_at  # Используем то же время что и для ended_at
                event_record = schemas.FileEventCreate(
                    session_id=session.id,
                    event_type='closed',
                    file_hash=file_hash,
                    event_timestamp=event_timestamp
                )
                crud.create_file_event(db, event_record)
                
                # Получаем информацию о файле для логов
                file = crud.get_file(db, session.file_id)
                user = crud.get_user(db, session.user_id)
                
                duration = session.ended_at - session.started_at
                print(f"🔒 Closed session {session.id} for file {file.file_path} (user: {user.username}, duration: {duration}, ended_at: {session.ended_at})")
                return {
                    "action": "session_closed", 
                    "session_id": str(session.id),
                    "duration": str(duration),
                    "ended_at": session.ended_at.isoformat(),
                    "resume_count": session.resume_count
                }
        except ValueError:
            pass
    
    return {"action": "no_session_found"}

# Новый эндпоинт для создания сессий (для совместимости с мониторингом)
@app.post("/api/sessions")
async def create_session(session_data: dict, db: Session = Depends(get_db)):
    """Создает сессию на сервере и возвращает session_id"""
    try:
        print(f"📝 Creating session: {session_data}")
        
        # Получаем или создаем пользователя
        username = session_data.get('username')
        user = crud.get_user_by_username(db, username)
        if not user:
            user_data = schemas.UserCreate(username=username, email=f"{username}@example.com")
            user = crud.create_user(db, user_data)
        
        # Получаем или создаем файл
        file_path = session_data.get('file_path')
        file_name = session_data.get('file_name', os.path.basename(file_path))
        file = crud.get_file_by_path(db, file_path)
        if not file:
            file_data = schemas.FileCreate(file_path=file_path, file_name=file_name)
            file = crud.create_file(db, file_data)
        
        # ПРОВЕРКА ДУБЛИРОВАНИЯ - ДОБАВЛЕНО
        active_sessions = crud.get_active_sessions_by_user_and_file(db, user.id, file.id)
        if active_sessions:
            print(f"⚠️ Active session already exists, returning existing: {active_sessions[0].id}")
            return {"id": str(active_sessions[0].id), "status": "existing"}
        
        # Создаем сессию
        session_data = schemas.FileSessionCreate(
            user_id=user.id,
            file_id=file.id,
            started_at=datetime.now(),
            last_activity=datetime.now(),
            hash_before=session_data.get('file_hash'),
            resume_count=session_data.get('resume_count', 0)
        )
        
        session = crud.create_file_session(db, session_data)
        
        print(f"✅ Created session {session.id} for {file_path}")
        return {"id": str(session.id), "status": "created"}
        
    except Exception as e:
        print(f"❌ Error creating session: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating session: {str(e)}")

# Простые эндпоинты для проверки данных
@app.get("/api/users")
async def get_users(db: Session = Depends(get_db)):
    """Получает список пользователей"""
    users = db.query(models.User).all()
    return {"users": [{"id": str(user.id), "username": user.username} for user in users]}

@app.get("/api/files")
async def get_files(db: Session = Depends(get_db)):
    """Получает список файлов"""
    files = db.query(models.File).all()
    return {"files": [{"id": str(file.id), "path": file.file_path, "name": file.file_name} for file in files]}

@app.get("/api/sessions")
async def get_sessions(db: Session = Depends(get_db)):
    """Получает список сессий"""
    sessions = db.query(models.FileSession).all()
    return {"sessions": [
        {
            "id": str(session.id), 
            "user_id": str(session.user_id),
            "file_id": str(session.file_id),
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "last_activity": session.last_activity.isoformat(),
            "resume_count": session.resume_count,
            "is_commented": session.is_commented
        } for session in sessions
    ]}

@app.get("/api/events")
async def get_events(db: Session = Depends(get_db)):
    """Получает список событий"""
    events = db.query(models.FileEvent).all()
    return {"events": [
        {
            "id": str(event.id),
            "session_id": str(event.session_id),
            "event_type": event.event_type,
            "file_hash": event.file_hash,
            "timestamp": event.event_timestamp.isoformat()
        } for event in events
    ]}

# НОВЫЕ ЭНДПОИНТЫ ДЛЯ КОММЕНТАРИЕВ

@app.post("/api/comments", response_model=schemas.Comment)
async def create_comment(comment: schemas.CommentCreate, db: Session = Depends(get_db)):
    """Создает комментарий для сессии"""
    try:
        # Проверяем существование сессии
        session = crud.get_file_session(db, comment.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Проверяем существование пользователя
        user = crud.get_user(db, comment.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Проверяем, нет ли уже комментария для этой сессии
        existing_comment = crud.get_comment_by_session(db, comment.session_id)
        if existing_comment:
            raise HTTPException(status_code=400, detail="Comment already exists for this session")
        
        session.ended_at = datetime.now()
        session.is_commented = True

        # Создаем комментарий
        db_comment = crud.create_comment(db, comment)

        db.commit()

        return db_comment
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating comment: {str(e)}")

@app.get("/api/comments", response_model=List[schemas.CommentWithUser])
async def get_comments(
    skip: int = 0, 
    limit: int = 100,
    change_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Получает список комментариев с возможностью фильтрации по типу изменения"""
    try:
        # Базовый запрос с join пользователя
        query = db.query(
            models.Comment,
            models.User.username
        ).join(
            models.User, models.Comment.user_id == models.User.id
        )
        
        # Фильтрация по типу изменения если указан
        if change_type:
            query = query.filter(models.Comment.change_type == change_type)
        
        comments = query.offset(skip).limit(limit).all()
        
        # Преобразуем в схему
        result = []
        for comment, username in comments:
            result.append(schemas.CommentWithUser(
                id=comment.id,
                content=comment.content,
                change_type=comment.change_type,
                created_at=comment.created_at,
                username=username
            ))
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting comments: {str(e)}")

@app.get("/api/comments/{session_id}", response_model=schemas.CommentWithUser)
async def get_comment_by_session(session_id: str, db: Session = Depends(get_db)):
    """Получает комментарий по ID сессии"""
    try:
        session_uuid = uuid.UUID(session_id)
        result = crud.get_comment_with_user(db, session_uuid)
        
        if not result:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        comment, username = result
        return schemas.CommentWithUser(
            id=comment.id,
            content=comment.content,
            change_type=comment.change_type,
            created_at=comment.created_at,
            username=username
        )
        
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting comment: {str(e)}")

@app.get("/api/sessions-with-comments", response_model=List[schemas.SessionWithDetails])
async def get_sessions_with_comments(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Получает сессии с комментариями"""
    try:
        sessions = crud.get_sessions_with_comments(db, skip=skip, limit=limit)
        
        result = []
        for session, file_path, file_name, username, comment in sessions:
            session_details = schemas.SessionWithDetails(
                id=session.id,
                started_at=session.started_at,
                ended_at=session.ended_at,
                last_activity=session.last_activity,
                resume_count=session.resume_count,
                file_path=file_path,
                file_name=file_name,
                username=username,
                comment=None
            )
            
            if comment:
                session_details.comment = schemas.CommentWithUser(
                    id=comment.id,
                    content=comment.content,
                    change_type=comment.change_type,
                    created_at=comment.created_at,
                    username=username
                )
            
            result.append(session_details)
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting sessions: {str(e)}")

@app.get("/api/change-types")
async def get_change_types():
    """Возвращает доступные типы изменений"""
    return {
        "change_types": [
            "technical_changes",    # Изменение технических решений
            "design_changes",       # Изменение оформления/дизайна
            "content_changes",      # Изменение содержания
            "bug_fixes",           # Исправление ошибок
            "optimization",        # Оптимизация
            "refactoring",         # Рефакторинг
            "new_feature",         # Новая функциональность
            "documentation",       # Изменение документации
            "other"                # Другое
        ]
    }

@app.get("/api/sessions/{session_id}/details", response_model=schemas.SessionWithDetails)
async def get_session_details(session_id: str, db: Session = Depends(get_db)):
    """Получает детальную информацию о сессии"""
    try:
        session_uuid = uuid.UUID(session_id)
        result = crud.get_session_with_details(db, session_uuid)
        
        if not result:
            raise HTTPException(status_code=404, detail="Session not found")
        
        session, file_path, file_name, username, comment = result
        
        session_details = schemas.SessionWithDetails(
            id=session.id,
            started_at=session.started_at,
            ended_at=session.ended_at,
            last_activity=session.last_activity,
            resume_count=session.resume_count,
            file_path=file_path,
            file_name=file_name,
            username=username,
            comment=None
        )
        
        if comment:
            session_details.comment = schemas.CommentWithUser(
                id=comment.id,
                content=comment.content,
                change_type=comment.change_type,
                created_at=comment.created_at,
                username=username
            )
        
        return session_details
        
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting session details: {str(e)}")

# [file name]: main.py (ДОПОЛНЕННАЯ ВЕРСИЯ - добавляем в конец файла)

# НОВЫЕ ЭНДПОИНТЫ ДЛЯ ОПРЕДЕЛЕНИЯ ТЕКУЩИХ РЕДАКТОРОВ
@app.get("/api/current-editors/{file_path}")
async def get_current_editors(file_path: str, db: Session = Depends(get_db)):
    """Возвращает текущих редакторов файла на основе активных сессий"""
    try:
        # Находим файл
        file = crud.get_file_by_path(db, file_path)
        if not file:
            return {"current_editors": [], "file_exists": False}
        
        # Находим активные сессии для этого файла
        active_sessions = db.query(models.FileSession).filter(
            models.FileSession.file_id == file.id,
            models.FileSession.ended_at.is_(None)
        ).all()
        
        current_editors = []
        for session in active_sessions:
            user = crud.get_user(db, session.user_id)
            if user:
                current_editors.append({
                    "username": user.username,
                    "last_activity": session.last_activity.isoformat(),
                    "session_id": str(session.id)
                })
        
        # Сортируем по последней активности (сначала самые активные)
        current_editors.sort(key=lambda x: x["last_activity"], reverse=True)
        
        return {
            "file_path": file_path,
            "file_name": file.file_name,
            "current_editors": current_editors,
            "total_editors": len(current_editors),
            "file_exists": True
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting current editors: {str(e)}")

@app.get("/api/multi-user-files")
async def get_multi_user_files(db: Session = Depends(get_db)):
    """Возвращает файлы с несколькими активными редакторами"""
    try:
        # Находим файлы с более чем одной активной сессией
        multi_user_files = []
        
        # Получаем все активные сессии сгруппированные по файлам
        active_sessions_by_file = db.query(
            models.FileSession.file_id,
            models.File.file_path,
            models.File.file_name
        ).join(
            models.File, models.FileSession.file_id == models.File.id
        ).filter(
            models.FileSession.ended_at.is_(None)
        ).group_by(
            models.FileSession.file_id,
            models.File.file_path,
            models.File.file_name
        ).having(
            db.func.count(models.FileSession.id) > 1
        ).all()
        
        for file_id, file_path, file_name in active_sessions_by_file:
            # Получаем редакторов для этого файла
            editors = db.query(
                models.User.username,
                models.FileSession.last_activity
            ).join(
                models.FileSession, models.FileSession.user_id == models.User.id
            ).filter(
                models.FileSession.file_id == file_id,
                models.FileSession.ended_at.is_(None)
            ).all()
            
            multi_user_files.append({
                "file_path": file_path,
                "file_name": file_name,
                "editors": [{"username": editor[0], "last_activity": editor[1].isoformat()} for editor in editors],
                "editor_count": len(editors)
            })
        
        return {
            "multi_user_files": multi_user_files,
            "total_multi_user_files": len(multi_user_files)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting multi-user files: {str(e)}")

@app.put("/api/users/{user_id}/username")
async def update_username(
    user_id: str, 
    username_update: schemas.UsernameUpdate,  # Используем схему
    db: Session = Depends(get_db)
):
    """Обновляет username пользователя"""
    try:
        # Проверяем валидность UUID
        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID format")
        
        # Получаем пользователя
        user = crud.get_user(db, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        new_username = username_update.username
        
        # Проверяем, что новый username не пустой
        if not new_username.strip():
            raise HTTPException(status_code=400, detail="Username cannot be empty")
        
        # Проверяем, не занят ли username другим пользователем
        existing_user = crud.get_user_by_username(db, new_username)
        if existing_user and existing_user.id != user_uuid:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Сохраняем старый username для логов
        old_username = user.username
        
        # Обновляем username
        user.username = new_username
        db.commit()
        db.refresh(user)
        
        print(f"✅ Updated username for user {user_id}: {old_username} -> {new_username}")
        
        return {
            "id": str(user.id),
            "old_username": old_username,
            "new_username": user.username,
            "email": user.email,
            "updated_at": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating username: {str(e)}")

@app.get("/api/user-activity/{username}")
async def get_user_activity(username: str, db: Session = Depends(get_db)):
    """Возвращает активность пользователя"""
    try:
        user = crud.get_user_by_username(db, username)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Получаем все сессии пользователя
        all_sessions = db.query(models.FileSession).filter(
            models.FileSession.user_id == user.id
        ).all()

        active_files = []
        recent_files = []

        for session in all_sessions:
            # Получаем информацию о файле
            file = crud.get_file(db, session.file_id)
            if not file:
                continue

            session_info = {
                "file_path": file.file_path,
                "file_name": file.file_name,
                "session_started": session.started_at.isoformat(),
                "last_activity": session.last_activity.isoformat(),
                "session_id": str(session.id),
                "resume_count": session.resume_count,
                "is_commented": session.is_commented
            }

            if session.ended_at is None:
                # Активная сессия
                active_files.append(session_info)
            else:
                # Закрытая сессия (проверяем, была ли она в последние 24 часа)
                time_diff = datetime.now() - session.ended_at
                if time_diff <= timedelta(hours=24):
                    session_info["session_ended"] = session.ended_at.isoformat()
                    session_info["session_duration"] = (session.ended_at - session.started_at).total_seconds()
                    recent_files.append(session_info)

        return {
            "username": username,
            "user_id": str(user.id),
            "active_files": active_files,
            "recent_files": recent_files,
            "active_count": len(active_files),
            "recent_count": len(recent_files)
        }
        
    except Exception as e:
        print(f"❌ Error in get_user_activity: {str(e)}")
        import traceback
        print(f"🔍 Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error getting user activity: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)