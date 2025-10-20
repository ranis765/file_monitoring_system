# main.py (сервер)
import sys
import os
import requests
import asyncio
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
from .schemas import FileSessionCreate
from .crud import get_active_session_by_user_and_file, create_file_session, update_file_session_activity

async def notify_agents_about_event(endpoint: str, data: dict):
    """Асинхронно уведомляет агенты о событиях"""
    # Получаем список агентов из конфигурации
    agents = api_config.get('agents', ['http://localhost:8080'])
    
    async def notify_agent(agent_url: str):
        try:
            full_url = f"{agent_url}/api/agent/{endpoint}"
            response = requests.post(
                full_url,
                json=data,
                timeout=5
            )
            if response.status_code == 200:
                print(f"✅ Notified agent {agent_url} about {endpoint}")
            else:
                print(f"⚠️ Agent {agent_url} returned {response.status_code}")
        except Exception as e:
            print(f"🔇 Could not notify agent {agent_url}: {e}")
    
    # Запускаем уведомления асинхронно
    tasks = [notify_agent(agent_url) for agent_url in agents]
    await asyncio.gather(*tasks, return_exceptions=True)
# Загружаем конфигурацию API
api_config = get_api_config()

async def sync_sessions_with_agents(db: Session):
    agents = api_config.get('agents', ['http://localhost:8080'])
    for agent_url in agents:
        try:
            response = requests.get(f"{agent_url}/api/agent/active-sessions", timeout=5)
            if response.status_code == 200:
                agent_sessions = response.json().get("sessions", [])
                # Создаём множество ключей сессий агента для быстрого поиска
                agent_session_keys = {
                    f"{s['file_path']}:{s['username']}" for s in agent_sessions
                }
                # Получаем все активные сессии на сервере
                server_sessions = db.query(models.FileSession).filter(
                    models.FileSession.ended_at.is_(None)
                ).all()
                # Закрываем серверные сессии, которых нет в агенте
                for server_session in server_sessions:
                    file = crud.get_file(db, server_session.file_id)
                    user = crud.get_user(db, server_session.user_id)
                    session_key = f"{file.file_path}:{user.username}"
                    if session_key not in agent_session_keys and not server_session.is_commented:
                        server_session.ended_at = datetime.now()
                        db.commit()
                        print(f"Closed orphaned server session: {session_key}")
                # Создаём/обновляем сессии от агента
                for session in agent_sessions:
                    file = crud.get_file_by_path(db, session["file_path"])
                    if not file:
                        file = crud.create_file(db, schemas.FileCreate(
                            file_path=session["file_path"],
                            file_name=os.path.basename(session["file_path"])
                        ))
                    user = crud.get_user_by_username(db, session["username"])
                    if not user:
                        user = crud.create_user(db, schemas.UserCreate(
                            username=session["username"]
                        ))
                    db_session = crud.get_active_session_by_user_and_file(db, user.id, file.id)
                    if not db_session:
                        session_data = schemas.FileSessionCreate(
                            user_id=user.id,
                            file_id=file.id,
                            started_at=datetime.fromisoformat(session["started_at"]),
                            last_activity=datetime.fromisoformat(session["last_activity"]),
                            hash_before=session["hash_before"],
                            resume_count=session["resume_count"],
                            is_commented=session["is_commented"],
                            id=uuid.UUID(session["session_id"]) if session["session_id"] else None
                        )
                        crud.create_file_session(db, session_data)
                    else:
                        if db_session.last_activity < datetime.fromisoformat(session["last_activity"]):
                            db_session.last_activity = datetime.fromisoformat(session["last_activity"])
                            db_session.hash_before = session["hash_before"]
                            db.commit()
            else:
                print(f"Agent {agent_url} returned {response.status_code}")
        except Exception as e:
            print(f"Failed to sync with agent {agent_url}: {e}")

app = FastAPI(
    title="File Monitoring API",
    description="API для отслеживания изменений файлов с системой комментариев",
    version="1.0.0"
)


@app.on_event("startup")
async def startup_event():
    db = next(get_db())
    await sync_sessions_with_agents(db)
    db.close()


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

