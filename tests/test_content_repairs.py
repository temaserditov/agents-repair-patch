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
        self.copy = self.engine.with_name(COPY)
        self.pointer = self.project/'tools/swift-design-engine/active.json'

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, ready=False, search_path=None):
        with patch.object(content, '_developer_tools', return_value=ready), patch.object(content, '_cutout_python_ready', return_value=ready):
            return by_code(content.plan(self.home, 'Darwin', search_path=os.environ.get('PATH', '') if search_path is None else search_path))

    def test_copy_isolates_tmp_reinstall_passes_and_statuses(self):
        before = tree(self.home)
        items = self.plan()
        self.assertEqual(tree(self.home), before)
        self.assertEqual(items['content:mac-repair']['status'], 'needs-fix')
        self.assertEqual(items['content:mac-repair']['paths'], [self.pointer])
        self.assertEqual(items['content:mac-lesson-tmp']['status'], 'skipped:needs-native-build')
        self.assertEqual(items['content:mac-cutout-python']['status'], 'skipped:needs-content-runtime')
        self.assertIsNone(items['content:mac-repair']['apply']())
        self.assertEqual(tree(self.engine), {k[len('Documents/Content/tools/swift-design-engine/releases/'+VERSION+'/'):]: v for k, v in before.items()
                                             if k.startswith('Documents/Content/tools/swift-design-engine/releases/'+VERSION+'/')})
        self.assertEqual(self.pointer.read_text(), json.dumps({'version': COPY})+'\n')
        for name in ['doc.sh', 'pip.sh']:
            path = self.copy/name
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual(subprocess.run(['bash', '-n', str(path)]).returncode, 0)
        self.assertNotIn(b'/tmp/doc-page', (self.copy/'doc.sh').read_bytes()); self.assertNotIn(b'/tmp/pip-', (self.copy/'pip.sh').read_bytes())
        self.assertEqual((self.copy/'board.swift').stat().st_ino, (self.engine/'board.swift').stat().st_ino)
        self.assertEqual(list((self.copy/'out').iterdir()), [], 'generated output is not linked into the copy')
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')
        reinstall_check(package(), self.project, VERSION, 'Darwin')
        reinstall_pointer(self.project, 'tools/swift-design-engine')
        again = self.plan()['content:mac-repair']
        self.assertIn('уже есть', again['detail'])
        again['apply']()
        self.assertEqual(sorted(p.name for p in self.engine.parent.iterdir()), [VERSION, COPY])
        self.assertEqual(self.plan()['content:mac-repair']['status'], 'unchanged')

    def test_cutout_ready_reports_unchanged(self):
        write(self.engine/'tools/person-cutout', b'#!/bin/sh\n', 0o755)
        bindir = self.root/'bin'; write(bindir/'python3', '#!/bin/sh\nexit 0\n', 0o755)
        self.assertEqual(self.plan(ready=True, search_path=str(bindir))['content:mac-cutout-python']['status'], 'unchanged')

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
    def test_messages_are_fixed_literals(self):
        source = (BUNDLE/'vendor/content-repairs.py').read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Raise) and node.exc is not None and isinstance(node.exc, ast.Call):
                self.assertTrue(all(isinstance(a, ast.Constant) for a in node.exc.args), ast.dump(node))
        self.assertNotIn('import yaml', source)


if __name__ == '__main__':
    unittest.main()
