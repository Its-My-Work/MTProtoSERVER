"""MTProxy Management API — интеграция с mtprotoserver-backup."""
import asyncio
import json
import logging
import os
import tomllib
import tomli_w

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
import requests
import secrets as secrets_module
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

app = FastAPI(title="MTProxy Manager")

DATA_FILE = Path("data/mtproxy_api_data.json")
API_TOKEN = "b6dac45f37528793a7d84166556f7c74"
SERVER_IP = "152.114.192.137"
FAKE_DOMAIN = "cloudflare.com"

# Переменные для интеграции с mtprotoserver
MTPROXY_WEBUI_URL = "http://152.114.192.137:8088"
MTPROXY_WEBUI_PASSWORD = "2nn3b2nn3B!!"


def verify_token(authorization: str = Header(...)):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid token")


def authenticate_webui():
    """Аутентификация в mtprotoserver webui и получение auth_token."""
    if not MTPROXY_WEBUI_PASSWORD:
        raise HTTPException(status_code=500, detail="MTPROXY_WEBUI_PASSWORD not set")
    resp = requests.post(f"{MTPROXY_WEBUI_URL}/api/auth/login", data={"password": MTPROXY_WEBUI_PASSWORD})
    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to authenticate with webui")
    token = resp.cookies.get("auth_token")
    if not token:
        raise HTTPException(status_code=500, detail="No auth_token received")
    return token


def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"users": {}, "secrets": {}}


def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2, default=str))


def reload_mtg():
    """Reload MTG config in the container."""
    try:
        subprocess.run(['docker', 'exec', 'mtproto-proxy', 'kill', '-HUP', '1'], check=False)
        logging.info("MTG config reloaded")
    except Exception as e:
        logging.warning(f"Failed to reload MTG: {e}")


def generate_fake_tls_secret() -> str:
    """Generate fake TLS secret using local MTG"""
    try:
        result = subprocess.run(
            ['docker', 'run', '--rm', 'mtg-multi',
             'generate-secret', '--hex', FAKE_DOMAIN],
            capture_output=True, text=True, timeout=10
        )
        secret = result.stdout.strip()
        if not secret.startswith('ee') or len(secret) < 34:
            raise RuntimeError("MTG returned invalid secret")
        return secret
    except Exception:
        # Fallback to simple secret
        return secrets_module.token_hex(16)


class CreateProxyRequest(BaseModel):
    user_id: int
    username: str | None = None
    days: int = 30
    buyer_id: int | None = None  # for gift tracking


class RevokeProxyRequest(BaseModel):
    user_id: int
    secret: str | None = None  # revoke specific or all


@app.get("/health")
async def health():
    # Check webui availability
    try:
        resp = requests.get(f"{MTPROXY_WEBUI_URL}/", timeout=5)
        status = "ok" if resp.status_code == 200 else "error"
    except:
        status = "unavailable"
    data = load_data()
    active = sum(1 for s in data["secrets"].values() if s.get("active", True))
    return {"status": status, "active_secrets": active}