@app.get("/api/files/{file_id}", response_model=schemas.File)
async def get_file(file_id: str, db: Session = Depends(get_db)):
    try:
        file_uuid = uuid.UUID(file_id)
        db_file = crud.get_file(db, file_uuid)
        if db_file is None:
            raise HTTPException(status_code=404, detail="File not found")
        return db_file
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting file: {str(e)}")
    
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
        return await handle_file_created(db, user.id, file.id, event_data, file_hash, resume_count, session_id)
    elif event_type == 'modified':
        return await handle_file_modified(db, user.id, file.id, event_data, file_hash, session_id, resume_count)
    elif event_type == 'deleted':
        return await handle_file_deleted(db, user.id, file.id, event_data, session_id)
    elif event_type == 'closed':
        return await handle_file_closed(db, session_id, file_hash, event_data)
    elif event_type == 'moved':
        return await handle_file_moved(db, event_data, file_hash)
    elif event_type == 'timeout':
        return await handle_session_timeout(db, session_id, event_data)
    else:
        raise ValueError(f"Unknown event type: {event_type}")
    

# async def handle_file_created(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, file_hash: str = None, resume_count: int = 0, session_id: str = None):
#     """Обрабатывает создание файла - создает новую сессию только если нет активных"""
    
#     # ПРОВЕРКА: есть ли активная сессия у этого пользователя для этого файла
#     active_session = crud.get_active_session_by_user_and_file(db, user_id, file_id)
    
#     if active_session:
#         # Если есть активная сессия - обновляем ее вместо создания новой
#         print(f"🔄 Active session {active_session.id} exists, updating instead of creating new")
        
#         # СОХРАНЯЕМ ФЛАГ is_commented ПРИ ОБНОВЛЕНИИ
#         is_commented = active_session.is_commented
        
#         active_session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
#         if file_hash:
#             active_session.hash_before = file_hash
#         active_session.resume_count = resume_count
        
#         # ВОССТАНАВЛИВАЕМ ФЛАГ is_commented
#         active_session.is_commented = is_commented
#         db.commit()
#         db.refresh(active_session)
        
#         # Создаем событие created
#         event_record = schemas.FileEventCreate(
#             session_id=active_session.id,
#             event_type='created',
#             file_hash=file_hash,
#             event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
#         )
#         crud.create_file_event(db, event_record)
        
#         print(f"🔄 Updated existing session {active_session.id} for created file")
#         return {"action": "session_updated", "session_id": str(active_session.id), "resumed": False}
    
#     # ПРОВЕРКА: есть ли закрытая сессия для возобновления (только если resume_count > 0)
#     recent_session = None
#     if resume_count > 0:
#         recent_session = crud.get_recent_closed_session(db, user_id, file_id)
    
#     # ВАЖНОЕ ИСПРАВЛЕНИЕ: Не возобновляем прокомментированные сессии
#     if recent_session and recent_session.is_commented:
#         print(f"🚫 Cannot resume commented session {recent_session.id} for user {user_id}")
#         recent_session = None
    
#     if recent_session:
#         # Возобновляем существующую сессию ТОЛЬКО если она не была прокомментирована
#         recent_session.ended_at = None
#         recent_session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
#         recent_session.resume_count = resume_count
#         recent_session.hash_before = file_hash
        
#         db.commit()
#         db.refresh(recent_session)
        
#         print(f"🔄 Resumed session {recent_session.id} (resume count: {resume_count})")
#         session = recent_session
#     else:
#         # Создаем новую сессию
#         session_data_dict = {
#             'user_id': user_id,
#             'file_id': file_id,
#             'started_at': datetime.fromisoformat(event_data.get('event_timestamp')),
#             'last_activity': datetime.fromisoformat(event_data.get('event_timestamp')),
#             'hash_before': file_hash,
#             'resume_count': resume_count,
#             'is_commented': False  # НОВАЯ СЕССИЯ - КОММЕНТАРИЕВ НЕТ
#         }
        
#         # Если session_id валидный и не существует, используем его
#         if session_id:
#             try:
#                 session_uuid = uuid.UUID(session_id)
#                 existing_session = crud.get_file_session(db, session_uuid)
#                 if not existing_session:
#                     session_data_dict['id'] = session_uuid
#                     print(f"🎯 Using provided session ID for created event: {session_id}")
#             except ValueError:
#                 print(f"⚠️ Invalid session ID for created event, generating new one")
        
