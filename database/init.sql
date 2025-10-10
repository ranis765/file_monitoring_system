CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Пользователи
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Файлы
CREATE TABLE files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path VARCHAR(1000) NOT NULL UNIQUE,
    file_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Сессии работы с файлами
CREATE TABLE file_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    file_id UUID NOT NULL REFERENCES files(id),
    started_at TIMESTAMP NOT NULL,
    last_activity TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    hash_before VARCHAR(64),
    hash_after VARCHAR(64),
    is_commented BOOLEAN DEFAULT FALSE,
    resume_count INTEGER DEFAULT 0  -- ДОБАВЛЕНО ЭТО ПОЛЕ
);

-- 4. События файлов
CREATE TABLE file_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES file_sessions(id),
    event_type VARCHAR(20) NOT NULL,
    file_hash VARCHAR(64),
    event_timestamp TIMESTAMP NOT NULL
);

-- 5. Комментарии
CREATE TABLE comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES file_sessions(id),
    user_id UUID NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    change_type VARCHAR(50) NOT NULL DEFAULT 'other',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Отчеты
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date DATE NOT NULL,
    report_type VARCHAR(20) DEFAULT 'daily',
    file_format VARCHAR(10) NOT NULL,
    file_path VARCHAR(1000),
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Тестовые данные
INSERT INTO users (username, email) VALUES 
('test_user', 'test@example.com'),
('admin', 'admin@example.com'),
('developer', 'dev@example.com');

INSERT INTO files (file_path, file_name) VALUES 
('/monitor/test_file.py', 'test_file.py'),
('/monitor/readme.md', 'readme.md'),
('/monitor/config.yaml', 'config.yaml');