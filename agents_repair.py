#!/usr/bin/env python3
"""Conservative repair of existing course installations. Python 3.9+, no account data."""
import argparse
import ast
from datetime import datetime, timezone
from contextvars import ContextVar
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.error
import urllib.request
import uuid
import io
import zipfile
sys.dont_write_bytecode = True

HERE = Path(__file__).absolute().parent
NAMES = {'marketer', 'copywriter', 'designer', 'editor', 'tech', 'assistant', 'sales'}
PROTECTED = {'.env', 'auth.json', 'config.yaml', 'sessions', 'logs', 'memories', 'cache', 'cron', 'state.db', 'cookies'}
SECRET_URL = re.compile(rb'''secret=["']*[^&\s"'\\]*''', re.I)
PACKAGE_HELPERS = ('finish.py', 'update.py', 'student-runtime.py', 'security-tools.py',
                   'local-stt.py', 'setup-linux-whisper.py', 'retire-failure-support.py')
NEW_SKILLS = {'designer': 'carousel-style-brief', 'editor': 'motion-brief-builder'}
CONTENT_MODELS = {
    'arm64': {'ggml-tiny.bin':'be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21'},
    'x64': {
        'sherpa-onnx-whisper-tiny/tiny-encoder.int8.onnx':'d24fb083ae3b1041fc24e97971d60e280c9342201fbb67b0ab428a8b4a51a434',
        'sherpa-onnx-whisper-tiny/tiny-decoder.int8.onnx':'d2fece8dd42771f1df975c6c0445770d0c292bf7547c2cae04a6c0cc57540925',
        'sherpa-onnx-whisper-tiny/tiny-tokens.txt':'b34b360dbb493e781e479794586d661700670d65564001f23024971d1f2fa126'},
}
KANBAN = re.compile(r'(?m)^# Infobiz Agents multi-gateway defaults\nkanban:[ \t]*\n  dispatch_in_gateway: false[ \t]*(?:\n|$)')

PATH_ROOTS = ContextVar('repair_path_roots', default=(HERE,))