#         session_data = schemas.FileSessionCreate(**session_data_dict)
#         session = crud.create_file_session_with_id(db, session_data)
#         print(f"✅ Created NEW session {session.id} for file {event_data.get('file_path')}")
    
#     # Создаем событие файла
#     event_record = schemas.FileEventCreate(
#         session_id=session.id,
#         event_type='created',
#         file_hash=file_hash,
#         event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
#     )
#     crud.create_file_event(db, event_record)
    
#     return {"action": "session_created", "session_id": str(session.id), "resumed": recent_session is not None}

async def handle_file_created(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, file_hash: str = None, resume_count: int = 0, session_id: str = None):
    """Обрабатывает создание файла - создает новую сессию только если нет активных"""
    
    # ПРОВЕРКА: есть ли активная сессия у этого пользователя для этого файла
    active_session = crud.get_active_session_by_user_and_file(db, user_id, file_id)
    
    if active_session:
        # Если есть активная сессия - обновляем ее вместо создания новой
        print(f"🔄 Active session {active_session.id} exists, updating instead of creating new")
        
        # СОХРАНЯЕМ ФЛАГ is_commented ПРИ ОБНОВЛЕНИИ
        is_commented = active_session.is_commented
        
        active_session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
        if file_hash:
            active_session.hash_before = file_hash
        active_session.resume_count = resume_count
        
        # ВОССТАНАВЛИВАЕМ ФЛАГ is_commented
        active_session.is_commented = is_commented
        db.commit()
        db.refresh(active_session)
        
        # Создаем событие created
        event_record = schemas.FileEventCreate(
            session_id=active_session.id,
            event_type='created',
            file_hash=file_hash,
            event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
        )
        crud.create_file_event(db, event_record)
        
        print(f"🔄 Updated existing session {active_session.id} for created file")
        return {"action": "session_updated", "session_id": str(active_session.id), "resumed": False}
    
    # ПРОВЕРКА: есть ли закрытая сессия для возобновления (только если resume_count > 0)
    recent_session = None
    if resume_count > 0:
        recent_session = crud.get_recent_closed_session(db, user_id, file_id)
    
    # ВАЖНОЕ ИСПРАВЛЕНИЕ: Не возобновляем прокомментированные сессии
    if recent_session and recent_session.is_commented:
        print(f"🚫 Cannot resume commented session {recent_session.id} for user {user_id}")
        recent_session = None
    
    if recent_session:
        # Возобновляем существующую сессию ТОЛЬКО если она не была прокомментирована
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
        session_data_dict = {
            'user_id': user_id,
            'file_id': file_id,
            'started_at': datetime.fromisoformat(event_data.get('event_timestamp')),
            'last_activity': datetime.fromisoformat(event_data.get('event_timestamp')),
            'hash_before': file_hash,
            'resume_count': resume_count,
            'is_commented': False  # НОВАЯ СЕССИЯ - КОММЕНТАРИЕВ НЕТ
        }
        
        # Если session_id валидный и не существует, используем его
        if session_id:
            try:
                session_uuid = uuid.UUID(session_id)
                existing_session = crud.get_file_session(db, session_uuid)
                if not existing_session:
                    session_data_dict['id'] = session_uuid
                    print(f"🎯 Using provided session ID for created event: {session_id}")
            except ValueError:
                print(f"⚠️ Invalid session ID for created event, generating new one")
        
        session_data = schemas.FileSessionCreate(**session_data_dict)
        session = crud.create_file_session_with_id(db, session_data)
        print(f"✅ Created NEW session {session.id} for file {event_data.get('file_path')}")
    
    # Создаем событие файла
    event_record = schemas.FileEventCreate(
        session_id=session.id,
        event_type='created',
        file_hash=file_hash,
        event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
    )
    crud.create_file_event(db, event_record)
    
    return {"action": "session_created", "session_id": str(session.id), "resumed": recent_session is not None}

