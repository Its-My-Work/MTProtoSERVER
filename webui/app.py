from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
import json
import os
import subprocess
import secrets as sec
import psutil
from datetime import datetime

app = FastAPI(title="MTProtoSERVER Web UI")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DATA_DIR = "/app/data"
CONFIG_DIR = "/app/config"
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
PROXIES_FILE = os.path.join(DATA_DIR, "proxies.json")

def load_json(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_json(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

def get_settings():
    return load_json(SETTINGS_FILE)

def get_users():
    return load_json(USERS_FILE)

def save_users(users):
    save_json(USERS_FILE, users)

def get_proxies():
    return load_json(PROXIES_FILE)

def save_proxies(proxies):
    save_json(PROXIES_FILE, proxies)

def get_proxy_link(ip, port, secret):
    return f"tg://proxy?server={ip}&port={port}&secret={secret}"

def get_proxy_stats():
    try:
        result = subprocess.run(
            ['docker', 'compose', 'ps'],
            capture_output=True, text=True, cwd='/opt/mtprotoserver'
        )
        return result.stdout
    except:
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
            # Handle datetime-local format YYYY-MM-DDTHH:MM
            if 'T' in expires_input:
                expires = expires_input.replace('T', ' ')
            elif len(expires_input) == 10:  # Только дата
                expires = f"{expires_input} 23:59:59"
            else:
                return JSONResponse({'status': 'error', 'message': 'Неверный формат даты. Используйте YYYY-MM-DDTHH:MM или YYYY-MM-DD'}, status_code=400)
            # Валидация даты
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
        'created_at': 'now',
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
        target_proxy['secret']
    )
    return JSONResponse({'status': 'ok', 'secret': new_secret, 'link': link})

@app.post("/api/users/{user_id}/toggle")
async def toggle_user(user_id: int):
    users_data = get_users()
    users = users_data.get('users', [])
    for u in users:
        if u['id'] == user_id:
            u['enabled'] = not u.get('enabled', True)
            break
    save_users(users_data)
    return JSONResponse({'status': 'ok'})

@app.post("/api/users/{user_id}/delete")
async def delete_user(user_id: int):
    users_data = get_users()
    users = users_data.get('users', [])
    users = [u for u in users if u['id'] != user_id]
    users_data['users'] = users
    save_users(users_data)
    return JSONResponse({'status': 'ok'})

@app.post("/api/proxies/add")
async def add_proxy(request: Request):
    form = await request.form()
    label = form.get('label', 'proxy')
    port = int(form.get('port', 443))
    domain = form.get('domain', 'cloudflare.com')

    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    next_id = proxies_data.get('next_id', 1)

    domain_hex = domain.encode().hex()
    random_part = sec.token_hex(14)
    secret = f"ee{random_part}{domain_hex}"

    new_proxy = {
        'id': next_id,
        'label': label,
        'port': port,
        'domain': domain,
        'secret': secret,
        'enabled': True,
        'created_at': 'now',
        'connections': 0,
        'traffic_in': 0,
        'traffic_out': 0
    }

    proxies.append(new_proxy)
    proxies_data['proxies'] = proxies
    proxies_data['next_id'] = next_id + 1
    save_proxies(proxies_data)

    # Обновляем proxy_count в settings
    settings = get_settings()
    settings['proxy_count'] = len(proxies)
    save_json(SETTINGS_FILE, settings)

    link = get_proxy_link(settings.get('proxy_ip', '0.0.0.0'), port, secret)
    return JSONResponse({'status': 'ok', 'secret': secret, 'link': link})

@app.post("/api/proxies/{proxy_id}/toggle")
async def toggle_proxy(proxy_id: int):
    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    for p in proxies:
        if p['id'] == proxy_id:
            p['enabled'] = not p.get('enabled', True)
            break
    save_proxies(proxies_data)
    return JSONResponse({'status': 'ok'})

@app.post("/api/proxies/{proxy_id}/delete")
async def delete_proxy(proxy_id: int):
    proxies_data = get_proxies()
    proxies = proxies_data.get('proxies', [])
    proxies = [p for p in proxies if p['id'] != proxy_id]
    proxies_data['proxies'] = proxies

    settings = get_settings()
    settings['proxy_count'] = len(proxies)
    save_json(SETTINGS_FILE, settings)

    save_proxies(proxies_data)
    return JSONResponse({'status': 'ok'})

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
        metrics += f"# HELP mtproto_user_traffic_in Traffic in for {u['label']}\n"
        metrics += f"# TYPE mtproto_user_traffic_in counter\n"
        metrics += f'mtproto_user_traffic_in{{user="{u["label"]}"}} {u.get("traffic_in", 0)}\n'
    return HTMLResponse(content=metrics)

def check_expired_users():
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
        # Уведомление через бота
        if bot_token and admin_chat_id:
            def escape_markdown(text):
                return text.replace('*', '\\*').replace('_', '\\_').replace('`', '\\`').replace('[', '\\[').replace(']', '\\]')
            
            message = f"⏰ *Истек срок действия пользователей:*\\n\\n" + "\\n".join(f"• {escape_markdown(user)}" for user in expired_users)
            subprocess.run([
                'curl', '-s', '-X', 'POST', f'https://api.telegram.org/bot{bot_token}/sendMessage',
                '-d', f'chat_id={admin_chat_id}',
                '-d', f'text={message}',
                '-d', 'parse_mode=Markdown'
            ], capture_output=True)
