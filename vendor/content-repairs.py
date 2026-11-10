"""TASK48: conservative repairs of an installed Content engine. Python 3.9+, stdlib only.

BUG 0008  portable contract.py: the neutral theme ``client`` is rejected.
BUG 0009  portable board.py: ``Board`` lacks ``layout``; the PDF export fails.
BUG 0010  portable resources/models/u2net_human_seg.onnx is missing.
BUG 0013  macOS person-cutout.sh: bare python3 without PIL/numpy/cv2 (detected only).
OBS 0007/0008  macOS doc.sh / pip.sh use fixed /tmp names shared by every run.

The installer's own version directory ``releases/<v>`` is never written: a
reinstall of the same revision compares those bytes with the package
(setup-tariff-content.py, "Immutable Content release differs" / "Changed
native Content source preserved"). A sibling ``<v>-repair1`` is built instead:
unchanged files are hard links to ``<v>``, the reviewed fixes are new files,
the model is added there, and ``active.json`` is switched to it. When a
reinstall puts ``active.json`` back on ``<v>``, the intact copy is reused.

Fixes are exact reviewed fragment replacements whose result equals the owner's
verified address-level repair (FIX_20261109_student_content_engine).
Windows is diagnosed only (hard links and long pip paths, BUG 0020).
"""
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

MODEL_NAME = 'u2net_human_seg.onnx'
MODEL_SHA256 = '01eb6a29a5c4d8edb30b56adad9bb3a2a0535338e480724a213e0acfd2d1c73c'
MODEL_SIZE = 175997641
MODEL_URL = 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/' + MODEL_NAME
GITHUB_WEB = 'https://github.com/'
GITHUB_API = 'https://api.github.com/'
AGENT = 'SerditovAgents'
# The installed cutout.py must pin exactly these weights before any download.
CUTOUT_BINDING = ('MODEL_SHA256="' + MODEL_SHA256 + '"').encode()
VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,200}')
COPY_NAME = re.compile(r'[a-zA-Z0-9_.-]{1,180}')  # agents_repair.windows / content_ready
SUFFIX = '-repair1'
TEXT_LIMIT = 1024 * 1024

CONTRACT_FIX = [
    (b'THEMES = {\n    "orange", "dark", "bone", "clay", "air", "retro", "serra", "euro",\n    "navy", "night",\n}\n',
     b'THEMES = {"client"}\n'),
    ('    theme = str(contract.get("стиль", "night")).strip().lower()\n'.encode(),
     '    theme = str(contract.get("стиль", "client")).strip().lower()\n'.encode()),
]
_BOARD_LINE = b'        self.r=BoardContent(cfg,fonts,self.card_rect[2] if self.card_rect else 1040)\n'
BOARD_FIX = [(_BOARD_LINE, _BOARD_LINE + b'        self.layout = self.r.layout\n')]