async def handle_session_timeout(db: Session, session_id: str, event_data: dict = None):
    """Обрабатывает таймаут сессии - закрывает сессию"""
    print(f"⏰ Processing session timeout for session: {session_id}")
    
    if session_id:
        try:
            session_uuid = uuid.UUID(session_id)
            session = crud.get_file_session(db, session_uuid)
            if session and session.ended_at is None:
                # Закрываем сессию по таймауту
                ended_at = datetime.fromisoformat(event_data['event_timestamp']) if event_data and 'event_timestamp' in event_data else datetime.now()
                
                session.ended_at = ended_at
                db.commit()
                
                # Создаем событие timeout
                event_timestamp = ended_at
                event_record = schemas.FileEventCreate(
                    session_id=session.id,
                    event_type='timeout',
                    file_hash=session.hash_after,
                    event_timestamp=event_timestamp
                )
                crud.create_file_event(db, event_record)
                
                file = crud.get_file(db, session.file_id)
                user = crud.get_user(db, session.user_id)
                
                duration = session.ended_at - session.started_at
                print(f"⏰ SUCCESS: Closed session {session.id} due to timeout (user: {user.username}, file: {file.file_path}, duration: {duration})")
                
                return {
                    "action": "session_timeout",
                    "session_id": str(session.id),
                    "duration": str(duration),
                    "ended_at": session.ended_at.isoformat()
                }
            else:
                print(f"❌ Session not found or already closed: {session_id}")
                return {"action": "session_not_found_or_closed"}
        except ValueError as e:
            print(f"❌ Invalid session ID format: {session_id}, error: {e}")
            return {"action": "invalid_session_id"}
    
    print(f"❌ No session ID provided for timeout event")
    return {"action": "no_session_id"}

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

async def handle_file_created(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, file_hash: str = None, resume_count: int = 0, session_id: str = None):
    """Обрабатывает создание файла - создает новую сессию"""
    # ПРОВЕРКА ДУБЛИРОВАНИЯ СЕССИЙ
    active_sessions = crud.get_active_sessions_by_user_and_file(db, user_id, file_id)
    if active_sessions:
        print(f"⚠️ Active session already exists for file, updating instead of creating new")
        session = active_sessions[0]
        session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
        if file_hash:
            session.hash_before = file_hash
        session.resume_count = resume_count
        # СОХРАНЯЕМ ФЛАГ is_commented ПРИ ОБНОВЛЕНИИ
        is_commented = session.is_commented
        db.commit()
        db.refresh(session)
        # ВОССТАНАВЛИВАЕМ ФЛАГ is_commented
        session.is_commented = is_commented
        db.commit()
        
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
        # СОХРАНЯЕМ ФЛАГ is_commented ПРИ ВОЗОБНОВЛЕНИИ
        is_commented = recent_session.is_commented
        db.commit()
        db.refresh(recent_session)
        # ВОССТАНАВЛИВАЕМ ФЛАГ is_commented
        recent_session.is_commented = is_commented
        db.commit()
        
        print(f"🔄 Resumed session {recent_session.id} (resume count: {resume_count})")
        session = recent_session
    else:
        # Создаем новую сессию, но используем session_id из события если он валидный
        session_data_dict = {
            'user_id': user_id,
            'file_id': file_id,
            'started_at': datetime.fromisoformat(event_data.get('event_timestamp')),
            'last_activity': datetime.fromisoformat(event_data.get('event_timestamp')),
            'hash_before': file_hash,
            'resume_count': resume_count,
            'is_commented': False  # НОВАЯ СЕССИЯ - КОММЕНТАРИЕВ ЕЩЕ НЕТ
        }
        
        # Если session_id валидный, используем его
        if session_id:
            try:
                session_uuid = uuid.UUID(session_id)
                # Проверяем, не существует ли уже сессия с таким ID
                existing_session = crud.get_file_session(db, session_uuid)
                if not existing_session:
                    session_data_dict['id'] = session_uuid
                    print(f"🎯 Using provided session ID for created event: {session_id}")
            except ValueError:
                print(f"⚠️ Invalid session ID for created event, generating new one")
        
        session_data = schemas.FileSessionCreate(**session_data_dict)
        session = crud.create_file_session_with_id(db, session_data)
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

# async def handle_file_modified(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, file_hash: str = None, session_id: str = None, resume_count: int = 0):
#     """Обрабатывает изменение файла - обновляет существующую сессию или создает новую"""
#     session = None
    
