"""TASK48 panel fixes (BUG 0006/0001/0015/0005) on the real reviewed panel bytes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT/'installer/repair-patch'
TIER = ROOT/'apps/lms/installer-assets/agents/agents-tier-3-agents-release-20261108.1.tar.gz'
BASE = ROOT/'apps/lms/installer-assets/v0.3.0/agent-web-shell-0.3.0.tar.gz'
RUNNER_UPDATED = '5ee341cd96fdb83ed916af4c640f4de586344c53ecca0b38b13a2a6dfc10fb9f'
RUNNER_SUPPORT_FIXED = '4cf98c24'  # verified address repair (registry BUG 0006)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


suite = module('panel_suite', BUNDLE/'tests/test_agents_repair.py')
repair = suite.repair
panel = module('panel_repairs_test', BUNDLE/'vendor/panel-repairs.py')


def payload(name):
    with tarfile.open(TIER) as archive:
        return archive.extractfile('profile/post-install/payload/'+name).read()


def base(name):
    with tarfile.open(BASE) as archive:
        return archive.extractfile('web-shell/'+name).read()


def node():
    for candidate in [shutil.which('node'), str(Path.home()/'.nvm/versions/node/v22.22.2/bin/node')]:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


SERVERS = {'b2469181': 'b24691818054ca2fe97355085c9975c4ff6a5213aa52004a8c0d683028c45b15',
           'bf7bdcd0': 'bf7bdcd0045997f964f1187f6fadafd409b56eca97f73c59c4af691b2f360b7e'}


NODE = node()
REAL_WHICH = shutil.which


class PanelModule(unittest.TestCase):
    def test_runner_shim_matches_verified_support_repair_and_reverts(self):
        updated = payload(RUNNER_UPDATED).decode()
        fixed = panel.fix_runner(updated)
        self.assertTrue(panel.sha(fixed).startswith(RUNNER_SUPPORT_FIXED))
        self.assertEqual(panel.runner_state(fixed), 'fixed')
        self.assertEqual(panel.fix_runner(fixed), fixed)
        self.assertEqual(panel.canonical_runner(fixed), updated)
        # The panel audit pin accepts exactly these two runner byte strings.
        self.assertEqual((panel.sha(updated), panel.sha(fixed)), (panel.RUNNER_UPDATED_SHA, panel.RUNNER_FIXED_SHA))
        self.assertEqual(panel.runner_state('print(1)\n'), 'unknown')

    def test_server_variants_fix_revert_and_registry_behaviour(self):
        binary = node()
        test = BUNDLE/'tests/preflight_topics.test.mjs'
        for label, digest in [('base', None), *SERVERS.items()]:
            with self.subTest(variant=label):
                text = (base('server.mjs') if digest is None else payload(digest)).decode()
                self.assertEqual(panel.server_states(text)['preflight'], 'applicable')
                fixed = panel.fix_server(text)
                self.assertEqual(panel.fix_server(fixed), fixed)
                # The 0.3.0 base panel has no student audit; only released variants pin the runner.
                self.assertEqual(panel.server_states(fixed), {'preflight': 'fixed', 'platforms-writer': 'fixed', 'runner-audit': 'unknown' if digest is None else 'fixed', 'sales-label': 'fixed'})
                expected = text if digest else panel.canonical_server_without_sales(fixed)
                self.assertEqual(panel.canonical_server(fixed) if digest else expected, text)
                if binary:
                    with tempfile.TemporaryDirectory() as directory:
                        source = Path(directory)/'server.mjs'; source.write_text(fixed, encoding='utf-8')
                        self.assertEqual(subprocess.run([binary, '--check', str(source)]).returncode, 0)
                        run = subprocess.run([binary, '--test', str(test)], env={**os.environ, 'WEBSHELL_SOURCE': str(source)}, capture_output=True, text=True)
                        self.assertEqual(run.returncode, 0, run.stdout[-2000:]+run.stderr[-2000:])
        if not binary:
            self.skipTest('node is not installed: syntax and registry behaviour tests were not run')

    def test_vendor_copies_stay_identical_to_release_sources(self):
        self.assertEqual((BUNDLE/'vendor/profile-context.py').read_bytes(), (ROOT/'installer/apps/installer-cli/windows-profile-context.py').read_bytes())
        repairs = json.loads((ROOT/'installer/runtime/student-panel-repairs.json').read_text(encoding='utf-8'))['server']
        self.assertIn(list(panel.SERVER_SALES[0]), repairs)


class PanelIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        original = repair.load
        def load(name):
            helper = original(name)
            if name == 'team-runtime':
                helper.inspect_profiles = lambda profiles, names, **kw: {n: {'status': 'ready'} for n in names}
            return helper
        self.patches = [patch.object(repair, 'load', side_effect=load),
                        patch.object(repair.Repair, 'panel_restart', lambda self: self.add('webshell-restart-pending', 'fixed' if (self.control/'web-shell-restart-pending').is_file() else 'unchanged'))]
        if NODE:
            self.patches.append(patch.object(repair.shutil, 'which', side_effect=lambda name: NODE if name == 'node' else REAL_WHICH(name)))
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def fixture(self, runner, server, version='0.23.0'):
        f = suite.Fixture(self.root, 'Darwin', 3)
        suite.write(f.install/'web-shell/runner.py', runner)
        suite.write(f.install/'web-shell/server.mjs', server)
        suite.write(f.hermes/'hermes-agent/install-stamp.json', json.dumps({'baseVersion': version}))
        return f

    def finding(self, out, code):
        return next(x for x in out['findings'] if x['code'] == code)

    def test_latest_panel_is_fixed_once_and_stays_recognised(self):
        if not node():
            self.skipTest('node is not installed')
        self.latest_panel_case('0.23.0')
        self.tearDown(); self.setUp()
        self.latest_panel_case('0.23.1')  # TASK52: same runner shim on Hermes 0.23.1

    def latest_panel_case(self, version):
        f = self.fixture(payload(RUNNER_UPDATED), payload(SERVERS['b2469181']), version=version)
        out = f.run()
        self.assertEqual(self.finding(out, 'webshell-runner-023')['status'], 'planned')
        self.assertEqual(self.finding(out, 'webshell-preflight')['status'], 'planned')
        self.assertEqual(hashlib.sha256((f.install/'web-shell/runner.py').read_bytes()).hexdigest(), RUNNER_UPDATED)
        out = f.run(True)
        self.assertEqual(self.finding(out, 'webshell-runner-023')['status'], 'fixed')
        self.assertTrue(hashlib.sha256((f.install/'web-shell/runner.py').read_bytes()).hexdigest().startswith(RUNNER_SUPPORT_FIXED))
        self.assertTrue((f.control/'web-shell-restart-pending').is_file())
        backups = list((f.control/'backups').glob('repair-*/web-shell/runner.py'))
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in backups], [RUNNER_UPDATED])
        (f.control/'web-shell-restart-pending').unlink()
        again = f.run(True)
        self.assertFalse(again['changed'])
        self.assertEqual(self.finding(again, 'webshell')['status'], 'unchanged')
        self.assertEqual(self.finding(again, 'webshell-runner-023')['status'], 'unchanged')
        self.assertEqual(self.finding(again, 'webshell-preflight')['status'], 'unchanged')
        panel_codes = {'webshell', 'webshell-runner-023', 'webshell-preflight'}
        self.assertEqual([x for x in again['findings'] if x['code'] in panel_codes and x['status'] != 'unchanged'], [])

    def test_release_20261110_panel_is_the_patched_panel_and_stays_unchanged(self):
        # FIX_0576: new installs receive exactly the bytes the patch writes, so a
        # patched student and a fresh install share one known updater variant.
        release = ROOT/'apps/lms/installer-assets/agents/agents-tier-3-agents-release-20261110.1.tar.gz'
        with tarfile.open(release) as archive:
            manifest = json.loads(archive.extractfile('profile/post-install/update-manifest.json').read())
            def current(name, before):
                variant = manifest['files'][name]['variants'][before]
                return archive.extractfile('profile/post-install/'+variant['path']).read()
            runner = current('runner.py', RUNNER_UPDATED)
            servers = {key: current('server.mjs', digest) for key, digest in SERVERS.items()}
        self.assertTrue(hashlib.sha256(runner).hexdigest().startswith(RUNNER_SUPPORT_FIXED))
        self.assertEqual(runner.decode(), panel.fix_runner(payload(RUNNER_UPDATED).decode()))
        for key, digest in SERVERS.items():
            self.assertEqual(servers[key].decode(), panel.fix_server(payload(digest).decode()), key)
            self.assertEqual(set(panel.server_states(servers[key].decode()).values()), {'fixed'}, key)
        if not node():
            self.skipTest('node is not installed')
        f = self.fixture(runner, servers['b2469181'])
        before = suite.tree(f.install/'web-shell')
        out = f.run(True)
        self.assertEqual(self.finding(out, 'webshell-runner-023')['status'], 'unchanged')
        self.assertEqual(self.finding(out, 'webshell-preflight')['status'], 'unchanged')
        self.assertEqual(suite.tree(f.install/'web-shell'), before)
        self.assertFalse((f.control/'web-shell-restart-pending').exists())

    def test_verify_reports_an_unfixed_runner(self):
        f = self.fixture(payload(RUNNER_UPDATED), payload(SERVERS['bf7bdcd0']))
        out = f.run(verify=True)
        self.assertEqual(self.finding(out, 'webshell-runner-023')['status'], 'unfixable')

    def test_old_panel_on_hermes_023_asks_for_update_first_and_is_untouched(self):
        f = self.fixture(base('runner.py'), base('server.mjs'))
        before = suite.tree(f.install/'web-shell')
        out = f.run(True)
        self.assertEqual(self.finding(out, 'webshell-runner-023')['status'], 'needs-platform-update')
        self.assertEqual(out['nextStep'], 'run-platform-update')
        self.assertEqual(suite.tree(f.install/'web-shell'), before)

    def test_edited_panel_is_reported_not_looped_and_kept(self):
        server = payload(SERVERS['b2469181']) + b'\n// learner edit\n'
        f = self.fixture(payload(RUNNER_UPDATED), server)
        out = f.run(True)
        self.assertEqual(self.finding(out, 'webshell')['status'], 'skipped:panel-edited')
        self.assertEqual((f.install/'web-shell/server.mjs').read_bytes(), server)
        self.assertNotIn('webshell-preflight', [x['code'] for x in out['findings']])

    def test_profile_context_on_manual_hermes_023(self):
        for version in ['0.23.0', '0.23.1']:
            with self.subTest(version=version):
                self.tearDown(); self.setUp()
                self.profile_context_case(version)

    def test_hermes_024_loader_is_not_rewritten(self):
        # TASK52: the loader transform is reviewed for 0.23.x only; on 0.24+ it is not offered.
        # (The runner shim is version-neutral: it falls back to the old import.)
        with tarfile.open(ROOT/'apps/lms/installer-assets/v0.3.0/hermes-agent-v0.23.0.tar.gz') as archive:
            member = next(m for m in archive.getmembers() if m.name.endswith('/agent/prompt_builder.py'))
            loader = archive.extractfile(member).read()
        f = self.fixture(payload(RUNNER_UPDATED), payload(SERVERS['b2469181']), version='0.24.0')
        suite.write(f.hermes/'hermes-agent/agent/prompt_builder.py', loader)
        out = f.run(True)
        self.assertNotIn('profile-context', [x['code'] for x in out['findings']])
        self.assertEqual((f.hermes/'hermes-agent/agent/prompt_builder.py').read_bytes(), loader)

    def profile_context_case(self, version):
        # Hermes 0.23.1 ships agent/prompt_builder.py byte-identical to 0.23.0.
        with tarfile.open(ROOT/'apps/lms/installer-assets/v0.3.0/hermes-agent-v0.23.0.tar.gz') as archive:
            member = next(m for m in archive.getmembers() if m.name.endswith('/agent/prompt_builder.py'))
            loader = archive.extractfile(member).read()
        f = self.fixture(payload(RUNNER_UPDATED), payload(SERVERS['b2469181']), version=version)
        target = f.hermes/'hermes-agent/agent/prompt_builder.py'
        suite.write(target, loader)
        self.assertEqual(self.finding(f.run(), 'profile-context')['status'], 'planned')
        self.assertEqual(target.read_bytes(), loader)
        self.assertEqual(self.finding(f.run(True), 'profile-context')['status'], 'fixed')
        self.assertIn(b'# serditov-windows-profile-context-v1', target.read_bytes())
        self.assertEqual([p.read_bytes() for p in (f.control/'backups').glob('repair-*/runtime/agent/prompt_builder.py')], [loader])
        self.assertEqual(self.finding(f.run(True), 'profile-context')['status'], 'unchanged')
        suite.write(target, loader+b'\n# local edit\n')
        self.assertEqual(self.finding(f.run(True), 'profile-context')['status'], 'skipped:unknown-runtime')
        suite.write(target, loader)
        suite.write(f.hermes/'hermes-agent/agent/system_prompt.py', '# student-profile-context-023-v1\n')
        self.assertEqual(self.finding(f.run(True), 'profile-context')['status'], 'unchanged')
        self.assertEqual(target.read_bytes(), loader)
        suite.write(f.hermes/'hermes-agent/install-stamp.json', json.dumps({'baseVersion': '0.22.7'}))
        self.assertNotIn('profile-context', [x['code'] for x in f.run()['findings']])

    def test_duplicate_platforms_are_merged_before_any_config_parse(self):
        f = self.fixture(payload(RUNNER_UPDATED), payload(SERVERS['b2469181']))
        config = f.profiles/'tech/config.yaml'
        original = config.read_bytes()
        doubled = b'platforms:\n  telegram:\n    enabled: false\n' + original + b'platforms:\n  telegram:\n    enabled: true\n'
        suite.write(config, doubled)
        self.assertEqual(self.finding(f.run(), 'platforms:tech')['status'], 'planned')
        self.assertEqual(config.read_bytes(), doubled)
        out = f.run(True)
        self.assertEqual(self.finding(out, 'platforms:tech')['status'], 'fixed')
        self.assertEqual(config.read_text().count('platforms:'), 1)
        import yaml
        self.assertEqual(yaml.safe_load(config.read_text())['platforms'], {'telegram': {'enabled': True}})
        saved = list((f.control/'backups').glob('repair-*/selections/platforms-tech.json'))
        self.assertEqual(len(saved), 1)
        self.assertNotIn('telegram', saved[0].read_text())
        self.assertEqual(self.finding(f.run(True), 'platforms:tech')['status'], 'unchanged')

    def test_old_windows_runner_gate_is_widened_once(self):
        commit = '93ead633'
        text = subprocess.run(['git', '-C', str(ROOT), 'show', commit+':apps/lms/installer-assets/windows-addon-v3/install.ps1'], capture_output=True)
        if text.returncode:
            self.skipTest('git history is not available')
        f = suite.Fixture(self.root, 'Windows', 3)
        runner = f.install/'control/runner.ps1'
        old = b'\xef\xbb\xbf' + text.stdout
        suite.write(runner, old)
        r = repair.Repair(f.home, f.install, 'Windows', f.hermes, apply=True)
        r.names = ['copywriter', 'designer', 'tech']; r.unreadable = set(); r.files = {}
        r.backup = f.control/'backups/repair-test'
        r.compatibility()
        self.assertEqual(self.finding({'findings': r.findings}, 'windows-runner-gate')['status'], 'fixed')
        self.assertTrue(runner.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'0\\.2[23]', runner.read_bytes())
        self.assertEqual((r.backup/'control/runner.ps1').read_bytes(), old)
        again = repair.Repair(f.home, f.install, 'Windows', f.hermes, apply=True)
        again.names = r.names; again.unreadable = set(); again.compatibility()
        self.assertEqual(self.finding({'findings': again.findings}, 'windows-runner-gate')['status'], 'unchanged')

    def test_foreign_linux_user_unit_is_never_stopped(self):
        f = suite.Fixture(self.root, 'Linux', 1)
        unit = f.home/'.config/systemd/user/hermes-gateway.service'
        foreign = '[Service]\nExecStart=/opt/foreign-hermes/bin/hermes gateway run\n'
        suite.write(unit, foreign)
        out = f.run(True)
        self.assertEqual(self.finding(out, 'linux-user-gateway')['status'], 'skipped:foreign-user-unit')
        self.assertEqual(unit.read_text(), foreign)
        self.assertFalse(json.loads(f.state.read_text())['masked'])

    def test_content_offline_attempt_stays_unchanged_and_verify_flags_it(self):
        import types
        f = suite.Fixture(self.root, 'Linux', 3)
        calls = []
        def plan(home, system, download=True, search_path=None, hermes=None):
            calls.append(download)
            return [{'code': 'content:u2net-model', 'status': 'needs-fix', 'detail': 'model', 'paths': [], 'apply': lambda: 'unavailable:network'}]
        stub = types.SimpleNamespace(plan=plan, check_bundle=lambda: None)
        original = repair.load
        with patch.object(repair, 'load', side_effect=lambda name: stub if name == 'content-repairs' else original(name)):
            r = repair.Repair(f.home, f.install, 'Linux', f.hermes, apply=True)
            r.content()
            self.assertFalse(r.changed)
            self.assertEqual(self.finding({'findings': r.findings}, 'content:u2net-model')['status'], 'unavailable:network')
            v = repair.Repair(f.home, f.install, 'Linux', f.hermes, verify=True)
            v.content()
            self.assertEqual(self.finding({'findings': v.findings}, 'content:u2net-model')['status'], 'unfixable')
            self.assertEqual(v.result()['exitCode'], 2)
        self.assertEqual(calls, [True, True])

    def test_edited_runner_on_hermes_023_is_fixed_without_update_loop(self):
        runner = payload(RUNNER_UPDATED) + b'\n# learner edit\n'
        f = self.fixture(runner, payload(SERVERS['b2469181']))
        out = f.run(True)
        self.assertEqual(self.finding(out, 'webshell-runner-023')['status'], 'fixed')
        self.assertEqual(self.finding(out, 'webshell')['status'], 'skipped:panel-edited')
        fixed = (f.install/'web-shell/runner.py').read_bytes()
        self.assertTrue(fixed.endswith(b'# learner edit\n'))
        self.assertIn(b'from tools.approval_context import', fixed)
        again = f.run(True)
        self.assertEqual(self.finding(again, 'webshell-runner-023')['status'], 'unchanged')
        self.assertNotEqual(self.finding(again, 'webshell')['status'], 'needs-platform-update')

    def test_panel_platforms_writer_never_duplicates_the_block(self):
        if not NODE:
            self.skipTest('node is not installed')
        import yaml
        fixed = panel.fix_server(payload(SERVERS['b2469181']).decode())
        start = fixed.index('function ensureTelegramPlatformEnabled(')
        source = fixed[start:fixed.index('\nfunction ', start + 1)]
        cases = {
            'none': 'model:\n  default: x\n',
            'disabled': 'platforms:\n  telegram:\n    enabled: false\n    extra: 1\n  discord:\n    enabled: true\nmodel: x\n',
            'other-only': 'platforms:\n  discord:\n    enabled: true\nmodel: x\n',
            'no-enabled': 'platforms:\n  telegram:\n    token_env: T\n',
            'enabled': 'platforms:\n  telegram:\n    enabled: true\n',
            'flow': 'platforms: {telegram: {enabled: false}}\n',
            'inline-telegram': 'platforms:\n  telegram: {enabled: false}\n',
            # Codex round 2: column-0 comments inside nested mappings.
            'comment-before-telegram': 'platforms:\n# Telegram settings\n  telegram:\n    enabled: false\n    token_env: T\n  discord:\n    enabled: true\nmodel: x\n',
            'comment-before-enabled': 'platforms:\n  telegram:\n# toggle\n    enabled: false\n  discord:\n    enabled: true\nmodel: x\n',
            'trailing-comment': 'platforms:  # adapters\n  discord:\n    enabled: true\n# end\nmodel: x\n',
            'bom': '\ufeffplatforms:\n  discord:\n    enabled: true\nmodel: x\n',
            # Codex round 3: valid YAML in shapes the writer must not touch.
            'four-space': 'platforms:\n    telegram:\n        enabled: false\n        token_env: T\n    discord:\n        enabled: true\n',
            'one-space': 'platforms:\n telegram:\n  enabled: false\n',
            'six-space-enabled': 'platforms:\n  telegram:\n      enabled: false\n',
            'quoted-platforms': '"platforms":\n  telegram:\n    enabled: false\n',
            'quoted-telegram': 'platforms:\n  "telegram":\n    enabled: false\n',
            'quoted-enabled': 'platforms:\n  telegram:\n    "enabled": false\n',
            'crlf-comment': 'platforms:  # adapters\r\n  telegram:\r\n    enabled: false\r\nmodel: x\r\n',
            # Codex round 4: valid documents outside the plain block shape.
            'flow-root': '{model: x, platforms: {telegram: {enabled: false}, discord: {enabled: true}}}\n',
            'empty-flow': '{}\n',
            'document-end': 'model: x\n...\n',
            'indented-root': '  model: x\n  other: y\n',
            'escaped-key': '"platform\\u0073":\n  telegram:\n    enabled: false\n',
            'explicit-key': '? platforms\n: telegram: {enabled: false}\n',
            'merge-anchor': 'defaults: &defaults\n  platforms:\n    discord:\n      enabled: true\nprofile:\n  <<: *defaults\n',
            'hermes-like': 'model:\n  default: gpt-5\n  provider: openai-codex\ntoolsets:\n- file\n- terminal\nplatforms:\n  telegram:\n    enabled: false\n    extra:\n      token_env: TELEGRAM_BOT_TOKEN\nstt:\n  provider: local\n',
            'hermes-like-no-platforms': 'model:\n  default: gpt-5\ntoolsets:\n- file\n',
            # Codex round 5: values elsewhere must not change; empty enabled with a mapping.
            'keep-literal': 'description: |+\n  hello\n\n\n',
            'keep-folded': 'description: >+\n  hello\n\n\n',
            'strip-literal': 'description: |-\n  hello  ',
            'enabled-mapping': 'platforms:\n  telegram:\n    enabled:\n      setting: x\n  discord:\n    enabled: true\n',
            'multiline-quoted': 'note: "one\n  two"\nmodel: x\n',
            'no-final-newline': 'model: x',
            # Codex round 6: keys equal as Python objects are duplicates too.
            'equal-keys-bool': 'custom:\n  true: one\n  1: two\n',
            'equal-keys-float': 'custom:\n  1: one\n  1.0: two\n',
            'already-duplicated': 'platforms:\n  telegram:\n    enabled: false\n  telegram:\n    enabled: false\n',
        }
        script = ('const vm=require("vm");const {spawnSync}=require("child_process");const cases=JSON.parse(process.argv[1]);const out={};'
                  'for(const [k,v] of Object.entries(cases)){let w=null;const c={join:(a,b)=>b,profileDir:()=>"",readText:()=>v,atomicWriteFile:(p,t)=>{w=t},'
                  'spawnSync,process,HERMES_PYTHON:process.argv[3],HERMES_AGENT_ROOT:process.argv[4]};'
                  'vm.createContext(c);vm.runInContext(process.argv[2]+"\\nensureTelegramPlatformEnabled(\\"tech\\")",c);out[k]=w;}'
                  'console.log(JSON.stringify(out));')
        # The panel proves each write with Hermes' strict loader; here the PyYAML fallback.
        with tempfile.TemporaryDirectory() as agent_root:
            run = subprocess.run([NODE, '-e', script, json.dumps(cases), source, sys.executable, agent_root], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            written = json.loads(run.stdout)
            missing = subprocess.run([NODE, '-e', script, json.dumps(cases), source, agent_root+'/no-python', agent_root], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 0, missing.stderr)
            self.assertEqual(set(json.loads(missing.stdout).values()), {None}, 'no proof, no write')
        class Strict(yaml.SafeLoader):
            pass
        def mapping(loader, node, deep=False):
            keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
            if len(keys) != len(set(keys)):
                raise ValueError('duplicate key')
            return yaml.SafeLoader.construct_mapping(loader, node, deep)
        Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        for name in ['none', 'disabled', 'other-only', 'no-enabled', 'comment-before-telegram', 'comment-before-enabled', 'trailing-comment']:
            with self.subTest(case=name):
                text = written[name]
                self.assertEqual(len([l for l in text.split('\n') if l.startswith('platforms:')]), 1)
                data = yaml.load(text, Loader=Strict)
                self.assertIs(data['platforms']['telegram']['enabled'], True)
                if 'discord' in cases[name]:
                    self.assertEqual(data['platforms']['discord'], {'enabled': True})
        self.assertEqual(yaml.load(written['comment-before-telegram'], Loader=Strict)['platforms']['telegram']['token_env'], 'T')
        self.assertIsNone(written['already-duplicated'])
        for name in ['keep-literal', 'keep-folded', 'strip-literal', 'enabled-mapping', 'multiline-quoted', 'equal-keys-bool', 'equal-keys-float']:
            self.assertIsNone(written[name], name)
        self.assertTrue(written['no-final-newline'].startswith('model: x\n# Infobiz'))
        for name in ['flow-root', 'empty-flow', 'document-end', 'indented-root', 'escaped-key', 'explicit-key', 'merge-anchor']:
            self.assertIsNone(written[name], name)
        for name in ['hermes-like', 'hermes-like-no-platforms']:
            self.assertIsNotNone(written[name], name)
        for name in ['four-space', 'one-space', 'six-space-enabled', 'quoted-platforms', 'quoted-telegram', 'quoted-enabled']:
            self.assertIsNone(written[name], name)
        self.assertIn('\r\n', written['crlf-comment'])
        self.assertNotIn('\n', written['crlf-comment'].replace('\r\n', ''))
        # Every written file keeps all values except the target telegram.enabled.
        for name, text in written.items():
            if text is None:
                continue
            with self.subTest(values=name):
                before = yaml.load(cases[name].lstrip('\ufeff'), Loader=Strict) or {}
                after = yaml.load(text.lstrip('\ufeff'), Loader=Strict)
                telegram = (before.get('platforms') or {}).get('telegram') or {}
                expected = dict(before, platforms=dict(before.get('platforms') or {}, telegram=dict(telegram, enabled=True)))
                self.assertEqual(after, expected)
        self.assertTrue(written['bom'].startswith('\ufeffplatforms:'))
        bom = yaml.load(written['bom'].lstrip('\ufeff'), Loader=Strict)
        self.assertEqual(bom['platforms'], {'telegram': {'enabled': True}, 'discord': {'enabled': True}})
        self.assertEqual(yaml.safe_load(written['disabled'])['platforms']['discord'], {'enabled': True})
        self.assertEqual(yaml.safe_load(written['disabled'])['platforms']['telegram']['extra'], 1)
        self.assertEqual(yaml.safe_load(written['no-enabled'])['platforms']['telegram']['token_env'], 'T')
        for name in ['enabled', 'flow', 'inline-telegram']:
            self.assertIsNone(written[name], name)

    def test_windows_panel_task_is_started_only_when_ours(self):
        import types
        f = suite.Fixture(self.root, 'Windows', 3)
        suite.write(f.install/'control/webshell-runtime.json', json.dumps({'schema': 2, 'taskName': 'SerditovAgentsWebShell-0123456789abcdef'}))
        class Health:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        opener = types.SimpleNamespace(open=lambda url, timeout=None: Health())
        for row, expected, changed in [({'state': 'Started', 'owned': True}, 'fixed', True),
                                       ({'state': 'Running', 'owned': True}, 'unchanged', False),
                                       ({'state': 'Ready', 'owned': False}, 'skipped:task-not-owned', False),
                                       ({'state': 'Missing'}, 'skipped:task-not-owned', False)]:
            with self.subTest(row=row):
                calls = []
                def command(args, timeout=30, row=row):
                    calls.append(args)
                    return subprocess.CompletedProcess(args, 0, json.dumps(row), '')
                r = repair.Repair(f.home, f.install, 'Windows', f.hermes, apply=True)
                with patch.object(repair, 'command', side_effect=command), patch.object(repair.urllib.request, 'build_opener', return_value=opener):
                    r.windows_panel_start()
                self.assertEqual(self.finding({'findings': r.findings}, 'windows-panel-restart')['status'], expected)
                self.assertEqual(r.changed, changed)
                self.assertIn("SerditovAgentsWebShell-0123456789abcdef", calls[0][-1])
                self.assertIn(str(f.install/'control/runner.ps1'), calls[0][-1])
        suite.write(f.install/'control/webshell-runtime.json', json.dumps({'taskName': 'Evil; Remove-Item'}))
        r = repair.Repair(f.home, f.install, 'Windows', f.hermes, apply=True)
        with patch.object(repair, 'command') as command:
            r.windows_panel_start()
        command.assert_not_called()
        self.assertEqual(self.finding({'findings': r.findings}, 'windows-panel-restart')['status'], 'skipped:task-unknown')


if __name__ == '__main__':
    unittest.main()
