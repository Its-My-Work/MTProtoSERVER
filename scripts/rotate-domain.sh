#!/bin/bash
# Скрипт автоматической ротации домена FakeTLS

set -euo pipefail

INSTALL_DIR="/opt/mtprotoserver"
DOMAINS_FILE="$INSTALL_DIR/config/domains.txt"
CONFIG_FILE="$INSTALL_DIR/config/settings.json"
LOG_FILE="$INSTALL_DIR/data/rotate-domain.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Читаем текущий домен
CURRENT_DOMAIN=$(grep -o '"fake_domain"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" | cut -d'"' -f4)
log "Текущий домен: $CURRENT_DOMAIN"

# Выбираем следующий домен
DOMAINS=()
while IFS= read -r line; do
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue
    DOMAINS+=("$line")
done < "$DOMAINS_FILE"

# Находим следующий домен
NEXT_INDEX=0
for i in "${!DOMAINS[@]}"; do
    if [ "${DOMAINS[$i]}" = "$CURRENT_DOMAIN" ]; then
        NEXT_INDEX=$(( (i + 1) % ${#DOMAINS[@]} ))
        break
    fi
done

NEW_DOMAIN="${DOMAINS[$NEXT_INDEX]}"
log "Новый домен: $NEW_DOMAIN"

# Обновляем конфиг
sed -i "s/\"fake_domain\": \"[^\"]*\"/\"fake_domain\": \"$NEW_DOMAIN\"/" "$CONFIG_FILE"

# Генерируем новый секрет через mtg (правильный формат)
docker pull nineseconds/mtg:2 -q >/dev/null 2>&1 || true

NEW_SECRET=$(docker run --rm nineseconds/mtg:2 generate-secret "$NEW_DOMAIN" 2>/dev/null)
if [ -z "$NEW_SECRET" ]; then
    log "❌ Не удалось сгенерировать секрет через mtg!"
    exit 1
fi

NEW_SECRET_HEX=$(docker run --rm nineseconds/mtg:2 generate-secret --hex "$NEW_DOMAIN" 2>/dev/null)
if [ -z "$NEW_SECRET_HEX" ]; then
    log "❌ Не удалось сгенерировать hex-секрет через mtg!"
    exit 1
fi

# Обновляем docker-compose (заменяем секрет в команде simple-run)
# Секрет в base64 формате для mtg
cd "$INSTALL_DIR"

# Обновляем секрет в docker-compose.yml
# Формат в command: simple-run --prefer-ip=prefer-ipv4 0.0.0.0:<port> <secret>
# Заменяем старый секрет на новый (base64 формат)
sed -i -E "s|^([[:space:]]+)(ee[a-f0-9]+\|[A-Za-z0-9+/=]+)$|\1${NEW_SECRET}|" "$INSTALL_DIR/docker-compose.yml"

# Перезапускаем прокси
docker compose up -d mtproto-proxy

log "✅ Домен изменён на $NEW_DOMAIN"
log "✅ Новый секрет (base64): $NEW_SECRET"
log "✅ Новый секрет (hex): $NEW_SECRET_HEX"
