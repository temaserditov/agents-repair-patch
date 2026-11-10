"""Offline regression fixtures for vendor/content-repairs.py (TASK48: BUG 0008-0010, 0013, OBS 0007/0008).

Fixtures are built from the real installed bytes of the 20261108.1 tier-3
package, read from the tarball in the repository. No network, real services
or user accounts. The installer's version directory must stay byte-identical,
so a reinstall of the same revision (setup-tariff-content.py) still passes.
"""
import ast
import atexit
import errno
from functools import lru_cache
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
import urllib.error
from unittest.mock import patch
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT/'installer/repair-patch'
TARBALL = ROOT/'apps/lms/installer-assets/agents/agents-tier-3-agents-release-20261108.1.tar.gz'
SETUP = ROOT/'installer/packages/shared/setup-tariff-content.py'
VERSION = 'agents-release-20261108.1'
COPY = VERSION+'-repair1'
MAC_COPY = VERSION+'-repair2'
# SHA256 of the owner's verified address-level repair (repair-student-content-case.py) of the same bytes.
OWNER_CONTRACT = 'e5c9ddb95b33edf86781ed1f87885f7b5c2ff582fa088ea90d1b629bcae05826'
OWNER_BOARD = '3b2e132d2e5020ffb17a6d71acf2e82d9c1ebdbe46b7c5e086bf9704f8b6079e'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result

content = module('repair_content_repairs', BUNDLE/'vendor/content-repairs.py')


@lru_cache(maxsize=1)
def package():
    """profile/content of the real tier package, extracted once (regular files only)."""
    target = Path(tempfile.mkdtemp(prefix='content-package-'))
    atexit.register(shutil.rmtree, str(target), True)
    with tarfile.open(TARBALL) as archive:
        for member in archive.getmembers():
            name = member.name
            if not member.isfile() or not name.startswith('profile/content/') or '..' in name.split('/'):
                continue
            path = target/name[len('profile/content/'):]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
            path.chmod(0o755 if member.mode & 0o111 else 0o644)
    return target


def real(name):
    return (package()/name).read_bytes()


def write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(mode)


def tree(root):
    return {p.relative_to(root).as_posix(): ('link:'+os.readlink(p) if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob('*')) if p.is_file() or p.is_symlink()}


def by_code(items):
    return {i['code']: i for i in items}


def apply_all(items):
    return {i['code']: i['apply']() for i in items if i['status'] == 'needs-fix'}


def native_template(relative):
    return relative.suffix == '.json' and relative.parts[0].startswith('layouts-')


def reinstall_check(source, project, version, system):
    """setup-tariff-content.py lines 138-147 and 186-193: the byte comparison a reinstall
    of the same revision performs on the installer's version directories."""
    release = project/'tools/agents-engine/releases'/version
    for path in sorted(source.rglob('*')):
        rel = path.relative_to(source)
        if not path.is_file() or rel.parts[0] not in {'swift', 'portable'}:
            continue
        target = release/rel
        if target.exists() and target.read_bytes() != path.read_bytes():
            if system == 'Darwin' and rel.parts[0] == 'swift' and native_template(Path(*rel.parts[1:])):
                continue
            raise ValueError('Immutable Content release differs')
    if system == 'Darwin':
        target = project/'tools/swift-design-engine/releases'/version
        for incoming in sorted((source/'swift').rglob('*')):
            if not incoming.is_file():
                continue
            destination = target/incoming.relative_to(source/'swift')
            if destination.exists() and destination.read_bytes() != incoming.read_bytes():
                if native_template(incoming.relative_to(source/'swift')):
                    continue
                raise ValueError('Changed native Content source preserved')


def reinstall_pointer(project, store, version=VERSION):
    """What the installer's atomic(...active.json) writes on a reinstall."""
    (project/store/'active.json').write_text(json.dumps({'version': version})+'\n')


def install_release(release):
    for part in ['portable', 'swift']:
        shutil.copytree(str(package()/part), str(release/part))


def linux_fixture(root):
    home = root/'home'
    project = home/'Documents/Content'
    release = project/'tools/agents-engine/releases'/VERSION
    install_release(release)
    interpreter = root/'uv-python/bin/python3.12'; write(interpreter, b'#!/bin/sh\n', 0o755)
    runtime = release/'portable/runtime'
    (runtime/'bin').mkdir(parents=True)
    os.symlink(str(interpreter), str(runtime/'bin/python'))   # uv venv: absolute interpreter link
    os.symlink('python', str(runtime/'bin/python3'))
    write(runtime/'pyvenv.cfg', 'home = '+str(interpreter.parent)+'\n')
    write(runtime/'lib/python3.12/site-packages/skia.py', b'# runtime\n')
    write(release/'portable/resources/models/sherpa-onnx-whisper-tiny/tiny-tokens.txt', b'tokens\n')
    write(project/'tools/agents-engine/active.json', json.dumps({'version': VERSION})+'\n')
    return home, project, release


def mac_fixture(root):
    home = root/'home'
    project = home/'Documents/Content'
    install_release(project/'tools/agents-engine/releases'/VERSION)
    write(project/'tools/agents-engine/active.json', json.dumps({'version': VERSION})+'\n')
    engine = project/'tools/swift-design-engine/releases'/VERSION
    shutil.copytree(str(package()/'swift'), str(engine))
    write(engine/'out/spk_overlay.png', b'generated earlier')
    write(project/'tools/swift-design-engine/active.json', json.dumps({'version': VERSION})+'\n')
    return home, project, engine


class Response(io.BytesIO):
    pass


