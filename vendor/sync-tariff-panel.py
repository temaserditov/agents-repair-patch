"""Narrow, idempotent profile-list adapter; never reads an env/auth file."""
import json
from pathlib import Path
import os
import platform
import subprocess
import uuid
from datetime import datetime, timezone

def guard(path):
    for p in [path,*path.parents]:
        if p.is_symlink():raise ValueError('Linked panel destination')
        try:
            if getattr(p.lstat(),'st_file_attributes',0)&0x400:raise ValueError('Linked panel destination')
        except FileNotFoundError:pass

def atomic(path,text):
    guard(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    try:
        with tmp.open('x',encoding='utf-8') as out:out.write(text);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()

MARKER='// agents-tariff-profiles-v1'
ORDER='const ALL_PROFILE_ORDER = ["default", "marketer", "copywriter", "designer", "editor", "tech", "assistant", "sales"];'
HELPER='''// agents-tariff-profiles-v1
function installerAllowedProfiles() {
  const root = process.env.AGENT_INSTALL_CONTROL_ROOT || join(process.env.HOME || process.env.USERPROFILE || ".", "InfobizAgents", "control", "tariffs");
  try {
    const value = JSON.parse(readFileSync(join(root, "allowed-profiles.json"), "utf8"));
    const permitted = ["marketer","copywriter","designer","editor","tech","assistant","sales"];
    if (!Array.isArray(value.profiles) || value.profiles.length < 5 || value.profiles.some(id => !permitted.includes(id))) return "__invalid__";
    return value.profiles.join(",");
  } catch (error) {
    return error.code === "ENOENT" ? process.env.AGENT_PROFILE_ALLOW || "" : "__invalid__";
  }
}
'''

def patch(text):
    if MARKER in text:
        if HELPER not in text or ORDER not in text or 'String(installerAllowedProfiles())' not in text:raise ValueError('Edited profile adapter; merge explicitly')
        return text
    import re
    matches=list(re.finditer(r'const ALL_PROFILE_ORDER = \[[^\n]+\];',text))
    if len(matches)!=1 or text.count('String(process.env.AGENT_PROFILE_ALLOW || "")')!=1:raise ValueError('Unknown WebShell profile source')
    text=text[:matches[0].start()]+ORDER+text[matches[0].end():]
    return text.replace('const PROFILE_ALLOW = new Set(',HELPER+'\nconst PROFILE_ALLOW = new Set(',1).replace('String(process.env.AGENT_PROFILE_ALLOW || "")','String(installerAllowedProfiles())',1)

def patch_control(text):
    old='const PROFILE_NAMES = ["default", "marketer", "copywriter", "designer", "tech", "sales"];'
    new='const PROFILE_NAMES = ["default", "marketer", "copywriter", "designer", "editor", "tech", "assistant", "sales"];'
    old_pattern='const PROFILE_PATTERN = /^(default|marketer|copywriter|designer|tech|sales)$/;'
    new_pattern='const PROFILE_PATTERN = /^(default|marketer|copywriter|designer|editor|tech|assistant|sales)$/;'
    if new in text and new_pattern in text:return text
    if text.count(old)!=1 or text.count(old_pattern)!=1:raise ValueError('Unknown signed-client profile list; preserve source for explicit review')
    return text.replace(old,new,1).replace(old_pattern,new_pattern,1)

RESTART_MARKER='web-shell-restart-pending'

def restart_panel(home,control):
    """A service-manager failure must not interrupt managed-file delivery."""
    home,control=Path(home),Path(control)
    marker=control/RESTART_MARKER
    guard(marker)
    system=platform.system()
    plist=home/'Library/LaunchAgents/com.infobiz.agents.web-shell.plist'
    def run(args):
        try:
            return subprocess.run(args,check=False,stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,timeout=30).returncode==0
        except (OSError,subprocess.TimeoutExpired):
            return False
    if system=='Darwin' and plist.is_file():
        guard(plist)
        domain=f'gui/{os.getuid()}'
        kick=['launchctl','kickstart','-k',domain+'/com.infobiz.agents.web-shell']
        ok=run(kick)
        if not ok:
            # FIX_0563: bootstrap already starts the RunAtLoad panel. kickstart -k
            # right after it can kill the instance in launchd's spawn trampoline,
            # and launchd then abandons a live process that keeps the port.
            ok=run(['launchctl','bootstrap',domain,str(plist)]) or run(kick)
        reason='launchctl-failed'
    elif system=='Linux':
        try:
            state=subprocess.run(['systemctl','show','-p','LoadState','--value','infobiz-web-shell.service'],
                                 check=False,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                 text=True,timeout=30)
            load=(state.stdout or '').strip()
        except (OSError,subprocess.TimeoutExpired):
            load=''
        if load in {'masked','LoadState=masked'}:
            ok=False;reason='systemctl-masked'
        elif Path('/etc/systemd/system/infobiz-web-shell.service').is_file() or load in {'loaded','LoadState=loaded'}:
            ok=run(['systemctl','restart','infobiz-web-shell.service'])
            reason='systemctl-failed'
        else:
            return {'panel':'not-installed'}
    else:
        return {'panel':'not-installed'}
    if ok:
        if marker.exists():marker.unlink()
        return {'panel':'configured'}
    atomic(marker,json.dumps({'time':datetime.now(timezone.utc).isoformat(),'reason':reason})+'\n')
    return {'panel':'restart-pending','reason':reason}

def sync(home,control,profiles):
    home,control=Path(home),Path(control)
    if not set(profiles)<={'marketer','copywriter','designer','editor','tech','assistant','sales'}:raise ValueError('Unknown panel profile')
    guard(control);control.mkdir(parents=True,exist_ok=True)
    target=control/'allowed-profiles.json'
    if target.is_symlink():raise ValueError('Linked profile list')
    atomic(target,json.dumps({'profiles':profiles})+'\n')
    client=home/'InfobizAgents/web-shell/scripts/managed-agent-control.mjs'
    if client.is_file():
        guard(client)
        before=client.read_text(encoding='utf-8');after=patch_control(before)
        if after!=before:
            backup=control/'signed-client-profiles.preimage.mjs'
            if not backup.exists():atomic(backup,before)
            atomic(client,after)
    server=home/'InfobizAgents/web-shell/server.mjs'
    if not server.is_file():return {'panel':'not-installed'}
    guard(server)
    before=server.read_text(encoding='utf-8');after=patch(before)
    if after!=before:
        backup=control/'web-shell-profile-adapter.preimage.mjs'
        if not backup.exists():atomic(backup,before)
        atomic(server,after)
    result=restart_panel(home,control)
    if result['panel']=='not-installed':result={'panel':'configured'}
    return {**result,'profiles':profiles}