DOC_FIX = [(
    b'mkdir -p "$(dirname "$OUT")" /tmp/doc-pages\n'
    b'PAGES=()\n'
    b'i=0\n'
    b'for cfg in "$@"; do\n'
    b'  i=$((i + 1))\n'
    b'  python3 - "$cfg" "$STYLE" "$i" <<\'PY\'\n'
    b'import json, sys\n'
    b'c = json.load(open(sys.argv[1], encoding="utf-8"))\n'
    b'c["format"] = "a4"; c["theme"] = sys.argv[2]\n'
    + 'c.setdefault("card", {})["page"] = sys.argv[3]      # номер страницы проставляется сам\n'.encode() +
    b'c["out"] = f"/tmp/doc-pages/page-{int(sys.argv[3]):02d}"\n'
    b'json.dump(c, open("/tmp/doc-page.json", "w", encoding="utf-8"), ensure_ascii=False)\n'
    b'PY\n'
    b'  ./board /tmp/doc-page.json >/dev/null\n'
    b'  PAGES+=("$(printf \'/tmp/doc-pages/page-%02d.pdf\' $i)")\n',
    b'mkdir -p "$(dirname "$OUT")"\n'
    b'# OBS 0008: every run has its own pages; parallel guides no longer share /tmp names.\n'
    b'WORK="$(mktemp -d "${TMPDIR:-/tmp}/doc-pages.XXXXXX")"\n'
    b'trap \'rm -rf "$WORK"\' EXIT\n'
    b'PAGES=()\n'
    b'i=0\n'
    b'for cfg in "$@"; do\n'
    b'  i=$((i + 1))\n'
    b'  python3 - "$cfg" "$STYLE" "$i" "$WORK" <<\'PY\'\n'
    b'import json, sys\n'
    b'c = json.load(open(sys.argv[1], encoding="utf-8"))\n'
    b'c["format"] = "a4"; c["theme"] = sys.argv[2]\n'
    + 'c.setdefault("card", {})["page"] = sys.argv[3]      # номер страницы проставляется сам\n'.encode() +
    b'c["out"] = f"{sys.argv[4]}/page-{int(sys.argv[3]):02d}"\n'
    b'json.dump(c, open(f"{sys.argv[4]}/doc-page.json", "w", encoding="utf-8"), ensure_ascii=False)\n'
    b'PY\n'
    b'  ./board "$WORK/doc-page.json" >/dev/null\n'
    b'  PAGES+=("$(printf \'%s/page-%02d.pdf\' "$WORK" $i)")\n',
)]

PIP_FIX = [(
    b'$FFMPEG -v error -y -loop 1 -t 1.0 -i "$ON"  -r $FPS -c:v prores_ks -profile:v 4444 \\\n'
    b'        -pix_fmt yuva444p10le /tmp/pip-on.mov\n'
    b'$FFMPEG -v error -y -loop 1 -t 1.0 -i "$OFF" -r $FPS -c:v prores_ks -profile:v 4444 \\\n'
    b'        -pix_fmt yuva444p10le /tmp/pip-off.mov\n'
    b'printf "file \'/tmp/pip-on.mov\'\\nfile \'/tmp/pip-off.mov\'\\n" > /tmp/pip-cycle.txt\n'
    b'$FFMPEG -v error -y -f concat -safe 0 -i /tmp/pip-cycle.txt -c copy /tmp/pip-cycle.mov\n'
    b'\n'
    b'LOOPS=$(python3 -c "import math;print(max(0, math.ceil($SEC / 2) - 1))")\n'
    b'$FFMPEG -v error -y -stream_loop "$LOOPS" -i /tmp/pip-cycle.mov -t "$SEC" \\\n',
    b'# OBS 0008: the video intermediates of every run live in their own directory.\n'
    b'WORK="$(mktemp -d "${TMPDIR:-/tmp}/pip-live.XXXXXX")"\n'
    b'trap \'rm -rf "$WORK"\' EXIT\n'
    b'$FFMPEG -v error -y -loop 1 -t 1.0 -i "$ON"  -r $FPS -c:v prores_ks -profile:v 4444 \\\n'
    b'        -pix_fmt yuva444p10le "$WORK/pip-on.mov"\n'
    b'$FFMPEG -v error -y -loop 1 -t 1.0 -i "$OFF" -r $FPS -c:v prores_ks -profile:v 4444 \\\n'
    b'        -pix_fmt yuva444p10le "$WORK/pip-off.mov"\n'
    b'printf "file \'pip-on.mov\'\\nfile \'pip-off.mov\'\\n" > "$WORK/pip-cycle.txt"\n'
    b'$FFMPEG -v error -y -f concat -safe 0 -i "$WORK/pip-cycle.txt" -c copy "$WORK/pip-cycle.mov"\n'
    b'\n'
    b'LOOPS=$(python3 -c "import math;print(max(0, math.ceil($SEC / 2) - 1))")\n'
    b'$FFMPEG -v error -y -stream_loop "$LOOPS" -i "$WORK/pip-cycle.mov" -t "$SEC" \\\n',
)]
# Written by the prebuilt speaker and read by board/pip binaries: a shell edit cannot isolate it.
SPEAKER_PANEL = b'/tmp/speaker_panel.png'
CUTOUT_CALL = b'python3 "$CLEAN" "$TMP_DIR/raw.png" "$2"\n'