@app.post("/proxy/create", dependencies=[Depends(verify_token)])
async def create_proxy(req: CreateProxyRequest):
    """Create new proxy client via mtprotoserver API."""
    token = authenticate_webui()
    data = load_data()
    user_key = str(req.user_id)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=req.days)

    # Send request to mtprotoserver (теперь выбираем прокси, например proxy_id=1)
    resp = requests.post(f"{MTPROXY_WEBUI_URL}/api/clients/add",
                          data={"expiry_days": req.days, "label": (req.username or "proxy") + "_" + secrets_module.token_hex(4), "domain": FAKE_DOMAIN, "proxy_id": 1},
                          cookies={"auth_token": token})
    if resp.status_code != 200 or resp.json().get("status") != "ok":
        raise HTTPException(status_code=500, detail="Failed to create client")

    client_data = resp.json()
    new_secret = client_data["secret"]
    port = client_data["port"]
    link = client_data["link"]
    node_ip = client_data.get("node_ip", SERVER_IP)
    web_link = link  # Теперь link уже содержит правильный IP ноды

    # Store client info (сохраняем client_id для совместимости с revoke/delete)
    data["secrets"][new_secret] = {
        "user_id": req.user_id,
        "username": req.username,
        "client_id": client_data.get("client_id", 0),  # WebUI должен вернуть client_id
        "port": port,
        "node_ip": node_ip,
        "active": True,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "days": req.days,
        "buyer_id": req.buyer_id,
    }

    # Track user's secrets
    if user_key not in data["users"]:
        data["users"][user_key] = {"secrets": [], "username": req.username}
    data["users"][user_key]["secrets"].append(new_secret)
    data["users"][user_key]["username"] = req.username

    save_data(data)
    reload_mtg()

    logging.info(f"Создан прокси для пользователя {req.user_id} на {req.days} дней")

    return {
        "secret": new_secret,
        "link": link,
        "web_link": web_link,
        "expires_at": expires_at.isoformat(),
        "active_secrets_total": 1,  # Placeholder, since we don't have total
    }


@app.post("/proxy/revoke", dependencies=[Depends(verify_token)])
async def revoke_proxy(req: RevokeProxyRequest):
    """Revoke proxy client(s) via mtprotoserver API."""
    token = authenticate_webui()
    data = load_data()
    user_key = str(req.user_id)
    revoked = 0

    if req.secret:
        # Revoke specific secret
        if req.secret in data["secrets"]:
            client_id = data["secrets"][req.secret]["client_id"]
            resp = requests.post(f"{MTPROXY_WEBUI_URL}/api/clients/{client_id}/delete",
                                 cookies={"auth_token": token})
            if resp.status_code == 200:
                data["secrets"][req.secret]["active"] = False
                revoked = 1
    else:
        # Revoke all user's secrets
        if user_key in data["users"]:
            for s in data["users"][user_key]["secrets"]:
                if s in data["secrets"] and data["secrets"][s]["active"]:
                    client_id = data["secrets"][s]["client_id"]
                    resp = requests.post(f"{MTPROXY_WEBUI_URL}/api/clients/{client_id}/delete",
                                         cookies={"auth_token": token})
                    if resp.status_code == 200:
                        data["secrets"][s]["active"] = False
                        revoked += 1

    save_data(data)
    reload_mtg()
    return {"revoked": revoked, "active_secrets_total": revoked}  # Placeholder


@app.get("/proxy/user/{user_id}", dependencies=[Depends(verify_token)])
async def get_user_proxies(user_id: int):
    """Get all proxies for a user from local data."""
    data = load_data()
    user_key = str(user_id)

    if user_key not in data["users"]:
        return {"proxies": [], "count": 0}

    now = datetime.now(timezone.utc).isoformat()
    proxies = []
    for secret in data["users"][user_key]["secrets"]:
        info = data["secrets"].get(secret, {})
        if not info:
            continue
        is_expired = info.get("expires_at", "") < now
        is_active = info.get("active", False) and not is_expired
        port = info.get("port", 443)
        node_ip = info.get("node_ip", SERVER_IP)
        proxies.append({
            "secret": secret,
            "link": f"tg://proxy?server={node_ip}&port={port}&secret={secret}",
            "active": is_active,
            "created_at": info.get("created_at"),
            "expires_at": info.get("expires_at"),
            "days": info.get("days"),
        })

    return {"proxies": proxies, "count": len([p for p in proxies if p["active"]])}


