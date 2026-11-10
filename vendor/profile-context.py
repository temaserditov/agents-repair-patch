"""Reversible context-loader repair for two explicitly reviewed runtime pins."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

BASE_FILE_SHA256='660e7a2c311adf1b794c7b81c83090ea1b6f07619bd79be152c2f3c099ec0f05'
BASE_FUNCTION_SHA256='68255b0d6c499f778c4b552f44bee1014831a99a59884d79608e886f4952fc6d'
REVIEWED_SOURCES={
    BASE_FILE_SHA256:BASE_FUNCTION_SHA256,
    # v0.23.0 / 2bd378ed: same project priority and SOUL guard; a new safe-worker check.
    '92794560ec7e3e0396e9f33bc505c7d2e13aed1275f199b39ed934f3c54a9fcb':'8eeecd693300ee32b56d8ac19b18de60bcf2920c6891b61697995b629e8c7a4d',
    '64bc77a26641754b8c5ca76fc876687d3c9e81aad7816c2e76ce251a41121526':'2dee36955875e2fddd8425170ad73f76aa2f9280de1784ca732020c50f1fc1d9',
}
MARKER='# serditov-windows-profile-context-v1'
INSERT='''    # serditov-windows-profile-context-v1
    # Read only this profile's file, never its parent/default-profile chain.
    profile_path = (Path(home_override) if home_override is not None else get_hermes_home()).resolve()
    profile_context = ""
    for profile_name in ("AGENTS.override.md", "AGENTS.md", "agents.md"):
        profile_file = profile_path / profile_name
        profile_text = _read_context_file(profile_file)
        if profile_text:
            profile_context = _context_section(profile_text, profile_name, profile_name, profile_file, context_length)
            break
    # Keep normal project priority and avoid duplicating an identical section.
    if profile_context and not any(profile_context in section for section in sections):
        sections.insert(0, "## Active Profile Instructions\\n\\n" + profile_context)
'''


def sha(data):return hashlib.sha256(data).hexdigest()


def transform(raw):
    text=raw.decode('utf-8').replace('\r\n','\n')
    tree=ast.parse(text)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_context_files_prompt')
    current=ast.get_source_segment(text,function)
    if MARKER in current:
        if current.count(INSERT)!=1 or sha(current.replace(INSERT,'',1).encode()) not in REVIEWED_SOURCES.values():
            raise ValueError('Previously repaired function was edited; preserve it and review manually')
        return raw,False
    if REVIEWED_SOURCES.get(sha(raw))!=sha(current.encode()):
        raise ValueError('Unknown runtime source; no changes made')
    anchor='    if not skip_soul:\n'
    if current.count(anchor)!=1:raise ValueError('Unknown context-loader structure')
    updated=current.replace(anchor,INSERT+anchor,1)
    if text.count(current)!=1:raise ValueError('Ambiguous context-loader source')
    result=text.replace(current,updated,1).encode()
    compile(result,'agent/prompt_builder.py','exec')
    return result,True


def apply(runtime_root,backup_root):
    root=Path(runtime_root).resolve();path=root/'agent/prompt_builder.py'
    if path.is_symlink() or (path.parent.is_symlink()):raise ValueError('Linked runtime destination rejected')
    raw=path.read_bytes();updated,changed=transform(raw)
    backup=None
    if changed:
        backup=Path(backup_root)/('windows-profile-context-'+uuid.uuid4().hex)/'prompt_builder.py'
        backup.parent.mkdir(parents=True,exist_ok=False);backup.write_bytes(raw)
        fd,stage=tempfile.mkstemp(prefix='.profile-context-',suffix='.py',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as output:output.write(updated)
            os.replace(stage,path)
        finally:
            if os.path.exists(stage):os.unlink(stage)
    return {'fix':'windows-profile-context-v1','changed':changed,'before':sha(raw),'after':sha(updated),'backup':str(backup) if backup else None}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-root',required=True);parser.add_argument('--backup-root',required=True)
    args=parser.parse_args();print(json.dumps(apply(args.runtime_root,args.backup_root)))