ENGINES = {
    # kind: pointer, releases, fixes (relative to the version directory), link-tolerant
    # subtree (the Content-owned uv venv links its interpreter), subtrees not compared
    # for an existing copy, directories recreated empty instead of linked.
    'portable': {'pointer': 'tools/agents-engine/active.json', 'releases': 'tools/agents-engine/releases',
                 'fixes': {'portable/contract.py': ('BUG 0008', CONTRACT_FIX), 'portable/board.py': ('BUG 0009', BOARD_FIX)},
                 'links': 'portable/runtime', 'dynamic': ('portable/runtime', 'portable/resources/models'), 'fresh': ()},
    'mac': {'pointer': 'tools/swift-design-engine/active.json', 'releases': 'tools/swift-design-engine/releases',
            'fixes': {'doc.sh': ('OBS 0008', DOC_FIX), 'pip.sh': ('OBS 0008', PIP_FIX)},
            # Prebuilt Swift tools overwrite out/* in place; a link there would write into <v>.
            'links': None, 'dynamic': ('out',), 'fresh': ('out',)},
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _plain(path, root):
    """Reject links and special objects from ``root`` down to ``path``."""
    path, root = Path(path).absolute(), Path(root).absolute()
    chain = [path, *path.parents]
    for item in chain[:chain.index(root) + 1] if root in chain else chain[:1]:
        if item.is_symlink():
            raise ValueError('Linked path preserved')
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Reparse path preserved')
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('Special object preserved')


def _read(path, root, limit=TEXT_LIMIT):
    _plain(path, root)
    if path.stat().st_size > limit:
        raise ValueError('Oversized file preserved')
    return path.read_bytes()


def _atomic(path, data, root, mode):
    _plain(path, root)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, mode)
        _plain(path, root)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fragment_state(data, pairs):
    """'fixed' | 'applicable' | 'edited' for a list of (old, new) byte fragments."""
    states = []
    for old, new in pairs:
        if data.count(new) == 1 and (old in new or old not in data):
            states.append('fixed')
        elif data.count(old) == 1:
            states.append('applicable')
        else:
            return 'edited'
    return 'fixed' if all(s == 'fixed' for s in states) else 'applicable'


def fragment_fix(data, pairs):
    for old, new in pairs:
        if data.count(new) == 1 and (old in new or old not in data):
            continue
        if data.count(old) != 1:
            raise ValueError('Reviewed fragment not found')
        data = data.replace(old, new, 1)
    return data


def _item(code, detail, status, paths=(), apply=None):
    return {'code': code, 'detail': detail, 'status': status, 'paths': list(paths), 'apply': apply}


