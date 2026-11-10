Это полное задание для Codex на моём компьютере/сервере, не просьба объяснить текст.
Выполни работу до результата; лишних подтверждений не запрашивай.

Пакет: https://github.com/temaserditov/agents-repair-patch/releases/download/v2026.11.10.3/agents-repair-patch-v2026.11.10.3.zip
SHA-256 ZIP: указан в задании ученика и в SHA256SUMS релиза (внутри архива не хранится)
Исходники: https://github.com/temaserditov/agents-repair-patch/tree/v2026.11.10.3
Версия: v2026.11.10.3. Commit: 9bca80937c43b08a64bfa1abd063284625fd8a52.
Не скачивай другие версии, не меняй SHA. Если в строках «Пакет», «Исходники»
или «Версия» осталось поле в двойных фигурных скобках, остановись: опубликованный
пакет ещё не указан.

Цель: агенты на Hermes 0.23.0 (commit 2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19),
все агенты по моему тарифу и бонусам на месте, данные и личные правки сохранены.

1. Скачай ZIP в новую пустую папку и проверь SHA-256. Если ссылка отвечает 5xx
   (сбой GitHub), скачай тот же файл через API: из
   `https://api.github.com/repos/temaserditov/agents-repair-patch/releases/tags/v2026.11.10.3`
   возьми `url` ассета с именем `agents-repair-patch-v2026.11.10.3.zip` и скачай его
   `curl -fL -H 'Accept: application/octet-stream' <url> -o <имя>`. SHA-256 сверяй
   в любом случае; при несовпадении остановись. Распакуй, прочитай README и
   скрипты. Не запускай неизвестный файл до проверки. `vendor/bin/person-cutout` —
   готовая проверенная программа вырезания фона для Mac (её SHA-256 закреплён в
   README и manifest.json); сам её не запускай, ремонт только копирует её после
   сверки хеша. Это не повод останавливаться.
2. Диагностика. Mac/Linux: `bash repair.sh diagnose`. Windows:
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\repair.ps1 diagnose`.
   Bypass применяется только к этому процессу, системную политику не меняй.
   Если среда Codex запрещает запуск, выполни в обычном PowerShell разрешённым
   способом. На VPS выполняй НА сервере по SSH от того же пользователя, которым
   ставили агентов (обычно root), а не на ноутбуке. Покажи короткую сводку.
3. Ремонт: Mac/Linux `bash repair.sh repair --apply`; Windows
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\repair.ps1 repair --apply`.
   Повтори ту же команду и убедись, что второй запуск сообщает `changed:false`.
   Код 3 означает, что нужен следующий шаг, а не ошибку самого патча.
4. Mac и VPS: штатное обновление выполни ВСЕГДА, даже если шаг 3 вернул 0: оно
   ставит Hermes 0.23.0 и доставляет всех агентов по текущему тарифу и бонусам
   (например, Продажника и Ассистента), сохраняя профили, переписки и настройки.
   Попроси у меня в чате короткий код установки (кабинет
   https://school.serditov.ru/dashboard/agents , кнопка «Получить код»; вид
   `XXXX-XXXX`, одноразовый). Затем:
   Mac: `COURSE_TOKEN='XXXX-XXXX' bash <(curl -fsSL https://school.serditov.ru/install-mac.sh)`
   VPS (на сервере по SSH, от пользователя установки):
   `COURSE_TOKEN='XXXX-XXXX' bash <(curl -sL https://school.serditov.ru/v)`
   Код не печатай в отчёте и не сохраняй в файлы; если код не принят (403),
   попроси новый. Команда обновляет существующую установку и ничего не сносит.
   Windows, до v3 — перевод официального Hermes 0.22.x на 0.23.0 штатным путём
   самого Hermes (руками в папке Hermes ничего не меняй). Сохрани два блока ниже
   в файлы `hermes-check.ps1` и `hermes-stop.ps1` в «Загрузках» и запускай их
   `powershell -NoProfile -ExecutionPolicy Bypass -File <файл>`. Блок проверки
   только читает состояние:

