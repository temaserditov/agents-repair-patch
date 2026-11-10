"""Hermes 0.23 compatibility repairs for existing student installs (BUG 0014, BUG 0019).

Both repairs are exact reviewed transformations: unknown shapes are reported and
left untouched, the original bytes are re-checked before an atomic write, and a
second run reports ``unchanged``. Config values are never printed or returned.

* BUG 0014: duplicated top-level ``platforms:`` in ``config.yaml``. Old PyYAML
  used the last block; strict ruamel.yaml in Hermes 0.23 rejects the file. Only
  the earlier, already overridden blocks are removed; every other byte stays.
* BUG 0019: Windows v2 (and v3 releases .1-.3) ``control/runner.ps1`` accepts only
  Hermes 0.22.x in ``Get-ReviewedVersion``. Only the known gate line is widened
  to the 0.22.x/0.23.x form of the current Windows v3 helper.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

LIMIT = 8*1024*1024
BOM = '﻿'
PROFILE_NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')

# ---------------------------------------------------------------- BUG 0014 --

_PLAIN_KEY = re.compile(r'([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?:[ \t]|$)')
_DOUBLE_KEY = re.compile(r'"([^"\\]*)"[ \t]*:(?:[ \t]|$)')
_SINGLE_KEY = re.compile(r"'([^']*)'[ \t]*:(?:[ \t]|$)")
_DOC_START = re.compile(r'---(?:[ \t]+#.*)?[ \t]*$')


def _yaml_module(yaml=None):
    if yaml is not None:
        return yaml
    try:
        import yaml as module
    except ImportError:
        try:
            import hermes_yaml as module
        except ImportError:
            return None
    return module


def _body(line):
    return line.rstrip('\r\n')


def _top_level(lines):
    """Return [(key, start, end)] spans of top-level keys found at column 0.

    A span runs from the key line to the next top-level key line. Column-0
    comments and blank lines inside a span belong to it only when indented
    content follows them. Raises ValueError('unknown-shape') for any other
    column-0 construct (sequences, flow roots, anchors, tags, directives,
    complex/merge keys, document end markers or a second document).
    """
    keys = []
    started = False
    for index, line in enumerate(lines):
        body = _body(line)
        if index == 0 and body.startswith(BOM):
            raise ValueError('unknown-shape')
        if not body.strip() or body[0] in ' \t':
            continue
        if body.startswith('#'):
            continue
        if not started and not keys and _DOC_START.fullmatch(body):
            started = True
            continue
        match = _PLAIN_KEY.match(body) or _DOUBLE_KEY.match(body) or _SINGLE_KEY.match(body)
        if not match or match.group(1) == '<<':
            raise ValueError('unknown-shape')
        keys.append((match.group(1), index))
        started = True
    spans = []
    for number, (key, start) in enumerate(keys):
        end = keys[number+1][1] if number+1 < len(keys) else len(lines)
        spans.append((key, start, end))
    return spans


def _trimmed_end(lines, start, end):
    """End of the block itself: drop trailing blank lines and column-0 comments."""
    while end > start+1:
        body = _body(lines[end-1])
        if body.strip() and body[0] in ' \t':
            break
        if body.strip() and not body.startswith('#'):
            break
        end -= 1
    return end


def _load(yaml, text):
    try:
        return yaml.safe_load(text)
    except Exception:
        raise ValueError('unverifiable') from None


def dedupe_platforms(text, yaml=None):
    """Remove earlier overridden top-level ``platforms:`` blocks.

    Returns the new text, or None when there is nothing to do. Raises
    ValueError with a reason code ('unknown-duplicates', 'scalar-platforms',
    'unknown-shape', 'unverifiable', 'no-yaml-verifier') when the file is not a
    known, provably equivalent case. A leading BOM and CRLF endings are kept.
    """
    bom = BOM if text.startswith(BOM) else ''
    source = text[len(bom):]
    lines = source.splitlines(keepends=True)
    try:
        spans = _top_level(lines)
    except ValueError:
        candidates = sum(1 for line in lines if re.match(r'''(?:platforms|"platforms"|'platforms')[ \t]*:(?:[ \t]|$)''', _body(line)))
        if candidates < 2:
            return None
        raise
    seen = {}
    for key, _, _ in spans:
        seen[key] = seen.get(key, 0)+1
    others = sorted(key for key, count in seen.items() if count > 1 and key != 'platforms')
    if others:
        raise ValueError('unknown-duplicates')
    if seen.get('platforms', 0) < 2:
        return None
    module = _yaml_module(yaml)
    if module is None:
        raise ValueError('no-yaml-verifier')
    # Every span parses alone to exactly its own key: this proves the column-0
    # scan matches what a YAML parser sees, without loading the duplicated
    # document (a strict loader would reject it).
    effective, values = {}, []
    for key, start, end in spans:
        value = _load(module, ''.join(lines[start:end]))
        if not isinstance(value, dict) or list(value) != [key]:
            raise ValueError('unverifiable')
        effective[key] = value[key]
        if key == 'platforms':
            values.append(value[key])
    if not isinstance(values[-1], dict) or any(item is not None and not isinstance(item, dict) for item in values):
        raise ValueError('scalar-platforms')
    removed = [(start, _trimmed_end(lines, start, end)) for key, start, end in spans if key == 'platforms'][:-1]
    kept = list(lines)
    for start, end in sorted(removed, reverse=True):
        del kept[start:end]
    result = ''.join(kept)
    after = _load(module, result)
    if after != effective:
        raise ValueError('unverifiable')
    try:
        tolerant = module.safe_load(source)
    except Exception:
        tolerant = effective  # strict loader: span verification above is the proof
    if tolerant != effective:
        raise ValueError('unverifiable')
    if [key for key, _, _ in _top_level(result.splitlines(keepends=True))].count('platforms') != 1:
        raise ValueError('unverifiable')
    return bom+result


def dedupe_platforms_bytes(data, yaml=None):
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise ValueError('unreadable-encoding') from None
    result = dedupe_platforms(text, yaml)
    return None if result is None else result.encode('utf-8')


def removed_platform_blocks(before, after):
    """Count of removed blocks, for the Russian detail line (no values)."""
    count = lambda text: sum(1 for key, _, _ in _top_level(text.lstrip(BOM).splitlines(keepends=True)) if key == 'platforms')
    return count(before)-count(after)

# ---------------------------------------------------------------- BUG 0019 --

_FUNCTION = 'function Get-ReviewedVersion([string]$Text) {'
_RETURN = '  return $Matches[1]'
_OLD_PREFIX = ("  if($Text -notmatch '(?m)^Hermes Agent v?(0\\.22\\.[0-9]+)\\b'){throw 'This addon requires "
               "a reviewed official Hermes 0.22.x source installation. The code was not used.")
_NEW_PREFIX = ("  if($Text -notmatch '(?m)^Hermes Agent v?(0\\.2[23]\\.[0-9]+)\\b'){throw 'This addon requires "
               "a reviewed official Hermes 0.22.x/0.23.x source installation. The code was not used.")
_CURRENT_PREFIX = "  if($Text -notmatch '(?m)^Hermes Agent v?(0\\.2[23]\\.[0-9]+)\\b'){throw '"
# SHA-256 of the complete old gate line (without line ending), taken from git:
KNOWN_OLD_GATES = {
    # windows-addon-v2/install.ps1 de4e95f2 and 0431c239; windows-addon-v3 93ead633 (.1) .. 05caa91d.
    'd4972713ce14e2941a2870e18a2ecc489e3f7ec95cb1aace6adde00ddfe788c1': 'v2/v3.1',
    # windows-addon-v3/install.ps1 ca0210f3, 374d1007, b741b71d, 6f373920 (gate plus 0.22.7 install command).
    'e1ac3d0523df1239fe039abdd998d7b7b0071108647ea293e904dfeaa2fcb8f8': 'v3-0.22.7-command',
}
_PANEL_MARKER = '[switch]$RunWebShell'


def _gate_index(lines):
    headers = [index for index, line in enumerate(lines) if _body(line) == _FUNCTION]
    if len(headers) != 1:
        return None
    index = headers[0]
    if index+3 >= len(lines) or _body(lines[index+2]) != _RETURN or _body(lines[index+3]) != '}':
        return None
    return index+1


def runner_gate_state(text):
    """'old' (known 0.22-only gate), 'current' (accepts 0.22/0.23) or 'unknown'."""
    source = text[1:] if text.startswith(BOM) else text
    lines = source.splitlines(keepends=True)
    params = [_body(line) for line in lines[:10] if _body(line).startswith('param(')]
    if len(params) != 1 or _PANEL_MARKER not in params[0]:
        return 'unknown'  # v1 helper (no panel task) or an unrelated script
    index = _gate_index(lines)
    if index is None:
        return 'unknown'
    gate = _body(lines[index])
    if gate.startswith(_CURRENT_PREFIX):
        return 'current'
    if hashlib.sha256(gate.encode('utf-8')).hexdigest() in KNOWN_OLD_GATES and gate.startswith(_OLD_PREFIX):
        return 'old'
    return 'unknown'


def widen_runner_gate(text):
    """Widen only the known old gate line; None for current or unknown helpers."""
    if runner_gate_state(text) != 'old':
        return None
    bom = BOM if text.startswith(BOM) else ''
    lines = text[len(bom):].splitlines(keepends=True)
    index = _gate_index(lines)
    line = lines[index]
    ending = line[len(_body(line)):]
    lines[index] = _NEW_PREFIX+_body(line)[len(_OLD_PREFIX):]+ending
    result = bom+''.join(lines)
    if runner_gate_state(result) != 'current':
        raise ValueError('unverifiable')
    return result


def widen_runner_gate_bytes(data):
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        return None
    result = widen_runner_gate(text)
    return None if result is None else result.encode('utf-8')


def runner_gate_regex(text):
    """The .NET regex of Get-ReviewedVersion, unquoted from PowerShell ('' -> ')."""
    source = text[1:] if text.startswith(BOM) else text
    lines = source.splitlines(keepends=True)
    index = _gate_index(lines)
    if index is None:
        return None
    match = re.match(r"  if\(\$Text -notmatch '((?:[^']|'')*)'\)", _body(lines[index]))
    return match.group(1).replace("''", "'") if match else None

# ------------------------------------------------------------------ files ---


def _regular(path):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('linked-path')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('special-file')
    if getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('linked-path')
    if info.st_size > LIMIT:
        raise ValueError('oversized')
    return info


def _read(path):
    _regular(path)
    return Path(path).read_bytes()


def atomic_replace(path, data):
    """Replace an existing regular file atomically, keeping its permission bits."""
    path = Path(path)
    mode = stat.S_IMODE(_regular(path).st_mode)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, mode)
        _regular(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _applier(path, before, after):
    def apply(backup=None):
        # The caller may keep the original bytes; config.yaml can hold keys.
        current = _read(path)
        if current != before:
            raise ValueError('changed-since-plan')
        if backup is not None:
            backup(path, before)
        atomic_replace(path, after)
        return {'path': str(path), 'sha256Before': _sha(before), 'sha256After': _sha(after)}
    return apply


def _item(code, status, detail, path, apply=None, before=None):
    item = {'code': code, 'detail': detail, 'status': status, 'paths': [path], 'apply': apply}
    if before is not None:
        item['sha256'] = _sha(before)
    return item


_REASONS = {
    'unknown-duplicates': 'Повторяются другие верхние ключи; файл не изменялся.',
    'scalar-platforms': 'Раздел platforms не словарь; файл не изменялся.',
    'unknown-shape': 'Незнакомая структура YAML; файл не изменялся.',
    'unverifiable': 'Равенство действующих настроек не доказано; файл не изменялся.',
    'no-yaml-verifier': 'Нет YAML-библиотеки для проверки; файл не изменялся.',
    'unreadable-encoding': 'Файл не в UTF-8; не изменялся.',
    'linked-path': 'Ссылка вместо файла; не изменялась.',
    'special-file': 'Не обычный файл; не изменялся.',
    'oversized': 'Слишком большой файл; не изменялся.',
}


def _plan_config(path, code, yaml):
    try:
        if not path.exists() and not path.is_symlink():
            return None
        before = _read(path)
        after = dedupe_platforms_bytes(before, yaml)
    except ValueError as error:
        reason = str(error) if str(error) in _REASONS else 'unverifiable'
        return _item(code, 'skipped:'+reason, _REASONS[reason], path)
    except OSError:
        return _item(code, 'skipped:unreadable', 'Файл не читается; не изменялся.', path)
    if after is None:
        return _item(code, 'unchanged', 'Повторов platforms нет.', path, before=before)
    count = removed_platform_blocks(before.decode('utf-8'), after.decode('utf-8'))
    detail = 'Удалить перекрытые ранние блоки platforms: '+str(count)+'; действует последний, значения не меняются.'
    return _item(code, 'needs-fix', detail, path, _applier(path, before, after), before)


def _plan_runner(path):
    try:
        if not path.exists() and not path.is_symlink():
            return None
        before = _read(path)
        text = before.decode('utf-8')
    except ValueError as error:
        reason = str(error) if str(error) in _REASONS else 'unknown-runner'
        return _item('windows-runner-gate', 'skipped:'+reason, _REASONS.get(reason, 'Помощник не изменялся.'), path)
    except UnicodeError:
        return _item('windows-runner-gate', 'skipped:unreadable-encoding', _REASONS['unreadable-encoding'], path)
    except OSError:
        return _item('windows-runner-gate', 'skipped:unreadable', 'Файл не читается; не изменялся.', path)
    state = runner_gate_state(text)
    if state == 'current':
        return _item('windows-runner-gate', 'unchanged', 'Помощник панели принимает Hermes 0.22.x/0.23.x.', path, before=before)
    if state != 'old':
        return _item('windows-runner-gate', 'skipped:unknown-runner',
                     'Незнакомый или изменённый runner.ps1; нужен актуальный помощник Windows v3.', path)
    after = widen_runner_gate_bytes(before)
    return _item('windows-runner-gate', 'needs-fix',
                 'Помощник панели пропускает только Hermes 0.22.x; разрешить 0.22.x/0.23.x как в Windows v3.',
                 path, _applier(path, before, after), before)


def plan(home, hermes, install, system, profile_names, yaml=None):
    """Read-only plan. Each item's apply() re-checks the planned bytes, then writes."""
    home = Path(home)
    if hermes is None:
        local = os.environ.get('LOCALAPPDATA')
        hermes = Path(local)/'hermes' if system == 'Windows' and local else home/'.hermes'
    hermes = Path(hermes)
    items = []
    configs = [(hermes/'config.yaml', 'platforms:default')]
    for name in profile_names:
        if PROFILE_NAME.fullmatch(name or ''):
            configs.append((hermes/'profiles'/name/'config.yaml', 'platforms:'+name))
    for path, code in configs:
        item = _plan_config(path, code, yaml)
        if item is not None:
            items.append(item)
    if system == 'Windows' and install is not None:
        item = _plan_runner(Path(install)/'control/runner.ps1')
        if item is not None:
            items.append(item)
    return items