def _version(pointer, root):
    _plain(pointer, root)
    if not pointer.is_file():
        return None, None
    data = _read(pointer, root, 64 * 1024)
    try:
        value = json.loads(data.decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('Invalid Content registration') from None
    version = value.get('version') if isinstance(value, dict) else None
    if not isinstance(version, str) or not VERSION.fullmatch(version) or version in {'.', '..'}:
        raise ValueError('Invalid Content registration')
    return version, data


def plan(home, system, *, download=True, opener=None, search_path=None):
    """Planned Content repairs. Each item: code, detail, status, paths, apply.

    ``paths`` lists only files outside the new repair copy that ``apply()``
    replaces (``active.json``); the copy itself is new and needs no backup.
    ``apply()`` re-reads its preconditions (concurrent change -> ValueError).
    It returns None after a write, or a status string when nothing was
    written: ``'skipped:hardlink-unavailable'`` (copy), ``'unavailable:network'``,
    ``'unavailable:checksum'``, ``'unavailable:repair-copy-missing'`` (model).
    """
    home = Path(home).absolute()
    project = home / 'Documents/Content'
    try:
        _plain(project, home)
        if not project.is_dir():
            return []
        if system == 'Darwin':
            return _engine(home, project, 'mac', download, None, search_path)
        if system == 'Linux':
            return _engine(home, project, 'portable', download, opener or urllib.request.urlopen, None)
        if system == 'Windows':
            return _windows(home, project)
    except (ValueError, OSError):
        return [_item('content:registration', 'Регистрация Content нечитаема или содержит ссылку; Content не изменялся.',
                      'skipped:invalid-registration')]
    return []


def _entries(top, links):
    """(relative posix, 'dir'|'file'|'link') for a tree; links only inside ``links``."""
    result = []
    for directory, dirnames, filenames in os.walk(str(top)):
        base = Path(directory)
        for name in sorted(dirnames + filenames):
            path = base / name
            relative = path.relative_to(top).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if not (links and (relative + '/').startswith(links + '/')):
                    raise ValueError('Linked path preserved')
                result.append((relative, 'link'))
                if name in dirnames:
                    dirnames.remove(name)
            elif stat.S_ISDIR(info.st_mode):
                result.append((relative, 'dir'))
            elif stat.S_ISREG(info.st_mode):
                result.append((relative, 'file'))
            else:
                raise ValueError('Special object preserved')
    return result


def _under(relative, prefixes):
    return any(relative == p or relative.startswith(p + '/') for p in prefixes)


def _derived(relative):
    return '__pycache__' in relative.split('/') or relative.endswith(('.pyc', '.pyo'))


def _copy_intact(source, copy, expected, spec):
    """An existing copy is ours: every engine file of ``source`` is present unchanged
    (same inode or same bytes) and every fixed file holds the expected fixed bytes."""
    try:
        if not copy.is_dir() or copy.is_symlink():
            return False
        for relative, kind in _entries(source, spec['links']):
            if _under(relative, spec['dynamic']) or _derived(relative):
                continue
            mine, theirs = copy / relative, source / relative
            if kind == 'dir':
                if mine.is_symlink() or not mine.is_dir():
                    return False
                continue
            if mine.is_symlink() or not mine.is_file():
                return False
            if relative in expected:
                if mine.read_bytes() != expected[relative]:
                    return False
                continue
            a, b = mine.stat(), theirs.stat()
            if (a.st_ino, a.st_dev) != (b.st_ino, b.st_dev) and (a.st_size != b.st_size or mine.read_bytes() != theirs.read_bytes()):
                return False
        if spec['links']:
            python = copy / spec['links'] / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            if not os.path.lexists(str(python)):
                return False
        return True
    except (OSError, ValueError):
        return False


def _build_copy(source, releases, name, expected, spec, home):
    """Hard-link ``source`` into ``releases/<name>``; fixed files are new. None or a status."""
    final = releases / name
    temporary = releases / (name + '.tmp-' + uuid.uuid4().hex[:12])
    _plain(temporary, home)
    os.mkdir(str(temporary), 0o700)
    try:
        os.chmod(str(temporary), stat.S_IMODE(source.stat().st_mode))
        for relative, kind in _entries(source, spec['links']):
            src, dst = source / relative, temporary / relative
            if any(relative.startswith(p + '/') for p in spec['fresh']):
                continue  # Generated output: recreated empty, never linked.
            if kind == 'dir':
                os.mkdir(str(dst))
                os.chmod(str(dst), stat.S_IMODE(src.stat().st_mode))
            elif kind == 'link':
                os.symlink(os.readlink(str(src)), str(dst))
            elif relative in expected:
                fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(src.stat().st_mode))
                with os.fdopen(fd, 'wb') as out:
                    out.write(expected[relative])
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(str(dst), stat.S_IMODE(src.stat().st_mode))
            else:
                try:
                    os.link(str(src), str(dst))
                except OSError as error:
                    if error.errno in {errno.EXDEV, errno.EPERM, errno.EMLINK, errno.EACCES,
                                       getattr(errno, 'ENOTSUP', -1), getattr(errno, 'EOPNOTSUPP', -1)}:
                        return 'skipped:hardlink-unavailable'
                    raise
        for relative, data in expected.items():
            if (temporary / relative).read_bytes() != data:
                raise ValueError('Reviewed fragment not found')
        _plain(final, home)
        if os.path.lexists(str(final)):
            raise ValueError('Concurrent Content edit preserved')
        os.rename(str(temporary), str(final))
        return None
    finally:
        # Only the partial tree this call created: links and new files, never <v>.
        if os.path.lexists(str(temporary)):
            shutil.rmtree(str(temporary))


