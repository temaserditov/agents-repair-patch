"""Reviewed, reversible panel fixes for installed Mac/Linux WebShell (TASK48).

Each fix is an exact fragment replacement. A fix applies only when every old
fragment is present exactly once; an already-fixed file is left unchanged; any
other text is a learner/support edit and is reported, never rewritten.
``canonical`` reverses the fixes so a fixed file is still recognised as its
reviewed release variant (known SHA), instead of looking like an unknown edit.
"""
import ast
import hashlib

# BUG 0006: Hermes 0.23 moved the session-key context to tools.approval_context;
# 0.21.5 keeps it in tools.approval. Same shim as the verified support repair.
RUNNER_OLD_NAMES = ('            set_current_session_key,\n', '            reset_current_session_key,\n')
RUNNER_ANCHOR = '            unregister_gateway_notify,\n        )\n'
RUNNER_SHIM = ('        try:\n'
               '            from tools.approval_context import set_current_session_key, reset_current_session_key\n'
               '        except ImportError:\n'
               '            from tools.approval import set_current_session_key, reset_current_session_key\n')

# BUG 0001: topic words are routing hints, not proof of an external action.
# Readiness failures answer 503, explicit refusals 422, instead of HTTP 500.
PREFLIGHT_ERROR = '''function preflightError(preflight) {
  const failed = preflight.checks.filter((check) => !check.ok && check.blocking);
  const error = new Error(`preflight failed: ${failed.map((check) => check.failLabel || check.label).join("; ")}`);
  error.statusCode = failed.some((check) => check.id === "gateway") ? 503 : 422;
  error.code = "PREFLIGHT_REJECTED";
  error.checks = failed.map((check) => check.id);
  return error;
}

'''
SERVER_PREFLIGHT = [
    ('    { id: "prompt-risk", label: "Prompt does not look like browser/payment/order automation", failLabel: "Prompt looks like browser/payment/order automation", ok: promptRisk.blockers.length === 0, blocking: true },\n'
     '    { id: "prompt-role-policy", label: "Prompt matches selected agent role", failLabel: "Prompt does not match selected agent role", ok: roleRisk.blockers.length === 0, blocking: true },\n',
     '    // Subject words are routing hints, not evidence of an external action.\n'
     '    // Actual tool permissions and approvals remain enforced by the runner.\n'
     '    { id: "prompt-risk", label: "Prompt topic hints reviewed", ok: true, blocking: false },\n'
     '    { id: "prompt-role-policy", label: "Agent topic hints reviewed", ok: true, blocking: false },\n'),
    ('  const blockers = hits.filter((tag) => ["browser", "payment"].includes(tag));\n'
     '  const suggestedMode = blockers.length ? "quick" : hits.length ? "focused" : "quick";\n'
     '  return { hits: [...new Set(hits)], blockers: [...new Set(blockers)], suggestedMode };\n',
     '  const warnings = hits.filter((tag) => ["browser", "payment"].includes(tag));\n'
     '  const suggestedMode = warnings.length ? "quick" : hits.length ? "focused" : "quick";\n'
     '  return { hits: [...new Set(hits)], blockers: [], warnings: [...new Set(warnings)], suggestedMode };\n'),
    ('  const blockers = uniqueHits.filter((tag) => discouraged.has(tag) && !allowed.has(tag));\n'
     '  return {\n    checked: true,\n    blockers,\n    warnings: uniqueHits.filter((tag) => allowed.has(tag)),\n',
     '  const warnings = uniqueHits.filter((tag) => discouraged.has(tag) && !allowed.has(tag));\n'
     '  return {\n    checked: true,\n    blockers: [],\n    warnings,\n'),
    ('    failedChecks: preflight.checks.filter((check) => !check.ok).map((check) => check.id),\n',
     '    failedChecks: preflight.checks.filter((check) => !check.ok && check.blocking).map((check) => check.id),\n'),
    ('    const failed = preflight.checks.filter((check) => !check.ok).map((check) => check.label).join("; ");\n'
     '    throw new Error(`preflight failed: ${failed}`);\n',
     '    throw preflightError(preflight);\n'),
    ('function runGuardSnapshot(preflight) {\n', PREFLIGHT_ERROR+'function runGuardSnapshot(preflight) {\n'),
    ('    sendJson(res, 500, { error: error.message || String(error) });\n',
     '    const status = Number.isInteger(error.statusCode) && error.statusCode >= 400 && error.statusCode < 600 ? error.statusCode : 500;\n'
     '    sendJson(res, status, {\n'
     '      error: error.message || String(error),\n'
     '      ...(error.code === "PREFLIGHT_REJECTED" ? { code: error.code, checks: error.checks } : {}),\n'
     '    });\n'),
]
# BUG 0015: the sales profile shows its Russian name (same text as FIX_0560).
SERVER_SALES = [('    rop: "Продажник",\n    tech: "Технарь",', '    rop: "Продажник",\n    sales: "Продажник",\n    tech: "Технарь",')]

