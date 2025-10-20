import aiohttp
import json
from typing import Dict, List, Optional

class APIClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = None
    
    async def _ensure_session(self):
        if self.session is None:
            print("Создание новой ClientSession")  # Отладка
            self.session = aiohttp.ClientSession()
    
    async def _request(self, method: str, endpoint: str, **kwargs):
        await self._ensure_session()
        url = f"{self.base_url}{endpoint}"
        
        print(f"Выполнение {method} запроса к {url}")  # Отладка
        try:
            async with self.session.request(method, url, **kwargs) as response:
                print(f"Получен ответ от {url}: статус {response.status}")  # Отладка
                if response.status == 404:
                    return None  # Возвращаем None для 404 ошибок
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API вернул статус {response.status}: {error_text}")
                return await response.json()
        except aiohttp.ClientError as e:
            print(f"Ошибка ClientError при запросе к {url}: {str(e)}")  # Отладка
            raise Exception(f"API запрос не удался: {str(e)}")
        except Exception as e:
            print(f"Общая ошибка при запросе к {url}: {str(e)}")  # Отладка
            raise Exception(f"API ошибка: {str(e)}")
    
    async def get_users(self) -> Dict:
        return await self._request("GET", "/api/users")
    
    async def get_user_activity(self, username: str) -> Dict:
        return await self._request("GET", f"/api/user-activity/{username}")
    
    async def get_sessions_with_comments(self) -> List[Dict]:
        result = await self._request("GET", "/api/sessions-with-comments")
        return result if isinstance(result, list) else []
    
    async def get_sessions(self) -> Dict:
        return await self._request("GET", "/api/sessions")
    
    async def get_change_types(self) -> Dict:
        return await self._request("GET", "/api/change-types")
    
    async def create_comment(self, comment_data: Dict) -> Dict:
        return await self._request("POST", "/api/comments", json=comment_data)
    
    async def get_file(self, file_id: str) -> Dict:
        print(f"Вызов get_file для file_id: {file_id}")  # Отладка
        return await self._request("GET", f"/api/files/{file_id}")
    
    async def get_session_comments(self, session_id: str) -> List[Dict]:
        """Получить комментарии для конкретной сессии"""
        print(f"Вызов get_session_comments для session_id: {session_id}")  # Отладка
        
        # Используем существующий endpoint для получения комментария по session_id
        try:
            result = await self._request("GET", f"/api/comments/{session_id}")
            if result is not None:
                # Если найден комментарий, возвращаем его в списке
                return [result]
            return []
        except Exception as e:
            print(f"Ошибка при получении комментария для сессии {session_id}: {str(e)}")
            return []
    
    async def close(self):
        if self.session:
            print("Закрытие ClientSession")  # Отладка
            await self.session.close()

# Global API client instance
api_client = APIClient()