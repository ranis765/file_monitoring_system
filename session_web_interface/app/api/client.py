import aiohttp
import json
from typing import Dict, List, Optional

class APIClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = None
    
    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def _request(self, method: str, endpoint: str, **kwargs):
        await self._ensure_session()
        url = f"{self.base_url}{endpoint}"
        
        try:
            async with self.session.request(method, url, **kwargs) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API returned status {response.status}: {error_text}")
                return await response.json()
        except aiohttp.ClientError as e:
            raise Exception(f"API request failed: {str(e)}")
        except Exception as e:
            raise Exception(f"API error: {str(e)}")
    
    async def get_users(self) -> Dict:
        return await self._request("GET", "/api/users")
    
    async def get_user_activity(self, username: str) -> Dict:
        # Просто передаем русское имя как есть - aiohttp сам разберется с кодированием
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
    
    async def close(self):
        if self.session:
            await self.session.close()

# Global API client instance
api_client = APIClient()