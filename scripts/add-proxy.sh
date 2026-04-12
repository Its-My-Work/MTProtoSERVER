#!/bin/bash
set -euo pipefail

# ============================================================
# MTProtoSERVER — Добавить новый прокси
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'

INSTALL_DIR="/opt/mtprotoserver"
PROXIES_FILE="$INSTALL_DIR/data/proxies.json"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
SETTINGS_FILE="$INSTALL_DIR/config/settings.json"

E_OK="✅"
E_ERR="❌"
E_WARN="⚠️"
E_INFO="ℹ️"
E_ARROW="➜"
E_KEY="🔑"
E_NET="🌐"
E_SHIELD="🛡️"

log_ok()    { echo -e "${GREEN}${E_OK} $1${NC}"; }
log_err()   { echo -e "${RED}${E_ERR} $1${NC}"; }
log_warn()  { echo -e "${YELLOW}${E_WARN} $1${NC}"; }
log_info()  { echo -e "${BLUE}${E_INFO} $1${NC}"; }

print_sep() { echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# Проверка что установка существует
if [ ! -d "$INSTALL_DIR" ]; then
    log_err "MTProtoSERVER не установлен в $INSTALL_DIR"
    exit 1
fi

echo ""
print_sep
echo -e "${WHITE}${E_SHIELD}  MTProtoSERVER — Добавить новый прокси${NC}"
print_sep
echo ""

# Метка
read -p "Метка нового прокси (например: backup, friends): " label
label=${label:-"proxy-$(date +%s)"}

# Домен
echo ""
echo -e "${WHITE}Доступные домены для маскировки:${NC}"
echo -e "  1) cloudflare.com  (рекомендуется)"
echo -e "  2) 1c.ru"
echo -e "  3) sberbank.ru"
echo -e "  4) yandex.ru"
echo -e "  5) mail.ru"
echo -e "  6) vk.com"
echo -e "  7) gosuslugi.ru"
echo -e "  8) Ввести свой домен"
echo ""

read -p "Выберите домен [1-8] (по умолчанию 1): " domain_choice
domain_choice=${domain_choice:-1}

case $domain_choice in
    1) fake_domain="cloudflare.com" ;;
    2) fake_domain="1c.ru" ;;
    3) fake_domain="sberbank.ru" ;;
    4) fake_domain="yandex.ru" ;;
    5) fake_domain="mail.ru" ;;
    6) fake_domain="vk.com" ;;
    7) fake_domain="gosuslugi.ru" ;;
    8) read -p "Введите домен: " fake_domain ;;
    *) fake_domain="cloudflare.com" ;;
esac

log_ok "Домен: $fake_domain"

# Порт
read -p "Порт для нового прокси [443]: " port
port=${port:-443}

# Проверка занятости порта
while ss -tlnp 2>/dev/null | grep -q ":${port} "; do
    log_warn "Порт ${port} уже занят!"
    read -p "Введите другой порт: " port
done

log_ok "Порт: $port"

# Генерация секрета через mtg (правильный формат)
log_info "Генерация секрета через mtg..."
docker pull nineseconds/mtg:2 -q >/dev/null 2>&1 || true

secret=$(docker run --rm nineseconds/mtg:2 generate-secret "$fake_domain" 2>/dev/null)
if [ -z "$secret" ]; then
    log_err "Не удалось сгенерировать секрет через mtg! Проверьте Docker."
    exit 1
fi

secret_hex=$(docker run --rm nineseconds/mtg:2 generate-secret --hex "$fake_domain" 2>/dev/null)
if [ -z "$secret_hex" ]; then
    log_err "Не удалось сгенерировать hex-секрет через mtg!"
    exit 1
fi

log_ok "Секрет сгенерирован через mtg: ${E_KEY}"

# Получаем IP
SERVER_IP=$(grep -o '"proxy_ip"[[:space:]]*:[[:space:]]*"[^"]*"' "$SETTINGS_FILE" 2>/dev/null | cut -d'"' -f4 || echo "0.0.0.0")

