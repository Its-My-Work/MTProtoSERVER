from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import json
import os
import re
import subprocess
import secrets as sec
import psutil
import logging
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager


# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Пути к файлам конфигурации (исправлено для Docker) ---
BASE_DIR = os.environ.get('APP_BASE_DIR', '/app')
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
PROXIES_FILE = os.path.join(DATA_DIR, "proxies.json")


# --- Утилиты для работы с JSON ---
def load_json(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.warning(f"Не удалось загрузить {filepath}: {e}")
        return {}


def save_json(filepath, data):
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except OSError as e:
        logging.error(f"Не удалось сохранить {filepath}: {e}")
        raise


def get_settings():
    settings = load_json(SETTINGS_FILE)
    settings.setdefault('api_token', None)
    return settings


def generate_api_token():
    return sec.token_hex(32)


def get_users():
    return load_json(USERS_FILE)


def save_users(users):
    save_json(USERS_FILE, users)


def get_proxies():
    return load_json(PROXIES_FILE)


def save_proxies(proxies):
    save_json(PROXIES_FILE, proxies)


def get_proxy_link(ip, port, secret_hex):
    """Генерирует tg:// ссылку. secret_hex — hex-формат секрета для Telegram."""
    return f"tg://proxy?server={ip}&port={port}&secret={secret_hex}"


def generate_mtg_secret(domain: str) -> tuple:
    """
    Генерирует секрет через mtg (nineseconds/mtg:2).
    Возвращает (secret_base64, secret_hex).
    secret_base64 — для docker-compose/mtg конфига.
    secret_hex — для tg:// ссылок.
    """
    try:
        result_b64 = subprocess.run(
            ['docker', 'run', '--rm', 'nineseconds/mtg:2', 'generate-secret', domain],
            capture_output=True, text=True, timeout=30
        )
        if result_b64.returncode != 0 or not result_b64.stdout.strip():
            raise RuntimeError(f"mtg generate-secret failed: {result_b64.stderr}")
        secret_b64 = result_b64.stdout.strip()

        result_hex = subprocess.run(
            ['docker', 'run', '--rm', 'nineseconds/mtg:2', 'generate-secret', '--hex', domain],
            capture_output=True, text=True, timeout=30
        )
        if result_hex.returncode != 0 or not result_hex.stdout.strip():
            raise RuntimeError(f"mtg generate-secret --hex failed: {result_hex.stderr}")
        secret_hex = result_hex.stdout.strip()

        return secret_b64, secret_hex
    except Exception as e:
        logging.error(f"Ошибка генерации секрета через mtg: {e}")
        raise


def get_proxy_stats():
    try:
        result = subprocess.run(
            ['docker', 'compose', 'ps'],
            capture_output=True, text=True, cwd='/opt/mtprotoserver'
        )
        return result.stdout
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        logging.warning(f"Не удалось получить статус прокси: {e}")
        return "Недоступно"


def get_system_info():
    return {
        'cpu_percent': psutil.cpu_percent(),
        'memory_percent': psutil.virtual_memory().percent,
        'memory_total': psutil.virtual_memory().total // (1024**3),
        'memory_used': psutil.virtual_memory().used // (1024**3),
        'disk_percent': psutil.disk_usage('/').percent,
        'disk_total': psutil.disk_usage('/').total // (1024**3),
        'disk_used': psutil.disk_usage('/').used // (1024**3),
    }


# --- Утилита экранирования Markdown ---
def escape_markdown(text):
    """Экранирует спецсимволы Markdown для Telegram."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))


# --- Проверка expired пользователей ---
def check_expired_users():
    """Проверяет и отключает пользователей с истёкшим сроком."""
    users_data = get_users()
    users = users_data.get('users', [])
    settings = get_settings()
    bot_token = settings.get('bot_token', '')
    admin_chat_id = settings.get('admin_chat_id', '')
    expired_users = []

    for u in users:
        expires = u.get('expires', '')
        if expires and u.get('enabled', True):
            try:
                expire_time = datetime.strptime(expires, '%Y-%m-%d %H:%M:%S')
                if datetime.now() > expire_time:
                    u['enabled'] = False
                    expired_users.append(u['label'])
            except ValueError:
                pass  # Игнорируем неверный формат

    if expired_users:
        save_users(users_data)
        logging.info(f"Отключены expired пользователи: {', '.join(expired_users)}")
        # Уведомление через бота
        if bot_token and admin_chat_id:
            message = f"⏰ Истек срок действия пользователей:\n\n" + "\n".join(
                f"• {escape_markdown(user)}" for user in expired_users
            )
            try:
                subprocess.run([
                    'curl', '-s', '-X', 'POST',
                    f'https://api.telegram.org/bot{bot_token}/sendMessage',
                    '-d', f'chat_id={admin_chat_id}',
                    '-d', f'text={message}',
                    '-d', 'parse_mode=Markdown'
                ], capture_output=True, timeout=10)
            except (subprocess.SubprocessError, OSError) as e:
                logging.error(f"Ошибка отправки уведомления: {e}")


async def expired_check_loop():
    """Фоновая задача: проверка expired пользователей каждые 60 секунд."""
    while True:
        try:
            await asyncio.to_thread(check_expired_users)
        except Exception as e:
            logging.error(f"Ошибка в expired_check_loop: {e}")
        await asyncio.sleep(60)


# --- Lifespan для фоновых задач ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(expired_check_loop())
    logging.info("Фоновая проверка expired пользователей запущена")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logging.info("Фоновая проверка expired пользователей остановлена")


# --- Приложение FastAPI ---
app = FastAPI(title="MTProtoSERVER Web UI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- Функция проверки токена (поддержка cookie + Bearer header) ---
def verify_request_token(request: Request):
    """Проверяет токен из Bearer header или cookie. Вызывает HTTPException при неудаче."""
    token = None
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header[7:]
    else:
        token = request.cookies.get('api_token')

    if not token:
        raise HTTPException(status_code=401, detail="Токен не предоставлен")

    settings = get_settings()
    if settings.get('api_token') != token:
        raise HTTPException(status_code=401, detail="Неверный токен")
    return True


def verify_bearer_token(authorization: str = Header(None)):
    """Проверяет только Bearer token (для REST API v1)."""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail="Требуется Bearer token")
    token = authorization[7:]
    settings = get_settings()
    if settings.get('api_token') != token:
        raise HTTPException(status_code=401, detail="Неверный токен")
    return True


# --- Middleware авторизации ---
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    logging.info(f"Request: {request.method} {request.url.path} from {request.client.host}")

    # Пути, не требующие авторизации
    public_paths = ["/login", "/api/auth/verify", "/api/auth/logout"]
    if request.url.path.startswith("/static") or request.url.path in public_paths:
        response = await call_next(request)
        return response

    # REST API v1 — проверяем Bearer token в middleware (fail-closed)
    if request.url.path.startswith("/api/v1/"):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return JSONResponse(status_code=401, content={"error": "Bearer token required"})
        token = auth_header[7:]
        settings = get_settings()
        if settings.get('api_token') != token:
            return JSONResponse(status_code=401, content={"error": "Invalid token"})
        response = await call_next(request)
        return response

    # Проверяем токен в cookie или Bearer header
    token = request.cookies.get("api_token")
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header[7:]

    if not token:
        logging.warning(f"Unauthorized access attempt to {request.url.path} from {request.client.host}")
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        else:
            return RedirectResponse(url="/login", status_code=302)

    # Проверяем валидность токена
    settings = get_settings()
    if settings.get('api_token') != token:
        logging.warning(f"Invalid token attempt to {request.url.path} from {request.client.host}")
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        else:
            return RedirectResponse(url="/login", status_code=302)

    response = await call_next(request)
    return response


# ==========================================
# Страницы веб-интерфейса
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    settings = get_settings()
    users_data = get_users()
    proxies_data = get_proxies()
    system = get_system_info()
    users = users_data.get('users', [])
    proxies = proxies_data.get('proxies', [])
    active_users = len([u for u in users if u.get('enabled', True)])
    total_traffic = sum(u.get('traffic_in', 0) + u.get('traffic_out', 0) for u in users)
    active_proxies = len([p for p in proxies if p.get('enabled', True)])

    return templates.TemplateResponse("index.html", {
        "request": request,
        "settings": settings,
        "users_count": len(users),
        "active_users": active_users,
        "total_traffic": total_traffic,
        "system": system,
        "proxies": proxies,
        "active_proxies": active_proxies,
        "proxy_count": len(proxies)
    })


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    users_data = get_users()
    proxies_data = get_proxies()
    users = users_data.get('users', [])
    proxies = proxies_data.get('proxies', [])
    settings = get_settings()
    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": users,
        "proxies": proxies,
        "settings": settings
    })


@app.get("/proxies", response_class=HTMLResponse)
async def proxies_page(request: Request):
    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    settings = get_settings()
    return templates.TemplateResponse("proxies.html", {
        "request": request,
        "proxies": proxies,
        "settings": settings
    })


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    users_data = get_users()
    proxies_data = get_proxies()
    users = users_data.get('users', [])
    proxies = proxies_data.get('proxies', [])
    system = get_system_info()
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "users": users,
        "proxies": proxies,
        "system": system
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    settings = get_settings()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings
    })


@app.get("/diagnostics", response_class=HTMLResponse)
async def diagnostics_page(request: Request):
    system = get_system_info()
    proxy_status = get_proxy_stats()
    return templates.TemplateResponse("diagnostics.html", {
        "request": request,
        "system": system,
        "proxy_status": proxy_status
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


# ==========================================
# API авторизации
# ==========================================

@app.post("/api/auth/verify")
async def auth_verify(request: Request):
    """Проверяет токен и устанавливает cookie."""
    form = await request.form()
    token = form.get('token', '')
    settings = get_settings()
    if settings.get('api_token') == token:
        response = JSONResponse({'status': 'ok', 'message': 'Токен валиден'})
        response.set_cookie(
            key="api_token",
            value=token,
            httponly=True,
            samesite='lax',
            max_age=86400 * 30  # 30 дней
        )
        return response
    else:
        return JSONResponse({'status': 'error', 'message': 'Неверный токен'}, status_code=401)


@app.post("/api/auth/logout")
async def auth_logout():
    """Удаляет cookie и выходит из системы."""
    response = JSONResponse({'status': 'ok', 'message': 'Выход выполнен'})
    response.delete_cookie("api_token")
    return response


@app.post("/generate_api_token")
async def generate_new_api_token(request: Request):
    """Генерирует новый API токен и сохраняет в settings.json."""
    new_token = generate_api_token()
    settings = get_settings()
    settings['api_token'] = new_token
    save_json(SETTINGS_FILE, settings)
    # Обновляем cookie с новым токеном (не возвращаем полный токен в JSON для безопасности)
    response = JSONResponse({'status': 'success', 'token_preview': new_token[:8] + '...'})
    response.set_cookie(
        key="api_token",
        value=new_token,
        httponly=True,
        samesite='lax',
        max_age=86400 * 30
    )
    return response


# ==========================================
# API управления пользователями (веб-интерфейс)
# ==========================================

@app.post("/api/users/add")
async def add_user(request: Request):
    form = await request.form()
    label = form.get('label', 'user')
    proxy_id = int(form.get('proxy_id', 1))
    expires_input = form.get('expires', '').strip()
    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])

    target_proxy = None
    for p in proxies:
        if p['id'] == proxy_id:
            target_proxy = p
            break

    if not target_proxy:
        return JSONResponse({'status': 'error', 'message': 'Прокси не найден'}, status_code=400)

    # Обработка expires
    expires = ''
    if expires_input:
        try:
            if 'T' in expires_input:
                expires = expires_input.replace('T', ' ') + ':00'
            elif len(expires_input) == 10:
                expires = f"{expires_input} 23:59:59"
            else:
                return JSONResponse({'status': 'error', 'message': 'Неверный формат даты'}, status_code=400)
            datetime.strptime(expires, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return JSONResponse({'status': 'error', 'message': 'Неверная дата'}, status_code=400)

    users_data = get_users()
    users = users_data.get('users', [])
    next_id = users_data.get('next_id', 1)

    new_secret = sec.token_hex(16)

    new_user = {
        'id': next_id,
        'label': label,
        'proxy_id': proxy_id,
        'secret': new_secret,
        'enabled': True,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'max_connections': 0,
        'max_ips': 0,
        'data_quota': '0',
        'expires': expires,
        'traffic_in': 0,
        'traffic_out': 0,
        'connections': 0
    }

    users.append(new_user)
    users_data['users'] = users
    users_data['next_id'] = next_id + 1
    save_users(users_data)

    link = get_proxy_link(
        get_settings().get('proxy_ip', '0.0.0.0'),
        target_proxy['port'],
        target_proxy.get('secret_hex', target_proxy['secret'])
    )
    return JSONResponse({'status': 'ok', 'secret': new_secret, 'link': link})


@app.post("/api/users/{user_id}/toggle")
async def toggle_user(user_id: int):
    users_data = get_users()
    users = users_data.get('users', [])
    found = False
    for u in users:
        if u['id'] == user_id:
            u['enabled'] = not u.get('enabled', True)
            found = True
            break
    if not found:
        return JSONResponse({'status': 'error', 'message': 'Пользователь не найден'}, status_code=404)
    save_users(users_data)
    return JSONResponse({'status': 'ok'})


@app.post("/api/users/{user_id}/delete")
async def delete_user(user_id: int):
    users_data = get_users()
    users = users_data.get('users', [])
    new_users = [u for u in users if u['id'] != user_id]
    if len(new_users) == len(users):
        return JSONResponse({'status': 'error', 'message': 'Пользователь не найден'}, status_code=404)
    users_data['users'] = new_users
    save_users(users_data)
    return JSONResponse({'status': 'ok'})


# ==========================================
# API управления прокси (веб-интерфейс)
# ==========================================

@app.post("/api/proxies/add")
async def add_proxy(request: Request):
    form = await request.form()
    label = form.get('label', 'proxy')
    try:
        port = int(form.get('port', 443))
    except (ValueError, TypeError):
        return JSONResponse({'status': 'error', 'message': 'Некорректный порт'}, status_code=400)
    if not (1 <= port <= 65535):
        return JSONResponse({'status': 'error', 'message': 'Порт должен быть от 1 до 65535'}, status_code=400)
    domain = form.get('domain', 'cloudflare.com')

    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    next_id = proxies_data.get('next_id', 1)

    # Генерация секрета через mtg (правильный формат)
    try:
        secret, secret_hex = generate_mtg_secret(domain)
    except Exception as e:
        return JSONResponse({'status': 'error', 'message': f'Ошибка генерации секрета: {e}'}, status_code=500)

    new_proxy = {
        'id': next_id,
        'label': label,
        'port': port,
        'domain': domain,
        'secret': secret,
        'secret_hex': secret_hex,
        'enabled': True,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'connections': 0,
        'traffic_in': 0,
        'traffic_out': 0
    }

    proxies.append(new_proxy)
    proxies_data['proxies'] = proxies
    proxies_data['next_id'] = next_id + 1
    save_proxies(proxies_data)

    settings = get_settings()
    settings['proxy_count'] = len(proxies)
    save_json(SETTINGS_FILE, settings)

    link = get_proxy_link(settings.get('proxy_ip', '0.0.0.0'), port, secret_hex)
    return JSONResponse({'status': 'ok', 'secret': secret, 'secret_hex': secret_hex, 'link': link})


@app.post("/api/proxies/{proxy_id}/toggle")
async def toggle_proxy(proxy_id: int):
    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    found = False
    for p in proxies:
        if p['id'] == proxy_id:
            p['enabled'] = not p.get('enabled', True)
            found = True
            break
    if not found:
        return JSONResponse({'status': 'error', 'message': 'Прокси не найден'}, status_code=404)
    save_proxies(proxies_data)
    return JSONResponse({'status': 'ok'})


@app.post("/api/proxies/{proxy_id}/delete")
async def delete_proxy(proxy_id: int):
    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    new_proxies = [p for p in proxies if p['id'] != proxy_id]
    if len(new_proxies) == len(proxies):
        return JSONResponse({'status': 'error', 'message': 'Прокси не найден'}, status_code=404)
    proxies_data['proxies'] = new_proxies

    settings = get_settings()
    settings['proxy_count'] = len(new_proxies)
    save_json(SETTINGS_FILE, settings)

    save_proxies(proxies_data)
    return JSONResponse({'status': 'ok'})


# ==========================================
# API статуса и метрик
# ==========================================

@app.get("/api/status")
async def api_status():
    settings = get_settings()
    system = get_system_info()
    users_data = get_users()
    proxies_data = get_proxies()
    users = users_data.get('users', [])
    proxies = proxies_data.get('proxies', [])
    return JSONResponse({
        'proxy_ip': settings.get('proxy_ip'),
        'proxy_count': len(proxies),
        'active_proxies': len([p for p in proxies if p.get('enabled')]),
        'users_count': len(users),
        'active_users': len([u for u in users if u.get('enabled')]),
        'system': system
    })


@app.get("/api/metrics")
async def api_metrics():
    users_data = get_users()
    proxies_data = get_proxies()
    users = users_data.get('users', [])
    proxies = proxies_data.get('proxies', [])
    metrics = "# HELP mtproto_proxies_total Total proxies\n# TYPE mtproto_proxies_total gauge\n"
    metrics += f"mtproto_proxies_total {len(proxies)}\n"
    metrics += "# HELP mtproto_users_total Total users\n# TYPE mtproto_users_total gauge\n"
    metrics += f"mtproto_users_total {len(users)}\n"
    metrics += "# HELP mtproto_users_active Active users\n# TYPE mtproto_users_active gauge\n"
    metrics += f"mtproto_users_active {len([u for u in users if u.get('enabled')])}\n"
    for u in users:
        label = u.get('label', 'unknown')
        metrics += f'mtproto_user_traffic_in{{user="{label}"}} {u.get("traffic_in", 0)}\n'
    return HTMLResponse(content=metrics)


# ==========================================
# REST API v1 для внешних клиентов (Bearer token)
# ==========================================

@app.get("/api/v1/clients")
async def api_v1_list_clients(request: Request):
    """Список всех клиентов (пользователей). Требует Bearer token."""
    verify_bearer_token(request.headers.get('Authorization'))
    users_data = get_users()
    users = users_data.get('users', [])
    return JSONResponse({
        'status': 'ok',
        'clients': users,
        'total': len(users)
    })


@app.post("/api/v1/clients")
async def api_v1_add_client(request: Request):
    """Добавление клиента через REST API. Требует Bearer token."""
    verify_bearer_token(request.headers.get('Authorization'))
    data = await request.json()
    label = data.get('label', 'api-user')
    proxy_id = data.get('proxy_id', 1)
    expires = data.get('expires', '')

    # Валидация expires
    if expires:
        try:
            datetime.strptime(expires, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return JSONResponse({'status': 'error', 'message': 'Формат expires: YYYY-MM-DD HH:MM:SS'}, status_code=400)

    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    target_proxy = None
    for p in proxies:
        if p['id'] == proxy_id:
            target_proxy = p
            break

    if not target_proxy:
        return JSONResponse({'status': 'error', 'message': 'Прокси не найден'}, status_code=400)

    users_data = get_users()
    users = users_data.get('users', [])
    next_id = users_data.get('next_id', 1)
    new_secret = sec.token_hex(16)

    new_user = {
        'id': next_id,
        'label': label,
        'proxy_id': proxy_id,
        'secret': new_secret,
        'enabled': True,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'max_connections': 0,
        'max_ips': 0,
        'data_quota': '0',
        'expires': expires,
        'traffic_in': 0,
        'traffic_out': 0,
        'connections': 0
    }

    users.append(new_user)
    users_data['users'] = users
    users_data['next_id'] = next_id + 1
    save_users(users_data)

    settings = get_settings()
    link = get_proxy_link(
        settings.get('proxy_ip', '0.0.0.0'),
        target_proxy['port'],
        target_proxy.get('secret_hex', target_proxy['secret'])
    )
    return JSONResponse({
        'status': 'ok',
        'client': new_user,
        'link': link
    }, status_code=201)


@app.delete("/api/v1/clients/{client_id}")
async def api_v1_delete_client(client_id: int, request: Request):
    """Удаление клиента через REST API. Требует Bearer token."""
    verify_bearer_token(request.headers.get('Authorization'))
    users_data = get_users()
    users = users_data.get('users', [])
    new_users = [u for u in users if u['id'] != client_id]
    if len(new_users) == len(users):
        return JSONResponse({'status': 'error', 'message': 'Клиент не найден'}, status_code=404)
    users_data['users'] = new_users
    save_users(users_data)
    return JSONResponse({'status': 'ok', 'message': f'Клиент {client_id} удалён'})