#     if session_id:
#         # Пытаемся найти существующую сессию по ID из события
#         try:
#             session_uuid = uuid.UUID(session_id)
#             session = crud.get_file_session(db, session_uuid)
#             if session and session.is_commented:
#                 # ВАЖНОЕ ИСПРАВЛЕНИЕ: Не используем прокомментированные сессии
#                 print(f"🚫 Session {session.id} is commented, creating new session")
#                 session = None
#             elif session:
#                 print(f"🔍 Found session by ID: {session.id}")
#         except ValueError:
#             print(f"⚠️ Invalid session ID format: {session_id}")
#             pass  # Невалидный UUID
    
#     if not session:
#         # Ищем активную сессию для этого пользователя и файла
#         active_sessions = crud.get_active_sessions_by_user_and_file(db, user_id, file_id)
#         if active_sessions:
#             session = active_sessions[0]
#             print(f"🔍 Found active session by user and file: {session.id}")
    
#     if session:
#         # СОХРАНЯЕМ ФЛАГ is_commented ПЕРЕД ОБНОВЛЕНИЕМ
#         is_commented = session.is_commented
        
#         # Обновляем существующую сессию
#         session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
#         if file_hash:
#             session.hash_after = file_hash
#         if resume_count > session.resume_count:
#             session.resume_count = resume_count
        
#         # ВОССТАНАВЛИВАЕМ ФЛАГ is_commented
#         session.is_commented = is_commented
        
#         db.commit()
#         db.refresh(session)
        
#         print(f"📝 Updated session {session.id} for file {event_data.get('file_path')} (resume: {resume_count}, is_commented: {session.is_commented})")
#         action = "session_updated"
#     else:
#         # Создаем новую сессию, но используем session_id из события если он валидный
#         session_data_dict = {
#             'user_id': user_id,
#             'file_id': file_id,
#             'started_at': datetime.fromisoformat(event_data.get('event_timestamp')),
#             'last_activity': datetime.fromisoformat(event_data.get('event_timestamp')),
#             'hash_before': file_hash,
#             'resume_count': resume_count,
#             'is_commented': False  # НОВАЯ СЕССИЯ - КОММЕНТАРИЕВ ЕЩЕ НЕТ
#         }
        
#         # Если session_id валидный, используем его
#         if session_id:
#             try:
#                 session_uuid = uuid.UUID(session_id)
#                 # Проверяем, не существует ли уже сессия с таким ID
#                 existing_session = crud.get_file_session(db, session_uuid)
#                 if not existing_session:
#                     session_data_dict['id'] = session_uuid
#                     print(f"🎯 Using provided session ID for modified event: {session_id}")
#             except ValueError:
#                 print(f"⚠️ Invalid session ID for modified event, generating new one")
        
#         session_data = schemas.FileSessionCreate(**session_data_dict)
#         session = crud.create_file_session_with_id(db, session_data)
#         print(f"✅ Created new session {session.id} for modified file {event_data.get('file_path')}")
#         action = "session_created"
    
#     # Создаем событие файла
#     event_record = schemas.FileEventCreate(
#         session_id=session.id,
#         event_type='modified',
#         file_hash=file_hash,
#         event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
#     )
#     crud.create_file_event(db, event_record)
    
#     return {"action": action, "session_id": str(session.id)}