```powershell
# FIX_0582: the state of the official Hermes installation and its gateway, as
# lines HERMES_VERSION=, HERMES_HEAD=, GATEWAY=up <version>|down, PANEL=up|down.
# -WaitGatewaySeconds waits for a gateway that is starting. Read-only.
param([int]$WaitGatewaySeconds=0)
$ErrorActionPreference='Continue'
$hermesHome=if($env:HERMES_HOME){$env:HERMES_HOME}else{Join-Path $env:LOCALAPPDATA 'hermes'}
$hermes=Join-Path $hermesHome 'bin\hermes.exe'
$version='missing'
if(Test-Path -LiteralPath $hermes -PathType Leaf){$text=(& $hermes --version|Out-String);$version=if($text -match '(?m)^Hermes Agent v?(\S+)'){$Matches[1]}else{'unreadable'}}
$git=@(Get-ChildItem -Path (Join-Path $hermesHome 'tools\git*\cmd\git.exe') -ErrorAction SilentlyContinue|Select-Object -First 1)
$gitExe=if($git.Count){$git[0].FullName}else{$c=Get-Command git.exe -ErrorAction SilentlyContinue;if($c){$c.Source}else{''}}
$head='unknown'
if($gitExe){$out=([string](& $gitExe -C (Join-Path $hermesHome 'hermes-agent') rev-parse HEAD|Out-String)).Trim();if($out -match '^[0-9a-f]{40}$'){$head=$out}}
$gateway='down'
if($version -notin @('missing','unreadable')){
  $entry="runpy.run_module('__serditov_check__', run_name='__main__', alter_sys=True)"
  try{
    $command=(& $hermes --print-runtime-command --module __serditov_check__|Out-String)|ConvertFrom-Json
    $boot=[string]$command[3];if($boot.EndsWith($entry)){$boot=$boot.Substring(0,$boot.Length-$entry.Length)}
    $probe=Join-Path ([IO.Path]::GetTempPath()) ('hermes-check-'+[guid]::NewGuid().ToString('N')+'.py')
    [IO.File]::WriteAllText($probe,$boot+"`nimport json,os`nfrom pathlib import Path`nfrom gateway.control_socket import identify_gateway`nr=identify_gateway(Path(os.environ['SERDITOV_HERMES_HOME'])) or {}`nprint(json.dumps({'live':bool(r),'version':r.get('code_version')}))`n",(New-Object Text.UTF8Encoding($false)))
    $env:SERDITOV_HERMES_HOME=$hermesHome
    $deadline=[DateTime]::UtcNow.AddSeconds($WaitGatewaySeconds)
    do{
      $answer=([string](& ([string]$command[0]) -I -B $probe|Out-String)).Trim().Split("`n")[-1]|ConvertFrom-Json
      if($answer.live){$gateway='up '+[string]$answer.version;break}
      if([DateTime]::UtcNow -lt $deadline){Start-Sleep -Seconds 3}
    }while([DateTime]::UtcNow -lt $deadline)
  }catch{$gateway='unknown'}finally{if($probe){Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue}}
}
$panel='down'
try{$r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8787/api/windows-installer-health' -TimeoutSec 3;if($r.StatusCode -eq 200){$panel='up'}}catch{}
Write-Output ('HERMES_VERSION='+$version);Write-Output ('HERMES_HEAD='+$head);Write-Output ('GATEWAY='+$gateway);Write-Output ('PANEL='+$panel)
```

   Блок остановки:

```powershell
# FIX_0581/FIX_0582: before the official Hermes installer updates the Hermes
# folder, stop what runs from it: this Windows user's course watchdog, panel and
# control tasks (names as v3 builds them from the user SID), their node
# processes and the official gateway. Prints HERMES_STOPPED (exit 0). Otherwise
# it starts the course tasks again, re-enables the watchdog and prints
# HERMES_STILL_RUNNING (exit 3) or HERMES_STOP_FAILED (exit 4). Nothing is deleted.
# -Restore only returns the course tasks (HERMES_TASKS_RESTORED, exit 0): the last
# step when the update, its retry, the 0.22.7 return and -InstallTasks all failed.
# FIX_0585: a closed WMI/CIM or an external program past its timeout is a failure,
# never a false HERMES_STOPPED.
# FIX_0587: the panel is stopped WITHOUT windows-launcher.mjs --stop: that command
# unregisters the watchdog and panel tasks and disables recovery, so nothing could
# be returned afterwards. A return is verified: the three tasks exist, the watchdog
# is enabled and the panel answers (or its task runs); otherwise HERMES_RESTORE_FAILED.
param([switch]$Restore)
$ErrorActionPreference='Continue'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sha=[Security.Cryptography.SHA256]::Create()
try{$suffix=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sid)))).Replace('-','').Substring(0,16).ToLowerInvariant()}finally{$sha.Dispose()}
$names=@('Watchdog','WebShell','Control')|ForEach-Object{'SerditovAgents'+$_+'-'+$suffix}
$hermesHome=if($env:HERMES_HOME){$env:HERMES_HOME}else{Join-Path $env:LOCALAPPDATA 'hermes'}
function Restore-CourseTasks {
  # Returns the problems; an empty list means verified.
  $problems=@()
  foreach($n in $names){
    $task=Get-ScheduledTask -TaskPath '\' -TaskName $n -ErrorAction SilentlyContinue
    if(-not $task){$problems+=($n+': missing');continue}
    if($task.State -eq 'Disabled'){try{Enable-ScheduledTask -TaskPath '\' -TaskName $n -ErrorAction Stop|Out-Null}catch{$problems+=($n+': enable failed: '+$_.Exception.Message)}}
  }
  foreach($n in $names[1..2]){
    if(-not (Get-ScheduledTask -TaskPath '\' -TaskName $n -ErrorAction SilentlyContinue)){continue}
    try{Start-ScheduledTask -TaskPath '\' -TaskName $n -ErrorAction Stop}catch{$problems+=($n+': start failed: '+$_.Exception.Message)}
  }
  $watchdog=Get-ScheduledTask -TaskPath '\' -TaskName $names[0] -ErrorAction SilentlyContinue
  if($watchdog -and $watchdog.State -eq 'Disabled'){$problems+=($names[0]+': still disabled')}
  if(Get-ScheduledTask -TaskPath '\' -TaskName $names[1] -ErrorAction SilentlyContinue){
    $panel=$false;$deadline=[DateTime]::UtcNow.AddSeconds(90)
    do{
      try{$r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8787/api/windows-installer-health' -TimeoutSec 3;if($r.StatusCode -eq 200){$panel=$true;break}}catch{}
      Start-Sleep -Seconds 3
    }while([DateTime]::UtcNow -lt $deadline)
    if(-not $panel -and (Get-ScheduledTask -TaskPath '\' -TaskName $names[1] -ErrorAction SilentlyContinue).State -ne 'Running'){$problems+=($names[1]+': panel not answering and task not running')}
  }
  ,$problems
}
function Format-RestoreFailure($Problems) {
  # Emits the failure line or nothing; never a value mixed with a status line.
  if(@($Problems).Count){'HERMES_RESTORE_FAILED: '+(@($Problems) -join '; ')}
}
function Invoke-Bounded([string]$File,[string]$Arguments,[int]$Seconds){
  # Start-Process -Wait would wait for every child too (and without a limit) on 5.1.
  $process=Start-Process -FilePath $File -ArgumentList $Arguments -NoNewWindow -PassThru
  if(-not $process.WaitForExit($Seconds*1000)){
    $null=& taskkill.exe /PID $process.Id /T /F
    throw ('Timed out after '+$Seconds+' s: '+[IO.Path]::GetFileName($File))
  }
}
if($Restore){$failed=Format-RestoreFailure (Restore-CourseTasks);if($failed){Write-Output $failed;exit 5};Write-Output 'HERMES_TASKS_RESTORED';exit 0}
try{
  foreach($n in $names){
    if(-not (Get-ScheduledTask -TaskPath '\' -TaskName $n -ErrorAction SilentlyContinue)){continue}
    if($n -like 'SerditovAgentsWatchdog-*'){Disable-ScheduledTask -TaskPath '\' -TaskName $n -ErrorAction Stop|Out-Null}
    Stop-ScheduledTask -TaskPath '\' -TaskName $n -ErrorAction SilentlyContinue
  }
  $control=Join-Path $env:USERPROFILE 'InfobizAgents\control'
  $panelConfig=Join-Path $control 'webshell-runtime.json'
  # Our panel (launcher and server.mjs) runs from its appDir; the config, the tasks and recovery stay as they are.
  $owned=@(Join-Path $control 'control.mjs')
  if(Test-Path -LiteralPath $panelConfig -PathType Leaf){
    $cfg=Get-Content -LiteralPath $panelConfig -Raw -Encoding UTF8|ConvertFrom-Json
    if([string]$cfg.appDir){$owned+=([string]$cfg.appDir).TrimEnd('\')+'\'}
  }
  foreach($p in @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction Stop|Where-Object{$line=[string]$_.CommandLine;@($owned|Where-Object{$line.IndexOf($_,[StringComparison]::OrdinalIgnoreCase) -ge 0}).Count})){Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue}
  $hermes=Join-Path $hermesHome 'bin\hermes.exe'
  if(Test-Path -LiteralPath $hermes -PathType Leaf){Invoke-Bounded $hermes 'gateway stop' 180}
  $prefix=$hermesHome.TrimEnd('\')+'\'
  $deadline=[DateTime]::UtcNow.AddSeconds(30)
  do{
    $busy=@(Get-CimInstance Win32_Process -ErrorAction Stop|Where-Object{$_.ProcessId -ne $PID -and ([string]$_.ExecutablePath).StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)})
    if(-not $busy.Count){Write-Output 'HERMES_STOPPED';exit 0}
    Start-Sleep -Seconds 1
  }while([DateTime]::UtcNow -lt $deadline)
  Format-RestoreFailure (Restore-CourseTasks)
  Write-Output ('HERMES_STILL_RUNNING: '+(($busy|ForEach-Object{$_.Name+'#'+$_.ProcessId}) -join ', '))
  exit 3
}catch{
  $failure=$_.Exception.Message
  Format-RestoreFailure (Restore-CourseTasks)
  Write-Output ('HERMES_STOP_FAILED: '+$failure)
  exit 4
}
```

   Если проверка показала `HERMES_VERSION=0.22.…`:
   а) Скажи мне: ремонт (шаг 3) сам Hermes не копирует. Официальный установщик
      убирает локальные изменения кода Hermes и неотслеживаемые файлы в git stash
      (`hermes-install-autostash-…`) и оставляет свои резервные ссылки — они не
      теряются. Настройки, ключи, переписки и профили лежат вне папки кода и не
      меняются.
   б) Запусти блок остановки. `HERMES_STOPPED` — продолжай.
      `HERMES_STILL_RUNNING` — gateway мог не остановиться, потому что агент ещё
      выполняет задачу, или открыто окно Hermes: подожди несколько минут
      (попроси меня закрыть окно Hermes) и запусти блок ещё раз; если снова —
      пропусти в), переходи к v3 и сообщи мне. `HERMES_STOP_FAILED` или блок
      оборвался без этих слов — пропусти в), переходи к v3 и сообщи мне. Блок
      сам возвращает задачи курса, если не остановил всё, и проверяет возврат:
      строка `HERMES_RESTORE_FAILED` значит, что задачи курса не вернулись —
      обязательно скажи мне её целиком. `HERMES_STOP_FAILED` бывает и тогда,
      когда Windows не дала прочитать список процессов (WMI) или программа не
      завершилась за отведённое время.
   в) Скачай https://school.serditov.ru/windows-agents/instruction.md и выполни
      без изменений блок раздела «Обновление уже установленного официального
      Hermes 0.22.x до 0.23.0» в обычном Windows PowerShell. Затем блок проверки:
      нужно и `HERMES_VERSION=0.23.0`, и
      `HERMES_HEAD=2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`. Если хоть одно не
      так — выполни тот же блок обновления ещё раз (официальный установщик можно
      повторять) и снова проверь. Если опять не так — выполни блок «Возврат на
      официальный 0.22.7, если обновление не удалось дважды» из того же
      раздела, проверь `HERMES_HEAD=b200f5bc60e31588ba2cb60e288029c4a54ba5fa` и
      сообщи мне весь вывод. Руками ничего не чини.
   г) Переходи к v3.
   Windows: штатное обновление — Windows v3, его тоже выполни ВСЕГДА (и когда
   шаг 3 вернул `run-windows-v3`, и когда вернул 0): v3 ставится поверх
   существующей установки и доставляет агентов по тарифу и бонусам, сохраняя
   профили и данные. Делай по
   инструкции https://school.serditov.ru/windows-agents/instruction.md (скачай
   install-v3.ps1 во временный файл, сверь SHA-256 с хешем из инструкции и
   запусти `'XXXX-XXXX' | powershell -NoProfile -STA -ExecutionPolicy Bypass -File <файл> -CodeFromStdin`;
   при незавершённом Content повтори с `-ResumeContent` и новым кодом). Если
   сервер отвечает 503 «Файлы Windows ещё не подготовлены» — код не потрачен:
   не повторяй, сообщи мне, что Windows v3 временно недоступен на сервере, и
   переходи к шагу 5 с тем, что уже исправлено.
   После v3 на Windows — в любом исходе (успех, обновление Hermes не удалось,
   503, 403, v3 упал рано) — зарегистрируй задачи и запусти gateway штатной
   командой установки:
   `& ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $env:USERPROFILE 'InfobizAgents\control\runner.ps1')))) -InstallTasks`.
   Если эта команда упала (например, на проверке Hermes после неудачного
   обновления и возврата) или файла `runner.ps1` нет — верни задачи курса
   блоком остановки с ключом `-Restore`:
   `powershell -NoProfile -ExecutionPolicy Bypass -File hermes-stop.ps1 -Restore`
   (нужно `HERMES_TASKS_RESTORED`: задачи на месте, watchdog включён, панель
   отвечает; `HERMES_RESTORE_FAILED` — задачи курса не вернулись, это важно
   сказать мне), и только потом сообщи мне весь вывод.
   Затем блок проверки с ожиданием gateway:
   `powershell -NoProfile -ExecutionPolicy Bypass -File hermes-check.ps1 -WaitGatewaySeconds 90`.
   Нужно `GATEWAY=up <версия>`, где версия та же, что в `HERMES_VERSION` этого
   же вывода, и `PANEL=up`. Иначе сообщи мне весь вывод.
5. После обновления повтори ремонт ещё раз (та же команда, что в шаге 3, дважды;
   второй запуск — `changed:false`). Он доводит то, что приходит только со
   штатным обновлением: совместимость панели с Hermes 0.23, профильные
   инструкции, Content.
6. Проверка: `bash repair.sh verify --wait 60`, на Windows
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\repair.ps1 verify`,
   а затем ещё раз
   `powershell -NoProfile -ExecutionPolicy Bypass -File hermes-check.ps1 -WaitGatewaySeconds 90`:
   ремонт шага 5 мог перезапустить gateway. Нужно `GATEWAY=up <версия>` с той же
   версией, что в `HERMES_VERSION`, и `PANEL=up`.
   Покажи путь к JSON в «Загрузки», результаты и всё, что осталось
   непроверенным. Отдельно назови версию Hermes и список агентов из отчёта.
   Не объявляй успех, если verify не прошёл; пропуск проверки также назови честно.

Если владелец сам правил SOUL.md агента: сохрани его текст. Панель может
подсветить его как «без служебного маркера». Штатное обновление до
agents-release-20261105.2 или новее автоматически перенесёт личные правки и
восстановит маркер. Не удаляй и не заменяй личный текст вручную. Если ремонт
сообщает `skipped:panel-edited` или `skipped:support-edited`, это сохранённые
ручные правки панели: не перезаписывай их.

Не проси в чат пароли и ключи (короткий код установки просить можно). Не печатай `.env`, `auth.json`, токены,
cookie и переписки. Не переустанавливай Hermes вручную, не удаляй профили,
проекты или память. Не меняй права доступа, антивирус и системную
ExecutionPolicy. Не читай секреты для самостоятельного ремонта. Сохраняй личные правки.
