Twitch AI Bot 1.0.0
https://github.com/dudll300/twitch-ai-bot

Готовое приложение с GUI. Устанавливать Python не требуется.

Windows 10/11 (64 бит): распакуйте ZIP и запустите TwitchAIBot.exe.
macOS 12+: откройте DMG и перенесите TwitchAIBot.app в Applications.
  arm64 — Apple Silicon (M1 и новее); x86_64 — Mac с процессором Intel.
Linux x86_64: распакуйте tar.gz и запустите TwitchAIBot/TwitchAIBot.
  Поддерживается Ubuntu 22.04+ и совместимые дистрибутивы с glibc 2.35+.
  Нужен графический сеанс X11 или Wayland и системные библиотеки Qt/XCB.
  В Ubuntu при ошибке Qt xcb установите: sudo apt install libxcb-cursor0

Сборки Windows не подписаны сертификатом издателя. Сборки macOS имеют
локальную подпись для запуска, но не подписаны Developer ID и не нотариализованы.
Если macOS блокирует запуск, откройте «Системные настройки → Конфиденциальность
и безопасность» и используйте «Всё равно открыть» для скачанного приложения.

Настройки и токены хранятся локально:
  Windows: %APPDATA%\TwitchAIBot
  macOS: ~/Library/Application Support/TwitchAIBot
  Linux: ${XDG_CONFIG_HOME:-~/.config}/TwitchAIBot
При обновлении замените приложение; папку с данными сохраните.

Введите Twitch Client ID (Public), данные бота и канала, Base URL, AI API-ключ
и модель. Для тестирования AI подключать Twitch не требуется.
Полная инструкция: https://github.com/dudll300/twitch-ai-bot#readme