def _switch(pointer, home, before, name):
    if _read(pointer, home, 64 * 1024) != before:
        raise ValueError('Concurrent Content edit preserved')
    _atomic(pointer, (json.dumps({'version': name}) + '\n').encode(), home, stat.S_IMODE(pointer.stat().st_mode))


def _file_states(directory, fixes, home):
    """relative -> (state, bytes) with state fixed|applicable|edited|missing|linked."""
    states = {}
    for relative, (_, pairs) in fixes.items():
        path = directory / relative
        try:
            _plain(path, home)
            if not path.is_file():
                states[relative] = ('missing', None)
                continue
            data = _read(path, home)
            data.decode('utf-8')
        except (ValueError, OSError):
            states[relative] = ('linked', None)
            continue
        states[relative] = (fragment_state(data, pairs), data)
    return states


def _model_state(directory, home):
    cutout = directory / 'portable/cutout.py'
    target = directory / 'portable/resources/models' / MODEL_NAME
    _plain(cutout, home); _plain(target, home)
    if not cutout.is_file() or _read(cutout, home).count(CUTOUT_BINDING) != 1:
        return 'unknown-binding'
    if not target.exists():
        return 'missing'
    if not target.is_file() or target.stat().st_size != MODEL_SIZE or _file_sha(target) != MODEL_SHA256:
        return 'differs'
    return 'present'


