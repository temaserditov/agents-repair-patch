"""Installer-owned speech provisioning and account-bound Telegram configuration."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid


def plain(path):
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise ValueError('Linked runtime destination preserved')
        try:
            if getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked runtime destination preserved')
        except FileNotFoundError:
            pass


def atomic(path, data):
    plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with temporary.open('xb') as out:
            os.chmod(temporary, 0o600)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def env_set(text, key, value):
    # dotenv is not a shell: shlex.quote emits compound shell strings when a
    # command contains quotes, breaking paths with spaces in profile .env.
    value = json.dumps(value, ensure_ascii=False)
    pattern = re.compile(r'^[ \t]*(?:export\s+)?'+re.escape(key)+r'[ \t]*=.*$', re.M)
    line = key+'='+value
    return pattern.sub(lambda _: line, text) if pattern.search(text) else text+('' if not text or text.endswith('\n') else '\n')+line+'\n'


def env_values(text):
    try:
        from dotenv import dotenv_values
        return dotenv_values(stream=io.StringIO(text), interpolate=False)
    except ImportError:
        # Installed Hermes venvs ship python-dotenv; plain interpreters (CI, tests) use the same subset parser.
        values = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, value = line.split('=', 1)
            key = key.strip(); value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                inner = value[1:-1]
                if value[0] == '"':
                    try:
                        inner = json.loads(value)
                    except ValueError:
                        pass
                value = inner
            values[key] = value
        return values


def russian_language(section):
    value = section.get('language')
    if value is None or (isinstance(value, str) and value.strip().lower() in {'', 'en'}):
        section['language'] = 'ru'


def allowed_users(value):
    # Hermes accepts comma-separated IDs and usernames. Preserve both, in order.
    return list(dict.fromkeys(user for user in re.split(r'[,\s]+', value or '') if user))


def configure(profiles, names, owner_id, command, backup):
    try:
        import yaml
    except ImportError:
        # Official Windows Hermes (Python 3.14) ships hermes_yaml instead of PyYAML.
        import hermes_yaml as yaml
    if owner_id and not re.fullmatch(r'[1-9][0-9]{0,15}', owner_id):
        raise ValueError('Invalid account-bound Telegram ID')
    if not set(names) <= {'marketer','copywriter','designer','editor','tech','assistant','sales'}:
        raise ValueError('Unknown profile')
    root_env = profiles.parent/'.env'
    plain(root_env)
    root_text = root_env.read_text() if root_env.is_file() else ''
    default_users = allowed_users(env_values(root_text).get('TELEGRAM_ALLOWED_USERS'))
    if command:
        # Multiplex turns never publish profile .env into os.environ. This
        # non-secret command must also be loaded by the gateway's launch home.
        root_after = env_set(env_set(root_text, 'HERMES_LOCAL_STT_COMMAND', command),
                             'HERMES_LOCAL_STT_LANGUAGE', 'ru')
        if root_after != root_text or root_env.stat().st_mode & 0o777 != 0o600:
            atomic(root_env, root_after.encode())
    results = {}
    for name in names:
        if name not in {'marketer','copywriter','designer','editor','tech','assistant','sales'}:
            raise ValueError('Unknown profile')
        directory = profiles/name
        env, config = directory/'.env', directory/'config.yaml'
        plain(env); plain(config)
        before_env = env.read_bytes() if env.is_file() else b''
        before_config = config.read_bytes() if config.is_file() else b'{}\n'
        data = yaml.safe_load(before_config) or {}
        if not isinstance(data, dict):
            raise ValueError('Invalid profile config preserved')
        text = before_env.decode('utf-8')
        values = env_values(text)
        # Only the ID read from this installation owner's platform account.
        # Never derive it from a sample, default profile or another bot.
        if owner_id:
            users = [user for user in allowed_users(values.get('TELEGRAM_ALLOWED_USERS')) if user]
            if len(default_users) == 1 and re.fullmatch(r'[1-9][0-9]*', default_users[0]) and users == default_users:
                users = []
            text = env_set(text, 'TELEGRAM_ALLOWED_USERS', ','.join(dict.fromkeys([*users, owner_id])))
            text = env_set(text, 'GATEWAY_ALLOW_ALL_USERS', 'false')
            text = env_set(text, 'TELEGRAM_ALLOW_ALL_USERS', 'false')
        if data.get('stt') is None:
            data['stt'] = {}
        stt = data['stt']
        if not isinstance(stt, dict):
            raise ValueError('Invalid speech config preserved')
        russian_language(stt)
        for backend in ('groq', 'local'):
            if stt.get(backend) is None:
                stt[backend] = {}
            section = stt[backend]
            if not isinstance(section, dict):
                raise ValueError('Invalid speech backend config preserved')
            russian_language(section)
        if command:
            text = env_set(text, 'HERMES_LOCAL_STT_COMMAND', command)
            text = env_set(text, 'HERMES_LOCAL_STT_LANGUAGE', 'ru')
            provider = stt.get('provider')
            if not provider or (provider == 'local' and not stt['local'].get('model')) or (provider == 'groq' and not (values.get('GROQ_API_KEY') or '').strip()):
                stt.update({'enabled': True, 'provider': 'local_command'})
            if stt.get('provider') == 'local_command':
                text = env_set(text, 'INFOBIZ_VOICE_ENGINE', 'local')
        after_env = text.encode()
        after_config = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode()
        # Do not duplicate .env or config: either may contain model/bot keys.
        # Recovery metadata records only the changed, non-secret selections.
        saved = backup/name/'selections.json'
        if (before_env != after_env or before_config != after_config) and not saved.exists():
            old_stt = (yaml.safe_load(before_config) or {}).get('stt') or {}
            fields = {}
            for key in ['TELEGRAM_ALLOWED_USERS', 'GATEWAY_ALLOW_ALL_USERS', 'TELEGRAM_ALLOW_ALL_USERS']:
                match = re.search(r'^(?:export\s+)?'+key+r'=(.*)$', before_env.decode(), re.M)
                fields[key] = match[1] if match else None
            atomic(saved, (json.dumps({'envSelections': fields,
                'speech': {key: old_stt.get(key) for key in ['enabled','provider']},
                'languages': {key: (old_stt if key == 'stt' else old_stt.get(key) or {}).get('language') for key in ['stt','groq','local']},
                'priorLocalCommandPresent': bool(re.search(r'^HERMES_LOCAL_STT_COMMAND=', before_env.decode(), re.M))})+'\n').encode())
        for target, before, after in [(env, before_env, after_env), (config, before_config, after_config)]:
            if before == after:
                continue
            atomic(target, after)
        results[name] = {'telegram': 'bound' if owner_id else 'unverified',
                         'speech': ('local' if stt.get('provider') == 'local_command' else stt.get('provider', 'unchanged')) if command else 'unchanged'}
    return results


def provision(install, package):
    if not shutil.which('ffmpeg'):
        error = FileNotFoundError('FFmpeg is unavailable in gateway PATH')
        error.diagnostic_reason = 'ffmpeg-missing'
        raise error
    root = install/'local-stt'
    plain(root)
    marker = root/'installer-owned.json'
    owned = b'{"schema":1,"backend":"sherpa-onnx","version":"1.13.8"}\n'
    if root.exists() and (not marker.is_file() or marker.read_bytes() != owned):
        raise ValueError('Existing unknown speech runtime preserved')
    if not root.exists():
        root.mkdir(parents=True)
        atomic(marker, owned)
    python = root/'runtime/bin/python'
    if not python.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(root/'runtime')], check=True)
    requirements = root/'requirements-whisper.txt'
    expected = b'sherpa-onnx==1.13.8\n'
    if requirements.exists() and requirements.read_bytes() != expected:
        raise ValueError('Changed speech requirements preserved')
    if not requirements.exists():
        atomic(requirements, expected)
    spec = importlib.util.spec_from_file_location('student_whisper_model', package/'setup-linux-whisper.py')
    model = importlib.util.module_from_spec(spec); spec.loader.exec_module(model)
    model.install(root, python)
    adapter = package/'local-stt.py'
    plain(adapter)
    if not adapter.is_file():
        raise ValueError('Missing local speech adapter')
    prefix = shlex.join([str(python), '-B', str(adapter), '--models',
                         str(root/'resources/models'/model.MODEL)])
    return prefix.replace('{', '{{').replace('}', '}}')+' --input {input_path} --output {output_dir} --language {language}'


def repair(install, package):
    receipt = json.loads((install/'control/tariffs/last-apply.json').read_text())
    profiles = install.parent/'.hermes/profiles'
    backup = install/'control/tariffs/runtime-setting-preimages'
    owner = os.environ.get('AGENTS_OWNER_TELEGRAM_ID', '')
    # Language/identity repairs do not depend on PyPI or model availability.
    configure(profiles, receipt['profiles'], owner, '', backup)
    command = provision(install, package)
    return configure(profiles, receipt['profiles'], owner, command, backup)