@app.get("/proxy/stats", dependencies=[Depends(verify_token)])
async def get_stats():
    """Get overall proxy stats from local data."""
    data = load_data()
    now = datetime.now(timezone.utc).isoformat()
    total = len(data["secrets"])
    active = sum(
        1 for s in data["secrets"].values()
        if s.get("active", False) and s.get("expires_at", "") >= now
    )
    users_with_proxy = sum(
        1 for u in data["users"].values()
        if any(
            data["secrets"].get(s, {}).get("active", False)
            for s in u["secrets"]
        )
    )
    return {
        "total_secrets": total,
        "active_secrets": active,
        "users_with_proxy": users_with_proxy,
        "total_users": len(data["users"]),
    }


@app.post("/proxy/cleanup", dependencies=[Depends(verify_token)])
async def cleanup_expired():
    """Deactivate expired secrets > 7 days ago."""
    token = authenticate_webui()
    data = load_data()
    now = datetime.now(timezone.utc)
    cleaned = 0
    for secret, info in data["secrets"].items():
        if not info.get("active"):
            continue
        exp = info.get("expires_at", "")
        if not exp:
            continue
        try:
            expires = datetime.fromisoformat(exp)
            days_expired = (now - expires).days
            if days_expired > 7:
                client_id = info["client_id"]
                requests.post(f"{MTPROXY_WEBUI_URL}/api/clients/{client_id}/delete",
                              cookies={"auth_token": token})
                info["active"] = False
                cleaned += 1
            elif days_expired > 0:
                # Expired but within 7 days grace period - deactivate
                client_id = info["client_id"]
                requests.post(f"{MTPROXY_WEBUI_URL}/api/clients/{client_id}/delete",
                              cookies={"auth_token": token})
                info["active"] = False
                cleaned += 1
        except Exception:
            pass
    save_data(data)
    reload_mtg()
    return {"cleaned": cleaned, "active_secrets_total": cleaned}


@app.post("/proxy/delete", dependencies=[Depends(verify_token)])
async def delete_proxy(req: RevokeProxyRequest):
    """Delete a specific proxy via mtprotoserver API and return refund info."""
    token = authenticate_webui()
    data = load_data()

    if not req.secret or req.secret not in data["secrets"]:
        raise HTTPException(404, "Secret not found")

    info = data["secrets"][req.secret]
    if not info.get("active"):
        raise HTTPException(400, "Proxy already inactive")

    client_id = info["client_id"]
    resp = requests.post(f"{MTPROXY_WEBUI_URL}/api/clients/{client_id}/delete",
                         cookies={"auth_token": token})
    if resp.status_code != 200:
        raise HTTPException(500, "Failed to delete client")

    # Calculate remaining days for refund
    now = datetime.now(timezone.utc)
    created = datetime.fromisoformat(info["created_at"])
    expires = datetime.fromisoformat(info["expires_at"])
    total_days = (expires - created).days or 1
    remaining_days = max(0, (expires - now).days)

    # Deactivate
    info["active"] = False
    info["deleted_at"] = now.isoformat()
    save_data(data)
    reload_mtg()

    return {
        "deleted": True,
        "total_days": total_days,
        "remaining_days": remaining_days,
        "refund_percent": round(remaining_days / total_days * 100) if total_days > 0 else 0,
        "active_secrets_total": 1,  # Placeholder
    }


@app.get("/proxy/all_users", dependencies=[Depends(verify_token)])
async def get_all_users():
    """Get all users with their proxies from local data."""
    data = load_data()
    now = datetime.now(timezone.utc).isoformat()
    users = []
    for user_id, user_info in data["users"].items():
        proxies = []
        for secret in user_info.get("secrets", []):
            info = data["secrets"].get(secret, {})
            if not info:
                continue
            is_expired = info.get("expires_at", "") < now
            proxies.append({
                "secret": secret,
                "active": info.get("active", False) and not is_expired,
                "expires_at": info.get("expires_at"),
                "created_at": info.get("created_at"),
                "days": info.get("days"),
            })
        if proxies:
            users.append({
                "telegram_id": int(user_id),
                "username": user_info.get("username"),
                "proxies": proxies,
            })
    return {"users": users}
