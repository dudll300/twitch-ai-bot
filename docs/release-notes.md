Первый релиз Twitch AI Bot с готовым графическим приложением для Windows, macOS и Linux. Python устанавливать не требуется.

| Система | Файл | Запуск |
| --- | --- | --- |
| Windows 10/11, 64 бит | `Windows-x86_64.zip` | Распаковать и открыть `TwitchAIBot.exe` |
| macOS 12+, Apple Silicon | `macOS-arm64.dmg` | Перенести `TwitchAIBot.app` в Applications |
| macOS 12+, Intel | `macOS-x86_64.dmg` | Перенести `TwitchAIBot.app` в Applications |
| Linux x86_64, Ubuntu 22.04+ / glibc 2.35+ | `Linux-x86_64.tar.gz` | Распаковать и открыть `TwitchAIBot/TwitchAIBot` |

Включены подключение Twitch, настройки поведения, профили зрителей, самостоятельные реплики, проверки безопасности, локальный контекст, история и тестирование AI без Twitch.

Настройки сохраняются отдельно от приложения и остаются при обновлении:

- Windows: `%APPDATA%\TwitchAIBot`.
- macOS: `~/Library/Application Support/TwitchAIBot`.
- Linux: `$XDG_CONFIG_HOME/TwitchAIBot` или `~/.config/TwitchAIBot`.

Windows-сборка не подписана сертификатом издателя. macOS-сборки имеют локальную подпись, без Developer ID и нотариализации; для первого запуска может понадобиться **Системные настройки → Конфиденциальность и безопасность → Всё равно открыть**. Linux требует графического сеанса и системных библиотек Qt/XCB; на Ubuntu при ошибке `xcb` установите `libxcb-cursor0`.

Все четыре пакета проходят модульные проверки, проверки GUI и запуск упакованного GUI/worker без обращения к Twitch или платному AI. В `SHA256SUMS.txt` приведены контрольные суммы архивов.

[Инструкция по подключению и использованию](https://github.com/dudll300/twitch-ai-bot#readme).
