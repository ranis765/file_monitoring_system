
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
import uuid
from datetime import datetime
import os
import uvicorn

# Исправляем импорт с абсолютным путем
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.client import APIClient

app = FastAPI(title="Веб-интерфейс сессий", version="1.0.0")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# API client
api_client = APIClient(base_url="http://localhost:8000")

# Полностью отключаем авторизацию
DISABLE_AUTH = True

# Простое хранилище сессий (в памяти, для демо)
user_sessions = {}

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
        
        user_sessions_list = []
        users_data = await api_client.get_users()
        user_obj = next((u for u in users_data.get("users", []) if u["username"] == username), None)
        
        if user_obj:
            user_sessions_list = [
                session for session in all_sessions.get("sessions", [])
                if session.get("user_id") == user_obj["id"]
            ]
        else:
            user_sessions_list = all_sessions.get("sessions", [])
        
        print(f"Найдено сессий пользователя: {len(user_sessions_list)}")  # Отладка
        
        # Обогащаем сессии полями file_name и file_path
        active_uncommented = []
        for s in user_sessions_list:
            file_id = s.get("file_id", "")
            print(f"Обработка сессии {s.get('id')} с file_id: {file_id}")  # Отладка
            if not s.get("ended_at") or not s.get("is_commented"):
                file_info = await get_file_info(file_id)
                file_path = file_info.get("file_path", "Неизвестно")
                file_name = file_info.get("file_name", extract_filename(file_path))
                active_uncommented.append({
                    **s,
                    "file_name": file_name,
                    "file_path": file_path
                })
        
        user_sessions_list = [
            {
                **s,
                "file_name": (await get_file_info(s.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for s in user_sessions_list
        ]
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
        user_activity = await api_client.get_user_activity(username)
        change_types = await api_client.get_change_types()
        
        user_activity["active_files"] = [
            {
                **f,
                "file_name": (await get_file_info(f.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for f in user_activity.get("active_files", [])
        ]
        user_activity["recent_files"] = [
            {
                **f,
                "file_name": (await get_file_info(f.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for f in user_activity.get("recent_files", [])
        ]
        
        all_sessions = await api_client.get_sessions()
        user_sessions_list = [
            {
                **s,
                "file_name": (await get_file_info(s.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for s in all_sessions.get("sessions", []) if s.get("username") == username
        ]
        sorted_sessions = sorted(user_sessions_list, key=lambda x: x.get("started_at", ""), reverse=True)

        return templates.TemplateResponse("sessions.html", {
            "request": request,
            "username": username,
            "active_files": user_activity.get("active_files", []),
            "recent_files": user_activity.get("recent_files", []),
            "change_types": change_types.get("change_types", []),
            "sessions": sorted_sessions
        })
    except Exception as e:
        print(f"Ошибка в сессиях: {str(e)}")
        return templates.TemplateResponse("sessions.html", {
            "request": request,
            "username": username,
            "active_files": [],
            "recent_files": [],
            "change_types": [],
            "sessions": [],
            "error": f"Ошибка загрузки данных: {str(e)}"
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
        user = next((u for u in users_data.get("users", []) if u["username"] == username), None)
        
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
            if session.get("username") == username
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
        all_sessions_with_comments = await api_client.get_sessions_with_comments()
        
        all_sessions_with_comments = [
            {
                **s,
                "file_name": (await get_file_info(s.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(s.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for s in all_sessions_with_comments
        ]
        
        if sort_by == "user":
            all_sessions_with_comments.sort(key=lambda x: x.get("username", ""))
        elif sort_by == "date":
            all_sessions_with_comments.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        elif sort_by == "project":
            all_sessions_with_comments.sort(key=lambda x: x.get("file_path", ""))
        elif sort_by == "change_type":
            all_sessions_with_comments.sort(key=lambda x: x.get("comment", {}).get("change_type", ""))
        
        if project:
            all_sessions_with_comments = [s for s in all_sessions_with_comments if project in s.get("file_path", "")]
        if change_type:
            all_sessions_with_comments = [s for s in all_sessions_with_comments if s.get("comment", {}).get("change_type") == change_type]

        return templates.TemplateResponse("all_history.html", {
            "request": request,
            "username": username,
            "sessions": all_sessions_with_comments,
            "sort_by": sort_by,
            "project": project,
            "change_type": change_type
        })
    except Exception as e:
        print(f"Ошибка в общей истории: {str(e)}")
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
        user_activity["recent_files"] = [
            {
                **f,
                "file_name": (await get_file_info(f.get("file_id", ""))).get("file_name", extract_filename((await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно"))),
                "file_path": (await get_file_info(f.get("file_id", ""))).get("file_path", "Неизвестно")
            }
            for f in user_activity.get("recent_files", [])
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
