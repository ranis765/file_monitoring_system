#!/usr/bin/env python3
import os
import time
import threading
import random
import shutil
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile

# Добавляем путь к вашему агенту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitoring_agent.app.file_monitor import FileMonitor
from shared.logger import setup_logger
import requests

class MockAPIClient:
    """Mock API клиент для симуляции отправки событий на сервер"""
    
    def __init__(self, agent_id, server_url="http://localhost:8000"):
        self.agent_id = agent_id
        self.server_url = server_url
        self.sent_events = []
        self.logger = setup_logger(f"API_{agent_id}")
    
    def send_event(self, event_data):
        """Отправляет событие на сервер"""
        try:
            # Добавляем идентификатор агента
            event_data['agent_id'] = self.agent_id
            event_data['simulated'] = True
            
            # Логируем отправку
            self.logger.info(f"📨 [{self.agent_id}] Sending {event_data['event_type']} for {event_data['file_path']}")
            
            # Сохраняем для отладки
            self.sent_events.append({
                'timestamp': datetime.now(),
                'event': event_data
            })
            
            # Отправляем на реальный сервер если он запущен
            try:
                response = requests.post(
                    f"{self.server_url}/api/events",
                    json=event_data,
                    timeout=5
                )
                if response.status_code == 200:
                    self.logger.info(f"✅ [{self.agent_id}] Event delivered to server")
                    return True
                else:
                    self.logger.warning(f"⚠️ [{self.agent_id}] Server returned {response.status_code}")
            except requests.exceptions.RequestException:
                self.logger.warning(f"🌐 [{self.agent_id}] Server not available, event queued")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ [{self.agent_id}] Error sending event: {e}")
            return False
    
    def test_connection(self):
        """Проверяет соединение с сервером"""
        try:
            response = requests.get(f"{self.server_url}/health", timeout=5)
            return response.status_code == 200
        except:
            return False

