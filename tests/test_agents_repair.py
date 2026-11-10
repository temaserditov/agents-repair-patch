"""Offline regression fixtures. No real services, network, provider or user accounts."""
import ast
from functools import lru_cache
from contextlib import redirect_stdout, contextmanager
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
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch, Mock
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT/'installer/repair-patch'

def module(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result

repair = module('test_repair',BUNDLE/'agents_repair.py')
import yaml

def tree(root):
    return {p.relative_to(root).as_posix(): ('link:'+os.readlink(p) if p.is_symlink() else (hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mode & 0o777)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}

def write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data if isinstance(data,bytes) else data.encode())


@lru_cache(maxsize=1)
def upstream_bytes():
    with tarfile.open(ROOT/'apps/lms/installer-assets/v0.3.0/hermes-agent-v2026.9.24.tar.gz') as archive:
        path = next(m for m in archive.getmembers() if m.name.endswith('/hermes_cli/default_soul.py'))
        source = archive.extractfile(path).read().decode()
    node = next(n for n in ast.parse(source).body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='DEFAULT_SOUL_MD')
    return ast.literal_eval(node.value).encode()


class Fixture:
    def __init__(self, root, system='Darwin', tier=1, names=None):
        names = names or ['copywriter','designer','tech']
        self.home = root/'owner'
        self.hermes = self.home/('AppData/Local/hermes' if system=='Windows' else '.hermes')
        self.install = self.home/'InfobizAgents'
        self.control = self.install/'control/tariffs'
        self.profiles = self.hermes/'profiles'
        self.system = system
        self.files = {}
        for name in names:
            for leaf,data in [('AGENTS.md',('course agent '+name).encode()),('SOUL.md',upstream_bytes())]:
                write(self.profiles/name/leaf,data)
                self.files[name+'/'+leaf] = repair.sha(data if leaf=='AGENTS.md' else ('course soul '+name).encode())
            text = '# Infobiz Agents multi-gateway defaults\nkanban:\n  dispatch_in_gateway: false\nstt:\n  language: en\n'
            if name=='designer':
                text += 'platform_toolsets:\n  cli: [image_gen]\n  telegram: [image_gen]\n  web: [image_gen]\n'
            write(self.profiles/name/'config.yaml',text.replace('\n','\r\n') if system=='Windows' else text)
            write(self.profiles/name/'.env','GROQ_API_KEY=CANARY_PROFILE_SECRET\nTELEGRAM_ALLOWED_USERS=CANARY_PRIVATE_ID\n')
            write(self.profiles/name/'auth.json','{"key":"CANARY_AUTH_SECRET"}')
            write(self.profiles/name/'memories/student.md','PRIVATE_MEMORY_CANARY')
        write(self.hermes/'config.yaml','# Infobiz Agents multi-gateway defaults\nkanban:\n  dispatch_in_gateway: false\n')
        write(self.hermes/'.env','HERMES_LOCAL_STT_COMMAND=CANARY_ROOT_SECRET\n')
        write(self.install/'install.log',b'one https://fixture/?SeCrEt="FIRST_CANARY"&x=1 two ?secret=SECOND_CANARY\n')
        write(self.control/'tariff-files.json',json.dumps({'revision':'agents-native-content-20261103.1','schemaVersion':1,'files':self.files}))
        write(self.control/'last-apply.json',json.dumps({'tier':tier,'profiles':['copywriter','designer','tech']}))
        write(self.hermes/'hermes-agent/venv/bin/python',b'fixture-runtime')
        for name in names:
            write(self.control/'backups/20261103T123456-0123456789abcdef0123456789abcdef/incoming'/name/'SOUL.md', 'course soul '+name)
        if system=='Linux':
            self.fake_systemd(root)

    def fake_systemd(self, root):
        self.bin = root/'bin'; self.bin.mkdir()
        self.state = root/'systemd-state.json'
        self.state.write_text('{"active":true,"masked":false,"bus":"live"}')
        script = '''#!PYTHON
import json,os,sys
from pathlib import Path
p=Path(os.environ['REPAIR_TEST_SYSTEMD']); s=json.loads(p.read_text()); args=sys.argv[1:]
if '--user' in args:
 if s['bus']!='live' and ('disable' in args or not s['bus'].endswith('not found')):
  print(s['bus'],file=sys.stderr); sys.exit(1)
 if 'mask' in args:
  u=Path(os.environ['REPAIR_TEST_HOME'])/'.config/systemd/user/hermes-gateway.service';u.parent.mkdir(parents=True,exist_ok=True);u.symlink_to('/dev/null');s['masked']=True
elif 'show' in args:
 print('LoadState='+('not-found' if s.get('missing') else 'loaded')); print('ActiveState='+('active' if s['active'] else 'failed')); print('MainPID='+('4321' if s['active'] else '0'));print('NRestarts='+str(s.get('restarts',0)))
 print('FragmentPath=/etc/systemd/system/infobiz-hermes-gateway.service');print('DropInPaths=')
 import pwd
 print('User='+pwd.getpwuid(os.getuid()).pw_name)
 binary=os.environ['REPAIR_TEST_HOME']+'/.hermes/hermes-agent/venv/bin/hermes'
 print('ExecStart={ path='+binary+' ; argv[]='+binary+' gateway run ; ignore_errors=no ; }')
elif 'start' in args:s['active']=True
p.write_text(json.dumps(s)) if 'show' not in args else None
'''.replace('PYTHON',sys.executable)
        write(self.bin/'systemctl',script); (self.bin/'systemctl').chmod(0o755)
        write(self.bin/'ps', '''#!PYTHON
import json,os
s=json.load(open(os.environ['REPAIR_TEST_SYSTEMD']))
if s['active']:print('4321 1 '+os.environ['REPAIR_TEST_HOME']+'/.hermes/hermes-agent/venv/bin/hermes gateway run')
'''.replace('PYTHON',sys.executable)); (self.bin/'ps').chmod(0o755)
        write(self.home/'.config/systemd/user/hermes-gateway.service','[Service]\nExecStart='+str(self.hermes)+'/hermes-agent/venv/bin/hermes gateway run\n')

    def run(self, apply=False, verify=False, **kwargs):
        r = repair.Repair(self.home,self.install,self.system,self.hermes,apply=apply,verify=verify,**kwargs)
        # Never invoke real media utilities. Each media failure test overrides this.
        r.speech = lambda: r.add('local-voice',detail='synthetic gateway PATH checked')
        if self.system=='Linux':
            def identify(home):
                return {'pid':4321,'hermes_home':str(home),'supervisor':'systemd'} if json.loads(self.state.read_text())['active'] else None
            gateway=types.ModuleType('gateway');gateway.__path__=[]
            modules={'gateway':gateway,'gateway.control_socket':types.SimpleNamespace(identify_gateway=identify)}
            modules={k:v for k,v in modules.items() if k not in sys.modules}
            clock=[0.0]
            with patch.dict(sys.modules,modules),patch.object(repair.time,'sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)),patch.object(repair.time,'monotonic',side_effect=lambda:clock[0]),patch.dict(os.environ,{'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],'REPAIR_TEST_SYSTEMD':str(self.state),'REPAIR_TEST_HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home/'.config')}):
                return r.run()
        return r.run()


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        # These fixtures have no installed Hermes. Mock only its inspection
        # subprocess; configuration/file repair still uses the real helper.
        original_load = repair.load
        def load_with_runtime(name):
            helper = original_load(name)
            if name == 'team-runtime':
                helper.inspect_profiles = lambda profiles,names,**kw: {n:{'status':'ready'} for n in names}
            return helper
        self.runtime_patch = patch.object(repair,'load',side_effect=load_with_runtime)
        self.runtime_patch.start()
    def tearDown(self):
        self.runtime_patch.stop()
        self.temp.cleanup()
    def fixture(self, system='Darwin', tier=1):
        return Fixture(self.root,system,tier)
    def test_team_repair_reload_and_verify_real_tools(self):
        f=self.fixture('Windows');r=repair.Repair(f.home,f.install,'Windows',f.hermes,apply=True)
        r.names=['copywriter'];r.unreadable=set()
        helper=module('team_test',BUNDLE/'vendor/team-runtime.py')
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'inspect_profiles',return_value={'copywriter':{'status':'ready'}}) as inspect:
            r.team()
            self.assertTrue(r.changed)
            self.assertTrue(inspect.call_args.kwargs['reload'])
        r=repair.Repair(f.home,f.install,'Windows',f.hermes,verify=True);r.names=['copywriter'];r.unreadable=set()
        before=tree(f.home)
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'inspect_profiles',return_value={'copywriter':{'status':'missing-tools'}}) as inspect:
            r.team()
            self.assertFalse(inspect.call_args.kwargs['reload'])
        self.assertEqual(tree(f.home),before)
        # TASK43: an unconfirmed team probe is a warning, never exit 2/support.
        self.assertEqual(r.result()['exitCode'],0)
        finding=next(x for x in r.findings if x['code']=='team-tools:copywriter')
        self.assertEqual((finding['severity'],finding['status']),('warning','pending:team-tools'))
        self.assertIn('missing-tools',finding['detail']);self.assertIn('повтори verify',finding['detail'])
        for status,fragment in [('unavailable-runtime','exit 1: ModuleNotFoundError'),('pending-gateway-reload',''),('missing-gateway-plugin','')]:
            r=repair.Repair(f.home,f.install,'Windows',f.hermes,apply=True);r.names=['copywriter'];r.unreadable=set()
            with patch.object(repair,'load',return_value=helper),patch.object(helper,'inspect_profiles',return_value={'copywriter':{'status':status,'detail':fragment}}):
                r.team()
            finding=next(x for x in r.findings if x['code']=='team-tools:copywriter')
            self.assertEqual((finding['severity'],finding['status'],r.result()['exitCode']),('warning','pending:team-tools',0),status)
            self.assertIn(status,finding['detail'])
            if fragment:self.assertIn(fragment,finding['detail'])
        r=repair.Repair(f.home,f.install,'Windows',f.hermes,apply=True);r.names=['copywriter'];r.unreadable=set()
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'inspect_profiles',side_effect=ValueError('Linked team destination preserved')):
            r.team()
        finding=next(x for x in r.findings if x['code']=='team-tools:copywriter')
        self.assertEqual((finding['severity'],finding['status'],r.result()['exitCode']),('warning','pending:team-tools',0))
        self.assertNotIn('Linked team destination',finding['detail']);self.assertIn('ValueError',finding['detail'])

    def test_windows_multiplex_migration_and_pending_team_config(self):
        f=self.fixture('Windows');helper=module('team_multiplex',BUNDLE/'vendor/team-runtime.py')
        write(f.hermes/'bin/hermes.exe',b'fixture-launcher');state=f.hermes/'gateway_state.json'
        write(state,json.dumps({'served_profiles':['default']}))
        calls=[]
        def fake_command(args,timeout=30):
            calls.append((args,timeout))
            if args[1:3]==['gateway','migrate']:write(state,json.dumps({'served_profiles':['default','copywriter']}))
            return subprocess.CompletedProcess(args,0,'','')
        # verify: an unserved profile is pending, never exit 2
        r=repair.Repair(f.home,f.install,'Windows',f.hermes,verify=True);r.names=['copywriter'];r.unreadable=set()
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'unconfigured_reasons',return_value=[]),patch.object(helper,'inspect_profiles',return_value={'copywriter':{'status':'ready'}}),patch.object(repair,'command',side_effect=fake_command):
            r.team()
        finding=next(x for x in r.findings if x['code']=='windows-gateway-multiplex')
        self.assertEqual((finding['status'],finding['severity']),('pending:gateway-multiplex','warning'));self.assertIn('copywriter',finding['detail']);self.assertEqual(calls,[])
        self.assertEqual(r.result()['exitCode'],0)
        # apply: the official migration runs through the launcher before the probe
        r=repair.Repair(f.home,f.install,'Windows',f.hermes,apply=True);r.names=['copywriter'];r.unreadable=set()
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'unconfigured_reasons',return_value=[]),patch.object(helper,'inspect_profiles',return_value={'copywriter':{'status':'ready'}}) as probe,patch.object(repair,'command',side_effect=fake_command),patch.object(repair.time,'sleep'):
            r.team()
        self.assertEqual(calls,[([str(f.hermes/'bin/hermes.exe'),'gateway','migrate','--multiplex','--yes'],300)])
        finding=next(x for x in r.findings if x['code']=='windows-gateway-multiplex');self.assertEqual(finding['status'],'fixed');self.assertTrue(r.changed)
        self.assertTrue(probe.called)
        # already served: nothing to run; no record: nothing to say
        r=repair.Repair(f.home,f.install,'Windows',f.hermes,apply=True);r.names=['copywriter'];r.unreadable=set();calls.clear()
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'unconfigured_reasons',return_value=[]),patch.object(helper,'inspect_profiles',return_value={'copywriter':{'status':'ready'}}),patch.object(repair,'command',side_effect=fake_command):
            r.team()
        self.assertEqual(calls,[]);self.assertEqual(next(x for x in r.findings if x['code']=='windows-gateway-multiplex')['status'],'unchanged')
        state.unlink();r=repair.Repair(f.home,f.install,'Windows',f.hermes,apply=True);r.names=['copywriter'];r.unreadable=set()
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'unconfigured_reasons',return_value=[]),patch.object(helper,'inspect_profiles',return_value={'copywriter':{'status':'ready'}}),patch.object(repair,'command',side_effect=fake_command):
            r.team()
        self.assertFalse(any(x['code']=='windows-gateway-multiplex' for x in r.findings))
        # verify on a profile whose team config is off: pending with reason codes, exit 0; apply still fixes it
        r=repair.Repair(f.home,f.install,'Windows',f.hermes,verify=True);r.names=['copywriter'];r.unreadable=set()
        with patch.object(repair,'load',return_value=helper),patch.object(helper,'unconfigured_reasons',return_value=['plugin-not-enabled','toolsets-kanban-missing']):
            r.team()
        finding=next(x for x in r.findings if x['code']=='team-config:copywriter')
        self.assertEqual((finding['status'],finding['severity'],r.result()['exitCode']),('pending:team-config','warning',0))
        self.assertIn('plugin-not-enabled, toolsets-kanban-missing',finding['detail']);self.assertIn('repair --apply',finding['detail'])

    def test_dry_run_all_layouts_writes_no_byte(self):
        for system in ['Darwin','Linux','Windows']:
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                f = Fixture(Path(tmp).resolve(),system)
                before = tree(f.home)
                out = f.run()
                self.assertFalse(out['changed']); self.assertEqual(out['exitCode'],3)
                self.assertEqual(tree(f.home),before)
                self.assertTrue(any(x['status']=='planned' for x in out['findings']))
    def test_apply_and_repeat_all_layouts(self):
        for system in ['Darwin','Linux','Windows']:
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                f = Fixture(Path(tmp).resolve(),system)
                secrets = {p: p.read_bytes() for p in f.home.rglob('*') if p.name in {'.env','auth.json','student.md'}}
                self.assertTrue(f.run(True)['changed'])
                before = tree(f.home); again = f.run(True)
                self.assertFalse(again['changed']); self.assertEqual(before,tree(f.home))
                for p,data in secrets.items(): self.assertEqual(p.read_bytes(),data)
                cfg = yaml.safe_load((f.profiles/'designer/config.yaml').read_text())
                # TASK43: the Designer inherits the Hermes cli/telegram bundles after the
                # designer-tools repair; team activation must not pin them again. Kanban
                # reaches inherited platforms through the profile-wide opt-in.
                self.assertNotIn('cli',cfg['platform_toolsets']); self.assertNotIn('telegram',cfg['platform_toolsets'])
                self.assertIn('kanban',cfg['toolsets'])
                self.assertIn('code_execution',cfg['platform_toolsets']['web'])
                for backend in ['groq','local']: self.assertEqual(cfg['stt'][backend]['language'],'ru')
                self.assertNotIn(b'FIRST_CANARY',(f.install/'install.log').read_bytes())
                for name in ['copywriter','designer','tech']:
                    expected = (f.profiles/name/'AGENTS.md').read_bytes() if system=='Windows' else ('course soul '+name).encode()
                    self.assertEqual((f.profiles/name/'SOUL.md').read_bytes(),expected)
    def test_user_soul_addition_preserved(self):
        f = self.fixture()
        target = f.profiles/'designer/SOUL.md'; data = upstream_bytes()+b'\nstudent addition\n'; target.write_bytes(data)
        out = f.run(True); self.assertEqual(target.read_bytes(),data)
        self.assertTrue(any(x['code']=='soul:designer' and x['status']=='skipped:user-text' for x in out['findings']))
    def test_custom_toolsets_disabled_and_explicit_languages(self):
        for disabled in [False,True]:
            with self.subTest(disabled=disabled), tempfile.TemporaryDirectory() as tmp:
                f = Fixture(Path(tmp).resolve())
                target = f.profiles/'designer/config.yaml'; data = yaml.safe_load(target.read_text())
                data['platform_toolsets']['cli'] = ['web','file','my-tool']
                data['stt'] = {'language':'de','groq':{'language':'uk'},'local':{'language':'en'}}
                if disabled:data['agent'] = {'disabled_toolsets':['terminal']}
                target.write_text(yaml.safe_dump(data))
                out = f.run(True); cfg = yaml.safe_load(target.read_text())
                # TASK43: a saved cli list is the learner's; team tools reach Telegram only.
                self.assertEqual(cfg['platform_toolsets']['cli'],['web','file','my-tool'])
                self.assertEqual(cfg['stt']['language'],'de'); self.assertEqual(cfg['stt']['groq']['language'],'uk')
                self.assertEqual(cfg['stt']['local']['language'],'ru')
                if disabled:self.assertTrue(any(x['code']=='designer-tools' and x['status']=='skipped:explicitly-disabled' for x in out['findings']))
    def test_marked_kanban_only_and_preserve_other_fields(self):
        f = self.fixture(); target = f.hermes/'config.yaml'
        target.write_text('kanban:\n  dispatch_in_gateway: false\n')
        write(f.profiles/'tech/config.yaml','stt: {language: ru}\n')
        f.run(True); self.assertFalse(yaml.safe_load(target.read_text())['kanban']['dispatch_in_gateway'])
        text = '# Infobiz Agents multi-gateway defaults\nkanban:\n  dispatch_in_gateway: false\n  other: 2\n'
        self.assertEqual(yaml.safe_load(repair.kanban_text(text)),{'kanban':{'other':2}})
        for suffix in ['true','false # user edited']:
            text = '# Infobiz Agents multi-gateway defaults\nkanban:\n  dispatch_in_gateway: '+suffix+'\n'
            self.assertEqual(repair.kanban_text(text),text)
    def test_missing_soul_source_requires_update(self):
        f = self.fixture(); source = f.control/'backups/20261103T123456-0123456789abcdef0123456789abcdef/incoming/designer/SOUL.md'; source.unlink()
        out = f.run(True)
        self.assertTrue(any(x['code']=='soul:designer' and x['status']=='needs-platform-update' for x in out['findings']))
    def test_windows_source_hash_required_and_crlf_diagnosed(self):
        f = self.fixture('Windows'); target = f.profiles/'designer/AGENTS.md'; target.write_bytes(b'user addition')
        out = f.run(True)
        self.assertTrue(any(x['code']=='soul:designer' and x['status']=='needs-windows-v3' for x in out['findings']))
        self.assertTrue(any(x['code']=='config-format:designer' and x['detail']=='crlf: true' for x in out['findings']))
    def test_empty_soul(self):
        f = self.fixture(); (f.profiles/'designer/SOUL.md').write_bytes(b'\xef\xbb\xbf\r\n ')
        f.run(True); self.assertEqual((f.profiles/'designer/SOUL.md').read_bytes(),b'course soul designer')
    def test_incoming_without_inventory_hash_or_with_previous_hash(self):
        for previous in [None, repair.sha(b'previous package')]:
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as tmp:
                f=Fixture(Path(tmp).resolve())
                state=json.loads((f.control/'tariff-files.json').read_text())
                state['files'].pop('designer/SOUL.md')
                if previous: state['files']['designer/SOUL.md']=previous
                write(f.control/'tariff-files.json',json.dumps(state))
                latest=b'newest package SOUL\r\n'
                write(f.control/'backups/20261105T123456-ffffffffffffffffffffffffffffffff/incoming/designer/SOUL.md',latest)
                out=f.run(True)
                self.assertEqual((f.profiles/'designer/SOUL.md').read_bytes(),latest)
                self.assertEqual(json.loads((f.control/'tariff-files.json').read_text())['files']['designer/SOUL.md'],repair.sha(latest))
                self.assertIn({'code':'soul:designer','status':'fixed'},[{k:x[k] for k in ['code','status']} for x in out['findings']])
    def test_incoming_invalid_text_names_and_linked_paths_ignored(self):
        for candidate in [upstream_bytes(), b'\xef\xbb\xbf\r\n ', b'\xff']:
            with self.subTest(candidate=candidate[:15]), tempfile.TemporaryDirectory() as tmp:
                f=Fixture(Path(tmp).resolve())
                source=next((f.control/'backups').glob('*/incoming/designer/SOUL.md'))
                source.write_bytes(candidate)
                for name in ['repair-20261106T123456-ffffffffffffffffffffffffffffffff','20261106T123456-short','old','20261106T123456-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF']:
                    write(f.control/'backups'/name/'incoming/designer/SOUL.md',b'ignored package')
                linked=f.control/'backups/20261107T123456-ffffffffffffffffffffffffffffffff/incoming/designer/SOUL.md'
                linked.parent.mkdir(parents=True); linked.symlink_to(f.profiles/'designer/AGENTS.md')
                out=f.run(True)
                self.assertEqual((f.profiles/'designer/SOUL.md').read_bytes(),upstream_bytes())
                self.assertTrue(any(x['code']=='soul:designer' and x['status']=='needs-platform-update' for x in out['findings']))
    def test_panel_personal_soul_exact_exception_over_local_http(self):
        audit={'ok':False,'checks':[{'id':'instructions','ok':False}],
               'agents':[{'id':p,'instructions':p!='tech'} for p in ['designer','tech']]}
        responses={'audit':audit,'tool-policy':{'ok':True},'agents/designer/preflight':{'ok':True}}
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                endpoint=self.path.removeprefix('/api/'); calls.append(endpoint)
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(responses[endpoint]).encode())
            do_POST=do_GET
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=Thread(target=server.serve_forever);thread.start()
        try:
            f=self.fixture()
            write(f.install/'web-shell.url','http://127.0.0.1:'+str(server.server_port)+'/?token=local-fixture')
            for personal,other,endpoint_failure in [(True,False,None),(False,False,None),(True,True,None),(True,False,'tool-policy'),(True,False,'agents/designer/preflight')]:
                with self.subTest(personal=personal,other=other,endpoint=endpoint_failure):
                    r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True)
                    r.files=dict(f.files)
                    data=b'personal tech SOUL'
                    write(f.profiles/'tech/SOUL.md',data)
                    if not personal:r.files['tech/SOUL.md']=repair.sha(data)
                    r.soul('tech')
                    audit['checks']=[{'id':'instructions','ok':False}]+([{'id':'sessions','ok':False}] if other else [])
                    for endpoint in ['tool-policy','agents/designer/preflight']:responses[endpoint]['ok']=endpoint!=endpoint_failure
                    calls.clear();r.panel_api(True)
                    expected='skipped:user-soul' if personal and not other and not endpoint_failure else 'unfixable'
                    self.assertEqual(r.findings[-1]['status'],expected)
                    if expected=='skipped:user-soul':
                        self.assertIn('повтори штатное обновление, оно перенесёт правки',r.findings[-1]['detail'])
                        self.assertNotIn('поддержк',r.findings[-1]['detail'])
                    self.assertEqual(r.result()['exitCode'],0 if expected=='skipped:user-soul' else 2)
                    self.assertEqual(r.findings[-1]['severity'],'warning' if expected=='skipped:user-soul' else 'error')
                    self.assertEqual(calls,['tool-policy','audit','agents/designer/preflight'])
        finally:
            server.shutdown();server.server_close();thread.join()
    def test_redaction_matches_awk_and_preserves_permissions(self):
        f = self.fixture(); path = f.install/'install.log'
        text = b'''prefix ?SECRET='abc'&other=1 ?secret="xyz" ?sEcReT=aaa\\suffix\r\nsecret=<redacted>\n'''
        path.write_bytes(text); path.chmod(0o640)
        expected = repair.SECRET_URL.sub(b'secret=<redacted>',text)
        f.run(True); self.assertEqual(path.read_bytes(),expected); self.assertEqual(path.stat().st_mode & 0o777,0o640)
        self.assertEqual(repair.SECRET_URL.sub(b'secret=<redacted>',expected),expected)
    def test_no_env_read_by_release_configure(self):
        f = self.fixture()
        r = repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=True)
        r.speech = lambda: None
        r.runtime.env_values = lambda text: (_ for _ in ()).throw(AssertionError('no real env')) if 'CANARY' in text else {}
        out = r.run(); text = json.dumps(out)
        for value in ['CANARY_PROFILE_SECRET','CANARY_ROOT_SECRET','CANARY_PRIVATE_ID','CANARY_AUTH_SECRET','PRIVATE_MEMORY_CANARY','FIRST_CANARY','SECOND_CANARY']:
            self.assertNotIn(value,text)
        for path in f.control.rglob('*'):
            if path.is_file():
                for value in [b'CANARY_PROFILE_SECRET',b'CANARY_PRIVATE_ID',b'FIRST_CANARY']:self.assertNotIn(value,path.read_bytes())
    def test_cli_report_exit_codes_and_private_mode(self):
        f = self.fixture('Windows'); report = self.root/'report.json'; stdout = io.StringIO()
        with patch.dict(os.environ,{'AGENTS_REPAIR_HERMES_HOME':str(f.hermes)}), patch.object(repair.platform,'system',return_value='Windows'), redirect_stdout(stdout):
            code = repair.main(['diagnose','--home',str(f.home),'--json',str(report),'--no-report'])
        self.assertEqual(code,3); self.assertEqual(report.stat().st_mode & 0o777,0o600)
        self.assertIn('Пришли этот файл в поддержку.',stdout.getvalue())
        self.assertNotIn('CANARY',stdout.getvalue()); self.assertNotIn('FIRST_CANARY',report.read_text())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(repair.main(['diagnose','--home',str(self.root/'absent'),'--json',str(report)]),2)
        with patch.object(repair.Repair,'run',side_effect=RuntimeError('SECRET_CANARY')),redirect_stdout(io.StringIO()):
            self.assertEqual(repair.main(['diagnose','--home',str(f.home),'--json',str(report)]),1)
        self.assertNotIn('SECRET_CANARY',report.read_text())
        self.assertIn('Причина: RuntimeError',report.read_text())
    def test_failure_summary_repeats_only_patch_literals(self):
        self.assertEqual(repair.failure_summary(ValueError('Missing bundled helper')),'ValueError: Missing bundled helper')
        self.assertEqual(repair.failure_summary(ValueError('Linked team destination preserved')),'ValueError: Linked team destination preserved')
        self.assertEqual(repair.failure_summary(ValueError('token=SECRET_CANARY at /home/x/.env')),'ValueError')
        self.assertEqual(repair.failure_summary(RuntimeError('SECRET_CANARY')),'RuntimeError')
        self.assertEqual(repair.failure_summary(FileNotFoundError(2,'No such file or directory','/home/x/SECRET_CANARY.json')),'FileNotFoundError: No such file or directory')
        self.assertEqual(repair.failure_summary(UnicodeDecodeError('utf-8',b'\xff',0,1,'invalid start byte')),'UnicodeDecodeError')
    def test_diagnose_default_downloads_one_file_and_send_instruction(self):
        f=self.fixture('Windows');stdout=io.StringIO()
        with patch.dict(os.environ,{'AGENTS_REPAIR_HERMES_HOME':str(f.hermes)}),patch.object(repair.platform,'system',return_value='Windows'),redirect_stdout(stdout):
            self.assertEqual(repair.main(['diagnose','--home',str(f.home),'--no-report']),3)
        reports=list((f.home/'Downloads').glob('*.json'))
        self.assertEqual(len(reports),1)
        self.assertIn('findings',json.loads(reports[0].read_text()))
        self.assertIn('Пришли этот файл в поддержку.',stdout.getvalue())
    def test_symlink_ancestors_and_protected_inventory_refused(self):
        f = self.fixture(); original = f.profiles/'designer'; renamed = f.profiles/'original'; original.rename(renamed); original.symlink_to(renamed,target_is_directory=True)
        before = tree(f.home)
        with self.assertRaises(ValueError): f.run(True)
        self.assertEqual(before,tree(f.home))
        self.assertFalse(repair.managed_name('designer/.env'))
        self.assertFalse(repair.managed_name('designer/sub/auth.json'))
        self.assertFalse(repair.managed_name('designer/../SOUL.md'))
        self.assertFalse(repair.managed_name('designer/CON.txt'))
    def test_linux_missing_bus_variants_and_already_disabled(self):
        for bus in ['Failed to connect','No medium found','not defined','Failed to get D-Bus','Connection refused','live','Unit hermes-gateway.service not found']:
            with self.subTest(bus=bus), tempfile.TemporaryDirectory() as tmp:
                f = Fixture(Path(tmp).resolve(),'Linux'); state = json.loads(f.state.read_text()); state.update(bus=bus,active=False); f.state.write_text(json.dumps(state))
                out = f.run(True)
                # A not-found error still means a live bus; fixture must allow mask.
                self.assertTrue((f.home/'.config/systemd/user/hermes-gateway.service').is_symlink())
                self.assertTrue(json.loads(f.state.read_text())['active'])
                self.assertTrue(out['changed']); self.assertFalse(f.run(True)['changed'])
    def test_linux_missing_system_service_no_commands_to_user(self):
        f = self.fixture('Linux'); state = json.loads(f.state.read_text()); state['missing']=True; f.state.write_text(json.dumps(state))
        out = f.run(); self.assertEqual(out['exitCode'],2); self.assertTrue((f.home/'.config/systemd/user/hermes-gateway.service').is_file())
    def test_linux_verify_identity_and_restart_stability(self):
        f = self.fixture('Linux'); f.run(True)
        identity = types.ModuleType('gateway.control_socket'); identity.identify_gateway=lambda home:{'pid':4321,'hermes_home':str(home)}
        gateway = types.ModuleType('gateway'); gateway.__path__=[]
        original = repair.command
        def run(args,timeout=30):
            if args[0]=='ps':return subprocess.CompletedProcess(args,0,'4321 1 '+str(f.hermes)+'/hermes-agent/venv/bin/hermes gateway run\n','')
            return original(args,timeout)
        with patch.dict(sys.modules,{'gateway':gateway,'gateway.control_socket':identity}),patch.object(repair,'command',side_effect=run):
            out=f.run(verify=True)
        self.assertTrue(any(x['code']=='linux-single-gateway' and x['status']=='unchanged' for x in out['findings']))
    def test_windows_content_resume_next_step(self):
        f = self.fixture('Windows',3)
        out = f.run(True); self.assertEqual(out['nextStep'],'run-windows-v3-resume-content')
        self.assertEqual(out['exitCode'],3)
    def test_tirith_retry_and_windows_skip(self):
        f = self.fixture('Windows'); write(f.hermes/'.tirith-install-failed','download_failed')
        out=f.run(True); self.assertTrue(any(x['status']=='skipped:windows-official' for x in out['findings']))
    def test_windows_hermes_yaml_fallback(self):
        stub=types.ModuleType('hermes_yaml');stub.safe_load=yaml.safe_load;stub.safe_dump=yaml.safe_dump
        with patch.dict(sys.modules,{'yaml':None,'hermes_yaml':stub}):
            f=self.fixture('Windows');self.assertTrue(f.run(True)['changed'])
    def test_vendor_and_generated_baselines_match_sources(self):
        for name,source in {'sync-tariff-panel.py':ROOT/'installer/packages/shared','student-skill-repairs.py':ROOT/'installer/packages/shared','designer-tools.py':ROOT/'installer/packages/shared','student-runtime.py':ROOT/'installer/packages/shared','security-tools.py':ROOT/'installer/packages/shared','agents-diagnose.mjs':ROOT/'apps/lms/installer-assets/diagnostics','profile-probe.py':ROOT/'apps/lms/installer-assets/diagnostics'}.items():
            self.assertEqual((BUNDLE/'vendor'/name).read_bytes(),(source/name).read_bytes(),name)
        task=(BUNDLE/'ЗАДАНИЕ-ДЛЯ-CODEX.md').read_text()
        self.assertIn('автоматически перенесёт личные',task)
        self.assertNotIn('написать в поддержку',task)
        builder=module('baselines',ROOT/'installer/scripts/build-repair-baselines.py')
        self.assertEqual(json.loads((BUNDLE/'known-baselines.json').read_text()),builder.build())
        self.assertIn(repair.sha(repair.normalized_soul(upstream_bytes())),builder.build()['upstreamSoulHashes'])
    def test_bundle_deterministic_checksums_manifest_and_task(self):
        builder=module('zip_builder',ROOT/'installer/scripts/build-repair-patch.py')
        a=self.root/'a';b=self.root/'b';kwargs=dict(release_url='https://example.com/permanent.zip',source_url='https://example.com/source',commit='93ead633')
        builder.build('v2026.11.05.1',a,**kwargs);builder.build('v2026.11.05.1',b,**kwargs)
        self.assertEqual(tree(a),tree(b))
        payload=(a/'agents-repair-patch-v2026.11.05.1.zip').read_bytes()
        self.assertIn(repair.sha(payload),(a/'ЗАДАНИЕ-ДЛЯ-CODEX.md').read_text())
        import zipfile
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(set(archive.namelist()),set(manifest['files'])|{'manifest.json'})
            for name,digest in manifest['files'].items():self.assertEqual(repair.sha(archive.read(name)),digest)
    def test_verify_current_windows_exits_zero(self):
        f=self.fixture('Windows');f.run(True)
        state=json.loads((f.control/'tariff-files.json').read_text());state['revision']='agents-release-20261108.1'
        write(f.control/'tariff-files.json',json.dumps(state))
        for name in repair.PACKAGE_HELPERS:write(f.control/'post-install'/name,'fixture')
        for name in ['carousel-style-brief','motion-brief-builder']:write(f.profiles/'designer/skills'/name/'SKILL.md','fixture')
        write(f.install/'control/watchdog.js','fixture')
        write(f.install/'control/webshell-runtime.json','{"schema":2,"recoveryEnabled":true}')
        before=tree(f.home);out=f.run(verify=True)
        self.assertEqual(out['exitCode'],0);self.assertEqual(out['nextStep'],'none');self.assertEqual(tree(f.home),before)
        self.assertTrue(any(x['code']=='panel-api' and x['status']=='skipped:panel-token-unavailable' for x in out['findings']))
        write(f.install/'control/webshell-runtime.json','{"schema":2,"recoveryEnabled":true,"scheduledTasks":"deferred"}')
        before=tree(f.home);out=f.run(verify=True)
        self.assertEqual(out['exitCode'],0);self.assertEqual(out['nextStep'],'none');self.assertEqual(tree(f.home),before)
        task=next(x for x in out['findings'] if x['code']=='windows-panel-task')
        self.assertEqual(task['status'],'unavailable:task-query');self.assertEqual(task['severity'],'warning')
        self.assertFalse(any(x['status']=='needs-windows-v3' for x in out['findings']))
    def test_windows_tasks_check_live_scheduler_instead_of_receipt(self):
        f=self.fixture('Windows')
        task='SerditovAgentsWebShell-0123456789abcdef'
        names=[task,task.replace('WebShell-','Control-'),task.replace('WebShell-','Watchdog-'),'Hermes_Gateway_test']
        rows=[{'name':n,'state':'Ready','enabled':True,'available':True,'restart':3,'interactive':True,'logon':True,'delaySeconds':[120 if 'WebShell-' in n else 90 if 'Control-' in n else 180]} for n in names]
        modules={'hermes_cli.gateway_windows':types.SimpleNamespace(get_task_name=lambda:'Hermes_Gateway_test')}
        for defect in [None,'deleted','disabled','no-logon','no-restart','no-interactive','no-delay','watchdog-no-delay','gateway-disabled']:
            current=json.loads(json.dumps(rows))
            if defect=='deleted':current=current[1:]
            if defect=='disabled':current[0]['state']='Disabled'
            if defect=='no-delay':current[0]['delaySeconds']=[]
            if defect=='watchdog-no-delay':current[2]['delaySeconds']=[]
            if defect=='no-logon':current[0]['logon']=False
            if defect=='no-restart':current[0]['restart']=0
            if defect=='no-interactive':current[0]['interactive']=False
            if defect=='gateway-disabled':current[-1]['enabled']=False
            r=repair.Repair(f.home,f.install,'Windows',f.hermes,verify=True)
            def query(args, timeout):
                self.assertEqual(args[:3],['powershell.exe','-NoProfile','-NonInteractive'])
                self.assertIn('Get-ScheduledTask',args[-1]);self.assertNotIn('Start-ScheduledTask',args[-1])
                return subprocess.CompletedProcess(args,0,json.dumps(current),'')
            with patch.dict(sys.modules,modules),patch.object(repair,'command',side_effect=query):
                # Stale 'active'/'deferred' in JSON must never decide the result.
                r.windows_tasks({'taskName':task,'scheduledTasks':'active' if defect else 'deferred'})
            findings=r.findings
            failed=[x for x in findings if x['status']!='unchanged']
            self.assertEqual(bool(failed),defect is not None,findings)
            if failed:self.assertIn('-InstallTasks',failed[0]['detail'])

    def test_invalid_yaml_warning_without_exception_values(self):
        f=self.fixture('Windows');write(f.profiles/'designer/config.yaml','broken: [CANARY_UNCLOSED')
        report=self.root/'broken.json';stdout=io.StringIO()
        with patch.dict(os.environ,{'AGENTS_REPAIR_HERMES_HOME':str(f.hermes)}),patch.object(repair.platform,'system',return_value='Windows'),redirect_stdout(stdout):
            code=repair.main(['repair','--apply','--home',str(f.home),'--json',str(report)])
        self.assertEqual(code,3);self.assertNotIn('CANARY_UNCLOSED',stdout.getvalue()+report.read_text())
        out=json.loads(report.read_text());finding=next(f for f in out['findings'] if f['code']=='configuration:designer')
        self.assertEqual(finding['status'],'skipped:unreadable-config')
    def test_managed_skills_never_changed_and_secrets_not_hashed(self):
        f=self.fixture();name='copywriter/skills/old/SKILL.md'
        write(f.profiles/name,'knowledge/voicebank/INDEX.md tools/swift-design-engine/CONTENT_ENGINE.md')
        state=json.loads((f.control/'tariff-files.json').read_text());state['files'][name]=repair.sha((f.profiles/name).read_bytes());write(f.control/'tariff-files.json',json.dumps(state))
        before=(f.profiles/name).read_bytes();out=f.run(True);self.assertEqual(before,(f.profiles/name).read_bytes())
        finding=next(x for x in out['findings'] if x['code']=='managed-skills')
        self.assertIn('ссылками: 1',finding['detail']);self.assertEqual(finding['status'],'needs-platform-update')
    def test_missing_vendor_refuses_execution(self):
        f=self.fixture()
        old=repair.HERE
        with patch.object(repair,'HERE',self.root/'missing-bundle'):
            with self.assertRaises((ValueError,OSError)):f.run(True)
        self.assertEqual(repair.HERE,old)
    def test_linux_verify_rejects_other_pid_and_restart_change(self):
        f=self.fixture('Linux');f.run(True)
        identity=types.ModuleType('gateway.control_socket');identity.identify_gateway=lambda home:{'pid':999,'hermes_home':str(home)}
        gateway=types.ModuleType('gateway');gateway.__path__=[]
        original=repair.command
        def run(args,timeout=30):
            if args[0]=='ps':return subprocess.CompletedProcess(args,0,'4321 1 '+str(f.hermes)+'/hermes-agent/venv/bin/hermes gateway run\n999 1 python -m hermes_cli.main gateway run\n','')
            return original(args,timeout)
        with patch.dict(sys.modules,{'gateway':gateway,'gateway.control_socket':identity}),patch.object(repair,'command',side_effect=run):out=f.run(verify=True)
        self.assertEqual(out['exitCode'],2);self.assertEqual(out['nextStep'],'support')
    def test_windows_v1_v2_v3_detection(self):
        f=self.fixture('Windows')
        for version in [1,2,3]:
            if version>=2:write(f.install/'control/webshell-runtime.json',json.dumps({'schema':2,'recoveryEnabled':version==3}))
            if version==3:write(f.install/'control/watchdog.js','fixture')
            out=f.run();finding=next(x for x in out['findings'] if x['code']=='windows-addon')
            self.assertIn('v'+str(version),finding['detail']);self.assertEqual(finding['status'],'unchanged' if version==3 else 'needs-windows-v3')
    def test_mac_media_working_no_network_and_failure_is_finite(self):
        f=self.fixture();r=repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=True)
        with patch.object(repair.urllib.request,'urlopen',side_effect=AssertionError('must not download')):
            r.mac_media({'ffmpeg':True,'ffprobe':True},'')
        self.assertEqual(r.findings[-1]['status'],'unchanged')
        with patch.object(repair.platform,'machine',return_value='arm64'),patch.object(repair,'command',return_value=subprocess.CompletedProcess([],0,'15.7.7','')),patch.object(repair.urllib.request,'urlopen',side_effect=repair.urllib.error.URLError('SECRET_NETWORK_CANARY')):
            r.mac_media({'ffmpeg':False,'ffprobe':True},'')
        self.assertEqual(r.findings[-1]['status'],'unavailable:download-unavailable');self.assertNotIn('SECRET_NETWORK_CANARY',json.dumps(r.findings))
    def test_mac_media_download_hash_and_broken_backup(self):
        f=self.fixture();r=repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=True)
        import zipfile
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr('ffmpeg',b'fixture executable')
        payload=buffer.getvalue();r.baselines['macMedia']['arm64']['ffmpeg']['sha256']=repair.sha(payload)
        response=io.BytesIO(payload);response.url='https://ffmpeg.martin-riedl.de/fixture.zip'
        target=f.home/'.local/bin/ffmpeg';write(target,b'broken x86 executable')
        def download(req, timeout):
            if req.get_header('User-agent')!='SerditovAgentsRepair/1.0':
                raise repair.urllib.error.HTTPError(req.full_url,403,'CDN rejects default Python client',None,None)
            self.assertEqual(req.get_header('Accept'),'application/zip')
            self.assertEqual(timeout,30)
            return response
        with patch.object(repair.platform,'machine',return_value='arm64'),patch.object(repair,'command',return_value=subprocess.CompletedProcess([],0,'15.7.7','')),patch.object(repair.urllib.request,'urlopen',side_effect=download):
            r.mac_media({'ffmpeg':False,'ffprobe':True},'')
        self.assertEqual(r.findings[-1]['status'],'fixed');self.assertEqual(target.read_bytes(),b'fixture executable')
        self.assertEqual((r.backup/'media/ffmpeg').read_bytes(),b'broken x86 executable')
    def test_tirith_failure_repeat_does_not_write_backups(self):
        f=self.fixture();write(f.hermes/'.tirith-install-failed','download_failed');write(f.hermes/'hermes-agent/tools/tirith_security.py','fixture')
        r=repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=True)
        r.security.repair_tirith=lambda home,log:'tirith:skipped:download_failed'
        before=tree(f.home);r.tirith();self.assertEqual(tree(f.home),before);self.assertFalse(r.changed)
    def test_rehearsals_can_be_imported_without_native_execution(self):
        for name in ['mac-patch-rehearsal.mjs','ubuntu-patch-rehearsal.mjs','windows-patch-native-rehearsal.mjs']:
            result=subprocess.run(['node',str(ROOT/'scripts'/name),'--describe'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0);self.assertFalse(json.loads(result.stdout)['native'])
    def test_content_readiness_models_match_overlay(self):
        source=ast.parse((ROOT/'apps/lms/installer-assets/windows-addon-v3/overlay.py').read_text())
        fn=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='content_ready')
        expr=next(n.value for n in ast.walk(fn) if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='models')
        factory=next(n for n in source.body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='CONTENT_SPEECH_MODELS')
        namespace={};exec(compile(ast.Module(body=[factory],type_ignores=[]),'overlay-models','exec'),namespace)
        models=lambda arm:eval(compile(ast.Expression(expr),'overlay-readiness','eval'),{**namespace,'arm':arm})
        self.assertEqual(repair.CONTENT_MODELS,{'arm64':models(True),'x64':models(False)})
    def test_content_readiness_large_models_streamed(self):
        f=self.fixture('Windows');r=repair.Repair(f.home,f.install,'Windows',f.hermes)
        portable=self.root/'portable'
        for name in ['runtime/Scripts/python.exe','tools/ffmpeg/bin/ffmpeg.exe','cli.py','tools/whisper-arm64/Release/whisper-cli.exe']:
            write(portable/name,'fixture')
        chunk=b'\0'*(1024*1024);digest=hashlib.sha256()
        model=portable/'resources/models/fixture-model.bin';model.parent.mkdir(parents=True)
        with model.open('wb') as stream:
            for _ in range(9):stream.write(chunk);digest.update(chunk)
        original_open=Path.open;reads=[]
        class ModelStream:
            def __enter__(self):self.stream=original_open(model,'rb');return self
            def __exit__(self,*args):self.stream.close()
            def read(self,size=-1):
                self_test.assertEqual(size,1024*1024);reads.append(size)
                return self.stream.read(size)
        self_test=self
        def bounded_open(path,*args,**kwargs):
            self.assertEqual(path,model)
            self.assertEqual(args,('rb',))
            return ModelStream()
        models={'fixture-model.bin':digest.hexdigest()}
        with patch.dict(repair.CONTENT_MODELS,{'arm64':models,'x64':models}), \
             patch.object(repair,'read',side_effect=AssertionError('text reader must not read models')), \
             patch.object(Path,'read_bytes',side_effect=AssertionError('whole-file read forbidden')), \
             patch.object(Path,'open',bounded_open), \
             patch.object(repair.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')), \
             patch.dict(os.environ,{'PROCESSOR_ARCHITEW6432':'','PROCESSOR_ARCHITECTURE':''}):
            for arch in ['x86_64','arm64']:
                with self.subTest(arch=arch),patch.object(repair.platform,'machine',return_value=arch):
                    reads.clear();self.assertTrue(r.content_ready(portable));self.assertEqual(len(reads),10)
                    with patch.dict(models,{'fixture-model.bin':'0'*64}):
                        self.assertFalse(r.content_ready(portable))
    def test_model_hash_size_limit_and_growth(self):
        path=self.root/'model.bin'
        with path.open('wb') as stream:stream.truncate(2*1024*1024*1024+1)
        with patch.object(Path,'open',side_effect=AssertionError('oversized file must not be opened')):
            with self.assertRaisesRegex(ValueError,'Oversized file preserved'):repair.file_sha256(path)
        write(path,b'x')
        self.assertEqual(repair.file_sha256(path,limit=1),hashlib.sha256(b'x').hexdigest())
        with patch.object(Path,'open',return_value=io.BytesIO(b'xx')):
            with self.assertRaisesRegex(ValueError,'Oversized file preserved'):repair.file_sha256(path,limit=1)
    def test_content_readiness_rejects_linked_model_paths(self):
        f=self.fixture('Windows');r=repair.Repair(f.home,f.install,'Windows',f.hermes)
        portable=self.root/'portable'
        for name in ['runtime/Scripts/python.exe','tools/ffmpeg/bin/ffmpeg.exe','cli.py']:
            write(portable/name,'fixture')
        target=self.root/'real-model.bin';write(target,b'model')
        directory=self.root/'real-models';directory.mkdir();write(directory/'model.bin',b'model')
        resources=portable/'resources';resources.mkdir()
        (resources/'models').symlink_to(directory,target_is_directory=True)
        with patch.dict(repair.CONTENT_MODELS,{'x64':{'model.bin':repair.sha(b'model')}}), \
             patch.object(repair.platform,'machine',return_value='x86_64'), \
             patch.dict(os.environ,{'PROCESSOR_ARCHITEW6432':'','PROCESSOR_ARCHITECTURE':''}), \
             patch.object(repair.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
            with self.assertRaisesRegex(ValueError,'Linked path preserved'):r.content_ready(portable)
        link=self.root/'linked-model.bin';link.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'Linked path preserved'):repair.file_sha256(link)
    def test_panel_probe_only_when_known_and_token_local(self):
        f=self.fixture();r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True)
        write(f.install/'web-shell.url','http://127.0.0.1:8787/?token=PRIVATE_PANEL_CANARY')
        class Response(io.BytesIO):
            def __init__(self):super().__init__(b'{"ok":true}')
        class Opener:
            def open(self,req,timeout):
                self.last=req;return Response()
        opener=Opener()
        with patch.object(repair.urllib.request,'build_opener',return_value=opener):r.panel_api(True)
        self.assertEqual(r.findings[-1]['status'],'unchanged');self.assertNotIn('PRIVATE_PANEL_CANARY',json.dumps(r.findings))
        write(f.install/'web-shell.url','https://example.com/?token=PRIVATE_PANEL_CANARY')
        with patch.object(repair.urllib.request,'build_opener',side_effect=AssertionError('remote access forbidden')):r.panel_api(True)
        self.assertEqual(r.findings[-1]['status'],'skipped:panel-token-unavailable')
    def test_second_apply_unchanged_report_and_0600_backups(self):
        f=self.fixture();f.run(True)
        marker=f.control/'last-repair.json';before=marker.read_bytes();timestamp=marker.stat().st_mtime_ns
        out=f.run(True);self.assertFalse(out['changed']);self.assertEqual(marker.read_bytes(),before);self.assertEqual(marker.stat().st_mtime_ns,timestamp)
        for p in (f.control/'backups').glob('repair-*/**/*'):
            if p.is_file():self.assertEqual(p.stat().st_mode&0o777,0o600)
    def test_actual_full_runner_selection_without_agent(self):
        f=self.fixture();f.run(True)
        r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True);r.names=['designer']
        with tarfile.open(ROOT/'apps/lms/installer-assets/agents/agents-tier-1-agents-release-20261105.1.tar.gz') as archive:
            manifest=json.loads(archive.extractfile('profile/post-install/update-manifest.json').read())
            item=next(iter(manifest['files']['runner.py']['variants'].values()))
            write(f.install/'web-shell/runner.py',archive.extractfile('profile/post-install/'+item['path']).read())
        parent=types.ModuleType('hermes_cli');parent.__path__=[]
        config=types.ModuleType('hermes_cli.tools_config');config._get_platform_tools=lambda *args,**kwargs:['file','terminal','skills','web','code_execution','image_gen']
        with patch.dict(sys.modules,{'hermes_cli':parent,'hermes_cli.tools_config':config}):r.full_tools(True)
        self.assertEqual(r.findings[-1]['status'],'unchanged')
    def test_mac_pending_restart_in_diagnose_and_verify(self):
        for verify in [False,True]:
            for codes in [[1,0,0],[1,1,1]]:
                with self.subTest(verify=verify,codes=codes):
                    f=self.fixture();marker=f.control/'web-shell-restart-pending';write(marker,'{}')
                    write(f.home/'Library/LaunchAgents/com.infobiz.agents.web-shell.plist','fixture')
                    r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=verify)
                    with patch.object(repair.platform,'system',return_value='Darwin'),patch.object(repair.subprocess,'run',side_effect=[subprocess.CompletedProcess([],c) for c in codes]):
                        r.panel_restart()
                    fixed=codes[-1]==0
                    self.assertEqual(r.findings[-1]['status'],'fixed' if fixed else 'skipped:launchctl-unavailable')
                    self.assertEqual(marker.exists(),not fixed)

    def test_linux_pending_restart_in_diagnose_repair_and_verify(self):
        for apply,verify in [(False,False),(True,False),(False,True)]:
            for outcome in [0,1,FileNotFoundError(),subprocess.TimeoutExpired('systemctl',30)]:
                with self.subTest(apply=apply,verify=verify,outcome=outcome):
                    f=self.fixture();marker=f.control/'web-shell-restart-pending';write(marker,'original marker')
                    r=repair.Repair(f.home,f.install,'Linux',f.hermes,apply=apply,verify=verify)
                    kwargs={'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':subprocess.CompletedProcess([],outcome,'','')}
                    with patch.object(repair.subprocess,'run',**kwargs) as run:
                        r.panel_restart()
                    self.assertEqual(run.call_args.args[0],['systemctl','restart','infobiz-web-shell.service'])
                    self.assertEqual(run.call_args.kwargs['timeout'],30)
                    fixed=outcome==0
                    self.assertEqual(r.findings[-1]['status'],'fixed' if fixed else 'skipped:systemctl-unavailable')
                    self.assertEqual(r.changed,fixed)
                    self.assertEqual(marker.exists(),not fixed)
                    if not fixed:self.assertEqual(marker.read_text(),'original marker')
                    if fixed:
                        with patch.object(repair.subprocess,'run') as repeat:
                            r.panel_restart()
                        repeat.assert_not_called()

    def test_linux_pending_restart_ignores_missing_marker_and_preserves_link(self):
        f=self.fixture();r=repair.Repair(f.home,f.install,'Linux',f.hermes,verify=True)
        with patch.object(repair.subprocess,'run') as run:r.panel_restart()
        run.assert_not_called();self.assertEqual(r.findings,[])
        marker=f.control/'web-shell-restart-pending';target=f.home/'student.txt';write(target,'keep')
        marker.symlink_to(target)
        with patch.object(repair.subprocess,'run') as run,self.assertRaises(ValueError):r.panel_restart()
        run.assert_not_called();self.assertEqual(target.read_text(),'keep')

    def test_media_outside_gateway_path_is_named_not_missing(self):
        # FIX_0562: ffmpeg 9.x installed by Homebrew, gateway PATH without /opt/homebrew/bin.
        f=self.fixture();write(f.home/'Library/LaunchAgents/ai.hermes.gateway.plist',repair.plistlib.dumps({'EnvironmentVariables':{'PATH':str(f.home/'.local/bin')+':/usr/bin:/bin'}}))
        brew=self.root/'brew-bin';brew.mkdir();
        for name in ['ffmpeg','ffprobe']:
            target=brew/name;write(target,'#!/bin/sh\nexit 0\n');target.chmod(0o755)
        calls=[]
        def run(args,timeout=30):
            calls.append(args);return subprocess.CompletedProcess(args,0,'','')
        for verify,apply,status in [(True,False,'pending:media-path'),(False,False,'planned'),(False,True,'fixed')]:
            with self.subTest(verify=verify,apply=apply):
                r=repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=apply,verify=verify);r.names=['copywriter'];r.tier=3;r.unreadable=set()
                for name in ['ffmpeg','ffprobe']:
                    link=f.home/'.local/bin'/name
                    if link.is_symlink():link.unlink()
                with patch.object(repair,'MEDIA_TOOL_DIRS',(str(brew),),create=True),patch.object(repair.Repair,'MEDIA_TOOL_DIRS',(str(brew),)),patch.object(repair,'command',side_effect=run),patch.object(repair.platform,'machine',return_value='arm64'):
                    r.speech()
                by={x['code']:x for x in r.findings}
                self.assertEqual(by['media-path']['status'],status,by['media-path'])
                self.assertIn(str(brew/'ffmpeg'),by['media-path']['detail'])
                self.assertEqual(by['mac-ffmpeg']['status'],'unchanged','a found program is never a download case')
                self.assertEqual(by['local-voice']['status'],'needs-platform-update','the STT command itself is still absent in the fixture')
                self.assertEqual((f.home/'.local/bin/ffmpeg').is_symlink(),apply and not verify)
                if apply and not verify:self.assertEqual(os.readlink(f.home/'.local/bin/ffmpeg'),str(brew/'ffmpeg'));self.assertTrue(r.changed)
        # A gateway PATH without ~/.local/bin cannot be fixed by a link: the exact gap is reported.
        write(f.home/'Library/LaunchAgents/ai.hermes.gateway.plist',repair.plistlib.dumps({'EnvironmentVariables':{'PATH':'/usr/bin:/bin'}}))
        for name in ['ffmpeg','ffprobe']:
            link=f.home/'.local/bin'/name
            if link.is_symlink():link.unlink()
        r=repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=True);r.names=['copywriter'];r.tier=3;r.unreadable=set()
        with patch.object(repair.Repair,'MEDIA_TOOL_DIRS',(str(brew),)),patch.object(repair,'command',side_effect=run):
            r.speech()
        by={x['code']:x for x in r.findings}
        self.assertEqual(by['media-path']['status'],'needs-platform-update');self.assertIn('PATH gateway',by['media-path']['detail'])
        self.assertFalse((f.home/'.local/bin/ffmpeg').exists());self.assertFalse(r.changed)

    def test_gateway_runtime_predating_disk_update_is_restarted_officially(self):
        # FIX_0562: Hermes files replaced on disk, the gateway still runs the earlier code.
        f=self.fixture();stamp=f.hermes/'hermes-agent/install-stamp.json';write(stamp,json.dumps({'baseVersion':'0.23.0'}))
        launcher=f.hermes/'hermes-agent/venv/bin/hermes';write(launcher,'#!/bin/sh\n');launcher.chmod(0o755)
        identities=iter([{'pid':4242,'hermes_home':str(f.hermes)},{'pid':4242,'hermes_home':str(f.hermes)},{'pid':4343,'hermes_home':str(f.hermes)}])
        calls=[]
        def run(args,timeout=30):
            calls.append(args)
            if args[:2]==['ps','-o']:return subprocess.CompletedProcess(args,0,'   2-03:04:05\n','')
            return subprocess.CompletedProcess(args,0,'','')
        for verify,apply,status in [(True,False,'pending:gateway-restart'),(False,False,'planned')]:
            with self.subTest(verify=verify,apply=apply):
                r=repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=apply,verify=verify)
                with patch.object(repair.Repair,'gateway_identity',return_value={'pid':4242,'hermes_home':str(f.hermes)}),patch.object(repair,'command',side_effect=run):
                    r.gateway_runtime()
                self.assertEqual(r.findings[-1]['status'],status,r.findings[-1]);self.assertIn('pid 4242',r.findings[-1]['detail']);self.assertIn('0.23.0',r.findings[-1]['detail'])
                self.assertFalse(any(a[1:3]==['gateway','restart'] for a in calls));self.assertFalse(r.changed)
        r=repair.Repair(f.home,f.install,'Darwin',f.hermes,apply=True)
        with patch.object(repair.Repair,'gateway_identity',side_effect=lambda self_=None:next(identities)),patch.object(repair,'command',side_effect=run),patch.object(repair.time,'sleep'):
            r.gateway_runtime()
        self.assertEqual(r.findings[-1]['status'],'fixed',r.findings[-1]);self.assertIn('4343',r.findings[-1]['detail'])
        self.assertIn([str(launcher),'gateway','restart'],calls);self.assertTrue(r.changed)
        # A process started after the files were written is current; no restart is proposed.
        young=lambda args,timeout=30:subprocess.CompletedProcess(args,0,'00:03\n' if args[:2]==['ps','-o'] else '','')
        r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True)
        with patch.object(repair.Repair,'gateway_identity',return_value={'pid':4242,'hermes_home':str(f.hermes)}),patch.object(repair,'command',side_effect=young):
            r.gateway_runtime()
        self.assertEqual(r.findings[-1]['status'],'unchanged');self.assertIn('после текущей установки',r.findings[-1]['detail'])
        # No answering gateway: the check is skipped without a false restart demand.
        r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True)
        with patch.object(repair.Repair,'gateway_identity',return_value=None),patch.object(repair,'command',side_effect=young):
            r.gateway_runtime()
        self.assertEqual(r.findings[-1]['status'],'unchanged');self.assertIn('не отвечает',r.findings[-1]['detail'])

    def test_gateway_path_includes_homebrew_and_honors_existing_plist(self):
        f=self.fixture();r=repair.Repair(f.home,f.install,'Darwin',f.hermes)
        self.assertIn('/opt/homebrew/bin',r.gateway_path())
        write(f.home/'Library/LaunchAgents/ai.hermes.gateway.plist',repair.plistlib.dumps({'EnvironmentVariables':{'PATH':'/custom/gateway/tools:/usr/bin'}}))
        self.assertEqual(r.gateway_path(),'/custom/gateway/tools:/usr/bin')
    def test_inventory_backup_is_first_preimage(self):
        f=self.fixture('Windows');before=(f.control/'tariff-files.json').read_bytes();f.run(True)
        saved=list((f.control/'backups').glob('repair-*/tariff-files.json'))
        self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_bytes(),before)
    def test_report_cannot_overwrite_authentication(self):
        f=self.fixture('Windows');target=f.profiles/'designer/auth.json';before=target.read_bytes()
        with patch.dict(os.environ,{'AGENTS_REPAIR_HERMES_HOME':str(f.hermes)}),patch.object(repair.platform,'system',return_value='Windows'),redirect_stdout(io.StringIO()):
            code=repair.main(['diagnose','--home',str(f.home),'--json',str(target)])
        self.assertEqual(code,2);self.assertEqual(target.read_bytes(),before)
    def test_powershell_native_arguments_round_trip(self):
        if not shutil.which('pwsh'):
            self.skipTest('PowerShell parser unavailable')
        values=['with spaces','double "quote"','C:\\Program Files\\tool\\','кириллица','line\nnext']
        args_file=self.root/'arguments.json';write(args_file,json.dumps(values,ensure_ascii=False))
        script="$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile('"+str(BUNDLE/'repair.ps1')+"',[ref]$t,[ref]$e);foreach($name in @('Quote-Native','Invoke-RepairProcess')){$f=$a.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true);Invoke-Expression $f.Extent.Text};$v=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText('"+str(args_file)+"'));$r=Invoke-RepairProcess '"+sys.executable+"' (@('-c','import json,sys; print(json.dumps(sys.argv[1:],ensure_ascii=False))')+$v);[Console]::Out.Write($r.Output);exit $r.Code"
        result=subprocess.run(['pwsh','-NoProfile','-Command',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),values)
    def native_fixture_home(self):
        home=self.root/'native-home'
        qa=module('native_fixture_checks',ROOT/'installer/tests/native_release_checks.py')
        write(home/'repair/native_release_checks.py',(ROOT/'installer/tests/native_release_checks.py').read_bytes())
        write(home/'.hermes/config.yaml','{}')
        write(home/'.hermes/hermes-agent/install-stamp.json',json.dumps({'commit':'2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19','baseVersion':'0.23.0'}))
        write(home/'.hermes/hermes-agent/.infobiz-upstream-ref','release:v0.23.0\n')
        write(home/'.hermes/.env','HERMES_LOCAL_STT_COMMAND=synthetic-command\n')
        files={}
        for name,identity in qa.NAMES.items():
            p=home/'.hermes/profiles'/name
            cfg={'stt':{'language':'ru','groq':{'language':'ru'},'local':{'language':'ru'}},
                 'platform_toolsets':{'web':['file','terminal','code_execution','web','image_gen']}}
            write(p/'config.yaml',yaml.safe_dump(cfg))
            write(p/'SOUL.md','agents-profile-identity-v1 '+identity)
            write(p/'AGENTS.md','agents-profile-identity-v1 '+identity)
            files[name+'/SOUL.md']=repair.sha((p/'SOUL.md').read_bytes())
        write(home/'InfobizAgents/control/tariffs/tariff-files.json',json.dumps({'files':files,'revision':'agents-release-20261108.1'}))
        write(home/'InfobizAgents/install.log','fixture')
        return home
    def run_native_fixture(self,home,mode,*args):
        source=ROOT/'installer/tests/repair_native_checks.py'
        original_path=list(sys.path)
        try:
            with patch.object(Path,'home',return_value=home),patch.object(sys,'argv',['fixture',mode,*args]),redirect_stdout(io.StringIO()) as output:
                exec(compile(source.read_bytes(),str(source),'exec'),{'__name__':'__main__'})
            return json.loads(output.getvalue().strip().split('\n')[-1])
        finally:sys.path[:]=original_path
    def test_rehearsal_config_rejects_every_additional_failure(self):
        home=self.native_fixture_home();root=home/'.hermes'
        with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
            self.assertTrue(self.run_native_fixture(home,'native-config')['pass'])
            cases=[(root/'profiles/designer/SOUL.md',b'missing identity'),
                   (root/'profiles/tech/config.yaml',b'stt: {language: en}'),
                   (root/'config.yaml',b'kanban: {dispatch_in_gateway: false}'),
                   (root/'.env',b''),
                   (root/'profiles/designer/config.yaml',b'stt: {language: ru, groq: {language: ru}, local: {language: ru}}\nplatform_toolsets: {web: [image_gen]}')]
            for path,data in cases:
                with self.subTest(path=str(path.relative_to(root))):
                    before=path.read_bytes();path.write_bytes(data)
                    try:
                        with self.assertRaises(AssertionError):self.run_native_fixture(home,'native-config')
                    finally:path.write_bytes(before)
    def test_rehearsal_seed_imports_default_and_actual_unit_generator(self):
        home=self.native_fixture_home()
        parent=types.ModuleType('hermes_cli');parent.__path__=[]
        default=types.ModuleType('hermes_cli.default_soul');default.DEFAULT_SOUL_MD=upstream_bytes().decode()
        gateway=types.ModuleType('hermes_cli.gateway')
        unit_bytes=b'[Unit]\nDescription=synthetic generator canary\n'
        from unittest.mock import Mock
        gateway.generate_systemd_unit=Mock(return_value=unit_bytes.decode())
        with patch.dict(sys.modules,{'hermes_cli':parent,'hermes_cli.default_soul':default,'hermes_cli.gateway':gateway}),patch.object(sys,'platform','linux'),patch.dict(os.environ,{}):
            # The old package has no identity marker.
            (home/'.hermes/profiles/marketer/SOUL.md').write_bytes(b'old packaged marketer')
            (home/'.hermes/profiles/tech/SOUL.md').write_bytes(b'old packaged tech')
            sales=home/'.hermes/profiles/sales/config.yaml'
            data=yaml.safe_load(sales.read_text());data['kanban']={'dispatch_in_gateway':False};write(sales,yaml.safe_dump(data))
            self.assertTrue(self.run_native_fixture(home,'seed')['pass'])
            gateway.generate_systemd_unit.assert_called_once_with(system=False)
        self.assertEqual((home/'.hermes/profiles/marketer/SOUL.md').read_bytes(),upstream_bytes())
        incoming=list((home/'InfobizAgents/control/tariffs/backups').glob('*/incoming/marketer/SOUL.md'))
        self.assertEqual(len(incoming),1);self.assertRegex(incoming[0].parents[2].name,r'^\d{8}T\d{6}-[0-9a-f]{32}$')
        self.assertEqual(incoming[0].read_bytes(),b'old packaged marketer')
        self.assertNotIn('marketer/SOUL.md',json.loads((home/'InfobizAgents/control/tariffs/tariff-files.json').read_text())['files'])
        self.assertEqual((home/'.config/systemd/user/hermes-gateway.service').read_bytes(),unit_bytes)
    def test_rehearsal_panel_checks_failed_checks_and_exact_profile(self):
        home=self.native_fixture_home()
        responses={'audit':{'ok':True,'checks':[],
                            'agents':[{'id':p,'instructions':True} for p in ['designer','tech']]},
                   'tool-policy':{'ok':True,'checks':[]},'agents/designer/preflight':{'ok':True,'checks':[]}}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(responses[self.path.removeprefix('/api/')]).encode())
            do_POST=do_GET
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever);thread.start()
        def run():return self.run_native_fixture(home,'native-panel','http://127.0.0.1:'+str(server.server_port),'web_shell_token=synthetic')
        try:
            result=run();self.assertTrue(result['pass']);self.assertTrue(result['native']['audit']['pass']);self.assertEqual(result['identityProfiles'],[])
            responses['audit']['agents'][0]['instructions']=False
            with self.assertRaises(AssertionError):run()
            responses['audit']['agents'][0]['instructions']=True
            responses['audit']['checks'].append({'id':'sessions','ok':False})
            with self.assertRaises(AssertionError):run()
            responses['audit']['checks'].pop()
            for endpoint in ['tool-policy','agents/designer/preflight']:
                responses[endpoint]['ok']=False
                with self.assertRaises(AssertionError):run()
                responses[endpoint]['ok']=True
        finally:server.shutdown();server.server_close();thread.join()
    def test_managed_skills_fences_revision_and_real_archive_helpers(self):
        f=self.fixture()
        with tarfile.open(ROOT/'apps/lms/installer-assets/agents'/('agents-tier-3-'+repair.json_file(BUNDLE/'known-baselines.json')['revision']+'.tar.gz')) as archive:
            helpers={Path(m.name).name for m in archive.getmembers() if m.name.startswith('profile/post-install/') and m.name.endswith('.py')}
            self.assertEqual(helpers,set(repair.PACKAGE_HELPERS))
            for profile,skill in repair.NEW_SKILLS.items():
                self.assertIn('profile/agents/'+profile+'/skills/'+skill+'/SKILL.md',archive.getnames())
            for member in archive.getmembers():
                if member.name.startswith('profile/agents/') and member.isfile() and member.name.endswith('.md'):
                    name=member.name.removeprefix('profile/agents/')
                    data=archive.extractfile(member).read();write(f.profiles/name,data);f.files[name]=repair.sha(data)
        for name in helpers:write(f.control/'post-install'/name,'fixture')
        r=repair.Repair(f.home,f.install,'Darwin',f.hermes);r.files=f.files;r.names=list(repair.NAMES)
        for revision,expected in [('agents-native-content-20261103.1','needs-platform-update'),('agents-release-20261105.1','needs-platform-update'),('agents-release-20261105.2','needs-platform-update'),('agents-release-20261105.3','needs-platform-update'),('agents-release-20261106.1','needs-platform-update'),('agents-release-20261108.1','unchanged')]:
            r.state={'revision':revision};r.findings=[];before=tree(f.home);r.skills()
            findings={f['code']:f for f in r.findings}
            self.assertEqual(findings['managed-skills']['status'],expected)
            self.assertIn('ссылками: 0;',findings['managed-skills']['detail'])
            self.assertIn('скиллов: 0.',findings['managed-skills']['detail'])
            self.assertEqual(findings['package-helpers']['status'],'unchanged');self.assertEqual(before,tree(f.home))
        # Counters alone cannot require an update at the current revision.
        write(f.profiles/'designer/skills/carousel-style-brief/SKILL.md','VoiceBank tools/swift-design-engine/CONTENT_ENGINE.md')
        r.findings=[];r.skills();self.assertEqual(next(f for f in r.findings if f['code']=='managed-skills')['status'],'unchanged')

    def test_non_utf8_files_preserved_checks_continue_all_modes(self):
        for mode in ['diagnose','apply','verify']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory).resolve(),'Windows')
                paths={f.profiles/'tech/SOUL.md':'Личный текст',f.profiles/'designer/config.yaml':'# Настройки\nstt: {language: en}',
                       f.profiles/'copywriter/AGENTS.md':'Инструкции',f.hermes/'config.yaml':'# Настройки\nkanban: {}'}
                for path,text in paths.items():path.write_bytes(text.encode('cp1251'))
                before={p:p.read_bytes() for p in paths}
                out=f.run(apply=mode=='apply',verify=mode=='verify');findings={f['code']:f for f in out['findings']}
                for code in ['soul:tech','configuration:designer','configuration:default','instructions:copywriter']:
                    self.assertEqual(findings[code]['status'],'skipped:unreadable-encoding')
                    self.assertEqual(findings[code]['severity'],'warning');self.assertEqual(findings[code]['detail'],'файл не в UTF-8; не изменялся')
                self.assertNotIn('voice-language:designer',findings);self.assertNotIn('kanban:designer',findings)
                for code in ['managed-skills','package-helpers','logs','windows-addon','tirith']:self.assertIn(code,findings)
                self.assertIn('voice-language:tech',findings)
                r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True);r.names=['designer','tech'];r.tier=1
                with patch.object(repair.shutil,'which',return_value='/synthetic/tool'),patch.object(repair,'command',return_value=subprocess.CompletedProcess([],0)):
                    r.speech();r.full_tools(True)
                self.assertTrue(any(f['code']=='local-voice' for f in r.findings))
                self.assertEqual(sum(f['code']=='configuration:designer' for f in r.findings),1)
                # Verify still rejects the OTHER, repairable configuration failures.
                self.assertEqual(out['exitCode'],2 if mode=='verify' else 3)
                self.assertEqual({p:p.read_bytes() for p in paths},before)
                if mode=='apply':self.assertEqual(findings['configuration:tech']['status'],'fixed')

    def test_non_utf8_warning_alone_does_not_change_exit(self):
        f=self.fixture('Windows');f.run(True)
        state=repair.json_file(f.control/'tariff-files.json');state['revision']='agents-release-20261108.1';write(f.control/'tariff-files.json',json.dumps(state))
        for name in repair.PACKAGE_HELPERS:write(f.control/'post-install'/name,'fixture')
        write(f.install/'control/watchdog.js','fixture');write(f.install/'control/webshell-runtime.json','{"recoveryEnabled":true}')
        path=f.profiles/'tech/SOUL.md';path.write_bytes('Личный текст'.encode('cp1251'));before=tree(f.home)
        out=f.run(verify=True);self.assertEqual(out['exitCode'],0);self.assertEqual(tree(f.home),before)

    def test_panel_http_optional_token_auth_and_exact_audit_profiles(self):
        responses={'tool-policy':{'ok':True},'agents/designer/preflight':{'ok':True},
                   'audit':{'ok':False,'checks':[{'id':c,'ok':c!='instructions'} for c in ['instructions','gateways','gateway-runtime','sessions','toolsets','legacy-processes']],
                            'agents':[{'id':p,'instructions':p!='tech'} for p in ['designer','tech']]}}
        cookies=[];status=[200]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)));cookies.append(self.headers.get('Cookie'))
                self.send_response(status[0]);self.end_headers();self.wfile.write(json.dumps(responses[self.path.removeprefix('/api/')]).encode())
            do_POST=do_GET
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever);thread.start()
        try:
            f=self.fixture()
            def run(token='',extra_user=False):
                cookies.clear();write(f.install/'web-shell.url','http://127.0.0.1:'+str(server.server_port)+token)
                r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True)
                r.add('soul:tech','skipped:user-text')
                if extra_user:r.add('soul:designer','skipped:user-text')
                r.panel_api(True);return r
            for token,cookie in [('',None),('/?token=test%20token','web_shell_token=test%20token')]:
                r=run(token);self.assertEqual(r.findings[-1]['status'],'skipped:user-soul');self.assertEqual(cookies,[cookie]*3)
            self.assertEqual(run(extra_user=True).findings[-1]['status'],'unfixable')
            responses['audit']['agents'][0]['instructions']=False
            self.assertEqual(run().findings[-1]['status'],'unfixable')
            responses['audit']['agents'][0]['instructions']=True
            for code in [401,403]:
                status[0]=code;r=run();self.assertEqual(r.findings[-1]['status'],'skipped:panel-auth-required');self.assertEqual(r.result()['exitCode'],0)
                self.assertEqual(run('/?token=test').findings[-1]['status'],'unfixable')
        finally:server.shutdown();server.server_close();thread.join()

    def test_panel_exact_unreadable_apply_exception_stays_warning(self):
        responses={'tool-policy':{'ok':True},'agents/designer/preflight':{'ok':True},
                   'audit':{'ok':False,'checks':[{'id':'instructions','ok':False}],
                            'agents':[{'id':'tech','instructions':False},{'id':'designer','instructions':True}]}}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(responses[self.path.removeprefix('/api/')]).encode())
            do_POST=do_GET
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever);thread.start()
        try:
            f=self.fixture();path=f.profiles/'tech/SOUL.md';path.write_bytes('Личный текст'.encode('cp1251'));before=path.read_bytes()
            write(f.install/'web-shell.url','http://127.0.0.1:'+str(server.server_port))
            write(f.control/'last-apply.json',json.dumps({'conflicts':['tech/SOUL.md']}))
            def run(revision=None):
                r=repair.Repair(f.home,f.install,'Darwin',f.hermes,verify=True)
                r.files=repair.json_file(f.control/'tariff-files.json')['files'];r.state={'revision':revision or r.baselines['revision']}
                r.soul('tech');r.panel_api(True);return r
            r=run();self.assertEqual(r.findings[-1]['status'],'skipped:unreadable-instructions');self.assertEqual(r.result()['exitCode'],0)
            self.assertEqual(path.read_bytes(),before)
            responses['audit']['agents'][1]['instructions']=False
            self.assertEqual(run().findings[-1]['status'],'unfixable')
            responses['audit']['agents'][1]['instructions']=True
            responses['audit']['checks'].append({'id':'sessions','ok':False})
            self.assertEqual(run().findings[-1]['status'],'unfixable')
            responses['audit']['checks'].pop()
            self.assertEqual(run('old-revision').findings[-1]['status'],'unfixable')
            write(f.control/'last-apply.json','{"conflicts":[]}')
            self.assertEqual(run().findings[-1]['status'],'unfixable')
        finally:server.shutdown();server.server_close();thread.join()

    def test_missing_linux_user_unit_masks_in_advance(self):
        f=self.fixture('Linux');unit=f.home/'.config/systemd/user/hermes-gateway.service';unit.unlink()
        out=f.run(True);finding=next(f for f in out['findings'] if f['code']=='linux-user-gateway')
        self.assertEqual(finding['status'],'fixed');self.assertEqual(finding['detail'],'Маска создана заранее, служба не существовала.')
        self.assertTrue(unit.is_symlink());self.assertEqual(os.readlink(unit),'/dev/null')

    @contextmanager
    def linux_handoff(self, manual=True, **options):
        import pwd
        f=self.fixture('Linux')
        unit=f.home/'.config/systemd/user/hermes-gateway.service';unit.unlink();unit.symlink_to('/dev/null')
        r=repair.Repair(f.home,f.install,'Linux',f.hermes,**options)
        binary=str(f.hermes/'hermes-agent/venv/bin/hermes')
        props={'LoadState':'loaded','ActiveState':'active','MainPID':'51','NRestarts':'5',
               'FragmentPath':'/etc/systemd/system/infobiz-hermes-gateway.service','DropInPaths':'',
               'User':pwd.getpwuid(os.getuid()).pw_name,'ExecStart':'{ path='+binary+' ; argv[]='+binary+' gateway run ; ignore_errors=no ; }'}
        identity={'pid':31 if manual else 51,'hermes_home':str(f.hermes),'supervisor':'manual' if manual else 'systemd'}
        state=types.SimpleNamespace(r=r,props=props,identity=identity,pids=[31,51] if manual else [51],events=[],clock=0.,fail_drain=False,wrong_start=False,unstable=False,ps_fail=False,identify_fail=False)
        def identify(home):
            self.assertEqual(home,f.hermes)
            if state.identify_fail:raise ImportError('PRIVATE_IDENTITY_CANARY')
            return dict(state.identity) or None
        def graceful(pid,timeout):
            state.events.append(('SIGUSR1',pid,timeout))
            if state.fail_drain:return False
            state.pids.remove(pid);state.identity.clear();return True
        state.graceful=Mock(side_effect=graceful)
        def run(args,timeout=30):
            if args[0]=='ps':
                self.assertEqual(args,['ps','-eo','pid=,ppid=,args='])
                lines=['30 1 '+str(f.hermes/'hermes-agent/venv/bin/python')+' -m hermes_cli.stderr_timestamp --error-log ignored -- '+binary+' gateway run --replace',
                       '90 1 /other/.hermes/hermes-agent/venv/bin/hermes gateway run']
                lines += [str(pid)+' 30 '+str(f.hermes/'hermes-agent/venv/bin/python')+' -m hermes_cli.main gateway run --replace' for pid in state.pids]
                return subprocess.CompletedProcess(args,1 if state.ps_fail else 0,'\n'.join(lines),'')
            self.assertEqual(args[0],'systemctl')
            if args[1]=='show':
                if state.unstable and state.clock>=10:props['NRestarts']='6'
                return subprocess.CompletedProcess(args,0,'\n'.join(k+'='+v for k,v in props.items()),'')
            state.events.append(args[1])
            if args[1]=='stop':
                main=int(props['MainPID']);props.update(ActiveState='inactive',MainPID='0')
                if main in state.pids:state.pids.remove(main)
                if state.identity.get('pid')==main:state.identity.clear()
            if args[1]=='start':
                props.update(ActiveState='active',MainPID='52')
                state.pids.append(52);state.identity.update(pid=53 if state.wrong_start else 52,hermes_home=str(f.hermes),supervisor='systemd')
            return subprocess.CompletedProcess(args,0,'','')
        modules={'gateway':types.ModuleType('gateway'),'gateway.control_socket':types.SimpleNamespace(identify_gateway=identify),
                 'hermes_cli':types.ModuleType('hermes_cli'),'hermes_cli.gateway':types.SimpleNamespace(_graceful_restart_via_sigusr1=state.graceful)}
        with patch.dict(sys.modules,modules),patch.object(repair,'command',side_effect=run),patch.object(repair.time,'monotonic',side_effect=lambda:state.clock),patch.object(repair.time,'sleep',side_effect=lambda n:setattr(state,'clock',state.clock+n)),patch.dict(os.environ,{'XDG_CONFIG_HOME':str(f.home/'.config')}):
            yield state

    def test_linux_manual_gateway_handoff_and_repeat(self):
        with self.linux_handoff(apply=True) as s:
            s.r.linux()
            self.assertEqual(s.events,['stop',('SIGUSR1',31,120),'reset-failed','start'])
            self.assertEqual(s.r.findings[-1]['status'],'fixed');self.assertEqual(s.clock,30)
            self.assertTrue(s.r.changed)
            s.r.changed=False;s.r.findings.clear();s.events.clear()
            s.r.linux()
            self.assertEqual(s.r.findings[-1]['code'],'linux-gateway-owner')
            self.assertEqual(s.r.findings[-1]['status'],'unchanged');self.assertFalse(s.r.changed)
            self.assertEqual(s.events,[]);s.graceful.assert_called_once_with(31,120)

    def test_linux_service_already_owns_gateway(self):
        with self.linux_handoff(manual=False,apply=True) as s:
            s.r.linux();self.assertEqual(s.r.findings[-1]['status'],'unchanged')
            self.assertEqual(s.events,[]);s.graceful.assert_not_called();self.assertFalse(s.r.changed)

    def test_linux_owner_dry_run_and_verify_never_stop(self):
        for verify,status in [(False,'planned'),(True,'unfixable')]:
            with self.subTest(verify=verify), tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp).resolve()
                try:
                    with self.linux_handoff(verify=verify) as s:
                        s.r.linux();owner=next(f for f in s.r.findings if f['code']=='linux-gateway-owner')
                        self.assertEqual(owner['status'],status);self.assertFalse(s.r.changed);self.assertEqual(s.events,[])
                finally:self.root=old

    def test_linux_identity_unavailable_never_stops(self):
        with self.linux_handoff(apply=True) as s:
            for mode in ['exception','none','foreign','ps']:
                with self.subTest(mode=mode):
                    s.identify_fail=mode=='exception';s.ps_fail=mode=='ps'
                    s.identity.clear()
                    if mode!='none':s.identity.update(pid=31,hermes_home='/foreign' if mode=='foreign' else str(s.r.hermes),supervisor='manual')
                    s.r.linux()
                    self.assertEqual(s.r.findings[-1]['status'],'unavailable:identity-check')
                    self.assertEqual(s.events,[]);self.assertFalse(s.r.changed)
                    self.assertNotIn('PRIVATE_IDENTITY_CANARY',json.dumps(s.r.findings))

    def test_linux_unverified_unit_never_stops(self):
        with self.linux_handoff(apply=True) as s:
            for key,value in [('FragmentPath','/usr/lib/systemd/system/other.service'),('User','other'),('DropInPaths','/custom.conf'),('ExecStart',s.props['ExecStart'].replace(' gateway run',' gateway run --replace'))]:
                before=s.props[key];s.props[key]=value
                s.r.linux();self.assertEqual(s.r.findings[-1]['status'],'unavailable:gateway-unit-unverified')
                self.assertEqual(s.events,[]);self.assertFalse(s.r.changed);s.props[key]=before

    def test_linux_failed_drain_stops_without_retry_or_start(self):
        with self.linux_handoff(apply=True) as s:
            s.fail_drain=True;s.r.linux()
            self.assertEqual(s.r.findings[-1]['status'],'unavailable:gateway-handoff-failed')
            self.assertEqual(s.r.findings[-1]['severity'],'error')
            self.assertEqual(s.events,['stop',('SIGUSR1',31,120)])
            self.assertEqual(s.r.result()['exitCode'],2)

    def test_linux_handoff_deadline_and_instability(self):
        for failure,status,duration in [('wrong_start','gateway-handoff-failed',120),('unstable','gateway-unstable',10)]:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp).resolve()
                try:
                    with self.linux_handoff(apply=True) as s:
                        setattr(s,failure,True);s.r.linux()
                        self.assertEqual(s.r.findings[-1]['status'],'unavailable:'+status)
                        self.assertEqual(s.clock,duration);self.assertEqual(s.events.count('stop'),1)
                        self.assertEqual(s.events.count('start'),1);s.graceful.assert_called_once_with(31,120)
                finally:self.root=old

    def test_linux_second_gateway_triggers_handoff_and_fails_if_it_remains(self):
        with self.linux_handoff(manual=False,apply=True) as s:
            s.pids.append(32);s.r.linux()
            self.assertEqual(s.events,['stop'])
            self.assertEqual(s.r.findings[-1]['status'],'unavailable:gateway-handoff-failed')
            s.graceful.assert_not_called()

    def test_rehearsal_shebang_and_windows_explicit_utf8(self):
        source=(ROOT/'scripts/lib/repair-native-rehearsal.mjs').read_text()
        install=next(line for line in source.splitlines() if "run('old-install'" in line)
        self.assertIn('chmod 700 "$HOME/old-install.sh"\\n"$HOME/old-install.sh"',install)
        self.assertNotIn('bash "$HOME/old-install.sh"',install)
        self.assertIn("'vendor/student-skill-repairs.py'",source)
        windows=(ROOT/'scripts/windows-patch-native-rehearsal.mjs').read_text()
        self.assertNotIn('.read_text()',windows);self.assertNotIn('.write_text(',windows)
        self.assertIn(".write_bytes(soul.encode('utf-8'))",windows)
        self.assertIn("json.dumps(saved,ensure_ascii=False).encode('utf-8')",windows)

    def test_rehearsal_strict_posix_verdict(self):
        findings=[{'code':c,'status':'unchanged'} for c in ['package-revision','managed-skills','package-helpers','webshell','linux-single-gateway']]
        findings += [{'code':'soul:'+name,'status':'unchanged'} for name in ['marketer','copywriter','tech']] + [{'code':'panel-api','status':'unchanged'}]
        native={'verify':{'exitCode':0,'findings':findings},'apply':{'findings':[{'code':'soul:marketer','status':'fixed'}]},
                'repeat':{'changed':False,'findings':[{'code':'linux-gateway-owner','status':'unchanged'}]},
                'repaired':{'pass':True,'gateway':{'gatewayCount':1,'gatewayPids':[52],'mainPid':52,'activeState':'active','nRestarts':0,'stableSeconds':30,'userUnitMasked':True}},
                'config':{'pass':True,'native':{'check':'config','pass':True}},
                'panel':{'pass':True,'identityProfiles':[],'native':{'pass':True,'tool-policy':{'pass':True,'failedChecks':[]},'preflight':{'pass':True,'failedChecks':[]},'audit':{'pass':True,'failedChecks':[]}}},'full-tools':{'pass':True},'logs':{'pass':True}}
        code="import {assertPosixRepairVerdict as check} from './scripts/lib/repair-native-rehearsal.mjs'; const n=JSON.parse(process.argv[1]);check(n,false);check(n,true);"
        def run(value):return subprocess.run(['node','--input-type=module','-e',code,json.dumps(value)],capture_output=True).returncode
        self.assertEqual(run(native),0)
        import copy
        cases=[(['verify','findings',i,'status'],'needs-platform-update') for i in range(len(findings))]
        cases += [(['config','native','pass'],False),(['panel','native','audit','failedChecks'],['instructions','sessions']),(['panel','identityProfiles'],['tech','designer']),(['full-tools','pass'],False),(['logs','pass'],False),(['apply','findings',0,'status'],'unchanged')]
        cases += [(['repeat','changed'],True),(['repeat','findings',0,'status'],'fixed'),(['repaired','pass'],False)]
        cases += [(['repaired','gateway',key],value) for key,value in [('gatewayCount',2),('gatewayPids',[51,52]),('mainPid',51),('activeState','activating'),('nRestarts',-1),('stableSeconds',0),('userUnitMasked',False)]]
        for path,value in cases:
            changed=copy.deepcopy(native);target=changed
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            self.assertNotEqual(run(changed),0,path)

    def test_windows_rehearsal_accepts_only_merge_then_no_write_repeat(self):
        code="""import assert from 'node:assert/strict';
import {assertWindowsRepairUpgrade as check} from './scripts/windows-patch-native-rehearsal.mjs';
const first={merged:['tech/SOUL.md'],conflicts:[]}, repeat={changed:0,merged:[],conflicts:[]};
const text=Buffer.from('agents-profile-identity-v1\\nREPAIR_STUDENT_SOUL_ADDITION');
check(first,repeat,text,text,123,123);
for(const args of [
 [{merged:[],conflicts:[]},repeat,text,text,123,123],
 [first,{...repeat,changed:1},text,text,123,123],
 [first,{...repeat,merged:['tech/SOUL.md']},text,text,123,123],
 [first,{...repeat,conflicts:['tech/SOUL.md']},text,text,123,123],
 [first,repeat,text,Buffer.from('lost'),123,123],
 [first,repeat,text,text,123,124],
])assert.throws(()=>check(...args));
"""
        result=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_rehearsal_linux_stability_uses_actual_gateway_not_timestamp_wrapper(self):
        source=ast.parse((ROOT/'installer/tests/repair_native_checks.py').read_text())
        nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in {'gateway_state','check_linux_gateway'}]
        h=self.root/'learner';root=h/'.hermes';unit=h/'.config/systemd/user/hermes-gateway.service'
        unit.parent.mkdir(parents=True);unit.symlink_to('/dev/null')
        clock=[0.];restarts=[0];extra=[False]
        def output(args,**kwargs):
            if args[0]=='ps':
                return ('30 1 python -m hermes_cli.stderr_timestamp --error-log ignored -- python -m hermes_cli.main gateway run --replace\n'
                        '31 30 python -m hermes_cli.main gateway run --replace\n'+('32 1 hermes gateway run\n' if extra[0] else ''))
            return 'MainPID=31\nActiveState=active\nNRestarts='+str(restarts[0])+'\n'
        namespace={'h':h,'root':root,'os':os,'re':repair.re,'subprocess':types.SimpleNamespace(check_output=output),
                   'time':types.SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n))}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'native-gateway-check','exec'),namespace)
        modules={'gateway':types.ModuleType('gateway'),'gateway.control_socket':types.SimpleNamespace(identify_gateway=lambda home:{'pid':31,'hermes_home':str(root),'supervisor':'systemd'})}
        with patch.dict(sys.modules,modules):
            report=namespace['check_linux_gateway']()
            self.assertEqual(report,{'gatewayPids':[31],'gatewayCount':1,'mainPid':31,'activeState':'active','nRestarts':0,'stableSeconds':30,'userUnitMasked':True})
            self.assertEqual(clock[0],30)
            extra[0]=True
            with self.assertRaises(AssertionError):namespace['check_linux_gateway']()
            extra[0]=False;clock[0]=0
            def unstable_sleep(n):clock[0]+=n;restarts[0]+=1
            namespace['time'].sleep=unstable_sleep
            with self.assertRaisesRegex(AssertionError,'restarted'):namespace['check_linux_gateway']()


    def seven_profiles(self):
        return Fixture(self.root, 'Windows', names=sorted(repair.NAMES))

    def test_all_unmarked_kanban_false_removed_from_snapshot(self):
        f=self.seven_profiles()
        configs=[f.hermes/'config.yaml', *f.profiles.glob('*/config.yaml')]
        for path in configs:
            write(path,path.read_text().replace('# Infobiz Agents multi-gateway defaults\n',''))
        before=tree(f.home);plan=f.run()
        self.assertEqual(tree(f.home),before)
        planned=[x for x in plan['findings'] if x['code'].startswith('kanban:')]
        self.assertEqual(len(planned),8)
        self.assertTrue(all(x['status']=='planned' and 'false у всех 7 профилей' in x['detail'] for x in planned))
        out=f.run(True)
        for path in configs:
            self.assertNotIn('dispatch_in_gateway',(yaml.safe_load(path.read_text()).get('kanban') or {}))
        fixed=[x for x in out['findings'] if x['code'].startswith('kanban:')]
        self.assertTrue(all(x['status']=='fixed' for x in fixed))
        backups=list((f.control/'backups').glob('repair-*/selections/*.json'))
        self.assertTrue(any(json.loads(p.read_text()).get('unmarkedKanban') for p in backups))
        after=tree(f.home);self.assertFalse(f.run(True)['changed']);self.assertEqual(tree(f.home),after)

    def test_one_unmarked_false_of_seven_is_preserved(self):
        f=self.seven_profiles()
        for path in f.profiles.glob('*/config.yaml'):
            data=yaml.safe_load(path.read_text());data.pop('kanban')
            if path.parent.name=='sales':data['kanban']={'dispatch_in_gateway':False,'other':{'keep':True}}
            write(path,yaml.safe_dump(data))
        out=f.run(True)
        finding=next(x for x in out['findings'] if x['code']=='kanban:sales')
        self.assertEqual(finding['status'],'skipped:custom-selection')
        self.assertIn('false у 1 из 7 профилей',finding['detail'])
        self.assertEqual(yaml.safe_load((f.profiles/'sales/config.yaml').read_text())['kanban'],{'dispatch_in_gateway':False,'other':{'keep':True}})

    def test_mixed_marked_unmarked_and_expanded_blocks(self):
        f=self.seven_profiles()
        for index,path in enumerate(sorted(f.profiles.glob('*/config.yaml'))):
            if index%2:
                data=yaml.safe_load(path.read_text())
                data['kanban']['other']={'nested':[1,2], 'text':'preserved'}
                write(path,yaml.safe_dump(data))
        root=f.hermes/'config.yaml'
        # Exact text-only replacement preserves adjacent comments and fields.
        write(root,'# root comment\nkanban:\n  dispatch_in_gateway: false\n# next\ncustom: preserved\n')
        f.run(True)
        self.assertEqual(root.read_text(),'# root comment\nkanban: {}\n# next\ncustom: preserved\n')
        for index,path in enumerate(sorted(f.profiles.glob('*/config.yaml'))):
            kanban=yaml.safe_load(path.read_text()).get('kanban') or {}
            self.assertNotIn('dispatch_in_gateway',kanban)
            if index%2:self.assertEqual(kanban['other'],{'nested':[1,2],'text':'preserved'})

    def test_signature_does_not_create_root_config_or_key(self):
        f=self.seven_profiles();root=f.hermes/'config.yaml';root.unlink()
        f.run(True);self.assertFalse(root.exists())
        for text in ['# custom root\nagent: {}\n', 'kanban: {other: keep}\n', 'kanban: {dispatch_in_gateway: true}\n']:
            # Re-seed only the relevant key: each run must observe seven false values.
            for path in f.profiles.glob('*/config.yaml'):
                data=yaml.safe_load(path.read_text());data['kanban']={'dispatch_in_gateway':False};write(path,yaml.safe_dump(data))
            write(root,text);f.run(True);self.assertEqual(root.read_text(),text)

    def test_unreadable_config_is_local_warning_with_normal_exit(self):
        for kind in ['parser','validation','profile']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                f=Fixture(Path(tmp).resolve(),'Windows')
                path=f.profiles/'designer/config.yaml' if kind=='profile' else f.hermes/'config.yaml'
                before=b'agent: "string"\n' if kind!='parser' else b'UNSUPPORTED_OFFICIAL_YAML\n'
                write(path,before)
                r=repair.Repair(f.home,f.install,'Windows',f.hermes,apply=True)
                real_load=yaml.safe_load
                def limited(text):
                    if 'UNSUPPORTED_OFFICIAL_YAML' in text:raise RuntimeError('official parser cannot read this construction')
                    return real_load(text)
                r.yaml=types.SimpleNamespace(safe_load=limited,safe_dump=yaml.safe_dump)
                r.speech=lambda:None
                out=r.run();self.assertEqual(out['exitCode'],3);self.assertEqual(path.read_bytes(),before)
                finding=next(x for x in out['findings'] if x['code']=='configuration:'+('designer' if kind=='profile' else 'default'))
                self.assertEqual(finding,{'code':finding['code'],'status':'skipped:unreadable-config','severity':'warning','detail':'файл не разобран; не изменялся'})
                self.assertTrue(any(x['code']=='configuration:tech' and x['status']=='fixed' for x in out['findings']))
                self.assertTrue(any(x['code']=='windows-addon' for x in out['findings']))
                # A warning alone must not change an otherwise successful exit.
                r.findings=[finding];self.assertEqual(r.result()['exitCode'],0)
                if kind=='profile':r.full_tools(True)  # Must not reparse the skipped file.

    def test_windows_attributes_only_reject_reparse_within_managed_roots(self):
        f=self.fixture('Windows');target=f.profiles/'designer/config.yaml'
        original=Path.lstat;bad=[None]
        def attributes(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            attr=0
            if path in [f.home/'AppData',f.home/'AppData/Local',f.home]:attr=0x400|0x10|0x6
            if path==f.hermes:attr=0x10|0x2000|0x2  # Ordinary directory with Windows attributes.
            if path==bad[0]:attr=0x400|0x10
            return types.SimpleNamespace(st_mode=0 if attr&0x10 else info.st_mode,st_file_attributes=attr)
        token=repair.PATH_ROOTS.set((f.install,f.hermes))
        try:
            with patch.object(Path,'lstat',attributes),patch.object(Path,'is_symlink',return_value=False):
                repair.plain(target)
                repair.plain(f.home)
                for linked in [f.hermes,f.profiles/'designer',target,f.install/'control']:
                    bad[0]=linked
                    with self.assertRaisesRegex(ValueError,'Reparse'):repair.plain(target if linked!=f.install/'control' else f.control/'tariff-files.json')
        finally:repair.PATH_ROOTS.reset(token)

    def test_ci_debug_trace_is_opt_in_append_only_and_never_stdout(self):
        f=self.fixture('Windows');debug=self.root/'evidence/debug.log';report=self.root/'report.json'
        write(f.control/'tariff-files.json','{"files":{"CANARY_BAD_PATH":"invalid"}}')
        with patch.dict(os.environ,{'AGENTS_REPAIR_DEBUG_FILE':''}):
            try:raise ValueError('disabled debug')
            except ValueError:repair.debug_exception('disabled')
        self.assertFalse(debug.exists())
        env={'AGENTS_REPAIR_HERMES_HOME':str(f.hermes),'AGENTS_REPAIR_DEBUG_FILE':str(debug)}
        for attempt in range(2):
            output=io.StringIO()
            with patch.dict(os.environ,env),patch.object(repair.platform,'system',return_value='Windows'),redirect_stdout(output):
                self.assertEqual(repair.main(['diagnose','--no-report','--home',str(f.home),'--json',str(report)]),2)
            # TASK43: the patch's own literal reason names the failed check for
            # support; the traceback itself still never reaches stdout or the report.
            text=output.getvalue()+report.read_text()
            self.assertIn('Причина: ValueError: Invalid managed inventory',text)
            self.assertNotIn('Traceback',text);self.assertNotIn('File \"',text);self.assertNotIn('CANARY_BAD_PATH',text)
        trace=debug.read_text();self.assertGreaterEqual(trace.count('stage: package-inventory'),2)
        self.assertIn('Traceback (most recent call last)',trace);self.assertIn('ValueError: Invalid managed inventory',trace)
        with patch.dict(os.environ,{'AGENTS_REPAIR_DEBUG_FILE':'relative-debug.log'}):
            repair.debug_exception('ignored')
        self.assertFalse((ROOT/'relative-debug.log').exists())
        # Nonfatal parser failures retain their original traceback and stage too.
        write(f.control/'tariff-files.json',json.dumps({'files':f.files}))
        write(f.hermes/'config.yaml','broken: [CANARY_UNCLOSED')
        with patch.dict(os.environ,env):f.run(True)
        trace=debug.read_text();self.assertIn('configuration-preflight:default',trace);self.assertIn('CANARY_UNCLOSED',trace)

    def test_rehearsal_repaired_rejects_false_in_any_config(self):
        home=self.native_fixture_home();root=home/'.hermes';install=home/'InfobizAgents'
        project=home/'Documents/Content/research/repair-student.txt';write(project,'project')
        user=root/'profiles/tech/USER.md';write(user,'user')
        designer=root/'profiles/designer/config.yaml';data=yaml.safe_load(designer.read_text())
        data['platform_toolsets']['cli']=['web'];write(designer,yaml.safe_dump(data))
        editor=root/'profiles/editor/SOUL.md';write(editor,b'')
        marketer=root/'profiles/marketer/SOUL.md'
        digest=lambda p:repair.sha(p.read_bytes())
        write(home/'repair-native-seed.json',json.dumps({'soul':digest(root/'profiles/tech/SOUL.md'),'project':digest(project),'user':digest(user),'cli':['web'],'marketer':marketer.read_bytes().hex()}))
        findings=[{'code':'soul:marketer','status':'fixed'},{'code':'soul:editor','status':'needs-platform-update'}]
        for report in [install/'control/tariffs/last-repair.json',home/'Downloads/apply.json']:
            write(report,json.dumps({'findings':findings}))
        with patch.object(sys,'platform','darwin'):
            self.assertTrue(self.run_native_fixture(home,'repaired')['pass'])
            for path in [root/'config.yaml',*sorted((root/'profiles').glob('*/config.yaml'))]:
                before=path.read_bytes();data=yaml.safe_load(before);data['kanban']={'dispatch_in_gateway':False}
                write(path,yaml.safe_dump(data))
                try:
                    with self.assertRaises(AssertionError):self.run_native_fixture(home,'repaired')
                finally:write(path,before)

    def test_ci_layout_and_debug_handoff_are_wired(self):
        windows=(ROOT/'scripts/windows-patch-native-rehearsal.mjs').read_text()
        self.assertIn("process.env.AGENTS_REPAIR_DEBUG_FILE=join(root,'debug.log')",windows)
        self.assertLess(windows.index("checked('layout'"),windows.index("await run('diagnose'"))
        self.assertIn('ReparsePoint',windows)
        for name in ['last-apply.json','windows-install-receipt.json','tariff-files.json']:self.assertIn(name,windows)
        launcher=(BUNDLE/'repair.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("$start.EnvironmentVariables['AGENTS_REPAIR_DEBUG_FILE']=$env:AGENTS_REPAIR_DEBUG_FILE",launcher)
        workflow=(ROOT/'.github/workflows/windows-agents-repair.yml').read_text()
        self.assertIn('agents-repair-*/debug.log',workflow);self.assertIn('agents-repair-*/layout.log',workflow)

    def test_launcher_syntax_and_rehearsal_parse(self):
        for shell in ['bash','zsh']:
            if shutil.which(shell): self.assertEqual(subprocess.run([shell,'-n',str(BUNDLE/'repair.sh')],capture_output=True).returncode,0)
        if shutil.which('pwsh'):
            source="$t=$null;$e=$null;[Management.Automation.Language.Parser]::ParseFile('"+str(BUNDLE/'repair.ps1')+"',[ref]$t,[ref]$e)|Out-Null;if($e.Count){exit 1}"
            self.assertEqual(subprocess.run(['pwsh','-NoProfile','-Command',source],capture_output=True).returncode,0)
        for name in ['mac-patch-rehearsal.mjs','ubuntu-patch-rehearsal.mjs','windows-patch-native-rehearsal.mjs']:
            self.assertEqual(subprocess.run(['node','--check',str(ROOT/'scripts'/name)],capture_output=True).returncode,0,name)


if __name__=='__main__':
    unittest.main()