# BUG 0014 root cause: the panel appended a SECOND top-level platforms: block when
# the existing one did not already enable Telegram. Merge into the single block;
# leave unfamiliar (flow/inline) shapes untouched rather than duplicate the key.
PLATFORMS_OLD = ('  if (/^platforms:\\s*$/m.test(text) && /(^|\\n)  telegram:\\s*\\n(?:    .*\\n)*?    enabled:\\s*true\\b/m.test(text)) return;\n'
                 '  text = `${text.replace(/\\s*$/, "")}\\n\\n# Infobiz Agents messaging defaults\\nplatforms:\\n  telegram:\\n    enabled: true\\n`;\n'
                 '  atomicWriteFile(configPath, text, { encoding: "utf8", mode: 0o600 });\n')
PLATFORMS_NEW = (PLATFORMS_OLD.split('\n')[0] + '\n' + r'''  // BUG 0014: keep ONE top-level platforms: block and merge telegram.enabled into it.
  // Only the plain shape Hermes writes (unquoted keys, 2/4-space indent) is edited;
  // any other valid YAML is left exactly as it is rather than risk changing it.
  const original = text;
  const bom = text.startsWith("\uFEFF") ? "\uFEFF" : "";
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.slice(bom.length).split(eol);
  const meaningful = (line) => line.trim() !== "" && !line.trim().startsWith("#");
  // The whole file must be plain block YAML: unquoted top-level keys (or "- " items under
  // them), no flow root, documents, directives, explicit keys, tabs, merges, anchors/aliases.
  const first = lines.find(meaningful);
  if (first !== undefined && !/^[A-Za-z0-9_-]+:(\s|$)/.test(first)) return;
  for (const line of lines) {
    if (!meaningful(line)) continue;
    const indent = line.match(/^\s*/)[0];
    const code = line.replace(/\s#.*$/, "");
    if (/\t/.test(indent) || /^(---|\.\.\.|%|\{|\[)/.test(line) || /^\s*\?(\s|$)/.test(line)) return;
    if (/<<\s*:/.test(code) || /(^|[\s:,\[{-])[&*][A-Za-z0-9_-]/.test(code)) return;
    // Block scalars keep significant trailing lines; multi-line quoted scalars span lines.
    if (/:\s*[|>]/.test(code) || /^\s*-\s*[|>]/.test(code)) return;
    if ((code.match(/"/g) || []).length % 2 || (code.match(/'/g) || []).length % 2) return;
    if (!indent && !/^[A-Za-z0-9_-]+:(\s|$)/.test(line) && !/^-(\s|$)/.test(line)) return;
  }
  const tops = lines.filter((line) => /^["']?platforms["']?\s*:/.test(line));
  if (tops.length > 1) return;
  if (!tops.length) {
    const tail = text === "" || text.endsWith(eol) ? "" : eol;
    text = `${text}${tail}# Infobiz Agents messaging defaults${eol}platforms:${eol}  telegram:${eol}    enabled: true${eol}`;
  } else {
    const start = lines.findIndex((line) => /^platforms:\s*(#.*)?$/.test(line));
    if (start < 0) return;
    let end = start + 1;
    while (end < lines.length && (!meaningful(lines[end]) || /^\s/.test(lines[end]))) end++;
    const plain = (indent) => new RegExp(`^${indent}[A-Za-z0-9_-]+:(\\s|$)`);
    const children = lines.slice(start + 1, end).filter(meaningful);
    if (children.length && !plain("  ").test(children[0])) return;
    if (children.some((line) => !plain("  ").test(line) && !/^    /.test(line))) return;
    const telegrams = [];
    for (let i = start + 1; i < end; i++) if (/^  telegram:/.test(lines[i])) telegrams.push(i);
    if (telegrams.length > 1) return;
    if (!telegrams.length) lines.splice(start + 1, 0, "  telegram:", "    enabled: true");
    else {
      const at = telegrams[0];
      if (!/^  telegram:\s*(#.*)?$/.test(lines[at])) return;
      let stop = at + 1;
      while (stop < end && (!meaningful(lines[stop]) || /^   /.test(lines[stop]))) stop++;
      const sub = lines.slice(at + 1, stop).filter(meaningful);
      if (sub.length && !plain("    ").test(sub[0])) return;
      if (sub.some((line) => !plain("    ").test(line) && !/^      /.test(line))) return;
      const enabled = [];
      for (let i = at + 1; i < stop; i++) if (/^    enabled:/.test(lines[i])) enabled.push(i);
      if (enabled.length > 1) return;
      if (!enabled.length) lines.splice(at + 1, 0, "    enabled: true");
      else if (/^    enabled:\s*(true|false|True|False|yes|no|on|off)\s*(#.*)?$/.test(lines[enabled[0]])) lines[enabled[0]] = "    enabled: true";
      else return;
    }
    text = bom + lines.join(eol);
  }
  // Prove it with the strict loader Hermes uses: every value is unchanged except
  // platforms.telegram.enabled=true, and no key repeats. Otherwise keep the file.
  const check = [
    "import json,sys",
    "d=json.loads(sys.stdin.read())",
    "try:",
    " import hermes_yaml as Y",
    " load=Y.safe_load",
    "except ModuleNotFoundError as e:",
    " if e.name!='hermes_yaml':raise",
    " import yaml",
    " class S(yaml.SafeLoader):pass",
    " def m(l,n,deep=False):",
    "  k=[l.construct_object(x,deep=deep) for x,_ in n.value]",
    "  if len(k)!=len(set(k)):raise ValueError('duplicate key')",
    "  return yaml.SafeLoader.construct_mapping(l,n,deep)",
    " S.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,m)",
    " load=lambda t:yaml.load(t,Loader=S)",
    "b=load(d['before'].lstrip('\\ufeff')) or {}",
    "a=load(d['after'].lstrip('\\ufeff'))",
    "p=b.get('platforms') if isinstance(b,dict) else None",
    "p={} if p is None else p",
    "t=p.get('telegram') if isinstance(p,dict) else None",
    "t={} if t is None else t",
    "if not isinstance(b,dict) or not isinstance(p,dict) or not isinstance(t,dict):sys.exit(3)",
    "sys.exit(0 if a==dict(b,platforms=dict(p,telegram=dict(t,enabled=True))) else 4)",
  ].join("\n");
  const proof = spawnSync(HERMES_PYTHON, ["-B", "-c", check], {
    input: JSON.stringify({ before: original, after: text }), encoding: "utf8", timeout: 20000,
    cwd: HERMES_AGENT_ROOT, env: { ...process.env, PYTHONPATH: HERMES_AGENT_ROOT, PYTHONDONTWRITEBYTECODE: "1" },
  });
  if (proof.status !== 0) return;
  atomicWriteFile(configPath, text, { encoding: "utf8", mode: 0o600 });
''')
SERVER_PLATFORMS = [(PLATFORMS_OLD, PLATFORMS_NEW)]

