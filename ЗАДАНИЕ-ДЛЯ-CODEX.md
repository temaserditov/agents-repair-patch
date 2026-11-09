Это полное задание для Codex на моём компьютере/сервере, не просьба объяснить текст.
Выполни работу до результата; лишних подтверждений не запрашивай.

Пакет: https://github.com/temaserditov/agents-repair-patch/releases/download/v2026.11.09.1/agents-repair-patch-v2026.11.09.1.zip
SHA-256 ZIP: {{ZIP_SHA256}}
Исходники: https://github.com/temaserditov/agents-repair-patch/tree/v2026.11.09.1
Версия: v2026.11.09.1. Commit: e242603f51fb24be6b72c34e0644c45c0db95db6.
Не скачивай другие версии, не меняй SHA. Если остались незаполненные поля,
остановись: опубликованный пакет ещё не указан.

1. Скачай ZIP в новую пустую папку, проверь SHA-256. При несовпадении остановись.
   Распакуй, прочитай README и скрипты. Не запускай неизвестный файл до проверки.
2. На Mac/Linux запусти `bash repair.sh diagnose`. На Windows:
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\repair.ps1 diagnose`.
   Bypass применяется только к этому процессу, системную политику не меняй.
   Если среда Codex запрещает запуск, выполни в обычном PowerShell разрешённым
   способом. На VPS выполняй НА сервере по SSH от того же пользователя, которым
   ставили агентов (обычно root), а не на ноутбуке. Покажи короткую сводку.
3. Запусти ремонт: Mac/Linux `bash repair.sh repair --apply`; Windows
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\repair.ps1 repair --apply`.
   Повтори ту же команду и убедись, что второй запуск сообщает `changed:false`.
   Код 3 означает, что нужен следующий шаг, а не ошибку самого патча.
4. Если нужно штатное обновление, запусти его САМ. Попроси у меня в чате
   короткий код установки (кабинет https://school.serditov.ru/dashboard/agents ,
   кнопка «Получить код»; вид `XXXX-XXXX`, одноразовый). Затем:
   Mac: `COURSE_TOKEN='XXXX-XXXX' bash <(curl -fsSL https://school.serditov.ru/install-mac.sh)`
   VPS (на сервере по SSH, от пользователя установки):
   `COURSE_TOKEN='XXXX-XXXX' bash <(curl -sL https://school.serditov.ru/v)`
   Windows: скачай https://school.serditov.ru/windows-agents/install-v3.ps1 во
   временный файл, сверь SHA-256 с хешем из
   https://school.serditov.ru/windows-agents/instruction.md и запусти
   `'XXXX-XXXX' | powershell -NoProfile -STA -ExecutionPolicy Bypass -File <файл> -CodeFromStdin`;
   при незавершённом Content повтори с `-ResumeContent` и новым кодом.
   Та же команда обновляет существующую установку, ничего не сносит. Код не
   печатай в отчёте и не сохраняй в файлы; если код не принят (403), попроси новый.
5. После обновления запусти `bash repair.sh verify --wait 60`, на Windows
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\repair.ps1 verify`.
   Покажи путь к JSON в «Загрузки», результаты и всё, что осталось непроверенным.
   Не объявляй успех, если verify не прошёл; пропуск проверки также назови честно.

Если владелец сам правил SOUL.md агента: сохрани его текст. Панель может
подсветить его как «без служебного маркера». Повтори штатное обновление
до agents-release-20261105.2 или новее: оно автоматически перенесёт личные
правки и восстановит маркер. Не удаляй и не заменяй личный текст вручную.

Не проси в чат пароли и ключи (короткий код установки просить можно). Не печатай `.env`, `auth.json`, токены,
cookie и переписки. Не переустанавливай Hermes, не удаляй профили, проекты или
память. Не меняй права доступа, антивирус и системную ExecutionPolicy.
Не читай секреты для самостоятельного ремонта. Сохраняй личные правки.
