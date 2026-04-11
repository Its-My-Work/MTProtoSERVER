function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

function copyLink() {
    const link = document.getElementById('proxy-link').textContent;
    navigator.clipboard.writeText(link);
    showToast('Ссылка скопирована!', 'success');
}

async function apiRequest(url, method = 'GET', body = null) {
    const options = { method, headers: {} };
    if (body) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    if (response.status === 401) {
        window.location.href = '/login';
        throw new Error('Unauthorized');
    }
    return response.json();
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}

async function logout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) {
        // Игнорируем ошибки
    }
    window.location.href = '/login';
}

document.addEventListener('DOMContentLoaded', () => {
    const saved = localStorage.getItem('theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);

    // Генерация нового API токена
    const generateBtn = document.getElementById('generate-token-btn');
    if (generateBtn) {
        generateBtn.addEventListener('click', async () => {
            if (!confirm('Сгенерировать новый токен? Текущий станет недействительным.')) return;
            try {
                const result = await apiRequest('/generate_api_token', 'POST');
                if (result.status === 'success') {
                    const tokenField = document.getElementById('api-token');
                    if (tokenField) tokenField.value = result.token_preview || '(обновлён)';
                    showToast('Токен сгенерирован! Cookie обновлён.', 'success');
                } else {
                    showToast('Ошибка генерации токена', 'error');
                }
            } catch (e) {
                showToast('Ошибка: ' + e.message, 'error');
            }
        });
    }

    // Кнопка выхода
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', (e) => {
            e.preventDefault();
            logout();
        });
    }
});
