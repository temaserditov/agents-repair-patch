"""Offline fixtures for the Hermes 0.23 compatibility repairs (BUG 0014, BUG 0019).

The runner texts are the real helpers from git history; nothing touches a real
installation. PowerShell is exercised only when ``pwsh`` is installed locally.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT/'installer/repair-patch'

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result

compat = module('test_compat_repairs_module', BUNDLE/'vendor/compat-repairs.py')
try:
    import yaml
except ImportError:  # the patch itself runs under Hermes Python, which ships PyYAML
    yaml = None

# Served helper = BOM + install.ps1 (readWindowsAddonFile); stored as control/runner.ps1.
V2 = 'apps/lms/installer-assets/windows-addon-v2/install.ps1'
V3 = 'apps/lms/installer-assets/windows-addon-v3/install.ps1'
V1 = 'apps/lms/installer-assets/windows-addon-v1/install.ps1'
OLD_HELPERS = [('de4e95f2', V2), ('0431c239', V2), ('93ead633', V3), ('6f373920', V3)]
BANNER = 'Hermes Agent v{} (2026.11.1)\nProject: C:\\Users\\student\\AppData\\Local\\hermes\\hermes-agent\n'
# TASK52: Hermes 0.23.1 is accepted like 0.23.0; 0.24+ stays an honest refusal.
ACCEPT, REJECT = ['0.22.7', '0.23.0', '0.23.1'], ['0.24.0', '9.0.0']


def git_show(commit, path):
    try:
        result = subprocess.run(['git', '-C', str(ROOT), 'show', commit+':'+path], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.decode('utf-8')


def served(commit, path):
    text = git_show(commit, path)
    if text is None:
        raise unittest.SkipTest('git history is not available (shipped bundle copy)')
    return '\ufeff'+text


def tree(root):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777)
            for p in root.rglob('*') if p.is_file()}


def write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(mode)


def synthetic_runner(gate):
    return ('\ufeff[CmdletBinding()]\r\nparam([switch]$CodeFromStdin,[switch]$RunControl,[switch]$RunWebShell)\r\n'
            "$ErrorActionPreference='Stop'\r\nfunction Get-ReviewedVersion([string]$Text) {\r\n"+gate+'\r\n'
            '  return $Matches[1]\r\n}\r\n').encode('utf-8')


OLD_GATE = compat._OLD_PREFIX+"'}"


class StrictYaml:
    """Stand-in for a strict loader (ruamel-like): any duplicate mapping key is an error."""
    @staticmethod
    def safe_load(text):
        class Loader(yaml.SafeLoader):
            pass
        def mapping(loader, node, deep=False):
            keys = [loader.construct_object(key, deep=True) for key, _ in node.value]
            if len(keys) != len(set(map(repr, keys))):
                raise ValueError('duplicate key')
            return yaml.SafeLoader.construct_mapping(loader, node, deep)
        Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        return yaml.load(text, Loader=Loader)


@unittest.skipIf(yaml is None, 'PyYAML is not importable here: YAML equivalence checks need it')
class DuplicatePlatforms(unittest.TestCase):
    def test_keep_effective_last_mapping_and_other_bytes(self):
        before = ('# user comment\nmodel: custom\nplatforms:\n  telegram: false\n'
                  'temperature: 0.7\n# retained comment\nplatforms:\n'
                  '  telegram:\n    enabled: true\ntrailing: retained\n')
        after = compat.dedupe_platforms(before)
        self.assertEqual(after, '# user comment\nmodel: custom\ntemperature: 0.7\n'
            '# retained comment\nplatforms:\n  telegram:\n    enabled: true\ntrailing: retained\n')
        self.assertEqual(yaml.safe_load(before), yaml.safe_load(after))
        self.assertIsNone(compat.dedupe_platforms(after))

    def test_null_and_empty_shadows_and_three_blocks(self):
        before = 'platforms: null\nkeep: value\nplatforms: {}\nplatforms:\n  telegram: true\n'
        after = compat.dedupe_platforms(before)
        self.assertEqual(after, 'keep: value\nplatforms:\n  telegram: true\n')
        self.assertEqual(yaml.safe_load(before), yaml.safe_load(after))

    def test_crlf_and_bom_preserved(self):
        before = '\ufeffmodel: m\r\nplatforms:\r\n  telegram:\r\n    enabled: false\r\n\r\nagent:\r\n  x: 1\r\nplatforms:\r\n  telegram:\r\n    enabled: true\r\n'
        after = compat.dedupe_platforms_bytes(before.encode('utf-8'))
        self.assertEqual(after, '\ufeffmodel: m\r\n\r\nagent:\r\n  x: 1\r\nplatforms:\r\n  telegram:\r\n    enabled: true\r\n'.encode('utf-8'))
        self.assertNotIn(b'\n', after.replace(b'\r\n', b''))
        self.assertIsNone(compat.dedupe_platforms_bytes(after))

    def test_block_scalars_and_column_zero_comment_inside_block(self):
        before = ('notes: |\n  platforms:\n  # text, not a comment\n\n  tail\n'
                  'platforms:\n  telegram:\n# disabled for now\n    enabled: false\n  # indented note\n\n'
                  '# about the next key\nfold: >-\n  a\n  b\nplatforms:\n  telegram:\n    enabled: true\n')
        after = compat.dedupe_platforms(before)
        self.assertEqual(after, 'notes: |\n  platforms:\n  # text, not a comment\n\n  tail\n\n'
                         '# about the next key\nfold: >-\n  a\n  b\nplatforms:\n  telegram:\n    enabled: true\n')
        self.assertEqual(yaml.safe_load(before), yaml.safe_load(after))

    def test_strict_loader_still_proves_equivalence(self):
        before = 'platforms:\n  a: 1\nmodel: x\nplatforms:\n  a: 2\n'
        with self.assertRaises(ValueError):
            StrictYaml.safe_load(before)
        self.assertEqual(compat.dedupe_platforms(before, StrictYaml), 'model: x\nplatforms:\n  a: 2\n')

    def test_refusals(self):
        cases = {
            'model: one\nplatforms: {}\nmodel: two\nplatforms: {}\n': 'unknown-duplicates',
            'model: one\nmodel: two\n': 'unknown-duplicates',
            'platforms:\n  a: 1\nplatforms: true\n': 'scalar-platforms',
            'platforms:\nplatforms:\n': 'scalar-platforms',
            'platforms: telegram\nplatforms:\n  a: 1\n': 'scalar-platforms',
            'platforms: &p\n  a: 1\n&x y: 2\nplatforms:\n  a: 2\n': 'unknown-shape',
            'platforms:\n  a: 1\n<<: {b: 1}\nplatforms:\n  a: 2\n': 'unknown-shape',
            'platforms:\n  a: 1\n---\nplatforms:\n  a: 2\n': 'unknown-shape',
            'base: &b {a: 1}\nplatforms: {a: 0}\nplatforms: *b\n': 'unverifiable',
            'title: "first\nplatforms: x"\nplatforms:\n  a: 1\nplatforms:\n  a: 2\n': 'unverifiable',
            # Keep-chomped scalar would absorb the blank line left by the removed block.
            'a: |+\n  x\n\nplatforms:\n  t: 1\n\nplatforms:\n  t: 2\n': 'unverifiable',
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    compat.dedupe_platforms(text)
                self.assertEqual(str(caught.exception), reason)

    def test_nothing_to_do(self):
        for text in ['', '# only comment\n', 'platforms:\n  a: 1\nmodel: x\n', '- item\n', '{a: 1}\n', '---\nplatforms: {}\n']:
            with self.subTest(text=text):
                self.assertIsNone(compat.dedupe_platforms(text))


def accepts(regex, version):
    return re.search(regex, BANNER.format(version), re.M) is not None


class RunnerGate(unittest.TestCase):
    def test_real_old_helpers_are_widened_exactly(self):
        for commit, path in OLD_HELPERS:
            for crlf in [False, True]:
                with self.subTest(commit=commit, crlf=crlf):
                    text = served(commit, path)
                    if crlf:
                        text = text.replace('\n', '\r\n')
                    self.assertEqual(compat.runner_gate_state(text), 'old')
                    old = compat.runner_gate_regex(text)
                    self.assertEqual([accepts(old, v) for v in ACCEPT+REJECT], [True, False, False, False, False])
                    data = compat.widen_runner_gate_bytes(text.encode('utf-8'))
                    self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
                    result = data.decode('utf-8')
                    before_lines, after_lines = text.splitlines(keepends=True), result.splitlines(keepends=True)
                    self.assertEqual(len(before_lines), len(after_lines))
                    changed = [i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b]
                    self.assertEqual(len(changed), 1)
                    self.assertTrue(after_lines[changed[0]].endswith('\r\n' if crlf else '\n'))
                    self.assertIn('0.22.x/0.23.x source installation', after_lines[changed[0]])
                    new = compat.runner_gate_regex(result)
                    self.assertEqual(new, r'(?m)^Hermes Agent v?(0\.2[23]\.[0-9]+)\b')
                    self.assertEqual([accepts(new, v) for v in ACCEPT+REJECT], [True, True, True, False, False])
                    self.assertEqual(compat.runner_gate_state(result), 'current')
                    self.assertIsNone(compat.widen_runner_gate(result))

    def test_widened_regex_is_the_current_v3_gate(self):
        current = served('HEAD', V3)
        self.assertEqual(compat.runner_gate_state(current), 'current')
        self.assertIsNone(compat.widen_runner_gate(current))
        widened = compat.widen_runner_gate(served('de4e95f2', V2))
        self.assertEqual(compat.runner_gate_regex(widened), compat.runner_gate_regex(current))

    def test_unknown_helpers_are_left_alone(self):
        v1 = served('c66f0024', V1)
        self.assertEqual(compat.runner_gate_state(v1), 'unknown')  # no panel task in v1
        old = served('de4e95f2', V2)
        edited = [old.replace('The code was not used.', 'The code was not used!', 1),
                  old.replace("(0\\.22\\.[0-9]+)", "(0\\.2[0-9]\\.[0-9]+)", 1),
                  old.replace('function Get-ReviewedVersion', 'function Get-ReviewedVersionX', 1),
                  old+'\nfunction Get-ReviewedVersion([string]$Text) {\n'+OLD_GATE+'\n  return $Matches[1]\n}\n']
        for text in edited:
            with self.subTest(text=text[-60:]):
                self.assertEqual(compat.runner_gate_state(text), 'unknown')
                self.assertIsNone(compat.widen_runner_gate(text))
        self.assertIsNone(compat.widen_runner_gate_bytes(b'\xff\xfe'+old.encode('utf-16-le')))

    @unittest.skipUnless(shutil.which('pwsh'), 'pwsh is not installed: real PowerShell check not run')
    def test_real_powershell_function(self):
        script = r'''
param([Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Target,[ref]$tokens,[ref]$errors)
$fn=$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-ReviewedVersion'},$true)
. ([scriptblock]::Create($fn[0].Extent.Text))
$result=[ordered]@{errors=$errors.Count;functions=$fn.Count}
foreach($v in @('0.22.7','0.23.0','0.23.1','0.24.0','9.0.0')){try{$result[$v]=(Get-ReviewedVersion ("Hermes Agent v$v (2026.11.1)`nProject: x"))}catch{$result[$v]=$null}}
$result|ConvertTo-Json -Compress
'''
        # -File with the target as an argument: the helper is only parsed (AST), and
        # just its Get-ReviewedVersion definition is evaluated; nothing else runs.
        with tempfile.TemporaryDirectory() as directory:
            check = Path(directory)/'check-gate.ps1'
            check.write_text(script, encoding='utf-8')
            for commit, path in OLD_HELPERS:
                with self.subTest(commit=commit):
                    old = served(commit, path).encode('utf-8')
                    for name, data, expected in [('old.ps1', old, ['0.22.7', None, None, None, None]),
                                                 ('new.ps1', compat.widen_runner_gate_bytes(old), ['0.22.7', '0.23.0', '0.23.1', None, None])]:
                        file = Path(directory)/name
                        file.write_bytes(data)
                        run = subprocess.run(['pwsh', '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(check), str(file)],
                                             capture_output=True, text=True, timeout=120)
                        self.assertEqual(run.returncode, 0, run.stderr)
                        value = json.loads(run.stdout.strip().splitlines()[-1])
                        self.assertEqual((value['errors'], value['functions']), (0, 1))
                        self.assertEqual([value[v] for v in ['0.22.7', '0.23.0', '0.23.1', '0.24.0', '9.0.0']], expected)


@unittest.skipIf(yaml is None, 'PyYAML is not importable here: YAML equivalence checks need it')
class Planner(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.home = self.root/'home'
        self.hermes = self.home/'AppData/Local/hermes'
        self.install = self.home/'InfobizAgents'
        write(self.hermes/'config.yaml', 'model: x\nplatforms:\n  telegram: {enabled: true}\n')
        write(self.hermes/'profiles/editor/config.yaml', 'platforms:\n  a: 1\nmodel: x\nplatforms:\n  a: 2\n', 0o640)
        write(self.hermes/'profiles/sales/config.yaml', 'model: a\nmodel: b\nplatforms: {}\nplatforms: {}\n')
        write(self.hermes/'profiles/tech/config.yaml', '\ufeffplatforms:\r\n  a: 1\r\nplatforms:\r\n  a: 2\r\n'.encode('utf-8'))
        write(self.install/'control/runner.ps1', synthetic_runner(OLD_GATE), 0o644)

    def tearDown(self):
        self.directory.cleanup()

    def plan(self, system='Windows', names=('editor', 'sales', 'tech', 'marketer', '../x')):
        return compat.plan(self.home, self.hermes, self.install, system, list(names))

    def test_plan_apply_and_second_run(self):
        self.assertEqual(hashlib.sha256(OLD_GATE.encode()).hexdigest() in compat.KNOWN_OLD_GATES, True)
        before = tree(self.root)
        items = self.plan()
        self.assertEqual(tree(self.root), before)  # plan() has no side effects
        status = {item['code']: item['status'] for item in items}
        self.assertEqual(status, {'platforms:default': 'unchanged', 'platforms:editor': 'needs-fix',
                                  'platforms:sales': 'skipped:unknown-duplicates', 'platforms:tech': 'needs-fix',
                                  'windows-runner-gate': 'needs-fix'})
        for item in items:
            self.assertEqual(item['apply'] is not None, item['status'] == 'needs-fix')
            self.assertTrue(all(isinstance(path, Path) for path in item['paths']))
        saved = {}
        for item in items:
            if item['apply']:
                receipt = item['apply'](backup=lambda path, data: saved.__setitem__(path, data))
                self.assertNotEqual(receipt['sha256Before'], receipt['sha256After'])
        self.assertEqual(saved[self.install/'control/runner.ps1'], synthetic_runner(OLD_GATE))
        self.assertEqual((self.hermes/'profiles/editor/config.yaml').read_text(), 'model: x\nplatforms:\n  a: 2\n')
        self.assertEqual((self.hermes/'profiles/tech/config.yaml').read_bytes(), '\ufeffplatforms:\r\n  a: 2\r\n'.encode('utf-8'))
        self.assertEqual((self.hermes/'profiles/editor/config.yaml').stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.install/'control/runner.ps1').stat().st_mode & 0o777, 0o644)
        self.assertEqual(compat.runner_gate_state((self.install/'control/runner.ps1').read_text(encoding='utf-8')), 'current')
        again = {item['code']: item['status'] for item in self.plan()}
        self.assertEqual(again, dict(status, **{'platforms:editor': 'unchanged', 'platforms:tech': 'unchanged',
                                                 'windows-runner-gate': 'unchanged'}))
        self.assertEqual(sorted(p.name for p in (self.hermes/'profiles/editor').iterdir()), ['config.yaml'])

    def test_apply_refuses_a_file_changed_after_plan(self):
        items = {item['code']: item for item in self.plan()}
        path = self.hermes/'profiles/editor/config.yaml'
        path.write_text('platforms:\n  a: 1\nmodel: y\nplatforms:\n  a: 2\n')
        with self.assertRaises(ValueError):
            items['platforms:editor']['apply']()
        self.assertEqual(path.read_text(), 'platforms:\n  a: 1\nmodel: y\nplatforms:\n  a: 2\n')

    def test_non_windows_and_unknown_files(self):
        codes = [item['code'] for item in self.plan(system='Darwin')]
        self.assertNotIn('windows-runner-gate', codes)
        write(self.install/'control/runner.ps1', synthetic_runner(OLD_GATE.replace('0.22.x', '0.22')))
        link = self.hermes/'profiles/marketer/config.yaml'
        link.parent.mkdir(parents=True)
        os.symlink(self.hermes/'profiles/editor/config.yaml', link)
        write(self.hermes/'profiles/editor/config.yaml', b'platforms: {}\n\xff\nplatforms: {}\n')
        status = {item['code']: item['status'] for item in self.plan()}
        self.assertEqual(status['windows-runner-gate'], 'skipped:unknown-runner')
        self.assertEqual(status['platforms:marketer'], 'skipped:linked-path')
        self.assertEqual(status['platforms:editor'], 'skipped:unreadable-encoding')
        self.assertTrue(all(item['apply'] is None for item in self.plan() if item['status'] != 'needs-fix'))


if __name__ == '__main__':
    unittest.main()
