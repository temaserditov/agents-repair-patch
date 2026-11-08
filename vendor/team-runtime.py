"""Enable the reviewed team plugin; preserve explicit disables and local code."""
import codecs
import concurrent.futures
import hashlib
import json
from pathlib import Path
import os
import re
import time
import uuid
import subprocess
import sys
import importlib.util


def plain(path):
    for part in [path, *path.parents]:
        if part.is_symlink() or (part.exists() and getattr(part.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Linked team destination preserved')


def atomic(path, data):
    plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as output:
            os.chmod(temporary, 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render(yaml, config, before):
    """Block-style YAML (the panel reads ``disabled_toolsets`` as a block list);
    keep the file's BOM and CRLF so other readers see the same format as before."""
    text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, default_flow_style=False)
    if b'\r\n' in before and '\r\n' not in text:
        text = text.replace('\n', '\r\n')
    data = text.encode('utf-8')
    if before.startswith(codecs.BOM_UTF8):
        data = codecs.BOM_UTF8 + data
    return data


def configure(package, profiles, names, control):
    try:
        import yaml
    except ImportError:
        import hermes_yaml as yaml
    package, profiles, control = map(Path, (package, profiles, control))
    source = package/'plugins/infobiz-team'
    files = {name: (source/name).read_bytes() for name in ['plugin.yaml', '__init__.py']}
    status = {}
    for name in names:
        directory = profiles/name
        config_path = directory/'config.yaml'
        plain(config_path)
        if not config_path.is_file():
            status[name] = 'pending:profile-config'
            continue
        before = config_path.read_bytes()
        config = yaml.safe_load(before.decode('utf-8-sig')) or {}
        plugins = config.setdefault('plugins', {})
        if not isinstance(plugins, dict):
            raise ValueError('Invalid plugins config')
        if 'infobiz-team' in (plugins.get('disabled') or []) or 'kanban' in (config.get('agent', {}).get('disabled_toolsets') or []):
            status[name] = 'skipped:explicitly-disabled'
            continue
        destination = directory/'plugins/infobiz-team'
        receipt = control/('team-plugin-'+name+'.json')
        plain(destination); plain(receipt)
        previous = json.loads(receipt.read_text()) if receipt.is_file() else {}
        unknown = False
        if destination.exists():
            for path in destination.rglob('*'):
                plain(path)
                if not path.is_file():
                    continue
                rel = path.relative_to(destination).as_posix()
                # Python's own bytecode is not source or an ownership marker.
                if rel.startswith('__pycache__/'):
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if rel not in files or (path.read_bytes() != files[rel] and previous.get(rel) != digest):
                    unknown = True
        if unknown:
            status[name] = 'warning:custom-plugin-preserved; move plugins/infobiz-team to a backup outside plugins, then run the platform update'
            continue
        for rel, data in files.items():
            if not (destination/rel).is_file() or (destination/rel).read_bytes() != data:
                atomic(destination/rel, data)
        enabled = plugins.setdefault('enabled', [])
        if not isinstance(enabled, list):
            raise ValueError('Invalid enabled plugins')
        if 'infobiz-team' not in enabled:
            enabled.append('infobiz-team')
        # Kanban is default-off. Hermes (0.21.5 tarball and 0.23 alike,
        # tools_config._get_platform_tools) enables it for every platform that
        # has NO saved list when the profile-wide ``toolsets`` names it, so a
        # profile that inherits the Hermes bundles (the Designer after
        # designer-tools removed cli/telegram) gets kanban from this opt-in and
        # keeps inheriting new bundled tools. A saved platform list is
        # authoritative and ignores the opt-in, so an EXISTING explicit
        # telegram selection gains kanban; none is ever created. The team
        # tools are Telegram tools: a saved cli list is the learner's and is
        # never touched (the panel's full mode is the cli selection).
        tools = config.setdefault('toolsets', [])
        if not isinstance(tools, list):
            raise ValueError('Invalid profile toolsets')
        if 'kanban' not in tools:
            tools.append('kanban')
        platforms = config.get('platform_toolsets')
        if platforms is not None and not isinstance(platforms, dict):
            raise ValueError('Invalid platform toolsets')
        selected = (platforms or {}).get('telegram')
        if selected is not None:
            if not isinstance(selected, list):
                raise ValueError('Invalid platform toolset selection')
            if 'kanban' not in selected:
                selected.append('kanban')
        if config != (yaml.safe_load(before.decode('utf-8-sig')) or {}):
            # Stable preimage for the first team registration, no credentials.
            backup = control/'team-config-preimages'/(name+'.yaml')
            if not backup.exists():
                atomic(backup, before)
            atomic(config_path, render(yaml, config, before))
        atomic(receipt, (json.dumps({rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}, sort_keys=True)+'\n').encode())
        status[name] = 'enabled'
    return status


TEAM_TOOLS = {'team_project_create', 'team_workflow_create', 'team_project_status'}
STATUSES = {'ready', 'missing-tools', 'pending-gateway-reload', 'missing-gateway-plugin'}
# A (re)started multiplexer answers identify before it has reconciled the
# profiles it serves; within this window "home is not served" is transient.
SETTLE_SECONDS = 60.0
RELOAD_RETRY_SECONDS = 2.5
PROBE_TIMEOUT = 150
# A control-socket query that nobody answers must stay far inside the child's
# budget: two queries per profile, never the 30 s Hermes default each.
RELOAD_TIMEOUT = 5.0


def unconfigured_reasons(package, profiles, name):
    """Why the profile is not team-enabled (empty when it is). Read config/code
    only; diagnose and verify must never enable anything. The names are finite
    and free of config values so a report may repeat them."""
    try:
        import yaml
    except ImportError:
        import hermes_yaml as yaml
    directory = Path(profiles)/name
    config_path = directory/'config.yaml'
    plain(config_path)
    if not config_path.is_file():
        return ['config-missing']
    config = yaml.safe_load(config_path.read_text(encoding='utf-8-sig')) or {}
    plugins = config.get('plugins') or {}
    reasons = []
    if 'infobiz-team' not in (plugins.get('enabled') or []):
        reasons.append('plugin-not-enabled')
    if 'infobiz-team' in (plugins.get('disabled') or []):
        reasons.append('plugin-disabled')
    if 'kanban' in ((config.get('agent') or {}).get('disabled_toolsets') or []):
        reasons.append('kanban-disabled')
    # Telegram sees kanban through its saved list, or through the profile-wide
    # opt-in when it inherits the Hermes bundle (see configure).
    telegram = (config.get('platform_toolsets') or {}).get('telegram')
    if 'kanban' not in (telegram if isinstance(telegram, list) else (config.get('toolsets') or [])):
        reasons.append('telegram-kanban-missing' if isinstance(telegram, list) else 'toolsets-kanban-missing')
    for file in ['plugin.yaml', '__init__.py']:
        target = directory/'plugins/infobiz-team'/file
        plain(target)
        if not target.is_file():
            reasons.append('plugin-file-missing:'+file)
        elif target.read_bytes() != (Path(package)/'plugins/infobiz-team'/file).read_bytes():
            reasons.append('plugin-file-differs:'+file)
    return reasons


def configured(package, profiles, name):
    return not unconfigured_reasons(package, profiles, name)


def describe(answer):
    """The gateway's reload-plugins answer for a finding: outcome, reason and
    which home it judged; never plugin internals or credentials."""
    if answer is None:
        return 'no gateway answered on the profile socket or the root socket'
    kept = {key: answer.get(key) for key in ('reloaded', 'error', 'home') if answer.get(key) is not None}
    activations = answer.get('activations')
    if isinstance(activations, list):
        kept['activations'] = sorted(str(a.get('name') or a.get('key') or '?') for a in activations if isinstance(a, dict))[:12]
    return json.dumps(kept, ensure_ascii=False, sort_keys=True)[:400]


def reload_gateway(root=None, home=None):
    """Ask the gateway over the control socket (the profile's own socket, then the
    installed root's multiplexer for ``home``), exactly as ``reload_running_gateway``
    does on Hermes >= 0.22.6 but with the root handed in by the installer instead of
    resolved from the environment. Returns ``(answer, served_by)``; the answer is
    None when no gateway answered or this Hermes predates the verb."""
    try:
        from hermes_constants import get_hermes_home
        home = Path(home or get_hermes_home())
        if root is None:
            from hermes_constants import get_default_hermes_root
            root = Path(get_default_hermes_root())
        root = Path(root)
    except Exception:
        return None, 'unresolved'
    try:
        from gateway.control_socket import reload_gateway_plugins
    except ImportError:
        try:
            from hermes_cli.plugins_activation import reload_running_gateway
        except ImportError:
            return None, 'unsupported'
        return reload_running_gateway(), 'hermes'
    try:
        answer = reload_gateway_plugins(home, timeout=RELOAD_TIMEOUT)
        if answer is not None:
            return answer, 'profile'
        if root.resolve() != home.resolve():  # a served secondary: the multiplexer's socket
            return reload_gateway_plugins(root, profile_home=home, timeout=RELOAD_TIMEOUT), 'root'
        return None, 'profile'
    except Exception as error:
        return None, 'failed:'+type(error).__name__


def gateway_record(root):
    """The root gateway's own gateway_state.json (Hermes 0.21.5 and 0.23 write
    ``pid``, ``start_time`` and ``served_profiles``), or None."""
    try:
        path = Path(root)/'gateway_state.json'
        plain(path)
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding='utf-8-sig'))
        return record if isinstance(record, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def unserved(answer):
    return answer is not None and not answer.get('reloaded') and 'not served' in str(answer.get('error') or '')


def settled_reload(root, home):
    """Reload with a bounded settle: while the root gateway is younger than
    SETTLE_SECONDS (its own start_time) a refused "home is not served" or an
    unanswered socket is retried every RELOAD_RETRY_SECONDS, sooner once the
    record lists this profile among served_profiles. A gateway that has been up
    longer, or no gateway record at all, answers once: a genuinely unserved
    profile fails fast with the gateway's own detail."""
    name = Path(home).name if home is not None else None
    started = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        answer, served_by = reload_gateway(root, home)
        if answer and answer.get('reloaded'):
            break
        if not (answer is None or unserved(answer)):
            break
        record = gateway_record(root) if root is not None else None
        try:
            uptime = time.time() - float(record['start_time'])
        except (TypeError, ValueError, KeyError):
            uptime = None
        if record is None or uptime is None or uptime > SETTLE_SECONDS or time.monotonic()-started >= SETTLE_SECONDS:
            break
        served = record.get('served_profiles')
        if not (isinstance(served, list) and name in served):
            time.sleep(RELOAD_RETRY_SECONDS)
    elapsed = int(time.monotonic()-started)
    return answer, (served_by if attempts == 1 else served_by+' after %ds (%d attempts)' % (elapsed, attempts))


def inspect_current_profile(reload=False, root=None):
    """Use the real loader, platform selector and tool schemas; no Agent/model."""
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.config import load_config_readonly
    from hermes_cli.tools_config import _get_platform_tools
    from toolsets import resolve_toolset
    from tools.registry import registry
    get_plugin_manager().discover_and_load(force=True)
    selected = _get_platform_tools(load_config_readonly(), 'telegram')
    names = {name for toolset in selected for name in resolve_toolset(toolset)}
    visible = {schema['function']['name'] for schema in registry.get_definitions(names & TEAM_TOOLS, quiet=True)}
    missing = sorted(TEAM_TOOLS-visible)
    if missing:
        return {'status':'missing-tools', 'missing':missing}
    if reload:
        answer, served_by = settled_reload(root, os.environ.get('HERMES_HOME'))
        if not answer or not answer.get('reloaded'):
            return {'status':'pending-gateway-reload', 'missing':[], 'detail':'reload-plugins via '+served_by+': '+describe(answer)}
        # Re-check the gateway's profile activation, not merely local imports.
        activations = answer.get('activations') or []
        if not any(a.get('name') == 'infobiz-team' or a.get('key') == 'infobiz-team' for a in activations):
            return {'status':'missing-gateway-plugin', 'missing':[], 'detail':'reload-plugins via '+served_by+': '+describe(answer)}
    return {'status':'ready', 'missing':[]}


# The child mirrors the official launcher: the checkout first on sys.path and
# ``hermes_bootstrap`` before any Hermes import, so a PM install (Windows store
# Python) activates its dependency generation exactly as ``hermes`` does.
# Under the Mac/VPS venv adapter the bootstrap is already loaded and is a no-op.
CHILD = ('import os, sys, json, runpy\n'
         'source, helper, mode, root = sys.argv[1:5]\n'
         'sys.argv[1:] = []\n'
         'for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):\n'
         '    os.environ.pop(key, None)\n'
         'sys.path.insert(0, source)\n'
         'sys._hermes_pin_default_home = True\n'
         'try:\n'
         '    import hermes_bootstrap\n'
         'except ImportError:\n'
         '    pass\n'
         'module = runpy.run_path(helper)\n'
         'print(json.dumps(module["inspect_current_profile"](mode == "reload", root)))\n')

SECRET = re.compile(r'(?i)((?:token|key|secret|password|authorization|credential)[^\s=:]*\s*[=:]\s*)\S+|'
                    r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b|\b[A-Za-z0-9_-]{32,}\b')


def summarize(stream, limit=400):
    """A bounded, secret-free tail of a child's stderr for the finding detail."""
    if isinstance(stream, bytes):
        stream = stream.decode('utf-8', 'replace')
    lines = [line.strip() for line in (stream or '').splitlines() if line.strip()]
    text = ' | '.join(lines[-4:])
    text = SECRET.sub(lambda m: (m.group(1) or '') + '***', text)
    return text[-limit:]


def observe(source, home, reload):
    env = {**os.environ, 'HERMES_HOME':str(home), 'PYTHONDONTWRITEBYTECODE':'1'}
    try:
        # The installed root (profiles/..) is handed over explicitly: the
        # gateway that serves this profile listens on the root's socket.
        child = subprocess.run([sys.executable, '-I', '-B', '-c', CHILD, str(source), str(Path(__file__).resolve()),
                                'reload' if reload else 'verify', str(Path(home).parent.parent)],
                               env=env, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired as error:
        return {'status':'unavailable-runtime', 'detail':'timeout after %ds' % PROBE_TIMEOUT + (': '+summarize(error.stderr) if error.stderr else '')}
    except OSError as error:
        return {'status':'unavailable-runtime', 'detail':'spawn failed: '+type(error).__name__}
    if child.returncode != 0:
        return {'status':'unavailable-runtime', 'detail':'exit %d: %s' % (child.returncode, summarize(child.stderr) or 'no stderr')}
    try:
        observed = json.loads(child.stdout.strip().splitlines()[-1])
        status = observed.get('status')
    except (ValueError, IndexError, AttributeError):
        return {'status':'unavailable-runtime', 'detail':'unreadable probe output: '+summarize(child.stderr)}
    if status not in STATUSES:
        return {'status':'unavailable-runtime', 'detail':'unknown probe status: '+summarize(child.stderr)}
    result = {'status':status, 'missing':sorted(set(observed.get('missing') or []) & TEAM_TOOLS)}
    if status != 'ready' and isinstance(observed.get('detail'), str):
        result['detail'] = observed['detail'][:400]
    return result


def inspect_profiles(profiles, names, *, reload=False, source=None):
    # A fresh interpreter per profile avoids a registry cached for another home.
    if source is None:
        try:
            spec = importlib.util.find_spec('hermes_cli')
        except (ImportError, ValueError, AttributeError):
            spec = None
        if not spec or not spec.origin:
            return {name: {'status':'unavailable-runtime', 'detail':'hermes_cli is not importable'} for name in names}
        source = Path(spec.origin).resolve().parent.parent
    plain(Path(source))
    names = list(names)
    for name in names:
        plain(Path(profiles)/name)
    # Profiles are probed concurrently: each child pays the full plugin
    # discovery, so a serial sweep of seven profiles exceeds the install budget.
    workers = max(1, min(4, os.cpu_count() or 1, len(names) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(lambda name: observe(source, Path(profiles)/name, reload), names))
    return dict(zip(names, answers))
