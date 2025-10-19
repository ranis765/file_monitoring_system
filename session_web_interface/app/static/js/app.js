// app.js (изменения: все комментарии на русский; добавлены функции для сортировок в all-history)
/// Глобальный JavaScript приложения

// Автоматическое скрытие алертов через 5 секунд
document.addEventListener('DOMContentLoaded', function() {
    // Auto-dismiss alerts
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
    
    // Добавление состояний загрузки к кнопкам
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function() {
            const submitBtn = this.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Обработка...';
                submitBtn.disabled = true;
            }
        });
    });
});

// Функция форматирования дат
function formatDate(dateString) {
    if (!dateString) return 'N/A';
    
    const date = new Date(dateString);
    return date.toLocaleString('ru-RU');
}

// Функция форматирования длительности
function formatDuration(seconds) {
    if (!seconds) return 'N/A';
    
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    
    if (hours > 0) {
        return `${hours}ч ${minutes}м`;
    } else {
        return `${minutes}м`;
    }
}

// Функции API
async function apiRequest(endpoint, options = {}) {
    try {
        const response = await fetch(endpoint, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });
        
        if (!response.ok) {
            throw new Error(`Ошибка API: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Ошибка запроса API:', error);
        throw error;
    }
}

// Обновление данных пользователя
async function refreshUserData(username) {
    try {
        const data = await apiRequest(`/api/user-sessions/${username}`);
        return data;
    } catch (error) {
        showError('Ошибка обновления данных');
        return null;
    }
}

// Показ ошибки
function showError(message) {
    // Создание toast уведомления
    const toast = document.createElement('div');
    toast.className = 'toast align-items-center text-white bg-danger border-0 position-fixed top-0 end-0 m-3';
    toast.style.zIndex = '1060';
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <i class="fas fa-exclamation-circle me-2"></i>${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;
    
    document.body.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();
    
    // Удаление toast после скрытия
    toast.addEventListener('hidden.bs.toast', () => {
        document.body.removeChild(toast);
    });
}

// Показ успеха
function showSuccess(message) {
    // Создание toast уведомления
    const toast = document.createElement('div');
    toast.className = 'toast align-items-center text-white bg-success border-0 position-fixed top-0 end-0 m-3';
    toast.style.zIndex = '1060';
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <i class="fas fa-check-circle me-2"></i>${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;
    
    document.body.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();
    
    // Удаление toast после скрытия
    toast.addEventListener('hidden.bs.toast', () => {
        document.body.removeChild(toast);
    });
}

// Функции управления сессиями
function toggleSessionDetails(sessionId) {
    const details = document.getElementById(`session-details-${sessionId}`);
    if (details) {
        details.classList.toggle('d-none');
    }
}

// Авто-обновление данных каждые 30 секунд (опционально)
function startAutoRefresh(interval = 30000) {
    setInterval(() => {
        if (document.visibilityState === 'visible') {
            location.reload();
        }
    }, interval);
}

// Инициализация авто-обновления на панели
if (window.location.pathname === '/dashboard') {
    // startAutoRefresh(); // Раскомментировать для авто-обновления
}

// Добавлено для сортировок в all-history (п.3)
function changeSort(sortBy) {
    const url = new URL(window.location);
    url.searchParams.set('sort_by', sortBy);
    window.location = url.toString();
}