# Получаем текущий proxy_count
PROXY_COUNT=$(grep -o '"proxy_count"[[:space:]]*:[[:space:]]*[0-9]*' "$SETTINGS_FILE" 2>/dev/null | grep -o '[0-9]*$' || echo "1")
NEW_COUNT=$((PROXY_COUNT + 1))

# Обновляем proxy_count
sed -i "s/\"proxy_count\": [0-9]*/\"proxy_count\": ${NEW_COUNT}/" "$SETTINGS_FILE"

# Добавляем в proxies.json
if [ -f "$PROXIES_FILE" ]; then
    # Удаляем последнюю } и добавляем новую запись
    sed -i '$ d' "$PROXIES_FILE"
    sed -i '$ s/}$//' "$PROXIES_FILE"
    cat >> "$PROXIES_FILE" << PENTRY_EOF
        },
        {
            "id": ${NEW_COUNT},
            "label": "${label}",
            "port": ${port},
            "domain": "${fake_domain}",
            "secret": "${secret}",
            "secret_hex": "${secret_hex}",
            "enabled": true,
            "created_at": "$(date '+%Y-%m-%d %H:%M:%S')",
            "connections": 0,
            "traffic_in": 0,
            "traffic_out": 0
        }
    ],
    "next_id": $((NEW_COUNT + 1))
}
PENTRY_EOF
else
    cat > "$PROXIES_FILE" << PEOF
{
    "proxies": [
        {
            "id": ${NEW_COUNT},
            "label": "${label}",
            "port": ${port},
            "domain": "${fake_domain}",
            "secret": "${secret}",
            "secret_hex": "${secret_hex}",
            "enabled": true,
            "created_at": "$(date '+%Y-%m-%d %H:%M:%S')",
            "connections": 0,
            "traffic_in": 0,
            "traffic_out": 0
        }
    ],
    "next_id": $((NEW_COUNT + 1))
}
PEOF
fi

# Добавляем сервис в docker-compose.yml
cat >> "$COMPOSE_FILE" << COMPOSE_ADD_EOF

  mtproxy-${label}:
    image: nineseconds/mtg:2
    container_name: mtproto-proxy-${label}
    restart: unless-stopped
    ports:
      - "${port}:${port}"
    command: >
      simple-run
      --prefer-ip=prefer-ipv4
      ${secret}
      0.0.0.0:${port}
    volumes:
      - ./data/mtproxy-${label}:/data
    networks:
      - mtproto-net
    healthcheck:
      test: ["CMD", "ss", "-tlnp", "sport", "=:${port}"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
COMPOSE_ADD_EOF

# Создаём конфиг
mkdir -p "$INSTALL_DIR/mtproxy/${label}"
cat > "$INSTALL_DIR/mtproxy/${label}/config.toml" << MTCONF_EOF
# MTProto Proxy: ${label}
[general]
bind_to = "0.0.0.0:${port}"
secret = "${secret}"
fake_tls_domain = "${fake_domain}"
prefer_ip = "prefer-ipv4"

[anti_replay]
enabled = true
max_size = 16384

[timeouts]
inactivity = 300
keepalive = 60

[stats]
enabled = true
stats_file = "/data/stats.json"
MTCONF_EOF

# Перезапускаем
echo ""
log_info "Перезапуск контейнеров..."
cd "$INSTALL_DIR"
docker compose up -d

sleep 3

# Итог
echo ""
print_sep
echo -e "${GREEN}${E_OK}  Новый прокси добавлен!${NC}"
print_sep
echo ""
echo -e "${WHITE}  Метка:    ${CYAN}${label}${NC}"
echo -e "${WHITE}  Порт:     ${CYAN}${port}${NC}"
echo -e "${WHITE}  Домен:    ${CYAN}${fake_domain}${NC}"
echo -e "${WHITE}  Секрет:   ${CYAN}${secret}${NC}"
echo -e "${WHITE}  Hex:      ${CYAN}${secret_hex}${NC}"
echo ""
echo -e "${WHITE}  Ссылка:${NC}"
echo -e "${CYAN}   tg://proxy?server=${SERVER_IP}&port=${port}&secret=${secret_hex}${NC}"
echo ""
print_sep
