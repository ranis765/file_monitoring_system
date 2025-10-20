from fastapi import FastAPI, Request, Form, Depends, HTTPException, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
import uuid
from datetime import datetime
import os
import uvicorn
from typing import Optional, List, Dict

import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
app = FastAPI(title="Веб-интерфейс сессий", version="1.0.0")
from app.api.client import APIClient
# Mount static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# API client
api_client = APIClient(base_url="http://localhost:8000")

# Полностью отключаем авторизацию
DISABLE_AUTH = True

# Простое хранилище сессий (в памяти, для демо)
user_sessions = {}
def format_datetime(dt_string: str) -> str:
    """Форматирует дату из формата '2025-10-20T14:19:56' в '14:19 20.10.2025'"""
    if not dt_string:
        return "Неизвестно"
    
    try:
        # Парсим исходную дату
        dt = datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
        # Форматируем в нужный формат: "время дата"
        return dt.strftime("%H:%M %d.%m.%Y")
    except (ValueError, AttributeError):
        return "Неизвестно"

def format_date_only(dt_string: str) -> str:
    """Форматирует только дату из формата '2025-10-20T14:19:56' в '20.10.2025'"""
    if not dt_string:
        return "Неизвестно"
    
    try:
        dt = datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
        return dt.strftime("%d.%m.%Y")
    except (ValueError, AttributeError):
        return "Неизвестно"

def format_time_only(dt_string: str) -> str:
    """Форматирует только время из формата '2025-10-20T14:19:56' в '14:19'"""
    if not dt_string:
        return "Неизвестно"
    
    try:
        dt = datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
        return dt.strftime("%H:%M")
    except (ValueError, AttributeError):
        return "Неизвестно"
    
# После создания templates добавьте фильтры
templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["format_date"] = format_date_only
templates.env.filters["format_time"] = format_time_only
def get_username(request: Request):
    """Получить имя пользователя из сессии"""
    if DISABLE_AUTH:
        session_id = request.cookies.get("session_id")
        if session_id and session_id in user_sessions:
            return user_sessions[session_id]
        return None
    return None

def extract_filename(file_path: str) -> str:
    """Извлечь имя файла из пути"""
    if not file_path or file_path.strip() == "":
        return "Неизвестно"
    file_path = file_path.replace('\\', '/')
    return os.path.basename(file_path) or "Неизвестно"

async def get_file_info(file_id: str) -> dict:
    """Получить информацию о файле по file_id"""
    if not file_id or file_id.strip() == "":
        print(f"Ошибка: file_id пустой или отсутствует")
        return {"file_path": "Неизвестно", "file_name": "Неизвестно"}
    
    try:
        print(f"Отправка запроса для file_id: {file_id}")  # Отладка
        file_data = await api_client.get_file(file_id)
        print(f"Получены данные для file_id {file_id}: {file_data}")  # Отладка
        return file_data
    except Exception as e:
        print(f"Ошибка при получении данных файла {file_id}: {str(e)}")  # Отладка
        return {"file_path": "Неизвестно", "file_name": "Неизвестно"}

