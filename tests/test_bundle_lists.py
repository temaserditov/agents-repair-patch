"""Every rehearsal that copies the repair bundle into a guest must copy all of it.

Both host-driven rehearsals (Tart Mac/Ubuntu and the hosted Ubuntu fixture)
keep a literal file list. ``agents_repair.py`` refuses to run without any
vendored helper, so a list that lags behind ``vendor/`` turns every guest run
into "installation unfixable" (exit 2). The required set is derived from the
bundle on disk, never typed by hand, so the lists cannot drift again.
"""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT/'installer/repair-patch'
REHEARSALS = {
    'scripts/lib/repair-native-rehearsal.mjs': r"for\(const name of \[([^\]]*)\]\)\{\s*const bytes=readFileSync\(join\(repo,'installer/repair-patch',name\)\)",
    'scripts/lib/native-production-fixture.mjs': r"const names=\[([^\]]*)\];\s*const files=Object\.fromEntries\(names\.map\(n=>\[n,readFileSync\(join\(repo,'installer/repair-patch',n\)\)",
}


def required_bundle():
    names = {'agents_repair.py', 'repair.sh', 'known-baselines.json'}
    for path in (BUNDLE/'vendor').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            names.add(path.relative_to(BUNDLE).as_posix())
    return names


class RepairBundleLists(unittest.TestCase):
    def test_required_bundle_is_what_the_patch_loads(self):
        required = required_bundle()
        source = (BUNDLE/'agents_repair.py').read_text(encoding='utf-8')
        for helper in re.findall(r"load\('([a-z-]+)'\)", source):
            self.assertIn('vendor/'+helper+'.py', required, helper)
        for name in re.findall(r"for name in \[([^\]]*)\]:\s*plain\(HERE/'vendor'/name\)", source):
            for item in json.loads('['+name.replace("'", '"')+']'):
                self.assertIn('vendor/'+item, required, item)
        self.assertIn('vendor/team-runtime.py', required)
        self.assertIn('vendor/plugins/infobiz-team/__init__.py', required)
        self.assertIn('vendor/plugins/infobiz-team/plugin.yaml', required)

    def test_every_rehearsal_copies_the_complete_bundle(self):
        required = required_bundle()
        for relative, pattern in REHEARSALS.items():
            with self.subTest(rehearsal=relative):
                source = (ROOT/relative).read_text(encoding='utf-8')
                match = re.search(pattern, source)
                self.assertIsNotNone(match, 'bundle list not found in '+relative)
                listed = set(json.loads('['+match.group(1).replace("'", '"')+']'))
                self.assertEqual(sorted(required-listed), [], relative+' omits bundle files')
                self.assertEqual(sorted(listed-required-{'repair.ps1'}), [], relative+' copies files outside the bundle')
                for name in listed:
                    self.assertTrue((BUNDLE/name).is_file(), name)

    def test_tart_rehearsal_creates_nested_vendor_directories(self):
        source = (ROOT/'scripts/lib/repair-native-rehearsal.mjs').read_text(encoding='utf-8')
        self.assertIn("mkdir -p \"$HOME/repair/${name.includes('/')?name.slice(0,name.lastIndexOf('/')):''}\"", source)


if __name__ == '__main__':
    unittest.main()
