## Архитектура MTProtoSERVER

```mermaid
graph TD
    A[Пользователь] --> B[WebUI Flask App :8088]
    A --> C[Telegram Bot]
    B --> D[Config files: settings.json, auth.json]
    B --> E[Data files: users.json, proxies.json, clients.json, proxy.toml]
    E --> F[MTProxy-S1 Container: mtg-multi host mode]
    C --> D
    C --> E
    F --> G[MTProto Proxy Services на портах из proxy.toml]
    B --> H[Docker Socket для управления контейнерами]
```

### Компоненты:
- **WebUI**: Flask приложение для управления сервером, обновляет proxy.toml для MTG.
- **MTProxy-S1**: Контейнер с mtg-multi в host network mode, слушает порты из proxy.toml.
- **Bot**: Telegram бот в контейнере для управления.
- **Config/Data**: JSON файлы для настроек, proxy.toml - динамический конфиг MTG.

### Изменения:
- MTG слушает несколько портов из bind-to списка.
- Secrets: все секреты (base proxy + clients) в одном конфиге.
- Поддержка произвольных портов через host networking.