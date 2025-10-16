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

app = FastAPI(title="Session Web Interface", version="1.0.0")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# API client
api_client = APIClient(base_url="http://localhost:8000")

# Полностью отключаем авторизацию - теперь без фиксированного пользователя
DISABLE_AUTH = True

# Простое хранилище сессий (в памяти, для демо)
user_sessions = {}

def get_username(request: Request):
    """Get username from session"""
    if DISABLE_AUTH:
        # Получаем session_id из cookies
        session_id = request.cookies.get("session_id")
        if session_id and session_id in user_sessions:
            return user_sessions[session_id]
        return None
    return None

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    if DISABLE_AUTH:
        # Проверяем, есть ли уже пользователь в сессии
        username = get_username(request)
        if username:
            # Если пользователь уже выбран, перенаправляем на дашборд
            return RedirectResponse(url="/dashboard", status_code=303)
        # Иначе показываем страницу выбора пользователя
        return templates.TemplateResponse("user_select.html", {"request": request})
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(response: Response, username: str = Form(...)):
    """Handle user login"""
    if DISABLE_AUTH:
        # Создаем уникальный session_id
        session_id = str(uuid.uuid4())
        # Сохраняем пользователя в сессии
        user_sessions[session_id] = username
        
        # Сохраняем session_id в cookies
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            key="session_id", 
            value=session_id, 
            httponly=True,
            max_age=3600  # 1 час
        )
        return response
    
    # Для нормального режима (если включим авторизацию)
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
    """Main dashboard page"""
    username = get_username(request)
    
    # Если авторизация отключена и нет пользователя, показываем выбор пользователя
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    # Если все еще нет username и авторизация включена - редирект на логин
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        # Get user activity for stats
        user_activity = await api_client.get_user_activity(username)
        
        # Get all sessions and filter by user
        all_sessions = await api_client.get_sessions()
        user_sessions_list = []
        
        # Get user ID first
        users_data = await api_client.get_users()
        user_obj = next((u for u in users_data.get("users", []) if u["username"] == username), None)
        
        if user_obj:
            user_sessions_list = [
                session for session in all_sessions.get("sessions", [])
                if session.get("user_id") == user_obj["id"]
            ]
        else:
            # Если пользователь не найден в API, показываем все сессии
            user_sessions_list = all_sessions.get("sessions", [])
        
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "username": username,
            "sessions": user_sessions_list,
            "user_activity": user_activity
        })
    except Exception as e:
        print(f"Error in dashboard: {e}")  # Для отладки
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "username": username,
            "sessions": [],
            "error": f"Error loading sessions: {str(e)}"
        })

@app.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request):
    """Sessions page with commenting functionality"""
    username = get_username(request)
    
    # Если авторизация отключена и нет пользователя, показываем выбор пользователя
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    # Если все еще нет username и авторизация включена - редирект на логин
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        # Get active sessions and user info
        user_activity = await api_client.get_user_activity(username)
        change_types = await api_client.get_change_types()
        
        return templates.TemplateResponse("sessions.html", {
            "request": request,
            "username": username,
            "active_files": user_activity.get("active_files", []),
            "recent_files": user_activity.get("recent_files", []),
            "change_types": change_types.get("change_types", [])
        })
    except Exception as e:
        print(f"Error in sessions: {e}")  # Для отладки
        return templates.TemplateResponse("sessions.html", {
            "request": request,
            "username": username,
            "active_files": [],
            "recent_files": [],
            "change_types": [],
            "error": f"Error loading data: {str(e)}"
        })

@app.post("/comment")
async def add_comment(
    request: Request,
    session_id: str = Form(...),
    content: str = Form(...),
    change_type: str = Form(...),
    username: str = Form(...)
):
    """Submit a comment for a session"""
    try:
        # Get user ID
        users_data = await api_client.get_users()
        user = next((u for u in users_data.get("users", []) if u["username"] == username), None)
        
        if not user:
            # Если пользователь не найден, создаем его (для демо)
            user_data = {"username": username, "email": f"{username}@example.com"}
            # Здесь можно добавить вызов API для создания пользователя если нужно
            user = {"id": str(uuid.uuid4()), "username": username}  # Временный ID для демо
        
        # Create comment
        comment_data = {
            "session_id": session_id,
            "user_id": user["id"],
            "content": content,
            "change_type": change_type
        }
        
        await api_client.create_comment(comment_data)
        
        return RedirectResponse(url="/sessions?message=Comment+added+successfully", status_code=303)
    
    except Exception as e:
        print(f"Error adding comment: {e}")  # Для отладки
        return RedirectResponse(url=f"/sessions?error={str(e)}", status_code=303)

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """User history page"""
    username = get_username(request)
    
    # Если авторизация отключена и нет пользователя, показываем выбор пользователя
    if DISABLE_AUTH and not username:
        return RedirectResponse(url="/", status_code=303)
    
    # Если все еще нет username и авторизация включена - редирект на логин
    if not username and not DISABLE_AUTH:
        return RedirectResponse(url="/")
    
    try:
        # Get sessions with comments
        sessions_with_comments = await api_client.get_sessions_with_comments()
        
        # Filter user's sessions
        user_sessions_with_comments = [
            session for session in sessions_with_comments 
            if session.get("username") == username
        ]
        
        return templates.TemplateResponse("history.html", {
            "request": request,
            "username": username,
            "sessions": user_sessions_with_comments
        })
    except Exception as e:
        print(f"Error in history: {e}")  # Для отладки
        return templates.TemplateResponse("history.html", {
            "request": request,
            "username": username,
            "sessions": [],
            "error": f"Error loading history: {str(e)}"
        })

@app.get("/api/user-sessions/{username}")
async def get_user_sessions_api(username: str):
    """API endpoint to get user sessions"""
    try:
        user_activity = await api_client.get_user_activity(username)
        return user_activity
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Новый эндпоинт для смены пользователя
@app.get("/change-user")
async def change_user(response: Response, request: Request):
    """Change current user"""
    # Удаляем сессию
    session_id = request.cookies.get("session_id")
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="session_id")
    return response

# Простой эндпоинт для проверки работы
@app.get("/health")
async def health_check():
    return {"status": "ok", "auth_disabled": DISABLE_AUTH}

# Эндпоинт для отладки
@app.get("/debug")
async def debug_cookies(request: Request):
    username = get_username(request)
    session_id = request.cookies.get("session_id")
    return {
        "username": username,
        "session_id": session_id,
        "all_cookies": request.cookies,
        "active_sessions_count": len(user_sessions),
        "auth_disabled": DISABLE_AUTH
    }

# Очистка старых сессий при запуске
@app.on_event("startup")
async def startup_event():
    user_sessions.clear()

if __name__ == "__main__":
    # Запускаем без reload, чтобы избежать warning
    uvicorn.run(app, host="0.0.0.0", port=8001)