class UserSimulator:
    """Симулятор действий пользователя"""
    
    def __init__(self, user_id, test_directory, agent_id):
        self.user_id = user_id
        self.test_directory = Path(test_directory)
        self.agent_id = agent_id
        self.logger = setup_logger(f"User_{user_id}")
        self.created_files = []
        self.modified_files = []
        
        # Создаем директорию для пользователя
        self.user_dir = self.test_directory / f"user_{user_id}"
        self.user_dir.mkdir(exist_ok=True)
        
        self.logger.info(f"👤 User {user_id} initialized in {self.user_dir}")
    
    def create_file(self, filename, content=None):
        """Создает файл"""
        file_path = self.user_dir / filename
        
        if content is None:
            content = f"Content created by {self.user_id} at {datetime.now()}"
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            self.created_files.append(str(file_path))
            self.logger.info(f"📄 [{self.user_id}] Created file: {filename}")
            return str(file_path)
            
        except Exception as e:
            self.logger.error(f"❌ [{self.user_id}] Error creating file {filename}: {e}")
            return None
    
    def modify_file(self, file_path, additional_content=None):
        """Изменяет файл"""
        try:
            if not os.path.exists(file_path):
                self.logger.warning(f"⚠️ [{self.user_id}] File not found for modification: {file_path}")
                return False
            
            with open(file_path, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%H:%M:%S")
                new_content = f"\n\nModified by {self.user_id} at {timestamp}"
                if additional_content:
                    new_content += f"\n{additional_content}"
                f.write(new_content)
            
            if file_path not in self.modified_files:
                self.modified_files.append(file_path)
            
            self.logger.info(f"📝 [{self.user_id}] Modified file: {os.path.basename(file_path)}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ [{self.user_id}] Error modifying file {file_path}: {e}")
            return False
    
    def delete_file(self, file_path):
        """Удаляет файл"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                self.logger.info(f"🗑️ [{self.user_id}] Deleted file: {os.path.basename(file_path)}")
                return True
            else:
                self.logger.warning(f"⚠️ [{self.user_id}] File not found for deletion: {file_path}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ [{self.user_id}] Error deleting file {file_path}: {e}")
            return False
    
    def rename_file(self, old_path, new_filename):
        """Переименовывает файл"""
        try:
            if not os.path.exists(old_path):
                self.logger.warning(f"⚠️ [{self.user_id}] File not found for rename: {old_path}")
                return None
            
            new_path = os.path.join(os.path.dirname(old_path), new_filename)
            shutil.move(old_path, new_path)
            
            self.logger.info(f"🔄 [{self.user_id}] Renamed {os.path.basename(old_path)} -> {new_filename}")
            return new_path
            
        except Exception as e:
            self.logger.error(f"❌ [{self.user_id}] Error renaming file {old_path}: {e}")
            return None
    
    def work_on_shared_file(self, shared_file_path, iterations=3):
        """Работает с общим файлом (симуляция многопользовательской работы)"""
        self.logger.info(f"👥 [{self.user_id}] Starting work on shared file: {os.path.basename(shared_file_path)}")
        
        for i in range(iterations):
            time.sleep(random.uniform(2, 5))  # Случайная задержка
            
            success = self.modify_file(
                shared_file_path, 
                f"Edit #{i+1} by {self.user_id}"
            )
            
            if not success:
                break
            
            self.logger.info(f"👥 [{self.user_id}] Edit #{i+1} on shared file completed")
        
        self.logger.info(f"👥 [{self.user_id}] Finished work on shared file")

class MonitoringAgentSimulator:
    """Симулятор агента мониторинга"""
    
    def __init__(self, agent_id, user_simulator, test_directory, server_url="http://localhost:8000"):
        self.agent_id = agent_id
        self.user_simulator = user_simulator
        self.test_directory = test_directory
        self.server_url = server_url
        self.logger = setup_logger(f"Agent_{agent_id}")
        
        # Mock API клиент
        self.api_client = MockAPIClient(agent_id, server_url)
        
        # Создаем конфигурацию для мониторинга
        self.config = {
            'watch_paths': [str(test_directory)],
            'poll_interval': 1,
            'background_check_interval': 10,
            'sessions': {
                'session_timeout_minutes': 5,
                'max_session_hours': 24
            },
            'hashing': {
                'enabled': True,
                'method': 'md5',
                'max_file_size_mb': 50
            },
            'ignore_patterns': ['.tmp', '~$*'],
            'ignore_extensions': ['.log'],
            'ignore_dirs': ['temp', 'backup']
        }
        
        self.logger.info(f"🤖 Agent {agent_id} initialized for user {user_simulator.user_id}")
    
    def simulate_file_operations(self, duration_minutes=10):
        """Симулирует файловые операции в течение указанного времени"""
        self.logger.info(f"🎬 [{self.agent_id}] Starting simulation for {duration_minutes} minutes")
        
        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        operation_count = 0
        
        while datetime.now() < end_time:
            try:
                # Случайный выбор операции
                operation = random.choice([
                    'create_doc',
                    'create_excel', 
                    'modify_existing',
                    'delete_file',
                    'rename_file',
                    'work_shared'
                ])
                
                if operation == 'create_doc':
                    # Создание Word документа
                    filename = f"document_{self.user_simulator.user_id}_{operation_count}.docx"
                    file_path = self.user_simulator.create_file(filename)
                    if file_path:
                        self._simulate_event('created', file_path)
                
                elif operation == 'create_excel':
                    # Создание Excel файла
                    filename = f"spreadsheet_{self.user_simulator.user_id}_{operation_count}.xlsx"
                    file_path = self.user_simulator.create_file(filename)
                    if file_path:
                        self._simulate_event('created', file_path)
                
                elif operation == 'modify_existing':
                    # Изменение существующего файла
                    if self.user_simulator.created_files:
                        file_path = random.choice(self.user_simulator.created_files)
                        if self.user_simulator.modify_file(file_path):
                            self._simulate_event('modified', file_path)
                
                elif operation == 'delete_file':
                    # Удаление файла
                    if self.user_simulator.created_files:
                        file_path = random.choice(self.user_simulator.created_files)
                        if self.user_simulator.delete_file(file_path):
                            self._simulate_event('deleted', file_path)
                            self.user_simulator.created_files.remove(file_path)
                
                elif operation == 'rename_file':
                    # Переименование файла
                    if self.user_simulator.created_files:
                        file_path = random.choice(self.user_simulator.created_files)
                        new_name = f"renamed_{self.user_simulator.user_id}_{operation_count}.docx"
                        new_path = self.user_simulator.rename_file(file_path, new_name)
                        if new_path:
                            self._simulate_event('moved', file_path, new_path)
                            # Обновляем путь в списке
                            idx = self.user_simulator.created_files.index(file_path)
                            self.user_simulator.created_files[idx] = new_path
                
                elif operation == 'work_shared':
                    # Работа с общим файлом (если он существует)
                    shared_files = list(Path(self.test_directory).glob("shared_*.docx"))
                    if shared_files:
                        shared_file = random.choice(shared_files)
                        self.user_simulator.work_on_shared_file(str(shared_file), iterations=2)
                        self._simulate_event('modified', str(shared_file))
                
                operation_count += 1
                
                # Случайная пауза между операциями
                time.sleep(random.uniform(3, 8))
                
                # Периодическая проверка состояния
                if operation_count % 5 == 0:
                    self._check_system_state()
                    
            except Exception as e:
                self.logger.error(f"❌ [{self.agent_id}] Error in simulation: {e}")
                time.sleep(5)
        
        self.logger.info(f"🏁 [{self.agent_id}] Simulation completed. Total operations: {operation_count}")
    
    def _simulate_event(self, event_type, file_path, dest_path=None):
        """Симулирует отправку события файла"""
        event_data = {
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'event_type': event_type,
            'user_id': self.user_simulator.user_id,
            'event_timestamp': datetime.now().isoformat(),
            'file_hash': f"mock_hash_{random.randint(1000, 9999)}",  # Mock hash
            'session_id': f"mock_session_{random.randint(10000, 99999)}",
            'resume_count': random.randint(0, 2)
        }
        
        if event_type == 'moved' and dest_path:
            event_data.update({
                'old_file_path': file_path,
                'old_file_name': os.path.basename(file_path),
                'file_path': dest_path,
                'file_name': os.path.basename(dest_path)
            })
        
        # Отправляем событие через mock API клиент
        return self.api_client.send_event(event_data)
    
    def _check_system_state(self):
        """Периодически проверяет состояние системы"""
        try:
            # Проверяем соединение с сервером
            if self.api_client.test_connection():
                # Запрашиваем текущих редакторов
                response = requests.get(f"{self.server_url}/api/current-editors/test", timeout=5)
                if response.status_code == 200:
                    editors_data = response.json()
                    self.logger.info(f"📊 [{self.agent_id}] System check: {len(editors_data.get('current_editors', []))} active editors")
            
            # Логируем статистику агента
            self.logger.info(f"📈 [{self.agent_id}] Agent stats: {len(self.api_client.sent_events)} events sent")
            
        except Exception as e:
            self.logger.debug(f"🔍 [{self.agent_id}] System check failed: {e}")

def create_shared_files(test_directory):
    """Создает общие файлы для многопользовательской работы"""
    shared_dir = Path(test_directory) / "shared"
    shared_dir.mkdir(exist_ok=True)
    
    shared_files = [
        "shared_project.docx",
        "shared_data.xlsx", 
        "shared_presentation.pptx"
    ]
    
    created_files = []
    for filename in shared_files:
        file_path = shared_dir / filename
        with open(file_path, 'w') as f:
            f.write(f"Shared file created at {datetime.now()}\nInitial content")
        created_files.append(str(file_path))
    
    return created_files

def monitor_system_state(server_url, duration_minutes):
    """Мониторинг состояния системы во время симуляции"""
    logger = setup_logger("SystemMonitor")
    end_time = datetime.now() + timedelta(minutes=duration_minutes)
    
    logger.info("🖥️ Starting system monitoring...")
    
    while datetime.now() < end_time:
        try:
            # Проверяем здоровье сервера
            health_response = requests.get(f"{server_url}/health", timeout=5)
            
            # Получаем список пользователей
            users_response = requests.get(f"{server_url}/api/users", timeout=5)
            
            # Получаем многопользовательские файлы
            multi_user_response = requests.get(f"{server_url}/api/multi-user-files", timeout=5)
            
            if all(r.status_code == 200 for r in [health_response, users_response, multi_user_response]):
                users_data = users_response.json()
                multi_user_data = multi_user_response.json()
                
                logger.info(f"📊 SYSTEM: {len(users_data.get('users', []))} users, "
                          f"{len(multi_user_data.get('multi_user_files', []))} multi-user files")
                
                # Логируем многопользовательские файлы
                for file_info in multi_user_data.get('multi_user_files', []):
                    editors = [editor['username'] for editor in file_info.get('editors', [])]
                    logger.info(f"👥 MULTI-USER: {file_info['file_name']} - Editors: {', '.join(editors)}")
            
            time.sleep(30)  # Проверяем каждые 30 секунд
            
        except Exception as e:
            logger.error(f"❌ SYSTEM MONITOR ERROR: {e}")
            time.sleep(10)

def main():
    """Главная функция симуляции"""
    print("🎯 Multi-Agent Monitoring System Simulator")
    print("=" * 50)
    
    # Настройки симуляции
    SIMULATION_DURATION = 15  # минут
    SERVER_URL = "http://localhost:8000"
    
    # Создаем временную директорию для тестирования
    with tempfile.TemporaryDirectory() as temp_dir:
        test_directory = Path(temp_dir)
        print(f"📁 Test directory: {test_directory}")
        
        # Создаем общие файлы
        shared_files = create_shared_files(test_directory)
        print(f"📄 Created {len(shared_files)} shared files")
        
        # Создаем симуляторы пользователей
        users = [
            UserSimulator("user1", test_directory, "agent1"),
            UserSimulator("user2", test_directory, "agent2"),
            UserSimulator("user3", test_directory, "agent3")  # Добавляем третьего пользователя для разнообразия
        ]
        
        # Создаем агенты мониторинга
        agents = [
            MonitoringAgentSimulator("agent1", users[0], test_directory, SERVER_URL),
            MonitoringAgentSimulator("agent2", users[1], test_directory, SERVER_URL),
            MonitoringAgentSimulator("agent3", users[2], test_directory, SERVER_URL)
        ]
        
        print(f"👥 Created {len(users)} users with {len(agents)} monitoring agents")
        
        # Запускаем мониторинг системы в отдельном потоке
        monitor_thread = threading.Thread(
            target=monitor_system_state,
            args=(SERVER_URL, SIMULATION_DURATION + 1),
            daemon=True
        )
        monitor_thread.start()
        
        # Запускаем симуляцию агентов в отдельных потоках
        agent_threads = []
        for i, agent in enumerate(agents):
            thread = threading.Thread(
                target=agent.simulate_file_operations,
                args=(SIMULATION_DURATION,),
                daemon=True
            )
            thread.start()
            agent_threads.append(thread)
            print(f"🚀 Started agent {agent.agent_id}")
        
        # Ждем завершения симуляции
        print(f"⏰ Simulation running for {SIMULATION_DURATION} minutes...")
        
        for thread in agent_threads:
            thread.join(timeout=SIMULATION_DURATION * 60 + 10)
        
        # Собираем статистику
        print("\n📊 SIMULATION RESULTS:")
        print("=" * 50)
        
        total_events = 0
        for agent in agents:
            events_count = len(agent.api_client.sent_events)
            total_events += events_count
            print(f"🤖 {agent.agent_id}: {events_count} events sent")
        
        print(f"📈 TOTAL EVENTS: {total_events}")
        
        # Проверяем финальное состояние системы
        try:
            print("\n🔍 FINAL SYSTEM STATE:")
            multi_user_response = requests.get(f"{SERVER_URL}/api/multi-user-files", timeout=5)
            if multi_user_response.status_code == 200:
                multi_user_data = multi_user_response.json()
                print(f"👥 Multi-user files: {len(multi_user_data.get('multi_user_files', []))}")
                
                for file_info in multi_user_data.get('multi_user_files', []):
                    editors = [editor['username'] for editor in file_info.get('editors', [])]
                    print(f"   📄 {file_info['file_name']}: {', '.join(editors)}")
            
        except Exception as e:
            print(f"❌ Error checking final state: {e}")
        
        print("\n✅ Simulation completed!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Simulation interrupted by user")
    except Exception as e:
        print(f"❌ Simulation error: {e}")
        import traceback
        traceback.print_exc()