def debug_exception(stage):
    # Opt-in CI evidence only. Never include exception text in normal findings.
    target = os.environ.get('AGENTS_REPAIR_DEBUG_FILE')
    if not target or not Path(target).is_absolute():
        return
    try:
        path = Path(target)
        plain(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as stream:
            stream.write('stage: '+stage+'\n'+traceback.format_exc()+'\n')
    except Exception:
        pass  # A diagnostic destination must never change the repair result.


def load(name):
    path = HERE/'vendor'/(name+'.py')
    plain(path)
    if not path.is_file():
        raise ValueError('Missing bundled helper')
    spec = importlib.util.spec_from_file_location('repair_'+name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plain(path):
    # POSIX links are rejected throughout the chain. Windows system parents
    # (AppData/Local, user directory, drive) can carry reparse attributes;
    # only the target and entries within our managed roots are ours to reject.
    path = Path(path).absolute()
    for item in [path, *path.parents]:
        roots = PATH_ROOTS.get()
        managed = any(item == root or root in item.parents for root in roots)
        system_parent = any(item in root.parents for root in roots)
        scoped = managed or item == path and not system_parent
        if item.is_symlink() and (os.name != 'nt' or scoped):
            raise ValueError('Linked path preserved')
        try:
            info = item.lstat()
            attributes = getattr(info, 'st_file_attributes', 0)
            if scoped and attributes & 0x400:
                raise ValueError('Reparse path preserved')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or attributes & 0x10):
                raise ValueError('Special object preserved')
        except FileNotFoundError:
            pass


def read(path, limit=8*1024*1024):
    plain(path)
    if path.stat().st_size > limit:
        raise ValueError('Oversized file preserved')
    return path.read_bytes()


def file_sha256(path, limit=2*1024*1024*1024):
    # Models exceed the text-file cap. Bound both memory and total bytes,
    # including a file that grows after the initial size check.
    plain(path)
    if path.stat().st_size > limit:
        raise ValueError('Oversized file preserved')
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while True:
            block = stream.read(1024*1024)
            if not block:
                break
            total += len(block)
            if total > limit:
                raise ValueError('Oversized file preserved')
            digest.update(block)
    return digest.hexdigest()


def json_file(path):
    value = json.loads(read(path).decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Invalid installation metadata')
    return value


def atomic(path, data, mode=0o600):
    plain(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            os.chmod(temporary, mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        plain(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized_soul(data):
    return data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff').strip().encode()


def env_keys(path):
    # Do not decode or retain values. Only a fixed allowlist of names is returned.
    plain(path)
    if not path.is_file():
        return set()
    result = set()
    with path.open('rb') as stream:
        for line in stream:
            key = line.split(b'=', 1)[0].strip()
            if key.startswith(b'export '):
                key = key[7:].strip()
            if key in {b'HERMES_LOCAL_STT_COMMAND', b'GROQ_API_KEY'}:
                result.add(key.decode('ascii'))
    return result


def managed_name(name):
    if not isinstance(name, str) or '\\' in name or ':' in name or len(name)>400:
        return False
    parts = name.split('/')
    return (len(parts)>=2 and parts[0] in NAMES and
            all(re.fullmatch(r'[a-zA-Z0-9_.-]+', p) and p not in {'.', '..'} and
                not p.endswith(('.', ' ')) and p.lower() not in PROTECTED and not p.lower().startswith('.env') and
                p.split('.')[0].upper() not in {'CON','PRN','AUX','NUL', *['COM'+str(i) for i in range(1,10)], *['LPT'+str(i) for i in range(1,10)]}
                for p in parts))


def command(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                              env={**os.environ, 'LC_ALL':'C', 'PYTHONDONTWRITEBYTECODE':'1'},
                              **({'creationflags':0x08000000} if os.name=='nt' else {}))
    except (OSError, subprocess.TimeoutExpired):
        debug_exception('command')
        return subprocess.CompletedProcess(args, 127, '', '')


def kanban_text(text):
    return KANBAN.sub(lambda m: 'kanban:\n' if re.match(r'(?:[ \t]*\n)*[ \t]+\S', text[m.end():]) else 'kanban: {}\n', text)


class Repair:
    def __init__(self, home, install=None, system=None, hermes=None, apply=False, verify=False, wait=0):
        self.home = Path(home).absolute()
        self.install = Path(install).absolute() if install else self.home/'InfobizAgents'
        self.system = system or platform.system()
        self.hermes = Path(hermes or os.environ.get('AGENTS_REPAIR_HERMES_HOME') or self.home/'.hermes').absolute()
        self.profiles = self.hermes/'profiles'
        self.control = self.install/'control/tariffs'
        self.apply = apply
        self.verify = verify
        self.wait = wait
        self.findings = []
        self.changed = False
        self.failed = False
        self.unreadable = set()
        self.stage = 'initialization'
        self.backup = self.control/'backups'/('repair-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:10])
        self.baselines = json_file(HERE/'known-baselines.json')
        self.upstream = set(self.baselines['upstreamSoulHashes'])
        self.designer = load('designer-tools')
        self.runtime = load('student-runtime')
        self.security = load('security-tools')
        self.skill_repairs = load('student-skill-repairs')
        # Validate the complete bundle even if optional reporting is disabled.
        for name in ['agents-diagnose.mjs', 'profile-probe.py']:
            plain(HERE/'vendor'/name)
            if not (HERE/'vendor'/name).is_file():
                raise ValueError('Incomplete repair bundle')
        self.yaml = self.designer.yaml

    def text(self, path, code, limit=8*1024*1024):
        if path in self.unreadable:
            return None
        try:
            return read(path, limit).decode('utf-8-sig')
        except UnicodeError:
            debug_exception(self.stage+':text')
            if path not in self.unreadable:
                self.add(code, 'skipped:unreadable-encoding', 'файл не в UTF-8; не изменялся', 'warning')
                self.unreadable.add(path)
            return None

    def installed_json(self, path, code):
        text = self.text(path, code)
        if text is None:
            return None
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError('Invalid installation metadata')
        return value

    def yaml_load(self, data):
        text = data.decode('utf-8-sig') if isinstance(data, bytes) else data
        try:
            return self.yaml.safe_load(text)
        except Exception:
            debug_exception(self.stage+':yaml_load')
            raise ValueError('Invalid YAML preserved') from None

    def validate_config(self, data):
        if data is None:
            return
        if not isinstance(data,dict):
            raise ValueError('Invalid configuration')
        for key in ['stt','kanban','agent','platform_toolsets']:
            if data.get(key) is not None and not isinstance(data[key],dict):
                raise ValueError('Invalid configuration section')
        stt = data.get('stt') or {}
        if stt.get('provider') is not None and not isinstance(stt['provider'],str):
            raise ValueError('Invalid speech provider')
        for section in [stt,stt.get('groq'),stt.get('local')]:
            if section is None:
                continue
            if not isinstance(section,dict) or section.get('language') is not None and not isinstance(section['language'],str):
                raise ValueError('Invalid speech language')

    def add(self, code, status='unchanged', detail='', severity='info'):
        self.findings.append({'code':code, 'severity':severity, 'status':status, 'detail':detail})

    def update(self):
        return 'needs-windows-v3' if self.system=='Windows' else 'needs-platform-update'

    def mutation(self, code, detail, action):
        if self.verify:
            self.add(code, 'unfixable', 'Требуется локальный ремонт: '+detail, 'error')
        elif not self.apply:
            self.add(code, 'planned', detail, 'warning')
        else:
            try:
                action()
            except Exception:
                debug_exception(self.stage+':mutation')
                self.changed = True
                raise
            self.changed = True
            self.add(code, 'fixed', detail, 'info')

    def backup_file(self, path, relative, data=None):
        destination = self.backup/relative
        plain(destination)
        if destination.exists():
            return  # Preserve the first preimage when several SOULs update one inventory.
        atomic(destination, read(path) if data is None else data)

    def config(self, path, name):
        before = read(path)
        # The marked block must be inspected before any serializer drops comments.
        text = self.text(path, 'configuration:'+name)
        if text is None:
            return
        text = text.replace('\r\n', '\n')
        stripped = kanban_text(text)
        marked = stripped != text
        data = self.yaml_load(stripped) or {}
        unmarked = (not marked and self.kanban_signature
                    and (data.get('kanban') or {}).get('dispatch_in_gateway') is False)
        if unmarked:
            # Preserve unrelated bytes when the whole section is exactly the old
            # default. For an expanded/inline block, remove only this YAML key.
            exact = re.compile(r'(?m)^(?:# Infobiz Agents multi-gateway defaults\n)?kanban:[ \t]*\n  dispatch_in_gateway: false[ \t]*(?:\n|$)(?!(?:[ \t]*\n)*[ \t]+\S)')
            stripped, count = exact.subn('kanban: {}\n', text)
            del data['kanban']['dispatch_in_gateway']
            if not count:
                stripped = self.yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        kanban_changed = marked or unmarked
        if not isinstance(data, dict):
            raise ValueError('Invalid configuration')
        crlf = b'\r\n' in before
        self.add('config-format:'+name, detail='crlf: '+str(crlf).lower())
        designer_result = False
        languages = False
        prior = data.get('stt') or {}
        if not isinstance(prior, dict):
            raise ValueError('Invalid speech configuration')
        if name in NAMES:
            for section in [prior, prior.get('groq') or {}, prior.get('local') or {}]:
                if not isinstance(section, dict):
                    raise ValueError('Invalid speech backend')
                value = section.get('language')
                languages |= value is None or (isinstance(value, str) and value.strip().lower() in {'', 'en'})
        # Run the exact release helpers in a disposable, env-free mirror. The
        # release configure() otherwise reads real .env values even with empty ID.
        # Only this changed config is copied back. Never stage auth or .env.
        with tempfile.TemporaryDirectory(prefix='agents-repair-plan-') as directory:
            stage = Path(directory).resolve()/'runtime/profiles'/name/'config.yaml'
            stage.parent.mkdir(parents=True)
            stage.write_bytes(stripped.encode())
            if name=='designer':
                designer_result = self.designer.repair_designer_tools(stage)
            if languages:
                self.runtime.configure(stage.parent.parent, [name], '', '', Path(directory).resolve()/'selections')
            after = stage.read_bytes() if languages or designer_result is True else (stripped.encode() if kanban_changed else before)
            selections = stage.with_name('designer-tools.before.json')
            tools_backup = selections.read_bytes() if selections.is_file() else None
        change = before != after
        if kanban_changed:
            detail = ('запрет без метки снят по подписи установщика (false у всех %d профилей)' % len(self.names)
                      if unmarked else 'Удаляется помеченный блок установщика.')
            self.add('kanban:'+name, 'planned' if not self.apply and not self.verify else 'unchanged', detail)
        else:
            custom = (data.get('kanban') or {}).get('dispatch_in_gateway') is False
            self.add('kanban:'+name, 'skipped:custom-selection' if custom else 'unchanged', 'Явный выбор сохранён (false у %d из %d профилей).' % (self.kanban_false_count, len(self.names)) if custom else 'Запрета установщика нет.')
        if isinstance(designer_result, str):
            self.add('designer-tools', 'skipped:explicitly-disabled', 'Нужные инструменты явно запрещены учеником.')
        elif name=='designer':
            self.add('designer-tools', detail='Инструменты проверены; чужие списки сохранены.')
        if name in NAMES:
            self.add('voice-language:'+name, detail='Явные языки сохраняются; значения en и пустые заменяются на ru.')
        if change:
            def write():
                # Config may contain credentials: store only non-secret selections,
                # never a complete second copy of config.yaml.
                old_languages = {}
                for label, section in [('stt', prior), ('groq', prior.get('groq') or {}), ('local', prior.get('local') or {})]:
                    value = section.get('language')
                    old_languages[label] = value if value is None or isinstance(value,str) and value in {'', 'en', 'ru'} else 'custom-preserved'
                self.backup_file(path, 'selections/'+name+'.json', encoded({'languages':old_languages, 'markedKanban':marked, 'unmarkedKanban':unmarked}))
                if tools_backup:
                    target = path.with_name('designer-tools.before.json')
                    plain(target)
                    if not target.exists():
                        atomic(target, tools_backup)
                    atomic(self.backup/'selections/designer-tools.before.json', tools_backup)
                if read(path)!=before:
                    raise ValueError('Concurrent configuration edit preserved')
                atomic(path, after)
            self.mutation('configuration:'+name, 'Исправить настройки языка, инструментов или запрет установщика.', write)
            # Give every defect its final status rather than only an aggregate.
            status = self.findings[-1]['status']
            for f in self.findings:
                if (f['code']=='kanban:'+name and kanban_changed) or (f['code']=='voice-language:'+name and languages) or (name=='designer' and f['code']=='designer-tools' and designer_result is True):
                    f['status'] = status

    def soul(self, name):
        target = self.profiles/name/'SOUL.md'
        current = read(target) if target.is_file() else b''
        if target.is_file() and self.text(target, 'soul:'+name) is None:
            return
        normalized = normalized_soul(current)
        broken = not normalized or sha(normalized) in self.upstream
        if not broken:
            expected = self.files.get(name+'/SOUL.md')
            self.add('soul:'+name, 'unchanged' if expected==sha(current) else 'skipped:user-text', 'Текст ученика сохранён; стандартный SOUL отсутствует.')
            return
        incoming = None
        if self.system=='Windows':
            source = self.profiles/name/'AGENTS.md'
            if source.is_file():
                if self.text(source, 'instructions:'+name) is None:
                    return
                candidate = read(source)
                if self.files.get(name+'/AGENTS.md')==sha(candidate):
                    incoming = candidate
        else:
            # Old apply retained the PREVIOUS package hash (or no hash) on
            # conflict. Only apply's timestamp/UUID incoming directories qualify.
            sources = sorted((self.control/'backups').glob('*/incoming/'+name+'/SOUL.md'), reverse=True)
            for source in sources:
                if not re.fullmatch(r'\d{8}T\d{6}-[0-9a-f]{32}', source.parents[2].name):
                    continue
                try:
                    plain(source)
                    if not source.is_file():
                        continue
                    if self.text(source, 'soul-source:'+name) is None:
                        continue
                    candidate = read(source)
                    text = normalized_soul(candidate)
                except (OSError, ValueError, UnicodeError):
                    debug_exception(self.stage+':soul')
                    continue
                if text and sha(text) not in self.upstream:
                    incoming = candidate
                    break
        if incoming is None or not normalized_soul(incoming) or sha(normalized_soul(incoming)) in self.upstream:
            self.add('soul:'+name, self.update(), 'Стандартный или пустой SOUL; проверенного локального источника нет.', 'warning')
            return
        state_path = self.control/'tariff-files.json'
        state = self.installed_json(state_path, 'package-inventory')
        if state is None:
            return
        def write():
            self.backup_file(target, 'profiles/'+name+'/SOUL.md', current)
            if target.is_file() and read(target)!=current:
                raise ValueError('Concurrent SOUL edit preserved')
            atomic(target, incoming)
            self.backup_file(state_path, 'tariff-files.json')
            state['files'][name+'/SOUL.md'] = sha(incoming)
            atomic(state_path, encoded(state))
            self.files[name+'/SOUL.md'] = sha(incoming)
        self.mutation('soul:'+name, 'Восстановить SOUL из проверенного локального пакета.', write)

    def skills(self):
        owner_files = 0
        stale_content = 0
        pattern = re.compile(self.baselines['ownerPathsRegex'])
        for name in self.files:
            if not name.lower().endswith('.md'):
                continue
            path = self.profiles/name
            if not path.is_file():
                continue
            profile, leaf = name.split('/', 1)
            code = ('soul:'+profile if leaf=='SOUL.md' else 'instructions:'+profile if leaf=='AGENTS.md' else 'managed-file:'+name)
            text = self.text(path, code)
            if text is None:
                continue
            prose = ''.join(part for code, part in self.skill_repairs.markdown_parts(text) if not code)
            owner_files += bool(pattern.search(prose))
            stale_content += 'tools/swift-design-engine/CONTENT_ENGINE.md' in prose
        missing = 0
        for profile, skill in NEW_SKILLS.items():
            if profile in self.names:
                path = self.profiles/profile/'skills'/skill/'SKILL.md'
                plain(path)
                missing += not path.is_file()
        old = self.state.get('revision')!=self.baselines['revision']
        self.add('package-revision', self.update() if old else 'unchanged', 'Нужна новая ревизия пакета.' if old else 'Ревизия 5 ноября установлена.', 'warning' if old else 'info')
        # Code examples may retain author references in the new release by design.
        self.add('managed-skills', self.update() if old else 'unchanged',
                 'Файлов с личными ссылками: %d; старым путём Content: %d; отсутствующих новых скиллов: %d. Файлы не изменялись.'%(owner_files, stale_content, missing))
        helpers = PACKAGE_HELPERS
        for name in helpers:
            plain(self.control/'post-install'/name)
        absent = sum(not (self.control/'post-install'/name).is_file() for name in helpers)
        self.add('package-helpers', self.update() if absent else 'unchanged', 'Отсутствующих общих помощников: '+str(absent)+'.')

    def logs(self):
        paths = [self.install/'install.log', self.install/'update.log']
        if self.system=='Windows':
            paths += sorted((self.install/'control').glob('*.log'))
        count = 0
        for path in paths:
            plain(path)
            if not path.is_file():
                continue
            before = read(path, 128*1024*1024)
            after = SECRET_URL.sub(b'secret=<redacted>', before)
            if after==before:
                continue
            count += 1
            def write(path=path, before=before, after=after):
                # Never create another plaintext copy of leaked values.
                self.backup_file(path, 'logs/'+('control/' if path.parent.name=='control' else '')+path.name, after)
                mode = stat.S_IMODE(path.stat().st_mode)
                if read(path, 128*1024*1024)!=before:
                    raise ValueError('Concurrent log write preserved')
                atomic(path, after, mode)
            self.mutation('log-redaction:'+('control/' if path.parent.name=='control' else '')+path.name, 'Убрать значения secret из журнала, сохранив остальной текст и права.', write)
        self.add('logs', detail='Очищаемых журналов: '+str(count)+'.')
        self.add('nemo-relay', 'skipped:faq', 'nemo_relay в журнале не требует ремонта; это справочная запись.')
        self.add('legacy-lock-files', 'skipped:faq', 'Старые apply.lock и refresh.lock не удаляются: новый выпуск использует файловую блокировку.')

    def tirith(self):
        marker = self.hermes/'.tirith-install-failed'
        plain(marker)
        if not marker.is_file():
            self.add('tirith', detail='Нет отметки неудачной установки.')
            return
        if self.system=='Windows':
            self.add('tirith', 'skipped:windows-official', 'Официальная раскладка не совпадает с venv; нужен штатный Windows v3.')
            return
        if self.text(marker, 'tirith', 1024) is None:
            return
        # The bundled verifier is trusted only at the owned source location.
        plain(self.hermes/'hermes-agent/tools/tirith_security.py')
        if not self.apply or self.verify:
            self.add('tirith', 'planned' if not self.verify else 'unavailable:retry-required', 'Повторная проверка через установленный Hermes; без обхода проверки подписи.')
            return
        marker_before = read(marker, 1024)
        result = self.security.repair_tirith(self.hermes, lambda message: None)
        marker_changed = not marker.is_file() or read(marker,1024)!=marker_before
        if marker_changed:
            self.backup_file(marker, 'tirith-marker', marker_before)
            self.changed = True
        if result=='tirith:installed':
            self.changed = True
            self.add('tirith', 'fixed', 'Проверенный сканер установлен.')
        else:
            self.add('tirith', 'unavailable:verifier-retry', 'Повтор не завершён; подпись и источник не обходились.')

    def panel(self):
        if self.system=='Windows':
            self.windows()
            return
        values = {}
        for name in ['server.mjs','runner.py']:
            path = self.install/'web-shell'/name
            plain(path)
            values[name] = self.baselines['panel'][name].get(sha(read(path)), 'unknown-baseline') if path.is_file() else 'missing'
        unknown = 'unknown-baseline' in values.values()
        current = all(v=='updated-2026-11-05' for v in values.values())
        self.panel_current = current
        self.add('webshell', 'unchanged' if current else 'needs-platform-update',
                 '; '.join(name+': '+label for name,label in values.items())+
                 ('; documentation: skipped:unknown-baseline; личные правки обновление пропустит.' if unknown else ''))
        if self.verify:
            self.panel_api(current)
            self.full_tools(current)

    def full_tools(self, current):
        if not current or 'designer' not in self.names:
            self.add('designer-full-tools', 'skipped:panel-not-updated', 'Полный режим проверяется после обновления известной панели.')
            return
        text = self.text(self.profiles/'designer/config.yaml', 'configuration:designer')
        if text is None:
            return
        cfg = self.yaml_load(text) or {}
        if any(f['code']=='designer-tools' and f['status']=='skipped:explicitly-disabled' for f in self.findings):
            self.add('designer-full-tools', 'skipped:explicitly-disabled', 'Явные запреты ученика сохранены.')
            return
        source = self.hermes/'hermes-agent'
        try:
            sys.path.insert(0, str(source))
            from hermes_cli.tools_config import _get_platform_tools
            runner = self.text(self.install/'web-shell/runner.py', 'designer-full-tools')
            if runner is None:
                return
            tree = ast.parse(runner)
            forbidden = next(n for n in tree.body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='WEB_FORBIDDEN_TOOLSETS')
            main = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
            body = next(n for n in main.body if isinstance(n,ast.Try) and any(isinstance(c,ast.Assign) and getattr(c.targets[0],'id','')=='cfg' for c in n.body)).body
            start = next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='tool_mode')
            end = next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='agent')
            # Same bounded selection-only AST as native_release_checks.full_tools:
            # no agent construction, prompts, models or runtime file changes.
            class Environment:
                environ = {**os.environ, 'AGENT_WEB_TOOL_MODE':'full'}
                @staticmethod
                def getenv(key, default=None):
                    return Environment.environ.get(key, default)
            Environment.environ.pop('AGENT_WEB_TOOLSETS',None)
            scope = {'os':Environment,'cfg':cfg,'emit':lambda event:None,'_get_platform_tools':_get_platform_tools}
            exec(compile(ast.Module(body=[forbidden]+body[start:end-1],type_ignores=[]),'<verified-runner-tools>','exec'),scope)
            good = {'file','terminal'}<=set(scope['toolsets'])
            self.add('designer-full-tools','unchanged' if good else 'unfixable','Выбор инструментов полного режима проверен настоящим runner.', 'info' if good else 'error')
        except Exception:
            debug_exception(self.stage+':full_tools')
            self.add('designer-full-tools','unavailable:runtime-tool-check','Не удалось проверить выбор инструментов установленным Hermes.', 'error')
        finally:
            if sys.path and sys.path[0]==str(source):sys.path.pop(0)

    def gateway_path(self):
        pieces = [self.hermes/'node/bin',self.hermes/'hermes-agent/venv/bin',self.home/'.local/bin']
        if self.system=='Darwin':
            pieces = [self.install/'shims']+pieces+[self.home/'.cargo/bin']
            fallback = os.pathsep.join(map(str,pieces))+':/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
            # Prefer the existing launchd definition, including learner PATH edits.
            for name in ['ai.hermes.gateway.plist','com.infobiz.agents.web-shell.plist']:
                path = self.home/'Library/LaunchAgents'/name
                plain(path)
                if path.is_file():
                    value = plistlib.loads(read(path))
                    selected = (value.get('EnvironmentVariables') or {}).get('PATH')
                    if isinstance(selected,str) and selected:
                        return selected
            return fallback
        return os.pathsep.join(map(str,pieces))+':/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'

    def panel_api(self, current):
        if not current:
            self.add('panel-api', 'skipped:panel-not-updated', 'Сначала обновить панель.')
            return
        path = self.install/'web-shell.url'
        plain(path)
        if not path.is_file():
            self.add('panel-api', 'skipped:panel-token-unavailable', 'Штатный локальный адрес панели отсутствует.')
            return
        # Explicit exception: the panel credential stays in memory; no auth.json,
        # .env, browser cookies or sessions are read. Never follow redirects/proxy.
        address = self.text(path, 'panel-api', 16384)
        if address is None:
            return
        parsed = urllib.parse.urlsplit(address.strip())
        query = urllib.parse.parse_qs(parsed.query)
        token = query.get('token', [''])[0]
        if parsed.scheme!='http' or parsed.hostname not in {'localhost','127.0.0.1','::1'} or parsed.username or parsed.password or parsed.fragment or parsed.path not in {'','/'}:
            self.add('panel-api', 'skipped:panel-token-unavailable', 'Нет безопасного локального адреса.')
            return
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        good = True
        personal_soul = False
        try:
            for endpoint, body in [('tool-policy',None), ('audit',None), ('agents/designer/preflight', {'message':'открой файл'})]:
                headers = {'Content-Type':'application/json'}
                if token:
                    headers['Cookie'] = 'web_shell_token='+urllib.parse.quote(token, safe='')
                req = urllib.request.Request('http://'+parsed.netloc+'/api/'+endpoint,
                      headers=headers,
                      data=encoded(body) if body else None)
                with opener.open(req, timeout=10) as response:
                    result = json.loads(response.read(1024*1024))
                passed = result.get('ok') is True
                if endpoint=='audit' and not passed:
                    # STUDENT_AUDIT reports the aggregate instructions check and
                    # per-profile instructions flags. Do not exempt other checks,
                    # package-owned SOULs or a subset of personal profiles.
                    failed_checks = [c['id'] for c in result.get('checks', []) if c.get('ok') is not True]
                    agents = result.get('agents', [])
                    affected = [a['id'] for a in agents if a.get('instructions') is False]
                    user_profiles = {f['code'][5:] for f in self.findings if f['code'].startswith('soul:') and f['status']=='skipped:user-text'}
                    personal_soul = (failed_checks == ['instructions'] and bool(affected)
                                     and len(affected)==len(set(affected))
                                     and all(isinstance(a.get('instructions'), bool) for a in agents)
                                     and set(affected) == user_profiles)
                    passed = personal_soul
                good &= passed
            if good and personal_soul:
                self.add('panel-api', 'skipped:user-soul', 'Панель отмечает личный текст SOUL без служебного маркера; агенты работают; при желании поддержка добавит маркер', 'warning')
            else:
                self.add('panel-api', 'unchanged' if good else 'unfixable', 'Проверены инструменты, аудит и открытие файла.', 'info' if good else 'error')
        except urllib.error.HTTPError as error:
            debug_exception(self.stage+':panel_api')
            if not token and error.code in {401, 403}:
                self.add('panel-api', 'skipped:panel-auth-required', 'Локальная панель требует авторизацию.')
            else:
                self.add('panel-api', 'unfixable', 'Локальный API отклонил проверку.', 'error')
        except (urllib.error.URLError, TimeoutError, OSError):
            debug_exception(self.stage+':panel_api')
            self.add('panel-api', 'skipped:panel-unreachable', 'Локальный API недоступен; проверка не подтверждена.')
        except Exception:
            debug_exception(self.stage+':panel_api')
            self.add('panel-api', 'unfixable', 'Локальный API вернул неподтверждённый результат.', 'error')

    def windows(self):
        runtime_path = self.install/'control/webshell-runtime.json'
        plain(runtime_path)
        config = (self.installed_json(runtime_path, 'windows-addon') if runtime_path.is_file() else {})
        if config is None:
            return
        plain(self.install/'control/watchdog.js')
        v3 = (self.install/'control/watchdog.js').is_file() and bool(config.get('recoveryEnabled'))
        version = 3 if v3 else (2 if config.get('schema')==2 or (self.install/'windows-web-shell').is_dir() else 1)
        self.add('windows-addon', 'unchanged' if v3 else 'needs-windows-v3', 'Windows addon v'+str(version)+'. v3 исправляет сохранение настроек и восстановление панели.')
        project = self.home/'Documents/Content'
        active = project/'tools/agents-engine/active.json'
        wrapper = project/'bin/content-engine.py'
        plain(active); plain(wrapper)
        ready = active.is_file() and wrapper.is_file()
        if ready:
            value = self.installed_json(active, 'windows-content')
            if value is None:
                return
            version_name = value.get('version', '')
            if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,180}', version_name) or version_name in {'.','..'}:
                raise ValueError('Invalid Content registration')
            portable = project/'tools/agents-engine/releases'/version_name/'portable'
            # content_ready in the overlay checks these exact runtime entrypoints,
            # dependencies and model resources, beyond a mere active.json file.
            ready = self.content_ready(portable)
        self.content_incomplete = self.tier==3 and not ready
        self.add('windows-content', 'needs-windows-v3' if self.content_incomplete else 'unchanged',
                 'Незавершённый Content: нужен второй блок инструкции (-ResumeContent).' if self.content_incomplete else 'Content не требуется или готов.')
        self.add('windows-panel-task', 'skipped:v3-update' if not v3 else 'unchanged', 'Задачи не изменяются; восстановление устанавливает v3.')
        if self.verify:
            self.panel_api(v3)

    def content_ready(self, portable):
        arm = platform.machine().lower() in {'arm64','aarch64'} or any(os.environ.get(k,'').upper()=='ARM64' for k in ['PROCESSOR_ARCHITEW6432','PROCESSOR_ARCHITECTURE'])
        python = portable/'runtime/Scripts/python.exe'
        ffmpeg = portable/'tools/ffmpeg/bin/ffmpeg.exe'
        cli = portable/'cli.py'
        for path in [python, ffmpeg, cli]:
            plain(path)
            if not path.is_file():
                return False
        env = {k:v for k,v in os.environ.items() if k.upper() not in {'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'}}
        env['CONTENT_BRAND_FILE'] = str(self.home/'Documents/Content/brand/style.json')
        for args in [[str(python),'-I','-B','-c', 'import skia, PIL, numpy' + ('' if arm else '; import sherpa_onnx')], [str(ffmpeg),'-version'], [str(python),'-B',str(cli),'info']]:
            try:
                result = subprocess.run(args, env=env, capture_output=True, timeout=60, **({'creationflags':0x08000000} if os.name=='nt' else {}))
                if result.returncode:
                    return False
            except (OSError, subprocess.TimeoutExpired):
                debug_exception(self.stage+':content_ready')
                return False
        models = CONTENT_MODELS['arm64' if arm else 'x64']
        for name, digest in models.items():
            path = portable/'resources/models'/name
            plain(path)
            if not path.is_file() or file_sha256(path)!=digest:
                return False
        if arm:
            speech = portable/'tools/whisper-arm64/Release/whisper-cli.exe'
            plain(speech)
            return command([str(speech),'--help']).returncode==0
        return True

    def speech(self):
        if self.system=='Windows':
            return
        present = 'HERMES_LOCAL_STT_COMMAND' in env_keys(self.hermes/'.env')
        marker = self.install/'local-stt/installer-owned.json'
        plain(marker)
        # Use the gateway's installed PATH, not Codex's extra developer paths.
        gateway_path = self.gateway_path()
        tools = {}
        for name in ['ffmpeg','ffprobe']:
            binary = shutil.which(name, path=gateway_path)
            tools[name] = bool(binary and command([binary, '-version'], 10).returncode==0)
        providers = []
        for name in self.names:
            text = self.text(self.profiles/name/'config.yaml', 'configuration:'+name)
            if text is None:
                providers.append('unreadable-encoding')
                continue
            cfg = self.yaml_load(text) or {}
            provider = (cfg.get('stt') or {}).get('provider')
            providers.append(provider if provider in {'groq','local','local_command','openai','none',None} else 'custom')
        needed = self.tier==3 and (not present or not marker.is_file() or not all(tools.values()))
        self.add('local-voice', 'needs-platform-update' if needed else 'unchanged',
                 'Команда распознавания присутствует: %s; отметка установки: %s; ffmpeg: %s; ffprobe: %s; выбранные способы: %s.'%(present, marker.is_file(), tools['ffmpeg'],tools['ffprobe'],','.join(str(p) for p in providers)))
        if self.system=='Darwin':
            self.mac_media(tools, gateway_path)
            self.add('mac-homebrew', 'skipped:information' if platform.machine() in {'x86_64','amd64'} and not shutil.which('brew') else 'unchanged', 'Homebrew не устанавливается этим патчем.')

    def mac_media(self, tools, gateway_path):
        if all(tools.values()):
            self.add('mac-ffmpeg', detail='FFmpeg и FFprobe запускаются; загрузка не нужна.')
            return
        arch = platform.machine().lower()
        if arch=='x86_64' and command(['sysctl','-in','sysctl.proc_translated']).stdout.strip()=='1':
            arch = 'arm64'
        pins = self.baselines.get('macMedia', {}).get(arch)
        if not pins:
            self.add('mac-ffmpeg', 'needs-platform-update', 'Для этой архитектуры нет закреплённых сборок.')
            return
        version = command(['sw_vers','-productVersion']).stdout.strip().split('.')[0]
        if not version.isdigit() or int(version)<12:
            self.add('mac-ffmpeg', 'unavailable:unsupported-macos', 'Закреплённые сборки требуют macOS 12 или новее.', 'error')
            return
        if not self.apply or self.verify:
            self.add('mac-ffmpeg', 'planned' if not self.verify else 'unavailable:media-repair-required', 'Установить только сломанные медиапрограммы по закреплённым URL/SHA.', 'warning' if not self.verify else 'error')
            return
        try:
            for name, works in tools.items():
                if works:
                    continue
                target = self.home/'.local/bin'/name
                plain(target)
                previous = read(target, 256*1024*1024) if target.is_file() else None
                # Only HTTPS, pinned source, no auth, no externally supplied URLs.
                req = urllib.request.Request(pins[name]['url'])
                with urllib.request.urlopen(req, timeout=30) as response:
                    if urllib.parse.urlsplit(response.url).scheme!='https':
                        raise ValueError('Unsafe media redirect')
                    data = response.read(128*1024*1024+1)
                if len(data)>128*1024*1024 or sha(data)!=pins[name]['sha256']:
                    self.add('mac-ffmpeg', 'unavailable:checksum-failed', 'Загрузка не прошла проверку SHA; прежняя программа сохранена.', 'error')
                    return
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if archive.namelist()!=[name] or archive.getinfo(name).file_size>256*1024*1024:
                        raise ValueError('Unexpected media archive')
                    incoming = archive.read(name)
                # Test executable before replacing an existing broken program.
                with tempfile.TemporaryDirectory(prefix='agents-media-') as directory:
                    candidate = Path(directory).resolve()/name
                    candidate.write_bytes(incoming)
                    candidate.chmod(0o755)
                    if command([str(candidate),'-version'],10).returncode:
                        raise ValueError('Media architecture check failed')
                if previous is not None:
                    self.backup_file(target, 'media/'+name, previous)
                if target.is_file() and read(target,256*1024*1024)!=previous:
                    raise ValueError('Concurrent media edit')
                atomic(target, incoming, 0o755)
                self.changed = True
                if command([str(target),'-version'],10).returncode:
                    raise ValueError('Installed media check failed')
            self.add('mac-ffmpeg', 'fixed', 'Закреплённые медиапрограммы установлены и запущены.')
        except (OSError, TimeoutError, urllib.error.URLError):
            debug_exception(self.stage+':mac_media')
            self.add('mac-ffmpeg', 'unavailable:download-unavailable', 'Загрузка недоступна; штатное обновление можно повторить позже.', 'error')
        except Exception:
            debug_exception(self.stage+':mac_media')
            self.add('mac-ffmpeg', 'unavailable:media-check-failed', 'Проверка медиапрограммы не завершена.', 'error')

    def system_properties(self):
        result = command(['systemctl','show','infobiz-hermes-gateway.service','--property=LoadState,ActiveState,MainPID,NRestarts,FragmentPath,User,ExecStart,DropInPaths'])
        return dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line) if result.returncode==0 else {}

    def linux_unit_verified(self, props):
        import pwd
        binary = str(self.hermes/'hermes-agent/venv/bin/hermes')
        # systemctl show's parsed command, not bytes of a newer unit template.
        executable = re.match(r'^\{ path=(.*?) ; argv\[\]=(.*?) ;', props.get('ExecStart',''))
        return (props.get('FragmentPath')=='/etc/systemd/system/infobiz-hermes-gateway.service'
                and props.get('User')==pwd.getpwuid(os.getuid()).pw_name
                and props.get('DropInPaths')=='' and executable is not None
                and executable[1]==binary and executable[2]==binary+' gateway run')

    def linux_gateway_processes(self, identity):
        result = command(['ps','-eo','pid=,ppid=,args='])
        if result.returncode:
            raise ValueError('Process check unavailable')
        pids = []
        venv = str(self.hermes/'hermes-agent/venv/bin')+'/'
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields)!=3 or not fields[0].isdigit() or not fields[1].isdigit():
                continue
            try:
                args = shlex.split(fields[2])
            except ValueError:
                debug_exception(self.stage+':linux_gateway_processes')
                continue
            # stderr_timestamp includes its child's entire command in argv;
            # it is a wrapper, not a second gateway. Never signal that wrapper.
            if 'hermes_cli.stderr_timestamp' in args or not any(args[i:i+2]==['gateway','run'] for i in range(len(args)-1)):
                continue
            pid = int(fields[0])
            scoped = (identity.get('hermes_home')==str(self.hermes) and identity.get('pid')==pid
                      or any(a.startswith(venv) and '/' not in a[len(venv):] for a in args)
                      or 'HERMES_HOME='+str(self.hermes) in args)
            if scoped:
                pids.append(pid)
        return sorted(set(pids))

    def linux_identity_valid(self, identity):
        return (isinstance(identity,dict) and type(identity.get('pid')) is int and identity['pid']>0
                and identity.get('hermes_home')==str(self.hermes))

    def linux_owned(self, props, identity):
        return (self.linux_identity_valid(identity) and identity['pid']==int(props.get('MainPID') or '0')
                and identity.get('supervisor')!='manual' and props.get('ActiveState')=='active')

    def linux_stable(self, identify, seconds):
        try:
            before = self.system_properties()
            deadline = time.monotonic()+seconds
            while True:
                props = self.system_properties()
                identity = identify(self.hermes) or {}
                if (not self.linux_owned(props,identity) or self.linux_gateway_processes(identity)!=[identity['pid']]
                        or not before.get('NRestarts','').isdigit() or props.get('NRestarts')!=before['NRestarts']
                        or props.get('MainPID')!=before.get('MainPID')):
                    return False
                remaining = deadline-time.monotonic()
                if remaining<=0:
                    return True
                time.sleep(min(1,remaining))
        except Exception:
            debug_exception(self.stage+':linux_stable')
            return False

    def linux_gateway_owner(self, user_repaired=False):
        code = 'linux-gateway-owner'
        source = str(self.hermes/'hermes-agent')
        sys.path.insert(0,source)
        try:
            from gateway.control_socket import identify_gateway
            props = self.system_properties()
            if not self.linux_unit_verified(props):
                self.add(code,'unavailable:gateway-unit-unverified','Параметры системной службы изменились; передача отменена.','error')
                return
            identity = identify_gateway(self.hermes) or {}
            pids = self.linux_gateway_processes(identity)
            if not self.linux_identity_valid(identity):
                # A stopped installation with no live gateway can be started;
                # an occupied host without a verified identity cannot be adopted.
                if identity or pids or props.get('ActiveState')=='active':
                    raise ValueError('Identity unavailable')
                handoff = False
            else:
                if identity['pid'] not in pids:
                    raise ValueError('Identity process unavailable')
                handoff = (identity.get('supervisor')=='manual'
                           or str(identity['pid'])!=props.get('MainPID') or len(pids)>1)
            inactive = props.get('ActiveState')!='active'
            if handoff:
                self.add('linux-system-gateway', detail='Запуск службы входит в передачу владения.' if inactive else 'Системная служба активна.')
                if self.verify or not self.apply:
                    self.mutation(code, 'Передать gateway проверенной системной службе.', lambda: None)
                    return
                # Resolve the installed helper before freezing Restart=always.
                from hermes_cli.gateway import _graceful_restart_via_sigusr1
                self.changed = True
                try:
                    if command(['systemctl','stop','infobiz-hermes-gateway.service'],150).returncode:
                        raise ValueError('Stop failed')
                    identity = identify_gateway(self.hermes) or {}
                    remaining = self.linux_gateway_processes(identity)
                    if identity:
                        if (not self.linux_identity_valid(identity) or identity.get('supervisor')!='manual'
                                or identity['pid'] not in remaining):
                            raise ValueError('Owner changed')
                        # No signal escalation or retry: Hermes drains and exits.
                        if not _graceful_restart_via_sigusr1(identity['pid'],120):
                            raise ValueError('Drain failed')
                    elif remaining:
                        raise ValueError('Unknown owner')
                    if command(['systemctl','reset-failed','infobiz-hermes-gateway.service']).returncode or command(['systemctl','start','infobiz-hermes-gateway.service']).returncode:
                        raise ValueError('Start failed')
                    self.linux_wait_for_owner(identify_gateway)
                except Exception:
                    debug_exception(self.stage+':linux_gateway_owner')
                    self.add(code,'unavailable:gateway-handoff-failed','Передача владения не завершена; автоматических повторов нет.','error')
                    return
                if not self.linux_stable(identify_gateway,30):
                    self.add(code,'unavailable:gateway-unstable','Gateway нестабилен после передачи; автоматических повторов нет.','error')
                    return
                self.add(code,'fixed','Gateway передан системной службе; один процесс и NRestarts стабильны 30 с.')
            else:
                if inactive:
                    def start():
                        if command(['systemctl','reset-failed','infobiz-hermes-gateway.service']).returncode or command(['systemctl','start','infobiz-hermes-gateway.service']).returncode:
                            raise ValueError('Unable to start owned system unit')
                        self.linux_wait_for_owner(identify_gateway)
                    self.mutation('linux-system-gateway','Сбросить failed и запустить системную службу.',start)
                else:
                    self.add('linux-system-gateway', detail='Системная служба активна.')
                if self.apply and not self.verify and (inactive or user_repaired) and not self.linux_stable(identify_gateway,30):
                    self.add(code,'unavailable:gateway-unstable','Gateway нестабилен после ремонта службы.','error')
                    return
                self.add(code,detail='Gateway принадлежит системной службе.' if not inactive else 'Владение проверяется при запуске службы.')
        except Exception:
            debug_exception(self.stage+':linux_gateway_owner')
            self.add(code,'unavailable:identity-check','Не удалось подтвердить владельца через установленный Hermes.','error')
        finally:
            sys.path.pop(0)

    def linux_wait_for_owner(self, identify):
        deadline = time.monotonic()+120
        while time.monotonic()<deadline:
            if self.linux_owned(self.system_properties(), identify(self.hermes) or {}):
                return
            time.sleep(1)
        raise ValueError('Gateway handoff timed out')

    def linux(self):
        props = self.system_properties()
        if props.get('LoadState')!='loaded':
            self.add('linux-gateway', 'unavailable:system-unit-missing', 'Управляемая системная служба не найдена.', 'error')
            return
        if not self.linux_unit_verified(props):
            self.add('linux-gateway-owner','unavailable:gateway-unit-unverified','Параметры системной службы не подтверждены; служба не изменялась.','error')
            return
        user_repaired = False
        xdg = Path(os.environ.get('XDG_CONFIG_HOME', str(self.home/'.config')))
        # XDG belonging to another user must never redirect this repair.
        if self.home not in xdg.absolute().parents:
            raise ValueError('Foreign systemd configuration')
        unit = xdg/'systemd/user/hermes-gateway.service'
        masked = unit.is_symlink() and os.readlink(unit)=='/dev/null'
        plain(unit.parent)
        if not masked:
            plain(unit)
            existed = unit.is_file()
            def fix():
                result = command(['systemctl','--user','disable','--now','hermes-gateway.service'])
                output = result.stdout+result.stderr
                bus = not any(s in output for s in ['Failed to connect','No medium found','not defined','Failed to get D-Bus','Connection refused'])
                absent = any(s in output for s in ['does not exist','not loaded','No such file','not found'])
                if result.returncode and bus and not absent:
                    raise ValueError('Unable to disable owned user unit')
                if unit.is_file():
                    self.backup_file(unit, 'systemd/hermes-gateway.service')
                    fd, temporary_backup = tempfile.mkstemp(prefix=unit.name+'.infobiz-disabled.',dir=str(unit.parent))
                    os.close(fd)
                    os.replace(unit, Path(temporary_backup))
                if bus:
                    for args in [['systemctl','--user','--no-reload','mask','hermes-gateway.service'],['systemctl','--user','daemon-reload']]:
                        if command(args).returncode:
                            raise ValueError('Unable to mask owned user unit')
                else:
                    plain(unit)
                    unit.parent.mkdir(parents=True, exist_ok=True)
                    if os.path.lexists(unit):
                        raise ValueError('Concurrent unit edit preserved')
                    temporary = unit.with_name('.repair-mask-'+uuid.uuid4().hex)
                    os.symlink('/dev/null', temporary)
                    os.replace(temporary, unit)
                if not (unit.is_symlink() and os.readlink(unit)=='/dev/null'):
                    raise ValueError('User mask verification failed')
                if command(['systemctl','reset-failed','infobiz-hermes-gateway.service']).returncode:
                    raise ValueError('Unable to reset owned system unit')
            self.mutation('linux-user-gateway', 'Отключить и замаскировать вторую службу; исходный unit сохранить.' if existed else 'Маска создана заранее, служба не существовала.', fix)
            user_repaired = self.apply and not self.verify
        else:
            self.add('linux-user-gateway', detail='User-служба уже замаскирована.')
        self.linux_gateway_owner(user_repaired)
        if self.verify:
            self.linux_verify()

    def linux_verify(self):
        try:
            # Installed runtime only; identify_gateway talks to the gateway's
            # local control socket, never reads authorization or conversations.
            source = self.hermes/'hermes-agent'
            sys.path.insert(0, str(source))
            from gateway.control_socket import identify_gateway
            if not self.linux_identity_valid(identify_gateway(self.hermes)):
                raise ValueError('Identity unavailable')
            good = self.linux_stable(identify_gateway,self.wait)
            self.add('linux-single-gateway', 'unchanged' if good else 'unfixable', 'Один gateway, системный MainPID и число рестартов проверены; ожидание '+str(self.wait)+' с.', 'info' if good else 'error')
        except Exception:
            debug_exception(self.stage+':linux_verify')
            self.add('linux-single-gateway', 'unavailable:identity-check', 'Не удалось подтвердить PID через установленный Hermes.', 'error')
        finally:
            if sys.path and sys.path[0]==str(self.hermes/'hermes-agent'):
                sys.path.pop(0)

    def run(self):
        token = PATH_ROOTS.set((HERE, self.install, self.hermes))
        try:
            return self._run()
        except Exception:
            debug_exception(self.stage)
            raise
        finally:
            PATH_ROOTS.reset(token)

    def _run(self):
        self.stage = 'installation-paths'
        if self.system not in {'Darwin','Linux','Windows'}:
            raise ValueError('Unsupported operating system')
        for path in [self.home, self.install, self.hermes, self.profiles, self.control]:
            plain(path)
        self.stage = 'package-inventory'
        self.state = self.installed_json(self.control/'tariff-files.json', 'package-inventory')
        if self.state is None:
            self.logs()
            self.tirith()
            return self.result()
        self.files = self.state.get('files')
        if not isinstance(self.files, dict) or not self.files or len(self.files)>5000 or any(not managed_name(n) or not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) for n,h in self.files.items()):
            raise ValueError('Invalid managed inventory')
        for name in self.files:
            plain(self.profiles/name)
        self.names = sorted({name.split('/')[0] for name in self.files})
        self.stage = 'purchased-composition'
        receipt = self.control/'last-apply.json'
        self.tier = (self.installed_json(receipt, 'package-receipt') or {}).get('tier') if receipt.is_file() else None
        if self.tier not in {1,2,3}:
            # Windows overlay stores its tier in the Windows receipt instead.
            windows_receipt = self.control/'windows-install-receipt.json'
            self.tier = (self.installed_json(windows_receipt, 'windows-receipt') or {}).get('tier') if windows_receipt.is_file() else None
        if self.tier not in {1,2,3} and receipt not in self.unreadable and self.control/'windows-install-receipt.json' not in self.unreadable:
            raise ValueError('Missing purchased composition')
        configs = [(self.hermes/'config.yaml', 'default')] + [(self.profiles/name/'config.yaml',name) for name in self.names]
        # Snapshot every purchased profile before helpers or serializers mutate it.
        kanban_values = {}
        for path,name in configs:
            self.stage = 'configuration-preflight:'+name
            plain(path)
            if path.is_file():
                text = self.text(path, 'configuration:'+name)
                if text is not None:
                    try:
                        data = self.yaml_load(text)
                        self.validate_config(data)
                        kanban_values[name] = ((data or {}).get('kanban') or {}).get('dispatch_in_gateway')
                    except ValueError:
                        debug_exception(self.stage)
                        self.unreadable.add(path)
                        self.add('configuration:'+name, 'skipped:unreadable-config', 'файл не разобран; не изменялся', 'warning')
            elif name!='default':
                raise ValueError('Missing profile configuration')
        self.kanban_false_count = sum(kanban_values.get(name) is False for name in self.names)
        self.kanban_signature = bool(self.names) and self.kanban_false_count == len(self.names)
        for path,name in configs:
            self.stage = 'configuration:'+name
            if path.is_file() and path not in self.unreadable:
                self.config(path, name)
        for name in self.names:
            self.stage = 'soul:'+name
            self.soul(name)
        for stage in ['skills', 'logs', 'tirith', 'panel', 'speech']:
            self.stage = stage
            getattr(self, stage)()
        if self.system=='Linux':
            self.stage = 'linux'
            self.linux()
        self.stage = 'result'
        result = self.result()
        if self.apply and self.changed:
            atomic(self.control/'last-repair.json', encoded(result))
        return result

    def result(self):
        statuses = [f['status'] for f in self.findings]
        failed = any(s=='unfixable' or (s.startswith('unavailable:') and f['severity']=='error') for s,f in zip(statuses,self.findings))
        if failed:
            next_step, code = 'support', 2
        elif 'needs-windows-v3' in statuses:
            next_step, code = ('run-windows-v3-resume-content' if getattr(self,'content_incomplete',False) else 'run-windows-v3'), 3
        elif 'needs-platform-update' in statuses:
            next_step, code = 'run-platform-update', 3
        else:
            next_step, code = 'none', 0
        return {'schemaVersion':1, 'changed':self.changed, 'mode':'verify' if self.verify else 'apply' if self.apply else 'diagnose',
                'target':{'Darwin':'mac','Linux':'vps','Windows':'windows'}.get(self.system,'unsupported'),
                'findings':self.findings, 'nextStep':next_step, 'exitCode':code}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Диагностика и безопасный ремонт установленных агентов')
    parser.add_argument('command', choices=['diagnose','repair','verify'])
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--install-root', type=Path)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--no-report', action='store_true', help='Не отправлять inventory на платформу; локальный JSON сохраняется')
    parser.add_argument('--wait', type=int, default=0)
    args = parser.parse_args(argv)
    if args.wait<0 or args.wait>3600 or (args.apply and args.command!='repair'):
        parser.error('--apply допустим только для repair; --wait от 0 до 3600')
    try:
        repair = Repair(args.home, args.install_root, apply=args.apply, verify=args.command=='verify', wait=args.wait)
        result = repair.run()
    except (ValueError, OSError, UnicodeError) as error:
        debug_exception(getattr(locals().get('repair'), 'stage', 'initialization'))
        result = {'schemaVersion':1,'changed':locals().get('repair') is not None and repair.changed,'findings':[{'code':'installation','severity':'error','status':'unfixable','detail':'Установка отсутствует, повреждена или путь небезопасен. Тип: '+type(error).__name__}], 'nextStep':'support','exitCode':2}
    except Exception as error:
        debug_exception(getattr(locals().get('repair'), 'stage', 'initialization'))
        result = {'schemaVersion':1,'changed':locals().get('repair') is not None and repair.changed,'findings':[{'code':'internal','severity':'error','status':'unfixable','detail':'Проверка не завершена. Тип: '+type(error).__name__}], 'nextStep':'support','exitCode':1}
    destination = args.json or args.home/'Downloads'/('Ремонт-агентов-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.json')
    try:
        destination = destination.absolute()
        # --json is a report destination, never permission to replace runtime
        # configuration, credentials or an installation's managed metadata.
        roots = [args.home/'.hermes', args.install_root or args.home/'InfobizAgents']
        if os.environ.get('AGENTS_REPAIR_HERMES_HOME'):
            roots.append(Path(os.environ['AGENTS_REPAIR_HERMES_HOME']))
        if destination.suffix.lower()!='.json' or any(p.lower() in PROTECTED or p.lower().startswith('.env') for p in destination.parts) or any(root.absolute()==destination or root.absolute() in destination.parents for root in roots):
            raise ValueError('Unsafe report destination')
        atomic(destination, encoded(result))
        saved = True
    except Exception:
        debug_exception('save-report')
        saved = False
        result['exitCode'] = 2
        result['nextStep'] = 'support'
    print(json.dumps(result, ensure_ascii=False))
    summary = {'none':'Проверенные настройки исправны.', 'run-platform-update':'Нужно штатное обновление: новый код и та же команда из кабинета.', 'run-windows-v3':'Нужен Windows v3 по новой инструкции из кабинета.', 'run-windows-v3-resume-content':'Нужен Windows v3; для Content выполните второй блок инструкции (-ResumeContent).', 'support':'Проверка не завершена; нужна помощь поддержки.'}[result['nextStep']]
    if result['nextStep']=='none' and any(f['status']=='planned' for f in result['findings']):
        summary = 'План ремонта составлен; изменения не применялись.'
    print(summary)
    print('JSON: '+str(destination) if saved else 'Не удалось сохранить JSON в Загрузки.')
    return result['exitCode']


if __name__=='__main__':
    sys.exit(main())
