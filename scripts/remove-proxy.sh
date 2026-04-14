#!/bin/bash
set -euo pipefail

# ============================================================
# MTProtoSERVER — Удалить прокси
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="/opt/mtprotoserver"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"

E_OK="✅"
E_ERR="❌"
E_WARN="⚠️"
E_INFO="ℹ️"

log_ok()    { echo -e "${GREEN}${E_OK} $1${NC}"; }
log_err()   { echo -e "${RED}${E_ERR} $1${NC}"; }
log_warn()  { echo -e "${YELLOW}${E_WARN} $1${NC}"; }

if [ ! -d "$INSTALL_DIR" ]; then
    log_err "MTProtoSERVER не установлен"
    exit 1
fi

if [ -z "${1:-}" ]; then
    log_err "Укажите метку прокси: $0 <label>"
    echo ""
    echo "Доступные прокси:"
    docker compose -f "$COMPOSE_FILE" ps --format '{{.Name}}' 2>/dev/null | grep mtproto-proxy || echo "  Нет запущенных прокси"
    exit 1
fi

LABEL="$1"
CONTAINER_NAME="mtproto-proxy-${LABEL}"

echo -e "${YELLOW}⚠️  Удаление прокси: ${LABEL}${NC}"
read -p "Вы уверены? [y/N]: " confirm
confirm=${confirm:-N}
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Отменено."
    exit 0
fi

# Останавливаем контейнер
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# Удаляем из docker-compose.yml
if [ -f "$COMPOSE_FILE" ]; then
    # Удаляем блок сервиса
    python3 -c "
import re
with open('$COMPOSE_FILE', 'r') as f:
    content = f.read()
pattern = r'\n  mtproxy-${LABEL}:.*?(?=\n  [a-z]|\nnetworks:|\Z)'
content = re.sub(pattern, '', content, flags=re.DOTALL)
with open('$COMPOSE_FILE', 'w') as f:
    f.write(content)
" 2>/dev/null || true
fi

# Удаляем конфиг
rm -rf "$INSTALL_DIR/mtproxy/${LABEL}"
rm -rf "$INSTALL_DIR/data/mtproxy-${LABEL}"

log_ok "Прокси '${LABEL}' удалён"
echo "Перезапустите остальные контейнеры: cd $INSTALL_DIR && docker compose up -d"
