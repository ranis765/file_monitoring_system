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
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    started_at TIMESTAMP NOT NULL,
    last_activity TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    hash_before VARCHAR(64),
    hash_after VARCHAR(64),
    is_commented BOOLEAN DEFAULT FALSE,
    resume_count INTEGER DEFAULT 0
);

-- 4. События файлов
CREATE TABLE file_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES file_sessions(id) ON DELETE CASCADE,
    event_type VARCHAR(20) NOT NULL,
    file_hash VARCHAR(64),
    event_timestamp TIMESTAMP NOT NULL
);

-- 5. Комментарии
CREATE TABLE comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES file_sessions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

-- Индексы для улучшения производительности
CREATE INDEX idx_file_sessions_user_id ON file_sessions(user_id);
CREATE INDEX idx_file_sessions_file_id ON file_sessions(file_id);
CREATE INDEX idx_file_sessions_ended_at ON file_sessions(ended_at);
CREATE INDEX idx_file_sessions_active ON file_sessions(user_id, file_id, ended_at) WHERE ended_at IS NULL;

CREATE INDEX idx_file_events_session_id ON file_events(session_id);
CREATE INDEX idx_file_events_timestamp ON file_events(event_timestamp);
CREATE INDEX idx_file_events_type ON file_events(event_type);

CREATE INDEX idx_comments_session_id ON comments(session_id);
CREATE INDEX idx_comments_user_id ON comments(user_id);
CREATE INDEX idx_comments_change_type ON comments(change_type);

CREATE INDEX idx_files_path ON files(file_path);
CREATE INDEX idx_users_username ON users(username);

-- Представление для активных сессий
CREATE VIEW active_sessions AS
SELECT 
    fs.id,
    fs.user_id,
    fs.file_id,
    fs.started_at,
    fs.last_activity,
    fs.hash_before,
    fs.hash_after,
    fs.resume_count,
    u.username,
    f.file_path,
    f.file_name
FROM file_sessions fs
JOIN users u ON fs.user_id = u.id
JOIN files f ON fs.file_id = f.id
WHERE fs.ended_at IS NULL;

-- Представление для сессий с комментариями
CREATE VIEW sessions_with_comments AS
SELECT 
    fs.id as session_id,
    fs.started_at,
    fs.ended_at,
    fs.last_activity,
    fs.resume_count,
    u.username,
    f.file_path,
    f.file_name,
    c.content as comment_content,
    c.change_type,
    c.created_at as comment_created_at
FROM file_sessions fs
JOIN users u ON fs.user_id = u.id
JOIN files f ON fs.file_id = f.id
LEFT JOIN comments c ON fs.id = c.session_id
WHERE fs.is_commented = TRUE;

-- Функция для закрытия старых сессий
CREATE OR REPLACE FUNCTION close_old_sessions(max_age_hours INTEGER DEFAULT 24)
RETURNS INTEGER AS $$
DECLARE
    closed_count INTEGER;
BEGIN
    UPDATE file_sessions 
    SET ended_at = last_activity
    WHERE ended_at IS NULL 
    AND last_activity < (CURRENT_TIMESTAMP - (max_age_hours || ' hours')::INTERVAL);
    
    GET DIAGNOSTICS closed_count = ROW_COUNT;
    RETURN closed_count;
END;
$$ LANGUAGE plpgsql;

-- Функция для получения текущих редакторов файла
CREATE OR REPLACE FUNCTION get_current_editors(file_path_pattern VARCHAR)
RETURNS TABLE(
    username VARCHAR,
    file_path VARCHAR,
    file_name VARCHAR,
    last_activity TIMESTAMP,
    session_id UUID
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        u.username,
        f.file_path,
        f.file_name,
        fs.last_activity,
        fs.id as session_id
    FROM file_sessions fs
    JOIN users u ON fs.user_id = u.id
    JOIN files f ON fs.file_id = f.id
    WHERE fs.ended_at IS NULL
    AND f.file_path LIKE file_path_pattern
    ORDER BY fs.last_activity DESC;
END;
$$ LANGUAGE plpgsql;

-- Триггер для автоматического обновления is_commented
CREATE OR REPLACE FUNCTION update_session_commented_flag()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE file_sessions 
        SET is_commented = TRUE 
        WHERE id = NEW.session_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE file_sessions 
        SET is_commented = FALSE 
        WHERE id = OLD.session_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_comment_changes
    AFTER INSERT OR DELETE ON comments
    FOR EACH ROW
    EXECUTE FUNCTION update_session_commented_flag();



-- Вывод информации о созданных объектах
SELECT 'Database initialized successfully' as status;

-- Показать активные сессии
SELECT 'Active sessions:' as info;
SELECT username, file_path, last_activity, resume_count 
FROM active_sessions 
ORDER BY last_activity DESC;

-- Показать сессии с комментариями
SELECT 'Sessions with comments:' as info;
SELECT username, file_path, comment_content, change_type 
FROM sessions_with_comments;