"""TASK48: conservative repairs of an installed Content engine. Python 3.9+, stdlib only.

BUG 0008  portable contract.py: the neutral theme ``client`` is rejected.
BUG 0009  portable board.py: ``Board`` lacks ``layout``; the PDF export fails.
BUG 0010  portable resources/models/u2net_human_seg.onnx is missing.
BUG 0013  macOS person-cutout.sh: bare python3 without PIL/numpy/cv2, and the Swift
          person-cutout compiled on the learner's Mac (fails without a complete SDK).
          The repair copy carries a reviewed prebuilt universal binary and a fixed
          script; Content/.venv gets pinned, hash-checked wheels (FIX_0584).
OBS 0007/0008  macOS doc.sh / pip.sh use fixed /tmp names shared by every run.

The installer's own version directory ``releases/<v>`` is never written: a
reinstall of the same revision compares those bytes with the package
(setup-tariff-content.py, "Immutable Content release differs" / "Changed
native Content source preserved"). A sibling ``<v>-repairN`` is built instead:
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
import platform
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
# BUG 0013 (FIX_0584). The shipped script compiles person-cutout.swift on the
# learner's Mac; a broken Command Line Tools SDK (IOKit audio/IOAudioDefines.h)
# stops Vision there. The repair copy carries the reviewed prebuilt binary, so
# the script compiles only when no binary exists (an older binary is kept).
CUTOUT_BUILD = b'if [[ ! -x "$BIN" || "$BIN" -ot "$SRC" ]]; then\n  swiftc -O "$SRC" -o "$BIN"\nfi\n'
CUTOUT_BUILD_FIXED = (b'# BUG 0013: the repair copy carries a reviewed prebuilt person-cutout; compile only without it.\n'
                      b'if [[ ! -x "$BIN" ]]; then\n  swiftc -O "$SRC" -o "$BIN"\nfi\n')
CUTOUT_CALL_FIXED = (b'PYTHON=python3\n'
                     b'# BUG 0013: Documents/Content/.venv holds pinned Pillow/NumPy/OpenCV wheels; python3 stays the fallback.\n'
                     b'VENV_PYTHON="$ROOT/../../../../.venv/bin/python"\n'
                     b'if [[ -x "$VENV_PYTHON" ]]; then PYTHON="$VENV_PYTHON"; fi\n'
                     b'"$PYTHON" "$CLEAN" "$TMP_DIR/raw.png" "$2"\n')
CUTOUT_FIX = [(CUTOUT_BUILD, CUTOUT_BUILD_FIXED), (CUTOUT_CALL, CUTOUT_CALL_FIXED)]
# The prebuilt binary is bound to the one person-cutout.swift every released
# revision ships (20261101.1 ... 20261110.1). Built in a clean Tart macOS 15.7.7
# guest, Command Line Tools Swift 6.1.2, `swiftc -O -target {arm64,x86_64}-apple-macos12.0`,
# `lipo -create`, ad-hoc `codesign --force -s - --timestamp=none`; two clean builds
# gave identical bytes.
CUTOUT_SOURCE_SHA256 = 'fe9884fc2061c5d36d597ca1a9cd1ee5efcd973a2340f4eda8a065f2730494b1'
CUTOUT_BINARY_SHA256 = '0ed4bc48a15a71acd8fc6704e53a8e28c6f47afa170f0945924d38334aba2eea'
CUTOUT_BINARY = Path(__file__).resolve().parent / 'bin/person-cutout'
CUTOUT_RELATIVE = 'tools/person-cutout'


class BundleError(RuntimeError):
    """The patch bundle itself is incomplete: never a per-learner skip."""
# Wheels of a learner's verified manual repair (10.11), CPython 3.12, macOS.
# opencv-python-headless 5.0.0.93 needs macOS 13 (arm64) / 14 (Intel).
CUTOUT_PYTHON = '3.12.12'
CUTOUT_REQUIREMENTS = (
    'numpy==2.5.4'
    ' --hash=sha256:b11e8fda06a7d69f15ebf542660b74466c2e51094800c1fb794f47ad4faeef17'
    ' --hash=sha256:9cb18a327b49c5c337f972b03682f6a49855525faaf3c0d3e9c96cd0fd8880a8'
    ' --hash=sha256:c6342f54c67093cae5c0227eb0eb772fdb79f2a2c37a6eb278b9909ee06aa356'
    ' --hash=sha256:aec3fc4b32ff82421274f5d205c559c51c840c8df66a78efd7f3612dd005a26a\n'
    'pillow==13.0.0'
    ' --hash=sha256:fc481f742ccda20e9ed760b8f978dff50f2a4e9bc253ac167fb0a05b0783ef52'
    ' --hash=sha256:67c8d44ad89fa7eb88de5d6ef6199b1e03dd24974f85a67639fc8badb801fe79\n'
    'opencv-python-headless==5.0.0.93'
    ' --hash=sha256:030ca5e0837a2963ab36ef896baa9767eb8d2b83353fb28af5a521e40dd8756f'
    ' --hash=sha256:1e55af3abfb462eeeabe5c775f12bdb36216d8a93a3583d69e6bd6e1d6ba7d00\n'
)
CUTOUT_MIN_MACOS = {'arm64': 13, 'x86_64': 14}

ENGINES = {
    # kind: pointer, releases, fixes (relative to the version directory), link-tolerant
    # subtree (the Content-owned uv venv links its interpreter), subtrees not compared
    # for an existing copy, directories recreated empty instead of linked.
    # ``suffix`` names this patch's copy; ``previous`` names copies of earlier
    # patches, rebuilt from <v> with every fix (a copy is never edited in place).
    # ``optional`` fixes may be absent from a version; ``previous_fixes`` are the
    # fixes an earlier copy holds. ``additions`` are new files of the copy.
    'portable': {'pointer': 'tools/agents-engine/active.json', 'releases': 'tools/agents-engine/releases',
                 'fixes': {'portable/contract.py': ('BUG 0008', CONTRACT_FIX), 'portable/board.py': ('BUG 0009', BOARD_FIX)},
                 'links': 'portable/runtime', 'dynamic': ('portable/runtime', 'portable/resources/models'), 'fresh': (),
                 'suffix': '-repair1', 'previous': (), 'optional': (), 'previous_fixes': (), 'additions': False},
    'mac': {'pointer': 'tools/swift-design-engine/active.json', 'releases': 'tools/swift-design-engine/releases',
            'fixes': {'doc.sh': ('OBS 0008', DOC_FIX), 'pip.sh': ('OBS 0008', PIP_FIX), 'person-cutout.sh': ('BUG 0013', CUTOUT_FIX)},
            # Prebuilt Swift tools overwrite out/* in place; a link there would write into <v>.
            'links': None, 'dynamic': ('out',), 'fresh': ('out',),
            'suffix': '-repair2', 'previous': ('-repair1',), 'optional': ('person-cutout.sh',),
            'previous_fixes': ('doc.sh', 'pip.sh'), 'additions': True},
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


def plan(home, system, *, download=True, opener=None, search_path=None, hermes=None):
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
            return _engine(home, project, 'mac', download, None, search_path, hermes)
        if system == 'Linux':
            return _engine(home, project, 'portable', download, opener or urllib.request.urlopen, None, hermes)
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


def _copy_intact(source, copy, expected, spec, additions=None, skip=()):
    """An existing copy is ours: every engine file of ``source`` is present unchanged
    (same inode or same bytes), every fixed file holds the expected fixed bytes and
    every added file holds exactly the reviewed bytes."""
    try:
        if not copy.is_dir() or copy.is_symlink():
            return False
        for relative, kind in _entries(source, spec['links']):
            if _under(relative, spec['dynamic']) or _derived(relative) or relative in (additions or {}) or relative in skip:
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
        for relative, data in (additions or {}).items():
            mine = copy / relative
            if mine.is_symlink() or not mine.is_file() or mine.read_bytes() != data:
                return False
        return True
    except (OSError, ValueError):
        return False


def _build_copy(source, releases, name, expected, spec, home, additions=None):
    """Hard-link ``source`` into ``releases/<name>``; fixed and added files are new. None or a status."""
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
            if relative in (additions or {}):
                continue  # A learner-built artefact of the reviewed source: the copy gets the reviewed binary.
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
        for relative, data in (additions or {}).items():
            dst = temporary / relative
            if os.path.lexists(str(dst)) or not dst.parent.is_dir():
                raise ValueError('Reviewed fragment not found')
            fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
            with os.fdopen(fd, 'wb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(str(dst), 0o755)
        for relative, data in list(expected.items()) + list((additions or {}).items()):
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


def _prebuilt_cutout():
    """The reviewed universal person-cutout of this bundle. A missing or different
    file is an incomplete bundle: it fails the whole run, never a quiet skip."""
    try:
        data = CUTOUT_BINARY.read_bytes()
    except OSError:
        raise BundleError('Incomplete repair bundle') from None
    if sha(data) != CUTOUT_BINARY_SHA256:
        raise BundleError('Incomplete repair bundle')
    return data


def check_bundle():
    """For agents_repair start-up on every OS: the shipped binary is the reviewed one."""
    _prebuilt_cutout()


def _cutout_additions(source, states, home):
    """New files of the Mac copy: the prebuilt person-cutout for the reviewed Swift
    source and a script the patch owns. A binary the learner's Mac compiled into <v>
    is a build artefact of that same source: the copy gets the reviewed one instead."""
    if 'person-cutout.sh' not in states:
        return {}
    swift = source / 'tools/person-cutout.swift'
    try:
        _plain(swift, home)
        if not swift.is_file() or _file_sha(swift) != CUTOUT_SOURCE_SHA256:
            return {}
    except (ValueError, OSError):
        return {}  # A linked or unreadable source: no binary, the rest of Content is still repaired.
    return {CUTOUT_RELATIVE: _prebuilt_cutout()}


def _engine(home, project, kind, download, opener, search_path, hermes=None):
    spec = ENGINES[kind]
    pointer, releases = project / spec['pointer'], project / spec['releases']
    active, before = _version(pointer, home)
    if active is None:
        return []
    suffix = spec['suffix']
    on_copy = active.endswith(suffix)
    previous = None if on_copy else next((p for p in spec['previous'] if active.endswith(p)), None)
    base = active[:-len(suffix)] if on_copy else active[:-len(previous)] if previous else active
    name = base + suffix
    if not base or not COPY_NAME.fullmatch(name) or not VERSION.fullmatch(base):
        raise ValueError('Invalid Content registration')
    current, copy, origin = releases / active, releases / name, releases / base
    _plain(current, home); _plain(copy, home); _plain(origin, home)
    if not current.is_dir() or (previous and not origin.is_dir()):
        return [_item('content:' + kind + '-repair', 'Активная версия Content отсутствует; нужна штатная установка.', 'skipped:content-incomplete')]
    if kind == 'portable':
        runtime = current / 'portable/runtime/bin/python'
        if not (current / 'portable/cli.py').is_file() or not os.path.lexists(str(runtime)):
            return [_item('content:portable-repair', 'Content установлен не полностью; ремонт после завершения установки.', 'skipped:content-incomplete')]
    # A new copy always links the installer's own version: an earlier patch's copy is replaced, never edited.
    source = origin if previous else current
    states = _file_states(source, spec['fixes'], home)
    kept = []
    for relative in spec['optional']:
        state = states.get(relative, ('missing', None))[0]
        if state in {'missing', 'edited'}:
            states.pop(relative, None)
            if state == 'edited':
                kept.append(relative)  # The learner's own edit is linked as is.
    fixes = {r: spec['fixes'][r] for r in states}
    bugs = ', '.join(sorted({fixes[r][0] for r, (s, _) in states.items() if s == 'applicable'}))
    model = _model_state(current, home) if kind == 'portable' else None
    want_model = model == 'missing' and download
    additions = _cutout_additions(source, states, home) if spec['additions'] else {}
    code = 'content:portable-repair' if kind == 'portable' else 'content:mac-repair'
    label = ('Карусель: тема client (BUG 0008); PDF кадра урока (BUG 0009); модель U2Net (BUG 0010)' if kind == 'portable'
             else 'doc.sh и pip.sh: временные файлы каждого запуска отдельно (OBS 0008); '
                  'вырезание человека: готовая программа person-cutout и Python из Content/.venv (BUG 0013)')
    if kept:
        label += '. Изменённый вручную ' + ', '.join(kept) + ' сохранён как есть'
    items = []
    copy_ok = False
    if on_copy:
        bad = [r for r, (s, _) in states.items() if s != 'fixed']
        # The added binary is part of the copy: a deleted or replaced one is an edited copy.
        for relative, data in additions.items():
            mine = current / relative
            try:
                _plain(mine, home)
                ok = mine.is_file() and _file_sha(mine) == sha(data)
            except (ValueError, OSError):
                ok = False
            if not ok:
                bad.append(relative)
        if bad:
            items.append(_item(code, label + '. Починенная копия ' + name + ' изменена (' + ', '.join(sorted(bad)) + '); не трогается. '
                               'Чтобы починить заново, верните active.json на ' + base + ' и удалите каталог ' + name + '.',
                               'skipped:repair-copy-edited'))
        else:
            copy_ok = True
            items.append(_item(code, label + '. Работает починенная копия ' + name + '.', 'unchanged'))
    elif any(s in {'edited', 'missing', 'linked'} for s, _ in states.values()):
        items.append(_item(code, label + '. Файлы версии изменены вручную или неизвестны; ничего не тронуто.', 'skipped:edited'))
    elif previous and not _copy_intact(origin, current, {r: (fragment_fix(d, fixes[r][1]) if r in spec['previous_fixes'] and s == 'applicable' else d)
                                                         for r, (s, d) in states.items()}, spec, skip=(CUTOUT_RELATIVE,)):
        items.append(_item(code, label + '. Прежняя починенная копия ' + active + ' изменена; ничего не тронуто.', 'skipped:repair-copy-edited'))
    elif not bugs and not want_model and not additions:
        items.append(_item(code, label + '. Уже исправлено.', 'unchanged'))
    else:
        expected = {r: (fragment_fix(d, fixes[r][1]) if s == 'applicable' else d) for r, (s, d) in states.items()}
        if os.path.lexists(str(copy)):
            if _copy_intact(source, copy, expected, spec, additions):
                copy_ok = True

                def switch():
                    if not _copy_intact(source, copy, expected, spec, additions):
                        raise ValueError('Concurrent Content edit preserved')
                    _switch(pointer, home, before, name)
                items.append(_item(code, label + '. Починенная копия ' + name + ' уже есть; переключить active.json на неё.',
                                   'needs-fix', [pointer], switch))
            else:
                items.append(_item(code, label + '. Каталог ' + name + ' отличается от ожидаемой копии; ничего не тронуто. '
                                   'Если он не нужен, удалите его, и повтор ремонта соберёт копию заново.',
                                   'skipped:repair-copy-edited'))
        else:
            try:
                _entries(source, spec['links'])
            except (ValueError, OSError):
                items.append(_item(code, label + '. В версии есть ссылка или особый объект; копия не создаётся.', 'skipped:linked-path'))
            else:
                copy_ok = True

                def build():
                    for relative, (_, data) in states.items():
                        if _read(source / relative, home) != data:
                            raise ValueError('Concurrent Content edit preserved')
                    if _read(pointer, home, 64 * 1024) != before:
                        raise ValueError('Concurrent Content edit preserved')
                    outcome = _build_copy(source, releases, name, expected, spec, home, additions)
                    if outcome:
                        return outcome
                    _switch(pointer, home, before, name)
                items.append(_item(code, label + ': ' + (', '.join(x for x in [bugs, 'BUG 0013' if additions and 'BUG 0013' not in bugs else '']
                                                                  if x) or 'BUG 0010') + '. Создать починенную копию ' + name +
                                   ' (жёсткие ссылки на ' + base + ', исправленные и добавленные файлы новые) и переключить active.json.',
                                   'needs-fix', [pointer], build))
    if kind == 'portable':
        if not on_copy and copy.is_dir():
            model = _model_state(copy, home)  # The model lives in the copy, never in <v>.
        items.append(_model_item(home, current, copy, on_copy, copy_ok, model, download, opener))
    else:
        # The venv only helps a script that reads it: the fixed copy (built now or
        # already active) or a learner's own edit that already points there.
        will_fix = items[0]['status'] == 'needs-fix' and 'person-cutout.sh' in states
        items += [i for i in (_shared_panel_item(home, current),
                              _cutout_item(home, project, current, search_path, hermes, will_fix)) if i is not None]
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


def _cutout_python_ready(python):
    env = {k: v for k, v in os.environ.items() if k.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
    try:
        return subprocess.run([python, '-I', '-B', '-c', 'import PIL, numpy, cv2'], capture_output=True,
                              env=env, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


PM_UV = ("import sys; sys.path.insert(0, ''); from pm import uv_launcher; "
         "path = uv_launcher('uv'); print(path or '')")


def _pm_uv(hermes):
    """Hermes 0.23 PM's verified uv, asked the way setup-tariff-content.py asks it."""
    code, python = hermes / 'hermes-agent', hermes / 'hermes-agent/venv/bin/python'
    if not python.is_file():
        return None
    env = {k: v for k, v in os.environ.items() if k.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
    env['HERMES_HOME'] = str(hermes)
    try:
        result = subprocess.run([str(python), '-B', '-c', PM_UV], cwd=str(code), env=env, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = result.stdout.decode('utf-8', 'replace').strip().splitlines() if result.returncode == 0 else []
    return Path(lines[-1]) if lines and lines[-1] else None


def _uv(home, search_path, hermes=None):
    """uv as Content's own setup finds it: Hermes PM (0.23), the Hermes venv, the
    Hermes bin, ~/.local/bin, then PATH. Executed, never written."""
    hermes = Path(hermes) if hermes else home / '.hermes'
    found = shutil.which('uv', path=search_path if search_path is not None else os.environ.get('PATH', ''))
    candidates = [lambda: _pm_uv(hermes), lambda: hermes / 'hermes-agent/venv/bin/uv', lambda: hermes / 'bin/uv',
                  lambda: home / '.local/bin/uv', lambda: Path(found) if found else None]
    for candidate in candidates:
        path = candidate()
        if path is not None and path.is_file() and os.access(str(path), os.X_OK):
            return str(path)
    return None


def _macos_supported():
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x86_64'}.get(platform.machine().lower())
    major = (platform.mac_ver()[0] or '').split('.')[0]
    return bool(arch) and major.isdigit() and int(major) >= CUTOUT_MIN_MACOS[arch]


STALE_VENV = re.compile(r'\.venv\.tmp-[0-9a-f]{12}')
NETWORK_HINTS = ('failed to download', 'error sending request', 'dns error', 'failed to fetch', 'connection',
                 'timed out', 'could not connect', 'network', 'tls', 'certificate')
WHEEL_HINTS = ('no wheels with a matching platform tag', "doesn't have a source distribution or wheel",
               'no solution found', 'not supported on this platform', 'no matching distribution')


def _uv_outcome(result):
    text = (result.stderr or b'').decode('utf-8', 'replace').lower()
    if any(h in text for h in WHEEL_HINTS):
        return 'unavailable:no-compatible-wheel'
    if any(h in text for h in NETWORK_HINTS):
        return 'unavailable:network'
    return 'unavailable:cutout-runtime'


def create_cutout_venv(project, home, uv):
    """Content/.venv from CPython 3.12 and hash-pinned wheels; built aside, then renamed.

    Returns None when the environment is in place, or an ``unavailable:*`` status
    with nothing left behind (offline, no wheel for this macOS, failed runtime)."""
    venv = project / '.venv'
    temporary = project / ('.venv.tmp-' + uuid.uuid4().hex[:12])
    _plain(venv, home); _plain(temporary, home)
    if os.path.lexists(str(venv)):
        raise ValueError('Concurrent Content edit preserved')
    for stale in project.iterdir():
        # Only this function's own leftovers of an interrupted run (power loss, kill).
        if STALE_VENV.fullmatch(stale.name) and stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(str(stale), True)
    env = {k: v for k, v in os.environ.items() if k.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'}}
    store = home / '.local/share/infobiz-content/python'  # The store Linux Content already uses.
    _plain(store, home)
    env.update(UV_PYTHON_INSTALL_DIR=str(store), UV_NO_CONFIG='1')
    work = Path(tempfile.mkdtemp(prefix='content-cutout-'))
    try:
        requirements = work / 'requirements.txt'
        requirements.write_text(CUTOUT_REQUIREMENTS, encoding='utf-8')
        # --relocatable: activate and entry-point scripts use relative paths, so the
        # environment built aside keeps working under its final name.
        steps = [[uv, 'venv', '--python', CUTOUT_PYTHON, '--managed-python', '--relocatable', str(temporary)],
                 [uv, 'pip', 'install', '--python', str(temporary / 'bin/python'), '--require-hashes',
                  '--only-binary', ':all:', '--no-deps', '-r', str(requirements)]]
        for args in steps:
            try:
                result = subprocess.run(args, cwd=str(work), env=env, capture_output=True, timeout=1800)
            except subprocess.TimeoutExpired:
                return 'unavailable:network'
            except OSError:
                return 'unavailable:cutout-runtime'
            if result.returncode:
                return _uv_outcome(result)
        if not _cutout_python_ready(str(temporary / 'bin/python')):
            return 'unavailable:cutout-runtime'
        _plain(venv, home)
        if os.path.lexists(str(venv)):
            raise ValueError('Concurrent Content edit preserved')
        os.rename(str(temporary), str(venv))
        if not _cutout_python_ready(str(venv / 'bin/python')):
            shutil.rmtree(str(venv))
            return 'unavailable:cutout-runtime'
        return None
    finally:
        shutil.rmtree(str(work), True)
        if os.path.lexists(str(temporary)) and not temporary.is_symlink():
            shutil.rmtree(str(temporary), True)


def _active_script_uses_venv(home, project):
    """Does the person-cutout.sh of the version active.json names right now read Content/.venv?"""
    try:
        version, _ = _version(project / ENGINES['mac']['pointer'], home)
        if version is None:
            return False
        script = project / ENGINES['mac']['releases'] / version / 'person-cutout.sh'
        _plain(script, home)
        return script.is_file() and b'.venv' in _read(script, home)
    except (ValueError, OSError):
        return False


def _cutout_item(home, project, engine, search_path, hermes=None, will_fix=False):
    """BUG 0013: the Python half of person-cutout.sh (clean-cutout.py needs PIL/numpy/cv2)."""
    script = engine / 'person-cutout.sh'
    try:
        _plain(script, home)
        if not script.is_file():
            return None
        data = _read(script, home)
        venv = project / '.venv'
        _plain(venv, home)
    except ValueError:
        return None
    if CUTOUT_CALL not in data and b'.venv' not in data:
        return None
    code = 'content:mac-cutout-python'
    detail = 'Вырезание человека на Mac: Pillow/NumPy/OpenCV в Documents/Content/.venv (CPython ' + CUTOUT_PYTHON + ', закреплённые колёса, BUG 0013).'
    if os.path.lexists(str(venv)):
        if venv.is_dir() and not venv.is_symlink() and _cutout_python_ready(str(venv / 'bin/python')):
            return _item(code, detail + ' Среда на месте.', 'unchanged')
        return _item(code, detail + ' Content/.venv уже есть, но без нужных библиотек; не трогается '
                     '(удалите эту папку, и повтор ремонта создаст среду заново).', 'skipped:venv-preserved')
    if not will_fix and b'.venv' not in data:
        return _item(code, detail + ' Активный person-cutout.sh не починен (копия Content не создана) и эту среду не читает; '
                     'она не ставится.', 'skipped:script-not-repaired')
    if not _macos_supported():
        return _item(code, detail + ' Для этих колёс нужна macOS 13 (Apple Silicon) или 14 (Intel); ничего не ставится.',
                     'skipped:macos-too-old')
    uv = _uv(home, search_path, hermes)
    if not uv:
        return _item(code, detail + ' Нет uv из установки Hermes; нужна штатная установка.', 'skipped:uv-missing')

    def apply():
        # Runs after the copy item: the script now active must be the one that reads the venv.
        if not _active_script_uses_venv(home, project):
            return 'skipped:script-not-repaired'
        return create_cutout_venv(project, home, uv)
    return _item(code, detail + ' Создать среду (сеть: PyPI и сборка CPython от Astral через uv).', 'needs-fix', [], apply)
