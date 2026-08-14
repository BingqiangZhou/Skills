"""Shared loader for importing same-named scripts from different skills.

Each skill's scripts/ directory has its own ``_common.py`` and several share
the name ``check_updates.py``. ``unittest discover`` runs everything in one
process, so plain imports would collide in ``sys.modules``. Loading each
script under a unique module name (with its directory at the FRONT of
sys.path and any previously-loaded ``_common`` purged) keeps every module
bound to its own sibling copy.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SKILLS = REPO_ROOT / "plugins" / "daily-digest" / "skills"

_cache = {}


def load_script(unique_name, relative_path):
    """Import a repo script under ``unique_name`` (cached per process)."""
    if unique_name in _cache:
        return _cache[unique_name]
    path = REPO_ROOT / relative_path
    sys.path.insert(0, str(path.parent))
    # Drop any earlier-loaded sibling module of the same file name so this
    # script's own `import _common` / `import io_utils` resolves to ITS dir.
    for sibling in ("_common", "io_utils"):
        sys.modules.pop(sibling, None)
    spec = importlib.util.spec_from_file_location(unique_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = mod
    spec.loader.exec_module(mod)
    _cache[unique_name] = mod
    return mod


def rss_common():
    return load_script("rss_common_t",
                       "plugins/daily-digest/skills/rss-monitor/scripts/_common.py")


def rss_check():
    return load_script("rss_check_t",
                       "plugins/daily-digest/skills/rss-monitor/scripts/check_updates.py")


def rss_resolver():
    return load_script("rss_resolver_t",
                       "plugins/daily-digest/skills/rss-monitor/scripts/resolve_xiaoyuzhou_urls.py")


def gm_common():
    return load_script("gm_common_t",
                       "plugins/daily-digest/skills/github-monitor/scripts/_common.py")


def gm_check():
    return load_script("gm_check_t",
                       "plugins/daily-digest/skills/github-monitor/scripts/check_updates.py")


def tum_check():
    return load_script("tum_check_t",
                       "plugins/daily-digest/skills/tool-update-monitor/scripts/check_updates.py")


def gur():
    return load_script("gur_t",
                       "plugins/daily-digest/skills/daily-digest/scripts/generate_unified_report.py")


def pb():
    return load_script("pb_t",
                       "plugins/daily-digest/skills/daily-digest/scripts/prepare_batches.py")


def mn():
    return load_script("mn_t",
                       "plugins/daily-digest/skills/daily-digest/scripts/merge_narratives.py")