# The panel's own audit pins the runner bytes; accept the reviewed BUG 0006 fix
# too (= verified support repair 4cf98c24), otherwise "toolsets" fails forever.
RUNNER_UPDATED_SHA = '5ee341cd96fdb83ed916af4c640f4de586344c53ecca0b38b13a2a6dfc10fb9f'
RUNNER_FIXED_SHA = '4cf98c240e6d50676351e2b4e8e1096cada212e57e44368c65e06983403b00e4'
SERVER_AUDIT = [("assert hashlib.sha256(runner.read_bytes()).hexdigest()=='"+RUNNER_UPDATED_SHA+"', 'Unreviewed runner'",
                 "assert hashlib.sha256(runner.read_bytes()).hexdigest() in {'"+RUNNER_UPDATED_SHA+"','"+RUNNER_FIXED_SHA+"'}, 'Unreviewed runner'")]


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _pairs(text, pairs):
    """'fixed' / 'applicable' / 'unknown' for one all-or-nothing fix."""
    if all(new in text for _, new in pairs) and not any(old in text and old not in new for old, new in pairs):
        return 'fixed'
    if all(text.count(old) == 1 for old, _ in pairs):
        return 'applicable'
    return 'unknown'


def _apply(text, pairs):
    for old, new in pairs:
        text = text.replace(old, new, 1)
    return text


