const API_BASE = 'http://localhost:8000/api';

async function fetchAPI(endpoint, method = 'GET', body = null) {
    const username = localStorage.getItem('username');
    if (!username) throw new Error('No username found. Please login.');

    const options = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body) options.body = JSON.stringify({ ...body, username });  // Добавляем username в body

    const response = await fetch(`${API_BASE}${endpoint}`, options);
    if (!response.ok) throw new Error(`API error: ${response.statusText}`);
    return response.json();
}

// Получить сессии пользователя
async function getUserSessions() {
    return fetchAPI('/sessions');  // Ваш endpoint из main.py
}

// Получить историю пользователя
async function getUserHistory() {
    return fetchAPI(`/user-activity/${localStorage.getItem('username')}`);
}

// Получить все изменения
async function getAllChanges() {
    return fetchAPI('/sessions/comments');
}

// Получить типы изменений
async function getChangeTypes() {
    return fetchAPI('/change-types');
}

// Отправить комментарий
async function postComment(sessionId, content, changeType) {
    return fetchAPI('/comments', 'POST', {
        session_id: sessionId,
        content,
        change_type: changeType,
    });
}