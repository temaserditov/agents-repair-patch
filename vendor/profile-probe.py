"""Read-only loader probe. Never calls a model, a bot, or an authorization flow."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def probe(request):
    home = Path(request['home'])
    profile = request['profile']
    directory = home / 'profiles' / profile
    # A fresh process per profile: upstream global caches must not leak between bots.
    os.environ['HERMES_HOME'] = str(directory)
    os.environ['HERMES_ACTIVE_PROFILE'] = profile
    # -c uses an empty sys.path[0]; keep the reviewed runtime source importable
    # after entering the profile's own working directory.
    sys.path.insert(0, str(Path.cwd()))
    os.chdir(directory)
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(str(directory))
    from agent.prompt_builder import build_context_files_prompt
    from tools import skills_tool
    context = build_context_files_prompt(cwd=str(directory), home_override=directory)
    instructions = 'not-loaded'
    # Test real instruction text in the real loader's result, not a file's existence.
    for filename in ('AGENTS.override.md', 'AGENTS.md', 'agents.md', 'SOUL.md'):
        file = directory / filename
        if file.is_file() and not file.is_symlink():
            text = file.read_text(encoding='utf-8').strip()
            if text:
                if len(text) >= 80 and text in context:
                    instructions = 'loaded'
                # Generic SOUL must not mask an omitted course AGENTS.md.
                break
    discovered = {item['name'] for item in skills_tool._find_all_skills()}
    visible = []
    # Use Hermes' own frontmatter parser and resolver. Same-name shadowing must
    # not make a different skill appear as the promised course skill.
    project_dirs, all_dirs, _ = skills_tool._skill_search_dirs()
    for relative in request['skills']:
        file = home / 'profiles' / relative
        if not file.is_file() or file.is_symlink():
            continue
        text = file.read_text(encoding='utf-8')
        metadata, _ = skills_tool._parse_frontmatter(text[:4000])
        name = metadata.get('name', file.parent.name)
        if name not in discovered:
            continue
        # API differs between reviewed Hermes builds. Unknown resolver shapes
        # fail this observation instead of assuming that a matching name is enough.
        candidates = skills_tool._collect_skill_candidates(name, None, all_dirs)
        paths = [entry[1].resolve() for entry in candidates]
        if paths and all(path == file.resolve() for path in paths):
            visible.append(relative)
    gateway = 'unknown'
    tool_access = {key: 'unknown' for key in ('cli', 'telegram', 'web')}
    try:
        import yaml
        from hermes_cli.tools_config import _get_platform_tools
        config_path = directory / 'config.yaml'
        config = yaml.safe_load(config_path.read_text()) if config_path.is_file() and not config_path.is_symlink() else {}
        if not isinstance(config, dict):
            raise ValueError('Invalid profile tool configuration')
        required = {'skills', 'file', 'terminal'}
        if profile == 'designer':
            required.add('image_gen')
        for platform in tool_access:
            # WebShell chooses its own focused/full list when no explicit web
            # selection exists. Hermes' generic "hermes-web" alias is not a
            # permission denial and cannot prove this runner's live selection.
            if platform == 'web' and not isinstance((config.get('platform_toolsets') or {}).get('web'), list):
                continue
            enabled = _get_platform_tools(config, platform, include_default_mcp_servers=False)
            tool_access[platform] = 'allowed' if required <= set(enabled) else 'blocked'
    except (ImportError, AttributeError, TypeError, ValueError, OSError):
        pass
    try:
        from gateway import status
        read_pid = status.get_running_pid
        def no_cleanup(*args, **kwargs):
            kwargs['cleanup_stale'] = False
            return read_pid(*args, **kwargs)
        # Also covers the default-multiplexer fallback inside Hermes. This is a
        # disposable probe process, never a patch to the running gateway.
        status.get_running_pid = no_cleanup
        live = status.resolve_gateway_liveness(profile_dir=directory, use_cache=False, pid_probe=no_cleanup)
        gateway = 'unknown' if getattr(live, 'probe_error', False) else ('running' if live.running else 'stopped')
    except PermissionError:
        gateway = 'denied'
    except (ImportError, AttributeError, TypeError, OSError):
        pass
    return {'instructions': instructions, 'skills': visible, 'gateway': gateway, 'toolAccess': tool_access,
            'runtimeContext': 'unverified', 'probe': 'ok', 'reason': 'none'}


if __name__ == '__main__':
    request = json.loads(sys.stdin.read(256 * 1024))
    try:
        # Upstream may log local paths during discovery. Neither logs nor prompts
        # are included in the result or forwarded to the platform.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = probe(request)
    except Exception as error:
        reason = ('module-missing' if isinstance(error, ImportError) else
                  'access-denied' if isinstance(error, PermissionError) else
                  'profile-missing' if isinstance(error, FileNotFoundError) else
                  'api-unsupported' if isinstance(error, (AttributeError, TypeError)) else 'loader-failed')
        result = {'instructions': 'unknown', 'skills': [], 'gateway': 'unknown',
                  'runtimeContext': 'unverified', 'probe': 'failed', 'reason': reason}
    print(json.dumps(result))
