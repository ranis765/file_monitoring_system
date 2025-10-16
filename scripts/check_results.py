#!/usr/bin/env python3
import requests
import json
from datetime import datetime

def check_system_state(server_url="http://localhost:8000"):
    """Проверяет состояние системы после симуляции"""
    print("🔍 Checking System State After Simulation")
    print("=" * 50)
    
    try:
        # 1. Проверяем здоровье сервера
        health_response = requests.get(f"{server_url}/health")
        print(f"✅ Server Health: {health_response.status_code}")
        
        # 2. Получаем список пользователей
        users_response = requests.get(f"{server_url}/api/users")
        if users_response.status_code == 200:
            users_data = users_response.json()
            print(f"👥 Registered Users: {len(users_data.get('users', []))}")
            for user in users_data.get('users', []):
                print(f"   - {user['username']}")
        
        # 3. Получаем список файлов
        files_response = requests.get(f"{server_url}/api/files")
        if files_response.status_code == 200:
            files_data = files_response.json()
            print(f"📁 Tracked Files: {len(files_data.get('files', []))}")
        
        # 4. Получаем активные сессии
        sessions_response = requests.get(f"{server_url}/api/sessions")
        if sessions_response.status_code == 200:
            sessions_data = sessions_response.json()
            active_sessions = [s for s in sessions_data.get('sessions', []) if s.get('ended_at') is None]
            print(f"🔄 Active Sessions: {len(active_sessions)}")
        
        # 5. Получаем многопользовательские файлы
        multi_user_response = requests.get(f"{server_url}/api/multi-user-files")
        if multi_user_response.status_code == 200:
            multi_user_data = multi_user_response.json()
            multi_user_files = multi_user_data.get('multi_user_files', [])
            print(f"👥 Multi-User Files: {len(multi_user_files)}")
            
            for file_info in multi_user_files:
                editors = [editor['username'] for editor in file_info.get('editors', [])]
                print(f"   📄 {file_info['file_name']}")
                print(f"      Editors: {', '.join(editors)}")
                print(f"      Total editors: {file_info['editor_count']}")
        
        # 6. Получаем события
        events_response = requests.get(f"{server_url}/api/events")
        if events_response.status_code == 200:
            events_data = events_response.json()
            print(f"📨 Total Events: {len(events_data.get('events', []))}")
            
            # Группируем события по типам
            event_types = {}
            for event in events_data.get('events', []):
                event_type = event.get('event_type', 'unknown')
                event_types[event_type] = event_types.get(event_type, 0) + 1
            
            print("   Event types:")
            for event_type, count in event_types.items():
                print(f"      {event_type}: {count}")
        
        print("\n✅ System check completed!")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error connecting to server: {e}")
    except Exception as e:
        print(f"❌ Error during system check: {e}")

def test_current_editors(server_url="http://localhost:8000"):
    """Тестирует определение текущих редакторов для конкретных файлов"""
    print("\n🎯 Testing Current Editors Detection")
    print("=" * 50)
    
    try:
        # Получаем список файлов
        files_response = requests.get(f"{server_url}/api/files")
        if files_response.status_code == 200:
            files_data = files_response.json()
            
            # Тестируем для нескольких файлов
            for file_info in files_data.get('files', [])[:5]:  # Первые 5 файлов
                file_path = file_info['path']
                
                editors_response = requests.get(f"{server_url}/api/current-editors/{file_path}")
                if editors_response.status_code == 200:
                    editors_data = editors_response.json()
                    
                    if editors_data.get('file_exists'):
                        editors = editors_data.get('current_editors', [])
                        print(f"📄 {file_info['name']}:")
                        print(f"   Current editors: {len(editors)}")
                        for editor in editors:
                            print(f"   - {editor['username']} (last activity: {editor['last_activity']})")
                    else:
                        print(f"📄 {file_info['name']}: File not found in monitoring")
                
                print()  # Пустая строка между файлами
        
        print("✅ Current editors test completed!")
        
    except Exception as e:
        print(f"❌ Error testing current editors: {e}")

if __name__ == "__main__":
    check_system_state()
    test_current_editors()