class FakeNet:
    """URL opener double: answers per URL; records requests."""
    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requests.append((url, dict(request.header_items())))
        answer = self.answers.get(url, urllib.error.URLError('offline'))
        if isinstance(answer, Exception):
            raise answer
        return Response(answer)


API_TAG = content.GITHUB_API+'repos/danielgatis/rembg/releases/tags/v0.0.0'
API_ASSET = content.GITHUB_API+'repos/danielgatis/rembg/releases/assets/85857916'


def release_json(url=API_ASSET, name=content.MODEL_NAME):
    return json.dumps({'assets': [{'name': 'u2net.onnx', 'url': content.GITHUB_API+'repos/danielgatis/rembg/releases/assets/1'},
                                  {'name': name, 'url': url}]}).encode()


class InstallerContract(unittest.TestCase):
    def test_equivalent_check_matches_the_installer_source(self):
        source = SETUP.read_text(encoding='utf-8')
        for line in ["if not path.is_file() or rel.parts[0] not in {'swift','portable'}:continue",
                     'if target.exists() and target.read_bytes()!=path.read_bytes():',
                     "raise ValueError('Immutable Content release differs')",
                     'if destination.exists() and destination.read_bytes()!=incoming.read_bytes():',
                     "raise ValueError('Changed native Content source preserved')",
                     "atomic(project/'tools/agents-engine/active.json',json.dumps({'version':version})+'\\n')",
                     "atomic(store/'active.json',json.dumps({'version':version})+'\\n')"]:
            self.assertIn(line, source)
        wrapper = real('content-engine.py').decode()
        self.assertIn('version = json.loads((STORE / "active.json").read_text())["version"]', wrapper)
        self.assertIn('engine = STORE / "releases" / version', wrapper)
        self.assertTrue(content.COPY_NAME.fullmatch(COPY))

    def test_in_place_edit_would_break_a_reinstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project, release = linux_fixture(Path(tmp))
            reinstall_check(package(), project, VERSION, 'Linux')
            write(release/'portable/contract.py', real('portable/contract.py')+b'#\n')
            with self.assertRaises(ValueError):
                reinstall_check(package(), project, VERSION, 'Linux')


class LinuxRepairCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home, self.project, self.release = linux_fixture(Path(self.tmp.name))
        self.copy = self.release.with_name(COPY)
        self.pointer = self.project/'tools/agents-engine/active.json'

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, **kwargs):
        return by_code(content.plan(self.home, 'Linux', download=kwargs.pop('download', False), opener=kwargs.pop('opener', FakeNet({}))))

    def test_copy_built_installer_version_untouched_reinstall_passes_and_switch_back(self):
        before = tree(self.home)
        items = self.plan()
        self.assertEqual(tree(self.home), before, 'plan() must not write')
        item = items['content:portable-repair']
        self.assertEqual(item['status'], 'needs-fix')
        self.assertEqual(item['paths'], [self.pointer])
        self.assertEqual(items['content:u2net-model']['status'], 'skipped:download-disabled')
        original = tree(self.release)
        self.assertIsNone(item['apply']())
        self.assertEqual(tree(self.release), original, 'installer version directory byte-identical')
        self.assertEqual(json.loads(self.pointer.read_text()), {'version': COPY})
        self.assertEqual(self.pointer.read_text(), json.dumps({'version': COPY})+'\n')
        portable = self.copy/'portable'
        self.assertEqual(hashlib.sha256((portable/'contract.py').read_bytes()).hexdigest(), OWNER_CONTRACT)
        self.assertEqual(hashlib.sha256((portable/'board.py').read_bytes()).hexdigest(), OWNER_BOARD)
        self.assertEqual((portable/'cli.py').stat().st_ino, (self.release/'portable/cli.py').stat().st_ino, 'unchanged files are hard links')
        self.assertNotEqual((portable/'contract.py').stat().st_ino, (self.release/'portable/contract.py').stat().st_ino)
        self.assertEqual(os.readlink(str(portable/'runtime/bin/python')), os.readlink(str(self.release/'portable/runtime/bin/python')))
        self.assertEqual(sorted(p.name for p in self.release.parent.iterdir()), [VERSION, COPY], 'no temporary trees left')
        self.assertTrue(all(i['status'] == 'unchanged' for i in self.plan().values() if i['code'] != 'content:u2net-model'))
        # A reinstall of the same revision compares <v> with the package, then re-points active.json.
        reinstall_check(package(), self.project, VERSION, 'Linux')
        reinstall_pointer(self.project, 'tools/agents-engine')
        again = self.plan()['content:portable-repair']
        self.assertEqual(again['status'], 'needs-fix'); self.assertIn('уже есть', again['detail'])
        self.assertIsNone(again['apply']())
        self.assertEqual(sorted(p.name for p in self.release.parent.iterdir()), [VERSION, COPY], 'copy reused, not rebuilt')
        self.assertEqual(json.loads(self.pointer.read_text()), {'version': COPY})
        self.assertEqual(self.plan()['content:portable-repair']['status'], 'unchanged')

    def run_contract(self, path, theme):
        code = textwrap.dedent('''
            import importlib.util, sys
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("student_contract", sys.argv[1])
            contract = importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)
            data = {"мета": {"этап": "готово к сборке"},
                    "слайды": [{"layout": "statement", "текст": "ТЕСТ", "подпись": "Проверка"} for _ in range(5)]}
            if sys.argv[2] != "-": data["стиль"] = sys.argv[2]
            try:
                print(len(contract.normalize(data, Path("unused"))))
            except contract.ContractError:
                print("rejected")
        ''')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(path), theme], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_repaired_contract_and_board(self):
        self.assertEqual(self.run_contract(self.release/'portable/contract.py', 'client'), 'rejected', 'defect reproduced')
        self.plan()['content:portable-repair']['apply']()
        contract = self.copy/'portable/contract.py'
        self.assertEqual(self.run_contract(contract, 'client'), '5')
        self.assertEqual(self.run_contract(contract, '-'), '5', 'default theme is client')
        self.assertEqual(self.run_contract(contract, 'unexpected-theme'), 'rejected')
        self.assertEqual(self.run_contract(contract, 'night'), 'rejected', 'owner themes are not shipped')
        board = next(n for n in ast.parse((self.copy/'portable/board.py').read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'Board')
        init = next(n for n in board.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        names = [n.targets[0].attr for n in init.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Attribute)]
        self.assertLess(names.index('r'), names.index('layout'))

    def test_edited_copy_is_preserved(self):
        self.plan()['content:portable-repair']['apply']()
        write(self.copy/'portable/contract.py', b'# learner\n')
        self.assertEqual(self.plan()['content:portable-repair']['status'], 'skipped:repair-copy-edited')
        reinstall_pointer(self.project, 'tools/agents-engine')
        write(self.copy/'portable/contract.py', content.fragment_fix(real('portable/contract.py'), content.CONTRACT_FIX))
        self.assertEqual(self.plan()['content:portable-repair']['status'], 'needs-fix', 'restored copy is reusable')
        (self.copy/'portable/cli.py').unlink(); write(self.copy/'portable/cli.py', b'# learner cli\n')
        snapshot = tree(self.home)
        item = self.plan()['content:portable-repair']
        self.assertEqual(item['status'], 'skipped:repair-copy-edited')
        self.assertIsNone(item['apply'])
        self.assertEqual(tree(self.home), snapshot)

    def test_edited_installer_file_link_and_concurrent_pointer(self):
        write(self.release/'portable/contract.py', real('portable/contract.py').replace(b'"navy", "night",', b'"navy", "night", "mine",'))
        self.assertEqual(self.plan()['content:portable-repair']['status'], 'skipped:edited')
        write(self.release/'portable/contract.py', real('portable/contract.py'))
        os.symlink('/etc/hosts', str(self.release/'portable/resources/hosts'))
        self.assertEqual(self.plan()['content:portable-repair']['status'], 'skipped:linked-path')
        (self.release/'portable/resources/hosts').unlink()
        item = self.plan()['content:portable-repair']
        write(self.pointer, json.dumps({'version': 'other'})+'\n')
        with self.assertRaises(ValueError):
            item['apply']()
        self.assertFalse(self.copy.exists())

    def test_hardlinks_unavailable_writes_nothing(self):
        snapshot = tree(self.home)
        with patch.object(content.os, 'link', side_effect=OSError(errno.EXDEV, 'cross-device')):
            self.assertEqual(self.plan()['content:portable-repair']['apply'](), 'skipped:hardlink-unavailable')
        self.assertEqual(tree(self.home), snapshot)
        self.assertEqual(sorted(p.name for p in self.release.parent.iterdir()), [VERSION])

    def test_incomplete_and_missing_content(self):
        (self.release/'portable/runtime/bin/python').unlink()
        self.assertEqual([i['status'] for i in content.plan(self.home, 'Linux')], ['skipped:content-incomplete'])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(content.plan(Path(tmp), 'Linux'), [])
            write(Path(tmp)/'Documents/Content/tools/agents-engine/active.json', '{"version": "../x"}')
            self.assertEqual([i['status'] for i in content.plan(Path(tmp), 'Linux')], ['skipped:invalid-registration'])

    # --- U2Net into the copy ---
    def model_items(self, net):
        items = content.plan(self.home, 'Linux', opener=net)
        self.assertEqual(net.requests, [], 'plan() makes no network request')
        return items

    def test_offline_model_is_unavailable_copy_still_fixed(self):
        items = by_code(self.model_items(FakeNet({})))
        self.assertEqual(items['content:u2net-model']['paths'], [])
        results = apply_all(items.values())
        self.assertEqual(results, {'content:portable-repair': None, 'content:u2net-model': 'unavailable:network'})
        self.assertEqual(sorted(p.name for p in (self.copy/'portable/resources/models').iterdir()), ['sherpa-onnx-whisper-tiny'])

    def test_checksum_mismatch_writes_nothing(self):
        net = FakeNet({content.MODEL_URL: b'<html>captive portal</html>', API_TAG: release_json(), API_ASSET: b'x'*1000})
        self.assertEqual(apply_all(self.model_items(net))['content:u2net-model'], 'unavailable:checksum')
        self.assertFalse((self.copy/'portable/resources/models'/content.MODEL_NAME).exists())

    def test_api_fallback_after_web_504_into_copy_and_idempotent(self):
        weights = b'onnx-test-weights'*64
        net = FakeNet({content.MODEL_URL: urllib.error.HTTPError(content.MODEL_URL, 504, 'Gateway Timeout', {}, None),
                       API_TAG: release_json(), API_ASSET: weights})
        original = tree(self.release)
        with patch.object(content, 'MODEL_SHA256', hashlib.sha256(weights).hexdigest()), patch.object(content, 'MODEL_SIZE', len(weights)):
            self.assertEqual(apply_all(self.model_items(net)), {'content:portable-repair': None, 'content:u2net-model': None})
            self.assertEqual((self.copy/'portable/resources/models'/content.MODEL_NAME).read_bytes(), weights)
            self.assertEqual(dict(net.requests)[API_ASSET].get('Accept'), 'application/octet-stream')
            self.assertEqual({i['status'] for i in content.plan(self.home, 'Linux', opener=FakeNet({}))}, {'unchanged'})
            reinstall_pointer(self.project, 'tools/agents-engine')
            again = by_code(content.plan(self.home, 'Linux', opener=FakeNet({})))
            self.assertEqual(again['content:portable-repair']['status'], 'needs-fix')
            self.assertEqual(again['content:u2net-model']['status'], 'unchanged', 'model found in the copy')
        self.assertEqual(tree(self.release), original)
        reinstall_check(package(), self.project, VERSION, 'Linux')

    def test_unexpected_asset_url_is_not_followed(self):
        net = FakeNet({API_TAG: release_json(url='https://example.invalid/u2net_human_seg.onnx')})
        self.assertEqual(apply_all(self.model_items(net))['content:u2net-model'], 'unavailable:network')
        self.assertNotIn('https://example.invalid/u2net_human_seg.onnx', [u for u, _ in net.requests])

    def test_unknown_binding_or_different_model_preserved(self):
        write(self.release/'portable/resources/models'/content.MODEL_NAME, b'learner model')
        self.assertEqual(self.plan(download=True)['content:u2net-model']['status'], 'skipped:model-differs')
        (self.release/'portable/resources/models'/content.MODEL_NAME).unlink()
        write(self.release/'portable/cutout.py', real('portable/cutout.py').replace(content.MODEL_SHA256.encode(), b'0'*64))
        self.assertEqual(self.plan(download=True)['content:u2net-model']['status'], 'skipped:unknown-cutout-binding')


class WindowsDiagnosis(unittest.TestCase):
    def test_windows_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project, release = linux_fixture(Path(tmp))
            snapshot = tree(home)
            items = by_code(content.plan(home, 'Windows'))
            self.assertEqual({c: i['status'] for c, i in items.items()},
                             {'content:client-theme': 'skipped:windows-diagnostic-only', 'content:board-layout': 'skipped:windows-diagnostic-only',
                              'content:u2net-model': 'skipped:windows-diagnostic-only'})
            self.assertTrue(all(i['apply'] is None and 'BUG 0020' in i['detail'] for i in items.values()))
            self.assertEqual(tree(home), snapshot)


STUB_BOARD = '#!/bin/bash\nset -e\npython3 -c \'import json,sys;c=json.load(open(sys.argv[1]));open(c["out"]+".pdf","w").write(c["card"]["page"]+":"+c["theme"])\' "$1"\necho "$1" >> "$LOG"\n'
STUB_JOIN = '#!/bin/bash\nout="$1"; shift; cat "$@" > "$out"\n'
# ffmpeg double: concat list entries must resolve relative to the list file, as the real demuxer does.
STUB_FFMPEG = r'''#!/bin/bash
echo "$*" >> "$LOG"
for a; do last="$a"; done
prev=""
for a; do
  if [ "$prev" = "-i" ] && [[ "$a" == *.txt ]]; then
    dir="$(dirname "$a")"
    while read -r _ f; do f="${f#\'}"; f="${f%\'}"; [ -f "$dir/$f" ] || { echo "missing concat entry $f" >&2; exit 9; }; done < "$a"
  fi
  prev="$a"
done
echo data > "$last"
'''


class MacRepairCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home, self.project, self.engine = mac_fixture(self.root)
        self.copy = self.engine.with_name(MAC_COPY)
        self.pointer = self.project/'tools/swift-design-engine/active.json'
        self.uv = self.root/'hermes-uv'

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, ready=False, uv=True, supported=True):
        with patch.object(content, '_cutout_python_ready', return_value=ready), \
                patch.object(content, '_uv', return_value=str(self.uv) if uv else None), \
                patch.object(content, '_macos_supported', return_value=supported):
            return by_code(content.plan(self.home, 'Darwin', search_path=''))

    def test_copy_isolates_tmp_reinstall_passes_and_statuses(self):
        before = tree(self.home)
        items = self.plan()
        self.assertEqual(tree(self.home), before)
        self.assertEqual(items['content:mac-repair']['status'], 'needs-fix')
        self.assertEqual(items['content:mac-repair']['paths'], [self.pointer])
        self.assertEqual(items['content:mac-lesson-tmp']['status'], 'skipped:needs-native-build')
        self.assertEqual(items['content:mac-cutout-python']['status'], 'needs-fix')
        self.assertIn('BUG 0013', items['content:mac-repair']['detail'])
        self.assertIsNone(items['content:mac-repair']['apply']())
        self.assertEqual(tree(self.engine), {k[len('Documents/Content/tools/swift-design-engine/releases/'+VERSION+'/'):]: v for k, v in before.items()
                                             if k.startswith('Documents/Content/tools/swift-design-engine/releases/'+VERSION+'/')})
        self.assertEqual(self.pointer.read_text(), json.dumps({'version': MAC_COPY})+'\n')
        for name in ['doc.sh', 'pip.sh', 'person-cutout.sh']:
            path = self.copy/name
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual(subprocess.run(['bash', '-n', str(path)]).returncode, 0)
        self.assertNotIn(b'/tmp/doc-page', (self.copy/'doc.sh').read_bytes()); self.assertNotIn(b'/tmp/pip-', (self.copy/'pip.sh').read_bytes())
        self.assertEqual((self.copy/'board.swift').stat().st_ino, (self.engine/'board.swift').stat().st_ino)
        binary = self.copy/'tools/person-cutout'
        self.assertEqual(binary.read_bytes(), (BUNDLE/'vendor/bin/person-cutout').read_bytes())
        self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.engine/'tools/person-cutout').exists(), 'the installer version gets no binary')
        script = (self.copy/'person-cutout.sh').read_bytes()
        self.assertIn(b'if [[ ! -x "$BIN" ]]; then', script); self.assertNotIn(b'-ot "$SRC"', script)
        self.assertIn(b'.venv/bin/python', script)
        self.assertEqual(list((self.copy/'out').iterdir()), [], 'generated output is not linked into the copy')
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')
        reinstall_check(package(), self.project, VERSION, 'Darwin')
        reinstall_pointer(self.project, 'tools/swift-design-engine')
        again = self.plan()['content:mac-repair']
        self.assertIn('уже есть', again['detail'])
        again['apply']()
        self.assertEqual(sorted(p.name for p in self.engine.parent.iterdir()), [VERSION, MAC_COPY])
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')

    def test_cutout_venv_statuses(self):
        self.assertEqual(self.plan(uv=False)['content:mac-cutout-python']['status'], 'skipped:uv-missing')
        self.assertEqual(self.plan(supported=False)['content:mac-cutout-python']['status'], 'skipped:macos-too-old')
        (self.project/'.venv/bin').mkdir(parents=True)
        self.assertEqual(self.plan()['content:mac-cutout-python']['status'], 'skipped:venv-preserved')
        self.assertEqual(self.plan(ready=True)['content:mac-cutout-python']['status'], 'unchanged')

    def fake_uv(self, venv_exit=0, pip_exit=0, stderr=''):
        """uv double: `venv` makes bin/python (a shell that answers the import probe), `pip` records its argv."""
        log = self.root/'uv.log'
        write(self.uv, textwrap.dedent(f'''\
            #!/bin/bash
            echo "$@" >> {log}
            if [ "$1" = venv ]; then
              [ {venv_exit} = 0 ] || {{ echo {stderr!r} >&2; exit {venv_exit}; }}
              d="${{@: -1}}"; mkdir -p "$d/bin"; printf '#!/bin/sh\\nexit 0\\n' > "$d/bin/python"; chmod 755 "$d/bin/python"; exit 0
            fi
            for a in "$@"; do case "$a" in *requirements.txt) cp "$a" {self.root/'requirements.seen'};; esac; done
            [ {pip_exit} = 0 ] || {{ echo {stderr!r} >&2; exit {pip_exit}; }}
            '''), 0o755)
        return log

    def cutout_item(self, repaired=True):
        if repaired and not self.copy.exists():
            self.plan()['content:mac-repair']['apply']()   # agents_repair applies the copy first
        with patch.object(content, '_uv', return_value=str(self.uv)), patch.object(content, '_macos_supported', return_value=True):
            return by_code(content.plan(self.home, 'Darwin', search_path=''))['content:mac-cutout-python']

    def test_cutout_venv_is_built_aside_from_pinned_hashes_and_repeat_is_unchanged(self):
        log = self.fake_uv()
        item = self.cutout_item()
        self.assertEqual(item['status'], 'needs-fix'); self.assertEqual(item['paths'], [])
        self.assertIsNone(item['apply']())
        self.assertTrue((self.project/'.venv/bin/python').is_file())
        self.assertEqual([p.name for p in self.project.iterdir() if p.name.startswith('.venv.tmp-')], [])
        calls = log.read_text().splitlines()
        self.assertTrue(calls[0].startswith('venv --python '+content.CUTOUT_PYTHON+' --managed-python --relocatable '+str(self.project)+'/.venv.tmp-'))
        self.assertIn('--require-hashes', calls[1]); self.assertIn('--only-binary :all:', calls[1]); self.assertIn('--no-deps', calls[1])
        seen = (self.root/'requirements.seen').read_text()
        self.assertEqual(seen, content.CUTOUT_REQUIREMENTS)
        for line in seen.splitlines():
            self.assertRegex(line, r'^[a-z-]+==[0-9.]+( --hash=sha256:[0-9a-f]{64})+$')
        self.assertEqual(self.cutout_item()['status'], 'unchanged')

    def test_cutout_venv_offline_or_without_wheel_leaves_nothing(self):
        for kwargs, expected in [({'venv_exit': 2, 'stderr': 'error: Failed to download https://github.com/... error sending request'}, 'unavailable:network'),
                                 ({'pip_exit': 1, 'stderr': "Because opencv-python-headless==5.0.0.93 has no wheels with a matching platform tag"}, 'unavailable:no-compatible-wheel'),
                                 ({'pip_exit': 1, 'stderr': 'boom'}, 'unavailable:cutout-runtime')]:
            with self.subTest(expected=expected):
                self.fake_uv(**kwargs)
                item = self.cutout_item()
                before = tree(self.home)
                self.assertEqual(item['apply'](), expected)
                self.assertEqual(tree(self.home), before)
                self.assertFalse(os.path.lexists(str(self.project/'.venv')))
                self.assertEqual([p.name for p in self.project.iterdir() if p.name.startswith('.venv')], [])

    def run_cutout(self, venv=True):
        """The repaired person-cutout.sh with stand-ins for Vision and clean-cutout.py; swiftc must not run."""
        self.plan()['content:mac-repair']['apply']()
        stubs = self.root/'stubs'; calls = self.root/'cutout.log'
        write(stubs/'swiftc', '#!/bin/sh\necho swiftc >> '+str(calls)+'\nexit 9\n', 0o755)
        write(stubs/'python3', '#!/bin/sh\necho system-python >> '+str(calls)+'\ncp "$2" "$3"\n', 0o755)
        if venv:
            write(self.project/'.venv/bin/python', '#!/bin/sh\necho venv-python >> '+str(calls)+'\ncp "$2" "$3"\n', 0o755)
        binary = self.copy/'tools/person-cutout'
        binary.unlink(); write(binary, '#!/bin/sh\necho vision >> '+str(calls)+'\ncp "$1" "$2"\n', 0o755)
        write(self.root/'in.png', b'png')
        env = {**os.environ, 'PATH': str(stubs)+':/usr/bin:/bin', 'TMPDIR': str(self.root)}
        result = subprocess.run(['bash', str(self.copy/'person-cutout.sh'), str(self.root/'in.png'), str(self.root/'out.png')],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'out.png').read_bytes(), b'png')
        return calls.read_text().split()

    def test_repaired_script_prefers_content_venv_and_never_compiles(self):
        self.assertEqual(self.run_cutout(venv=True), ['vision', 'venv-python'])

    def test_repaired_script_falls_back_to_python3_without_venv(self):
        self.assertEqual(self.run_cutout(venv=False), ['vision', 'system-python'])

    def test_learner_built_binary_is_replaced_in_the_copy_and_edited_script_is_kept(self):
        write(self.engine/'tools/person-cutout', b'#!/bin/sh\n', 0o755)   # swiftc on the learner's own Mac
        self.assertIsNone(self.plan()['content:mac-repair']['apply']())
        self.assertEqual((self.copy/'tools/person-cutout').read_bytes(), (BUNDLE/'vendor/bin/person-cutout').read_bytes())
        self.assertEqual((self.engine/'tools/person-cutout').read_bytes(), b'#!/bin/sh\n', '<v> keeps its own artefact')
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')
        self.tearDown(); self.setUp()
        edited = (self.engine/'person-cutout.sh').read_bytes().replace(content.CUTOUT_CALL, b'"$ROOT/../../../../.venv/bin/python" "$CLEAN" "$TMP_DIR/raw.png" "$2"\n')
        write(self.engine/'person-cutout.sh', edited, 0o755)
        item = self.plan()['content:mac-repair']
        self.assertEqual(item['status'], 'needs-fix'); self.assertIn('сохранён как есть', item['detail'])
        self.assertIsNone(item['apply']())
        self.assertEqual((self.copy/'person-cutout.sh').stat().st_ino, (self.engine/'person-cutout.sh').stat().st_ino)
        self.assertNotIn(b'.venv', (self.copy/'doc.sh').read_bytes()); self.assertNotIn(b'/tmp/doc-page', (self.copy/'doc.sh').read_bytes())
        self.assertFalse((self.copy/'tools/person-cutout').exists(), 'no binary for a script the patch does not own')
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')

    def test_reinstall_then_self_built_binary_switches_back_to_the_copy(self):
        self.plan()['content:mac-repair']['apply']()
        reinstall_pointer(self.project, 'tools/swift-design-engine')
        write(self.engine/'tools/person-cutout', b'#!/bin/sh\n# swiftc output\n', 0o755)
        item = self.plan()['content:mac-repair']
        self.assertEqual(item['status'], 'needs-fix'); self.assertIn('уже есть', item['detail'])
        self.assertIsNone(item['apply']())
        self.assertEqual(json.loads(self.pointer.read_text()), {'version': MAC_COPY})
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')

    def test_deleted_or_replaced_binary_in_the_active_copy_is_an_edited_copy(self):
        self.plan()['content:mac-repair']['apply']()
        binary = self.copy/'tools/person-cutout'
        for change in ['replace', 'delete']:
            with self.subTest(change=change):
                binary.unlink(missing_ok=True) if sys.version_info >= (3, 8) else None
                if change == 'replace':
                    write(binary, b'#!/bin/sh\n', 0o755)
                item = self.plan()['content:mac-repair']
                self.assertEqual(item['status'], 'skipped:repair-copy-edited')
                self.assertIn('tools/person-cutout', item['detail']); self.assertIn('удалите каталог', item['detail'])

    def test_no_venv_when_the_copy_is_not_built(self):
        self.fake_uv()
        write(self.engine/'doc.sh', b'#!/bin/bash\n# my own\n', 0o755)   # copy skipped:edited
        items = self.plan()
        self.assertEqual(items['content:mac-repair']['status'], 'skipped:edited')
        self.assertEqual(items['content:mac-cutout-python']['status'], 'skipped:script-not-repaired')
        self.assertFalse(os.path.lexists(str(self.project/'.venv')))

    def test_venv_apply_rechecks_that_the_active_script_reads_it(self):
        log = self.fake_uv()
        with patch.object(content, '_uv', return_value=str(self.uv)), patch.object(content, '_macos_supported', return_value=True):
            items = by_code(content.plan(self.home, 'Darwin', search_path=''))
        self.assertEqual(items['content:mac-cutout-python']['status'], 'needs-fix')
        # The copy could not be built (no hard links here): the venv must not be created.
        self.assertEqual(items['content:mac-cutout-python']['apply'](), 'skipped:script-not-repaired')
        self.assertFalse(log.exists()); self.assertFalse(os.path.lexists(str(self.project/'.venv')))

    def test_stale_tmp_venv_of_an_interrupted_run_is_removed(self):
        self.fake_uv()
        stale = self.project/'.venv.tmp-0123456789ab'; write(stale/'bin/python', b'x')
        foreign = self.project/'.venv.tmp-mine'; foreign.mkdir()
        self.assertIsNone(self.cutout_item()['apply']())
        self.assertFalse(stale.exists()); self.assertTrue(foreign.is_dir(), 'only our own name pattern is removed')

    def test_linked_binary_skips_only_the_copy(self):
        os.symlink('/bin/sh', str(self.engine/'tools/person-cutout'))
        items = self.plan()
        self.assertEqual(items['content:mac-repair']['status'], 'skipped:linked-path')
        self.assertNotIn('content:registration', items)
        self.assertIn('content:mac-lesson-tmp', items)

    def test_linked_swift_source_gets_no_binary(self):
        swift = self.engine/'tools/person-cutout.swift'
        target = self.root/'elsewhere.swift'; shutil.move(str(swift), str(target)); os.symlink(str(target), str(swift))
        items = self.plan()
        self.assertNotIn('content:registration', items)
        self.assertNotEqual(items['content:mac-repair']['status'], 'needs-fix', 'a linked version is never copied')

    def test_missing_or_different_bundle_binary_fails_loudly(self):
        for data in [None, b'not the reviewed binary']:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp)/'person-cutout'
                if data is not None:
                    fake.write_bytes(data)
                with patch.object(content, 'CUTOUT_BINARY', fake):
                    with self.assertRaises(content.BundleError):
                        content.plan(self.home, 'Darwin', search_path='')
                    with self.assertRaises(content.BundleError):
                        content.check_bundle()

    def test_uv_is_found_like_content_setup(self):
        hermes = self.home/'.hermes'
        pm_uv = self.root/'pm/uv'; write(pm_uv, '#!/bin/sh\n', 0o755)
        write(hermes/'hermes-agent/venv/bin/python', '#!/bin/sh\necho noise; echo '+str(pm_uv)+'\n', 0o755)
        write(hermes/'hermes-agent/venv/bin/uv', '#!/bin/sh\n', 0o755)
        write(hermes/'bin/uv', '#!/bin/sh\n', 0o755)
        write(self.home/'.local/bin/uv', '#!/bin/sh\n', 0o755)
        self.assertEqual(content._uv(self.home, '', hermes), str(pm_uv))
        (hermes/'hermes-agent/venv/bin/python').unlink()
        self.assertEqual(content._uv(self.home, '', hermes), str(hermes/'hermes-agent/venv/bin/uv'))
        (hermes/'hermes-agent/venv/bin/uv').unlink()
        self.assertEqual(content._uv(self.home, '', hermes), str(hermes/'bin/uv'))
        (hermes/'bin/uv').unlink()
        self.assertEqual(content._uv(self.home, '', hermes), str(self.home/'.local/bin/uv'))
        (self.home/'.local/bin/uv').unlink()
        path = self.root/'pathbin'; write(path/'uv', '#!/bin/sh\n', 0o755)
        self.assertEqual(content._uv(self.home, str(path), hermes), str(path/'uv'))
        self.assertIsNone(content._uv(self.home, '', hermes))

    def make_previous_copy(self):
        """The copy patch v2026.11.10.1 built: links to <v>, doc.sh/pip.sh fixed, person-cutout.sh as shipped."""
        previous = self.engine.with_name(VERSION+'-repair1')
        for path in sorted(self.engine.rglob('*')):
            relative = path.relative_to(self.engine)
            target = previous/relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif relative.parts[0] != 'out':
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(str(path), str(target))
        (previous/'out').mkdir(exist_ok=True)
        for name, pairs in [('doc.sh', content.DOC_FIX), ('pip.sh', content.PIP_FIX)]:
            fixed = content.fragment_fix((self.engine/name).read_bytes(), pairs)
            (previous/name).unlink(); write(previous/name, fixed, 0o755)
        self.pointer.write_text(json.dumps({'version': previous.name})+'\n')
        return previous

    def test_copy_of_the_previous_patch_is_replaced_by_repair2(self):
        previous = self.make_previous_copy()
        before = tree(previous)
        item = self.plan()['content:mac-repair']
        self.assertEqual(item['status'], 'needs-fix'); self.assertIn('BUG 0013', item['detail'])
        self.assertIsNone(item['apply']())
        self.assertEqual(json.loads(self.pointer.read_text()), {'version': MAC_COPY})
        self.assertEqual(tree(previous), before, 'the earlier copy stays as it was')
        self.assertEqual((self.copy/'tools/person-cutout').read_bytes(), (BUNDLE/'vendor/bin/person-cutout').read_bytes())
        self.assertNotIn(b'/tmp/pip-', (self.copy/'pip.sh').read_bytes())
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')

    def test_edited_previous_copy_is_left_alone(self):
        previous = self.make_previous_copy()
        (previous/'doc.sh').write_bytes(b'#!/bin/bash\n# my own\n')
        before = tree(self.home)
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'skipped:repair-copy-edited')
        self.assertEqual(tree(self.home), before)

    def test_previous_copy_survives_a_binary_compiled_into_v_later(self):
        self.make_previous_copy()
        write(self.engine/'tools/person-cutout', b'#!/bin/sh\n', 0o755)
        item = self.plan()['content:mac-repair']
        self.assertEqual(item['status'], 'needs-fix')
        self.assertIsNone(item['apply']())
        self.assertEqual((self.copy/'tools/person-cutout').read_bytes(), (BUNDLE/'vendor/bin/person-cutout').read_bytes())

    def test_unknown_swift_source_gets_no_binary(self):
        write(self.engine/'tools/person-cutout.swift', b'// changed\n')
        self.assertIsNone(self.plan()['content:mac-repair']['apply']())
        self.assertFalse((self.copy/'tools/person-cutout').exists())

    def test_repaired_doc_sh_runs_in_parallel_without_shared_tmp(self):
        self.plan()['content:mac-repair']['apply']()
        write(self.copy/'board', STUB_BOARD, 0o755); write(self.copy/'joinpdf', STUB_JOIN, 0o755)
        (self.copy/'board.swift').unlink(); (self.copy/'joinpdf.swift').unlink()
        pages = self.root/'pages'
        for n in range(3):
            write(pages/('p%d.json' % n), json.dumps({'card': {'layout': 'doc-text'}}))
        scratch = self.root/'scratch'; scratch.mkdir()
        env = {**os.environ, 'TMPDIR': str(scratch), 'LOG': str(self.root/'board.log')}
        runs = [subprocess.Popen(['bash', str(self.copy/'doc.sh'), str(self.root/('out%d/guide.pdf' % k)), 'client', *[str(pages/('p%d.json' % n)) for n in range(3)]],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for k in range(2)]
        for run in runs:
            _, error = run.communicate(timeout=60)
            self.assertEqual(run.returncode, 0, error)
        for k in range(2):
            self.assertEqual((self.root/('out%d/guide.pdf' % k)).read_text(), '1:client2:client3:client')
        configs = (self.root/'board.log').read_text().split()
        self.assertEqual(len(configs), 6); self.assertEqual(len({str(Path(c).parent) for c in configs}), 2)
        self.assertTrue(all(c.startswith(str(scratch)) for c in configs))
        self.assertEqual(list(scratch.iterdir()), [], 'per-run directories removed')

    def test_repaired_pip_sh_keeps_intermediates_private(self):
        self.plan()['content:mac-repair']['apply']()
        for name in ['speaker', 'frame_exact', 'board', 'pip']:
            write(self.copy/name, '#!/bin/sh\nexit 0\n', 0o755)
            (self.copy/(name+'.swift')).unlink()
        write(self.copy/'out/spk-off_overlay.png', b'png'); write(self.copy/'out/lesson-speaker.png', b'on'); write(self.copy/'out/lesson-speaker-off.png', b'off')
        write(self.copy/'lesson.json', json.dumps({'out': 'out/lesson'}))
        stubs = self.root/'stubs'; write(stubs/'ffmpeg', STUB_FFMPEG, 0o755); write(stubs/'ffprobe', '#!/bin/sh\necho 240,240,yuva444p10le\n', 0o755)
        scratch = self.root/'scratch'; scratch.mkdir()
        log = self.root/'ffmpeg.log'
        env = {**os.environ, 'TMPDIR': str(scratch), 'LOG': str(log), 'CONTENT_FFMPEG': str(stubs/'ffmpeg'), 'CONTENT_FFPROBE': str(stubs/'ffprobe')}
        result = subprocess.run(['bash', str(self.copy/'pip.sh'), 'lesson.json', str(self.root/'pip.mov'), '4'], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root/'pip.mov').is_file())
        calls = log.read_text()
        self.assertNotIn('/tmp/pip-', calls)
        self.assertEqual(calls.count(str(scratch)), 5)  # on, off, concat list+output, loop input
        self.assertEqual(list(scratch.iterdir()), [])
        self.assertEqual((self.engine/'out/spk_overlay.png').read_bytes(), b'generated earlier', 'installer version untouched')


