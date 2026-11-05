"""Best-effort retry through the installed Hermes verifier; no custom download."""
import importlib.util
import os
from pathlib import Path
import shutil


def _load_tirith(home):
    source = home / 'hermes-agent/tools/tirith_security.py'
    if source.is_symlink() or not source.is_file():
        raise ImportError('Installed Hermes scanner helper unavailable')
    spec = importlib.util.spec_from_file_location('installer_tirith_security', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repair_tirith(home, log):
    """home is HERMES_HOME. Return a finite report value; log is a callable.

    The release assembler must call this after gateway from finish-install.py
    and put the returned value in report['security']. All failures are optional.
    """
    def report(value):
        result = 'tirith:' + value
        try:
            log('security: ' + result)
        except Exception:
            pass
        return result

    previous_home = os.environ.get('HERMES_HOME')
    try:
        home = Path(home)
        marker = home / '.tirith-install-failed'
        if home.is_symlink() or marker.is_symlink():
            return report('skipped:linked-path')
        if not marker.is_file():
            return report('skipped:no-marker')
        # Unknown, integrity and provenance failures are never bypassed.
        with marker.open(encoding='utf-8') as source:
            reason = source.read(128).strip()
        retryable = {'download_failed', 'unsupported_platform', 'cosign_missing',
                     'no_space', 'cross_device_copy_failed'}
        if reason not in retryable:
            return report('skipped:non-retryable')
        os.environ['HERMES_HOME'] = str(home)
        tirith = _load_tirith(home)
        if reason == 'unsupported_platform' and not tirith._detect_target():
            return report('skipped:unsupported_platform')
        if reason == 'cosign_missing' and not shutil.which('cosign'):
            return report('skipped:cosign_missing')
        installed, failure = tirith._install_tirith(log_failures=False)
        if installed and not (Path(installed).is_file() and os.access(installed, os.X_OK)):
            installed, failure = None, 'install-failed'
        # Hermes owns clearing/persisting its marker and resetting cached state.
        tirith._record_install_result(installed, failure)
        if installed:
            return report('installed')
        known = retryable | {'checksum_failed', 'cosign_verification_failed',
                             'binary_not_regular_file', 'binary_extract_failed', 'binary_not_in_archive'}
        return report('skipped:' + (failure if failure in known else 'install-failed'))
    except Exception:
        # Exception text may include URLs/config; never include it in the log.
        return report('skipped:helper-failed')
    finally:
        if previous_home is None:
            os.environ.pop('HERMES_HOME', None)
        else:
            os.environ['HERMES_HOME'] = previous_home