def _engine(home, project, kind, download, opener, search_path):
    spec = ENGINES[kind]
    pointer, releases = project / spec['pointer'], project / spec['releases']
    active, before = _version(pointer, home)
    if active is None:
        return []
    on_copy = active.endswith(SUFFIX)
    base = active[:-len(SUFFIX)] if on_copy else active
    name = base + SUFFIX
    if not base or not COPY_NAME.fullmatch(name):
        raise ValueError('Invalid Content registration')
    current, copy = releases / active, releases / name
    _plain(current, home); _plain(copy, home)
    if not current.is_dir():
        return [_item('content:' + kind + '-repair', 'Активная версия Content отсутствует; нужна штатная установка.', 'skipped:content-incomplete')]
    if kind == 'portable':
        runtime = current / 'portable/runtime/bin/python'
        if not (current / 'portable/cli.py').is_file() or not os.path.lexists(str(runtime)):
            return [_item('content:portable-repair', 'Content установлен не полностью; ремонт после завершения установки.', 'skipped:content-incomplete')]
    fixes = spec['fixes']
    states = _file_states(current, fixes, home)
    bugs = ', '.join(sorted({fixes[r][0] for r, (s, _) in states.items() if s == 'applicable'}))
    model = _model_state(current, home) if kind == 'portable' else None
    want_model = model == 'missing' and download
    code = 'content:portable-repair' if kind == 'portable' else 'content:mac-repair'
    label = ('Карусель: тема client (BUG 0008); PDF кадра урока (BUG 0009); модель U2Net (BUG 0010)' if kind == 'portable'
             else 'doc.sh и pip.sh: временные файлы каждого запуска отдельно (OBS 0008)')
    items = []
    copy_ok = False
    if on_copy:
        bad = [r for r, (s, _) in states.items() if s != 'fixed']
        if bad:
            items.append(_item(code, label + '. Починенная копия ' + name + ' изменена; не трогается.', 'skipped:repair-copy-edited'))
        else:
            copy_ok = True
            items.append(_item(code, label + '. Работает починенная копия ' + name + '.', 'unchanged'))
    elif any(s in {'edited', 'missing', 'linked'} for s, _ in states.values()):
        items.append(_item(code, label + '. Файлы версии изменены вручную или неизвестны; ничего не тронуто.', 'skipped:edited'))
    elif not bugs and not want_model:
        items.append(_item(code, label + '. Уже исправлено.', 'unchanged'))
    else:
        expected = {r: (fragment_fix(d, fixes[r][1]) if s == 'applicable' else d) for r, (s, d) in states.items()}
        if os.path.lexists(str(copy)):
            if _copy_intact(current, copy, expected, spec):
                copy_ok = True

                def switch():
                    if not _copy_intact(current, copy, expected, spec):
                        raise ValueError('Concurrent Content edit preserved')
                    _switch(pointer, home, before, name)
                items.append(_item(code, label + '. Починенная копия ' + name + ' уже есть; переключить active.json на неё.',
                                   'needs-fix', [pointer], switch))
            else:
                items.append(_item(code, label + '. Каталог ' + name + ' отличается от ожидаемой копии; ничего не тронуто.',
                                   'skipped:repair-copy-edited'))
        else:
            try:
                _entries(current, spec['links'])
            except (ValueError, OSError):
                items.append(_item(code, label + '. В версии есть ссылка или особый объект; копия не создаётся.', 'skipped:linked-path'))
            else:
                copy_ok = True

                def build():
                    for relative, (_, data) in states.items():
                        if _read(current / relative, home) != data:
                            raise ValueError('Concurrent Content edit preserved')
                    if _read(pointer, home, 64 * 1024) != before:
                        raise ValueError('Concurrent Content edit preserved')
                    outcome = _build_copy(current, releases, name, expected, spec, home)
                    if outcome:
                        return outcome
                    _switch(pointer, home, before, name)
                items.append(_item(code, label + ': ' + (bugs or 'BUG 0010') + '. Создать починенную копию ' + name +
                                   ' (жёсткие ссылки на ' + active + ', исправленные файлы новые) и переключить active.json.',
                                   'needs-fix', [pointer], build))
    if kind == 'portable':
        if not on_copy and copy.is_dir():
            model = _model_state(copy, home)  # The model lives in the copy, never in <v>.
        items.append(_model_item(home, current, copy, on_copy, copy_ok, model, download, opener))
    else:
        items += [i for i in (_shared_panel_item(home, current), _cutout_item(home, current, search_path)) if i is not None]
    return items


def _model_item(home, current, copy, on_copy, copy_ok, model, download, opener):
    code, detail = 'content:u2net-model', 'Модель вырезания человека U2Net из официального релиза rembg, SHA256 закреплён (BUG 0010).'
    if model == 'unknown-binding':
        return _item(code, detail + ' cutout.py ожидает другую модель; не изменялся.', 'skipped:unknown-cutout-binding')
    if model == 'differs':
        return _item(code, detail + ' Существующий файл модели отличается; сохранён.', 'skipped:model-differs')
    if model == 'present':
        return _item(code, detail + ' Модель на месте.', 'unchanged')
    if not download:
        return _item(code, detail + ' Загрузка отключена.', 'skipped:download-disabled')
    if not copy_ok:
        return _item(code, detail + ' Нет исправной починенной копии; модель в версию установщика не кладётся.', 'skipped:repair-copy-unavailable')
    directory = current if on_copy else copy
    target = directory / 'portable/resources/models' / MODEL_NAME

    def apply():
        if not (directory / 'portable/resources/models').is_dir():
            return 'unavailable:repair-copy-missing'
        _plain(target, home)
        if target.exists() or _read(directory / 'portable/cutout.py', home).count(CUTOUT_BINDING) != 1:
            raise ValueError('Concurrent Content edit preserved')
        return download_model(target, home, opener)
    return _item(code, detail + ' Загружается в починенную копию ' + directory.name + '.', 'needs-fix', [], apply)