class Bundle(unittest.TestCase):
    def test_repair_refuses_to_start_with_a_different_bundle_binary(self):
        spec = importlib.util.spec_from_file_location('repair_agents_bundle_check', BUNDLE/'agents_repair.py')
        repair = importlib.util.module_from_spec(spec); spec.loader.exec_module(repair)
        original = repair.load
        def load(name):
            module = original(name)
            if name == 'content-repairs':
                module.CUTOUT_BINARY = BUNDLE/'vendor/content-repairs.py'   # any other bytes
            return module
        with tempfile.TemporaryDirectory() as tmp, patch.object(repair, 'load', side_effect=load):
            with self.assertRaisesRegex(ValueError, 'Incomplete repair bundle'):
                repair.Repair(Path(tmp), Path(tmp)/'InfobizAgents', 'Darwin', Path(tmp)/'.hermes')

    def test_no_learner_identifiers_in_the_public_bundle(self):
        import re
        for path in sorted(BUNDLE.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py', '.md', '.sh', '.ps1', '.mjs', '.json', '.yaml', '.txt'}:
                text = path.read_text(encoding='utf-8', errors='replace')
                # Known learner request ids are split so this file does not contain them itself.
                known = '|'.join(['cmwae' + 'nfei', 'wsmr4' + 'qf4'])
                self.assertIsNone(re.search(r'DIAG-[A-Z]+-[A-Z0-9]{6,}|\bcm[a-z0-9]{20,}\b|' + known, text), path.name)

    def test_prebuilt_cutout_is_the_reviewed_universal_binary_for_every_released_source(self):
        data = (BUNDLE/'vendor/bin/person-cutout').read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), content.CUTOUT_BINARY_SHA256)
        self.assertEqual(data[:4], bytes.fromhex('cafebabe'), 'fat Mach-O (arm64 + x86_64)')
        self.assertIsNotNone(content._prebuilt_cutout())
        for tarball in sorted((ROOT/'apps/lms/installer-assets/agents').glob('agents-tier-3-*.tar.gz')):
            with tarfile.open(tarball) as archive:
                source = archive.extractfile('profile/content/swift/tools/person-cutout.swift').read()
            self.assertEqual(hashlib.sha256(source).hexdigest(), content.CUTOUT_SOURCE_SHA256, tarball.name)
        if sys.platform == 'darwin':
            info = subprocess.run(['/usr/bin/lipo', '-archs', str(BUNDLE/'vendor/bin/person-cutout')], capture_output=True, text=True)
            self.assertEqual(sorted(info.stdout.split()), ['arm64', 'x86_64'])
            self.assertEqual(subprocess.run(['/usr/bin/codesign', '-v', str(BUNDLE/'vendor/bin/person-cutout')]).returncode, 0)

    def test_messages_are_fixed_literals(self):
        source = (BUNDLE/'vendor/content-repairs.py').read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Raise) and node.exc is not None and isinstance(node.exc, ast.Call):
                self.assertTrue(all(isinstance(a, ast.Constant) for a in node.exc.args), ast.dump(node))
        self.assertNotIn('import yaml', source)


if __name__ == '__main__':
    unittest.main()