def _revert(text, pairs):
    for old, new in reversed(pairs):
        if text.count(new) == 1:
            text = text.replace(new, old, 1)
    return text


def runner_state(text):
    if 'from tools.approval_context import set_current_session_key, reset_current_session_key' in text:
        return 'fixed' if text.count(RUNNER_SHIM) == 1 else 'unknown'
    if all(text.count(n) == 1 for n in RUNNER_OLD_NAMES) and text.count(RUNNER_ANCHOR) == 1:
        return 'applicable'
    return 'unknown'


def fix_runner(text):
    if runner_state(text) != 'applicable':
        return text
    for name in RUNNER_OLD_NAMES:
        text = text.replace(name, '', 1)
    text = text.replace(RUNNER_ANCHOR, RUNNER_ANCHOR+RUNNER_SHIM, 1)
    ast.parse(text)
    return text


def canonical_runner(text):
    if runner_state(text) != 'fixed':
        return text
    text = text.replace(RUNNER_SHIM, '', 1)
    body = ''.join(RUNNER_OLD_NAMES)
    return text.replace(RUNNER_ANCHOR, body+RUNNER_ANCHOR, 1)


SERVER_FIXES = (('preflight', SERVER_PREFLIGHT), ('platforms-writer', SERVER_PLATFORMS), ('runner-audit', SERVER_AUDIT), ('sales-label', SERVER_SALES))


def server_states(text):
    return {name: _pairs(text, pairs) for name, pairs in SERVER_FIXES}


def fix_server(text):
    for name, pairs in SERVER_FIXES:
        if _pairs(text, pairs) == 'applicable':
            text = _apply(text, pairs)
    return text


def canonical_server(text):
    # Only the preflight fix is ours alone; the sales label is part of the
    # reviewed 20261108.1 variant and is kept when it was already there.
    for pairs in (SERVER_PREFLIGHT, SERVER_PLATFORMS, SERVER_AUDIT):
        if _pairs(text, pairs) == 'fixed':
            text = _revert(text, pairs)
    return text


def canonical_server_without_sales(text):
    text = canonical_server(text)
    if _pairs(text, SERVER_SALES) == 'fixed':
        text = _revert(text, SERVER_SALES)
    return text