async def handle_file_modified(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, file_hash: str = None, session_id: str = None, resume_count: int = 0):
    """Обрабатывает изменение файла - обновляет существующую сессию или создает новую"""
    session = None
    
    if session_id:
        # Пытаемся найти существующую сессию по ID из события
        try:
            session_uuid = uuid.UUID(session_id)
            session = crud.get_file_session(db, session_uuid)
            if session and session.is_commented:
                # ВАЖНОЕ ИСПРАВЛЕНИЕ: Не используем прокомментированные сессии
                print(f"🚫 Session {session.id} is commented, creating new session")
                session = None
            elif session:
                print(f"🔍 Found session by ID: {session.id}")
        except ValueError:
            print(f"⚠️ Invalid session ID format: {session_id}")
            pass  # Невалидный UUID
    
    if not session:
        # Ищем активную сессию для этого пользователя и файла
        active_session = crud.get_active_session_by_user_and_file(db, user_id, file_id)
        if active_session:
            session = active_session
            print(f"🔍 Found active session by user and file: {session.id}")
    
    if session:
        # СОХРАНЯЕМ ФЛАГ is_commented ПЕРЕД ОБНОВЛЕНИЕМ
        is_commented = session.is_commented
        
        # Обновляем существующую сессию
        session.last_activity = datetime.fromisoformat(event_data.get('event_timestamp'))
        if file_hash:
            session.hash_after = file_hash
        if resume_count > session.resume_count:
            session.resume_count = resume_count
        
        # ВОССТАНАВЛИВАЕМ ФЛАГ is_commented
        session.is_commented = is_commented
        
        db.commit()
        db.refresh(session)
        
        print(f"📝 Updated session {session.id} for file {event_data.get('file_path')} (resume: {resume_count}, is_commented: {session.is_commented})")
        action = "session_updated"
    else:
        # Создаем новую сессию, но используем session_id из события если он валидный
        session_data_dict = {
            'user_id': user_id,
            'file_id': file_id,
            'started_at': datetime.fromisoformat(event_data.get('event_timestamp')),
            'last_activity': datetime.fromisoformat(event_data.get('event_timestamp')),
            'hash_before': file_hash,
            'resume_count': resume_count,
            'is_commented': False  # НОВАЯ СЕССИЯ - КОММЕНТАРИЕВ ЕЩЕ НЕТ
        }
        
        # Если session_id валидный, используем его
        if session_id:
            try:
                session_uuid = uuid.UUID(session_id)
                # Проверяем, не существует ли уже сессия с таким ID
                existing_session = crud.get_file_session(db, session_uuid)
                if not existing_session:
                    session_data_dict['id'] = session_uuid
                    print(f"🎯 Using provided session ID for modified event: {session_id}")
            except ValueError:
                print(f"⚠️ Invalid session ID for modified event, generating new one")
        
        session_data = schemas.FileSessionCreate(**session_data_dict)
        session = crud.create_file_session_with_id(db, session_data)
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

# async def handle_file_deleted(db: Session, user_id: uuid.UUID, file_id: uuid.UUID, event_data: dict, session_id: str = None):
#     """Обрабатывает удаление файла - закрывает сессию и создает событие deleted"""
#     print(f"🗑️ Processing file deletion for file_id: {file_id}, session_id: {session_id}")
    
#     session = None
    
#     if session_id:
#         # Пытаемся найти существующую сессию
#         try:
#             session_uuid = uuid.UUID(session_id)
#             session = crud.get_file_session(db, session_uuid)
#         except ValueError:
#             pass  # Невалидный UUID
    
#     if not session:
#         # Ищем активную сессию для этого пользователя и файла
#         active_sessions = crud.get_active_sessions_by_user_and_file(db, user_id, file_id)
#         if active_sessions:
#             session = active_sessions[0]
#             print(f"🔍 Found active session for deletion: {session.id}")
    
#     if session:
#         # Закрываем сессию с временем из события
#         session.ended_at = datetime.fromisoformat(event_data.get('event_timestamp'))
#         db.commit()
#         db.refresh(session)
        
#         # Создаем событие удаления
#         event_record = schemas.FileEventCreate(
#             session_id=session.id,
#             event_type='deleted',
#             file_hash=None,  # Для удаленного файла хеша нет
#             event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
#         )
#         crud.create_file_event(db, event_record)
        
#         print(f"🗑️ Closed session {session.id} for deleted file and created deleted event")
#         return {"action": "session_closed", "session_id": str(session.id)}
#     else:
#         print(f"⚠️ No active session found for deleted file, creating standalone deleted event")
        
#         # Если сессии нет, все равно создаем событие deleted
#         # Создаем временную сессию для привязки события
#         session_data = schemas.FileSessionCreate(
#             user_id=user_id,
#             file_id=file_id,
#             started_at=datetime.fromisoformat(event_data.get('event_timestamp')),
#             last_activity=datetime.fromisoformat(event_data.get('event_timestamp')),
#             ended_at=datetime.fromisoformat(event_data.get('event_timestamp')),  # Сразу закрываем
#             hash_before=None,
#             resume_count=0
#         )
        
#         session = crud.create_file_session(db, session_data)
        
#         # Создаем событие удаления
#         event_record = schemas.FileEventCreate(
#             session_id=session.id,
#             event_type='deleted',
#             file_hash=None,
#             event_timestamp=datetime.fromisoformat(event_data.get('event_timestamp'))
#         )
#         crud.create_file_event(db, event_record)
        