def _windows(home, project):
    """Diagnosis only: a sibling copy needs extended paths and short names (BUG 0020)."""
    active, _ = _version(project / 'tools/agents-engine/active.json', home)
    if active is None:
        return []
    current = project / 'tools/agents-engine/releases' / active
    _plain(current, home)
    if not (current / 'portable/cli.py').is_file():
        return []
    reason = (' Ремонт на Windows только диагностикой: починенной копии нужны жёсткие ссылки и длинные пути pip '
              '(BUG 0020), проверено лишь на hosted Windows; адресный ремонт по FIX_20261109_windows_content.')
    items = []
    for relative, (bug, _) in ENGINES['portable']['fixes'].items():
        state, _ = _file_states(current, {relative: ENGINES['portable']['fixes'][relative]}, home)[relative]
        code = 'content:client-theme' if relative.endswith('contract.py') else 'content:board-layout'
        if state == 'fixed':
            items.append(_item(code, bug + ': исправлено.', 'unchanged'))
        elif state == 'applicable':
            items.append(_item(code, bug + ': дефект найден.' + reason, 'skipped:windows-diagnostic-only'))
        elif state != 'missing':
            items.append(_item(code, bug + ': файл изменён вручную; не изменялся.', 'skipped:edited'))
    model = _model_state(current, home)
    if model == 'present':
        items.append(_item('content:u2net-model', 'BUG 0010: модель на месте.', 'unchanged'))
    elif model == 'missing':
        items.append(_item('content:u2net-model', 'BUG 0010: нет модели U2Net.' + reason, 'skipped:windows-diagnostic-only'))
    return items


def _file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _fetch_verified(opener, request, target, root):
    """Stream into a sibling temporary file; publish only exact size and SHA256."""
    fd, temporary = tempfile.mkstemp(prefix='.' + MODEL_NAME + '-', dir=str(target.parent))
    try:
        digest, total = hashlib.sha256(), 0
        with os.fdopen(fd, 'wb') as out, opener(request, timeout=600) as response:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MODEL_SIZE:
                    return 'checksum'
                digest.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if total != MODEL_SIZE or digest.hexdigest() != MODEL_SHA256:
            return 'checksum'
        os.chmod(temporary, 0o644)
        _plain(target, root)
        if target.exists():
            raise ValueError('Concurrent Content edit preserved')
        os.replace(temporary, target)
        return 'ok'
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _api_asset(opener, url):
    """Exactly one same-named asset of the tagged release, as a REST asset URL (FIX_0565 pattern)."""
    owner, repo, _, _, tag, name = url[len(GITHUB_WEB):].split('/')
    request = urllib.request.Request(GITHUB_API + 'repos/' + owner + '/' + repo + '/releases/tags/' + tag,
                                     headers={'Accept': 'application/vnd.github+json', 'User-Agent': AGENT})
    with opener(request, timeout=60) as response:
        release = json.loads(response.read(8 * 1024 * 1024))
    name = urllib.parse.unquote(name)
    assets = [a.get('url') for a in (release.get('assets') or []) if isinstance(a, dict) and a.get('name') == name]
    prefix = GITHUB_API + 'repos/' + owner + '/' + repo + '/releases/assets/'
    if len(assets) != 1 or not isinstance(assets[0], str) or not re.fullmatch(re.escape(prefix) + r'\d+', assets[0]):
        return None
    return assets[0]


