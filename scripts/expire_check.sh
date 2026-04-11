#!/bin/bash
# Проверка истекших пользователей

set -euo pipefail

INSTALL_DIR="/opt/mtprotoserver"
LOG_FILE="$INSTALL_DIR/data/expire_check.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "🔍 Проверка истекших пользователей..."

# Запуск Python скрипта для проверки
cd "$INSTALL_DIR/webui"
python3 -c "
from app import check_expired_users
check_expired_users()
"

log "✅ Проверка завершена"