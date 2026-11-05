"""Restore the Designer's working tools without changing accounts or image settings."""
import json
import os
from pathlib import Path
import sys
import tempfile

try:
    import yaml
except ImportError:
    import hermes_yaml as yaml

LEGACY_DESIGNER_TOOLSETS = frozenset(("skills", "file", "terminal", "vision", "image_gen"))
# agent-web-shell-0.3.0/web-shell/runner.py, focused mode, plus generation.
WEB_DESIGNER_TOOLSETS = ("clarify", "code_execution", "file", "messaging", "skills",
                         "terminal", "vision", "web", "image_gen")
EXPLICITLY_DISABLED = "skipped: explicitly disabled"


def repair_designer_tools(config_path):
    path = Path(config_path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Linked Designer configuration")
    if not path.exists():
        return False
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Invalid Designer configuration")
    previous = data.get("platform_toolsets")
    if previous is not None and not isinstance(previous, dict):
        raise ValueError("Invalid Designer toolsets")
    toolsets = dict(previous or {})
    for platform in ("cli", "telegram", "web"):
        current = toolsets.get(platform)
        if current is not None and (not isinstance(current, list) or
                                    any(not isinstance(item, str) for item in current)):
            raise ValueError("Invalid Designer platform toolsets: " + platform)
        # Only repair selections written by our old installer. Respect custom
        # lists; CLI/Telegram must inherit Hermes bundles, including new tools.
        if current is None or set(current) <= LEGACY_DESIGNER_TOOLSETS:
            if platform == "web":
                toolsets[platform] = list(WEB_DESIGNER_TOOLSETS)
            else:
                toolsets.pop(platform, None)
    agent = data.get("agent") or {}
    if not isinstance(agent, dict):
        raise ValueError("Invalid Designer agent configuration")
    disabled = agent.get("disabled_toolsets", [])
    if isinstance(disabled, str):
        try:
            disabled = json.loads(disabled)
        except ValueError:
            disabled = [disabled]
    if isinstance(disabled, list) and set(WEB_DESIGNER_TOOLSETS).intersection(disabled):
        return EXPLICITLY_DISABLED
    if previous == toolsets:
        return False
    # Back up only tool selections. A complete config may contain credentials.
    backup = path.with_name("designer-tools.before.json")
    if backup.is_symlink():
        raise ValueError("Linked Designer toolset backup")
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not backup.is_file():
            raise ValueError("Invalid Designer toolset backup")
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"platform_toolsets": previous}, output, ensure_ascii=False, indent=2)
            output.write("\n")
    data["platform_toolsets"] = toolsets
    fd, temporary = tempfile.mkstemp(prefix=".designer-tools-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            yaml.safe_dump(data, output, allow_unicode=True, sort_keys=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


if __name__ == "__main__":
    changed = repair_designer_tools(sys.argv[1])
    print(changed if isinstance(changed, str) else
          "Designer tools restored" if changed else "Designer tools unchanged")