def download_model(target, root, opener):
    """Primary release URL, then the GitHub REST asset endpoint. Offline is not fatal.

    Link or concurrency errors still raise ValueError; only transport failures
    and wrong bytes become an ``unavailable:*`` result with nothing written.
    """
    transport = (OSError, http.client.HTTPException)
    failures = set()
    try:
        outcome = _fetch_verified(opener, urllib.request.Request(MODEL_URL, headers={'User-Agent': AGENT}), target, root)
        if outcome == 'ok':
            return None
        failures.add(outcome)
    except transport:
        failures.add('network')
    try:
        asset = _api_asset(opener, MODEL_URL)
    except transport + (ValueError, KeyError, TypeError, AttributeError):
        asset = None  # Unreachable API or unexpected metadata (json errors are ValueError).
    if asset:
        try:
            outcome = _fetch_verified(opener, urllib.request.Request(asset, headers={'Accept': 'application/octet-stream', 'User-Agent': AGENT}), target, root)
            if outcome == 'ok':
                return None
            failures.add(outcome)
        except transport:
            failures.add('network')
    else:
        failures.add('network')
    return 'unavailable:checksum' if 'checksum' in failures else 'unavailable:network'


def _shared_panel_item(home, engine):
    path = engine / 'lesson.sh'
    try:
        _plain(path, home)
        if not path.is_file() or SPEAKER_PANEL not in _read(path, home):
            return None
    except ValueError:
        return None
    return _item('content:mac-lesson-tmp', 'lesson.sh: общий /tmp/speaker_panel.png зашит в готовые программы speaker/board/pip; '
                 'изоляция параллельных запусков требует новой сборки движка (OBS 0008).', 'skipped:needs-native-build')


def _developer_tools():
    """True only with Command Line Tools/Xcode; never runs a /usr/bin shim that opens the installer dialog."""
    try:
        result = subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and Path(result.stdout.strip() or '/nonexistent').is_dir()


def _cutout_python_ready(python):
    env = {k: v for k, v in os.environ.items() if k.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
    try:
        return subprocess.run([python, '-I', '-B', '-c', 'import PIL, numpy, cv2'], capture_output=True,
                              env=env, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cutout_item(home, engine, search_path):
    script, clean = engine / 'person-cutout.sh', engine / 'tools/clean-cutout.py'
    binary, source = engine / 'tools/person-cutout', engine / 'tools/person-cutout.swift'
    try:
        for path in (script, clean, binary, source):
            _plain(path, home)
        if not script.is_file() or CUTOUT_CALL not in _read(script, home):
            return None
    except ValueError:
        return None
    code = 'content:mac-cutout-python'
    tools = None
    missing = []
    built = binary.is_file() and os.access(str(binary), os.X_OK) and not (source.is_file() and source.stat().st_mtime > binary.stat().st_mtime)
    if not built:
        tools = _developer_tools()
        if not tools:
            missing.append('готовая программа person-cutout (в пакете её нет, swiftc недоступен)')
    python = shutil.which('python3', path=search_path if search_path is not None else os.environ.get('PATH', ''))
    if python and os.path.realpath(python).startswith('/usr/bin/'):
        tools = _developer_tools() if tools is None else tools
        if not tools:
            python = None  # The system shim would open the Command Line Tools dialog.
    if not python:
        missing.append('python3')
    elif not _cutout_python_ready(python):
        missing.append('Pillow/NumPy/OpenCV в python3')
    if not missing:
        return _item(code, 'Вырезание человека на Mac: python3 с PIL/numpy/cv2 и программа person-cutout доступны (BUG 0013).', 'unchanged')
    return _item(code, 'Вырезание человека на Mac (person-cutout.sh) не работает: нет ' + ', '.join(missing) +
                 '. Нужна отдельная среда Content в новой поставке; патч ничего не ставит (BUG 0013).', 'skipped:needs-content-runtime')