async def get_session_comments(session_id: str) -> List[Dict]:
    """Получить комментарии для сессии"""
    try:
        comments = await api_client.get_session_comments(session_id)
        return comments if isinstance(comments, list) else []
    except Exception as e:
        print(f"Ошибка при получении комментариев для сессии {session_id}: {str(e)}")
        return []

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа"""
    if DISABLE_AUTH:
        username = get_username(request)
        if username:
            return RedirectResponse(url="/dashboard", status_code=303)
        return templates.TemplateResponse("user_select.html", {"request": request})
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(response: Response, username: str = Form(...)):
    """Обработка входа пользователя"""
    if DISABLE_AUTH:
        session_id = str(uuid.uuid4())
        user_sessions[session_id] = username
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            key="session_id", 
            value=session_id, 
            httponly=True,
            max_age=3600
        )
        return response
    
    session_id = str(uuid.uuid4())
    user_sessions[session_id] = username
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session_id", 
        value=session_id, 
        httponly=True,
        max_age=3600
    )
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Главная панель"""
    username = get_username(request)
    
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        print(f"Загрузка дашборда для пользователя: {username}")  # Отладка
        user_activity = await api_client.get_user_activity(username)
        all_sessions = await api_client.get_sessions()
        print(f"API sessions data: {all_sessions}")  # Отладка
        
        users_data = await api_client.get_users()
        user_obj = next((u for u in users_data.get("users", []) if u["username"].lower() == username.lower()), None)
        
        user_sessions_list = []
        if user_obj:
            for s in all_sessions.get("sessions", []):
                if s.get("user_id") == user_obj["id"]:
                    session_data = {
                        **s,
                        "file_name": (await get_file_info(s.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно"))),
                        "file_path": (await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно")
                    }
                    # Получаем комментарии для сессии
                    comments = await get_session_comments(s.get("id"))
                    if comments:
                        session_data["comments"] = comments
                        session_data["last_comment"] = comments[0]  # Последний комментарий
                    user_sessions_list.append(session_data)
        else:
            print(f"Пользователь {username} не найден, фильтрация по username")
            for s in all_sessions.get("sessions", []):
                if s.get("username", "").lower() == username.lower():
                    session_data = {
                        **s,
                        "file_name": (await get_file_info(s.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно"))),
                        "file_path": (await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно")
                    }
                    # Получаем комментарии для сессии
                    comments = await get_session_comments(s.get("id"))
                    if comments:
                        session_data["comments"] = comments
                        session_data["last_comment"] = comments[0]  # Последний комментарий
                    user_sessions_list.append(session_data)
        
        active_uncommented = [
            session for session in user_sessions_list 
            if not session.get("ended_at") or not session.get("is_commented")
        ]
        
        print(f"Найдено сессий пользователя: {len(user_sessions_list)}")  # Отладка
        print(f"Processed active_uncommented: {active_uncommented}")  # Отладка

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "username": username,
            "sessions": user_sessions_list,
            "active_uncommented": active_uncommented,
            "user_activity": user_activity
        })
    except Exception as e:
        print(f"Ошибка в панели: {str(e)}")
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "username": username,
            "sessions": [],
            "active_uncommented": [],
            "error": f"Ошибка загрузки сессий: {str(e)}"
        })

@app.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request):
    """Страница сессий с функцией комментирования"""
    username = get_username(request)
    
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        print(f"Загрузка сессий для пользователя: {username}")  # Отладка
        user_activity = await api_client.get_user_activity(username)
        change_types = await api_client.get_change_types()
        all_sessions = await api_client.get_sessions()
        print(f"API sessions data: {all_sessions}")  # Отладка
        
        users_data = await api_client.get_users()
        print(f"Users data: {users_data}")  # Отладка
        user_obj = next((u for u in users_data.get("users", []) if u["username"].lower() == username.lower()), None)
        
        user_sessions_list = []
        if user_obj:
            for s in all_sessions.get("sessions", []):
                if s.get("user_id") == user_obj["id"]:
                    session_data = {
                        **s,
                        "file_name": (await get_file_info(s.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно"))),
                        "file_path": (await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно")
                    }
                    # Получаем комментарии для сессии
                    comments = await get_session_comments(s.get("id"))
                    if comments:
                        session_data["comments"] = comments
                        session_data["last_comment"] = comments[0]  # Последний комментарий
                    user_sessions_list.append(session_data)
        else:
            print(f"Пользователь {username} не найден в /api/users, использую фильтрацию по username")
            for s in all_sessions.get("sessions", []):
                if s.get("username", "").lower() == username.lower():
                    session_data = {
                        **s,
                        "file_name": (await get_file_info(s.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно"))),
                        "file_path": (await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно")
                    }
                    # Получаем комментарии для сессии
                    comments = await get_session_comments(s.get("id"))
                    if comments:
                        session_data["comments"] = comments
                        session_data["last_comment"] = comments[0]  # Последний комментарий
                    user_sessions_list.append(session_data)
        
        print(f"Найдено сессий пользователя: {len(user_sessions_list)}")  # Отладка
        sorted_sessions = sorted(user_sessions_list, key=lambda x: x.get("started_at", ""), reverse=True)
        print(f"Обработанные сессии: {sorted_sessions}")  # Отладка

        return templates.TemplateResponse("sessions.html", {
            "request": request,
            "username": username,
            "active_files": user_activity.get("active_files", []),
            "change_types": change_types.get("change_types", []),
            "sessions": sorted_sessions
        })
    except Exception as e:
        print(f"Ошибка в сессиях: {str(e)}")
        return templates.TemplateResponse("sessions.html", {
            "request": request,
            "username": username,
            "active_files": [],
            "change_types": [],
            "sessions": [],
            "error": f"Ошибка загрузки данных: {str(e)}"
        })

@app.get("/session/{session_id}", response_class=HTMLResponse)
async def session_detail(request: Request, session_id: str):
    """Карточка сессии"""
    username = get_username(request)
    
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        print(f"Загрузка данных сессии {session_id} для пользователя: {username}")  # Отладка
        all_sessions = await api_client.get_sessions()
        session = next((s for s in all_sessions.get("sessions", []) if s.get("id") == session_id), None)
        
        if not session:
            return templates.TemplateResponse("session_detail.html", {
                "request": request,
                "username": username,
                "error": f"Сессия с ID {session_id} не найдена"
            })
        
        users_data = await api_client.get_users()
        user_obj = next((u for u in users_data.get("users", []) if u["username"].lower() == username.lower()), None)
        
        if user_obj and session.get("user_id") != user_obj["id"] and session.get("username", "").lower() != username.lower():
            return templates.TemplateResponse("sessions.html", {
                "request": request,
                "username": username,
                "error": "У вас нет доступа к этой сессии"
            })
        
        file_info = await get_file_info(session.get("file_id", ""))
        session["file_name"] = file_info.get("file_name", extract_filename(file_info.get("file_path", "Неизвестно")))
        session["file_path"] = file_info.get("file_path", "Неизвестно")
        
        # Получаем комментарии для сессии
        comments = await get_session_comments(session_id)
        if comments:
            session["comments"] = comments
            session["last_comment"] = comments[0]  # Последний комментарий
        
        return templates.TemplateResponse("session_detail.html", {
            "request": request,
            "username": username,
            "session": session
        })
    except Exception as e:
        print(f"Ошибка в карточке сессии: {str(e)}")
        return templates.TemplateResponse("session_detail.html", {
            "request": request,
            "username": username,
            "error": f"Ошибка загрузки сессии: {str(e)}"
        })
@app.post("/comment")
async def add_comment(
    request: Request,
    session_id: str = Form(...),
    content: str = Form(...),
    change_type: str = Form(...),
    username: str = Form(...)
):
    """Добавить комментарий к сессии"""
    try:
        users_data = await api_client.get_users()
        user = next((u for u in users_data.get("users", []) if u["username"].lower() == username.lower()), None)
        
        if not user:
            user_data = {"username": username, "email": f"{username}@example.com"}
            user = {"id": str(uuid.uuid4()), "username": username}
        
        comment_data = {
            "session_id": session_id,
            "user_id": user["id"],
            "content": content,
            "change_type": change_type
        }
        
        await api_client.create_comment(comment_data)
        
        return RedirectResponse(url="/sessions?message=Комментарий+добавлен+успешно", status_code=303)
    
    except Exception as e:
        print(f"Ошибка добавления комментария: {str(e)}")
        return RedirectResponse(url=f"/sessions?error={str(e)}", status_code=303)

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """Страница истории пользователя"""
    username = get_username(request)
    
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        sessions_with_comments = await api_client.get_sessions_with_comments()
        
        user_sessions_with_comments = [
            {
                **session,
                "file_name": (await get_file_info(session.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(session.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(session.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for session in sessions_with_comments 
            if session.get("username", "").lower() == username.lower()
        ]
        
        return templates.TemplateResponse("history.html", {
            "request": request,
            "username": username,
            "sessions": user_sessions_with_comments
        })
    except Exception as e:
        print(f"Ошибка в истории: {str(e)}")
        return templates.TemplateResponse("history.html", {
            "request": request,
            "username": username,
            "sessions": [],
            "error": f"Ошибка загрузки истории: {str(e)}"
        })

@app.get("/all-history", response_class=HTMLResponse)
async def all_history_page(request: Request, sort_by: str = "date", project: str = None, change_type: str = None):
    """Страница общей истории"""
    username = get_username(request)
    
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        # Получаем все сессии и пользователей
        all_sessions_data = await api_client.get_sessions()
        all_sessions = all_sessions_data.get("sessions", [])
        users_data = await api_client.get_users()
        users_map = {user["id"]: user["username"] for user in users_data.get("users", [])}
        
        # Обрабатываем каждую сессию
        processed_sessions = []
        for session in all_sessions:
            try:
                # Получаем username из user_id
                user_id = session.get("user_id")
                session_username = users_map.get(user_id, "Неизвестно")
                
                # Если username не найден по user_id, пробуем получить из самой сессии
                if session_username == "Неизвестно":
                    session_username = session.get("username", "Неизвестно")
                
                # Получаем информацию о файле
                file_info = await get_file_info(session.get("file_id", ""))
                
                # Создаем обработанную сессию
                processed_session = {
                    **session,
                    "username": session_username,
                    "file_name": file_info.get("file_name", extract_filename(file_info.get("file_path", "Неизвестно"))),
                    "file_path": file_info.get("file_path", "Неизвестно")
                }
                
                # Получаем комментарии для сессии
                comments = await get_session_comments(session.get("id"))
                if comments:
                    processed_session["comments"] = comments
                    processed_session["last_comment"] = comments[0]  # Последний комментарий
                    # Добавляем поле comment для обратной совместимости
                    processed_session["comment"] = comments[0]
                
                processed_sessions.append(processed_session)
                
            except Exception as e:
                print(f"Ошибка обработки сессии {session.get('id')}: {str(e)}")
                # Добавляем сессию с базовой информацией даже при ошибке
                processed_sessions.append({
                    **session,
                    "username": session.get("username", "Неизвестно"),
                    "file_name": "Ошибка загрузки",
                    "file_path": "Ошибка загрузки",
                    "comments": [],
                    "last_comment": None
                })
        
        # Сортировка
        if sort_by == "user":
            processed_sessions.sort(key=lambda x: x.get("username", ""))
        elif sort_by == "date":
            processed_sessions.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        elif sort_by == "project":
            processed_sessions.sort(key=lambda x: x.get("file_path", ""))
        elif sort_by == "change_type":
            processed_sessions.sort(key=lambda x: x.get("last_comment", {}).get("change_type", "") if x.get("last_comment") else "")
        
        # Фильтрация
        if project:
            processed_sessions = [s for s in processed_sessions if project.lower() in s.get("file_path", "").lower()]
        if change_type:
            processed_sessions = [s for s in processed_sessions if s.get("last_comment", {}).get("change_type") == change_type]

        print(f"Обработано сессий для all-history: {len(processed_sessions)}")
        for session in processed_sessions[:3]:  # Вывод первых 3 для отладки
            print(f"Сессия: пользователь={session.get('username')}, файл={session.get('file_name')}")

        return templates.TemplateResponse("all_history.html", {
            "request": request,
            "username": username,
            "sessions": processed_sessions,
            "sort_by": sort_by,
            "project": project,
            "change_type": change_type
        })
    except Exception as e:
        print(f"Ошибка в общей истории: {str(e)}")
        import traceback
        traceback.print_exc()
        return templates.TemplateResponse("all_history.html", {
            "request": request,
            "username": username,
            "sessions": [],
            "error": f"Ошибка загрузки истории: {str(e)}"
        })



@app.get("/api/user-sessions/{username}")
async def get_user_sessions_api(username: str):
    """API endpoint для сессий пользователя"""
    try:
        user_activity = await api_client.get_user_activity(username)
        user_activity["active_files"] = [
            {
                **f,
                "file_name": (await get_file_info(f.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for f in user_activity.get("active_files", [])
        ]
        return user_activity
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/change-user")
async def change_user(response: Response, request: Request):
    """Смена текущего пользователя"""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="session_id")
    return response

@app.get("/health")
async def health_check():
    """Проверка состояния сервера"""
    return {"status": "ok", "auth_disabled": DISABLE_AUTH}

@app.get("/debug")
async def debug_cookies(request: Request):
    """Отладка cookies и сессий"""
    username = get_username(request)
    session_id = request.cookies.get("session_id")
    return {
        "username": username,
        "session_id": session_id,
        "all_cookies": request.cookies,
        "active_sessions_count": len(user_sessions),
        "auth_disabled": DISABLE_AUTH
    }

@app.on_event("startup")
async def startup_event():
    """Очистка сессий при старте"""
    user_sessions.clear()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)