#         print(f"🗑️ Created standalone deleted event with session {session.id}")
#         return {"action": "deleted_event_created", "session_id": str(session.id)}

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
        active_session = crud.get_active_session_by_user_and_file(db, user_id, file_id)
        if active_session:
            session = active_session
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
    print(f"🔒 Processing file close for session: {session_id}")
    
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
                print(f"🔒 SUCCESS: Closed session {session.id} for file {file.file_path} (user: {user.username}, duration: {duration}, ended_at: {session.ended_at})")
                
                await notify_agents_about_event("close-session", {
                    "session_id": str(session.id),
                    "file_path": file.file_path,
                    "username": user.username,
                    "ended_at": session.ended_at.isoformat()
                })

                return {
                    "action": "session_closed", 
                    "session_id": str(session.id),
                    "duration": str(duration),
                    "ended_at": session.ended_at.isoformat(),
                    "resume_count": session.resume_count
                }
            else:
                print(f"❌ Session not found: {session_id}")
                return {"action": "session_not_found"}
        except ValueError as e:
            print(f"❌ Invalid session ID format: {session_id}, error: {e}")
            return {"action": "invalid_session_id"}
    
    print(f"❌ No session ID provided for close event")
    return {"action": "no_session_id"}


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
        
        # ПРОВЕРКА ДУБЛИРОВАНИЯ
        active_session = crud.get_active_session_by_user_and_file(db, user.id, file.id)
        if active_session:
            print(f"⚠️ Active session already exists, returning existing: {active_session.id}")
            return {"id": str(active_session.id), "status": "existing"}
        
        # Создаем сессию
        session_data = schemas.FileSessionCreate(
            user_id=user.id,
            file_id=file.id,
            started_at=datetime.now(),
            last_activity=datetime.now(),
            hash_before=session_data.get('file_hash'),
            resume_count=session_data.get('resume_count', 0),
            is_commented=False
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
    return {"sessions": [{
        "id": str(session.id),
        "user_id": str(session.user_id),
        "file_id": str(session.file_id),
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "last_activity": session.last_activity,
        "resume_count": session.resume_count,
        "is_commented": session.is_commented
    } for session in sessions]}

@app.get("/api/events")
async def get_events(db: Session = Depends(get_db)):
    """Получает список событий"""
    events = db.query(models.FileEvent).all()
    return {"events": [{
        "id": str(event.id),
        "session_id": str(event.session_id),
        "event_type": event.event_type,
        "timestamp": event.event_timestamp
    } for event in events]}

# НОВЫЕ ЭНДПОИНТЫ ДЛЯ КОММЕНТАРИЕВ (возвращены для веб-страницы)

@app.post("/api/comments", response_model=schemas.Comment)
async def create_comment(comment: schemas.CommentCreate, db: Session = Depends(get_db)):
    """Создает комментарий для сессии и гарантирует ее закрытие"""
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
        
        # ГАРАНТИРУЕМ, что сессия закрыта при комментировании
        if session.ended_at is None:
            session.ended_at = datetime.now()
            print(f"🔒 Auto-closing session {session.id} due to commenting")
        
        # Устанавливаем флаг комментирования
        session.is_commented = True

        # Создаем комментарий
        db_comment = crud.create_comment(db, comment)
        db.commit()

        file = crud.get_file(db, session.file_id)
        await notify_agents_about_event("comment-created", {
            "session_id": str(comment.session_id),
            "file_path": file.file_path,
            "username": user.username,
            "comment": {
                "content": comment.content,
                "change_type": comment.change_type,
                "created_at": db_comment.created_at.isoformat()
            }
        })

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

@app.get("/api/sessions/{session_id}/comments", response_model=List[schemas.Comment])
async def get_comments_by_session(session_id: str, db: Session = Depends(get_db)):
    """Получает комментарии для конкретной сессии"""
    try:
        session_uuid = uuid.UUID(session_id)
        
        # Получаем комментарии для сессии
        comments = db.query(models.Comment).filter(
            models.Comment.session_id == session_uuid
        ).all()
        
        return comments
        
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting comments: {str(e)}")

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
    uvicorn.run(
        app, 
        host=api_config.get('host', '127.0.0.1'), 
        port=api_config.get('port', 8000)
    )