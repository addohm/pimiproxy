"""
config_builder.py — loads the shared Mihomo config generator.

The generator itself lives in pimiproxy_common.py, the single source of truth
shared with pimiproxy-core.py and pimiproxy-ultralite-core.py. This module
locates it and re-exports build_config so the API always produces the same
config.yaml as the host CLI.

Lookup order:
  1. /pimiproxy/pimiproxy_common.py — the installed copy in the mounted
     install dir. Preferred: guarantees the API and the host CLI on this
     device run the *same file*, so a UI write and a CLI write can never
     disagree.
  2. api/_bundled_common.py — a copy baked into the image by
     scripts/2_rebuild.sh at build time. Fallback for install dirs that
     predate pimiproxy_common.py.
"""

import importlib.util
import sys
from pathlib import Path

_MOUNTED = Path("/pimiproxy/pimiproxy_common.py")
_BUNDLED = Path(__file__).with_name("_bundled_common.py")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location("pimiproxy_common", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pimiproxy_common"] = mod
    spec.loader.exec_module(mod)
    return mod


if _MOUNTED.exists():
    _common = _load(_MOUNTED)
elif _BUNDLED.exists():
    _common = _load(_BUNDLED)
else:
    raise ImportError(
        "pimiproxy_common.py not found at /pimiproxy (install dir not mounted?) "
        "and no bundled copy in the image. Rebuild with scripts/2_rebuild.sh."
    )

build_config       = _common.build_config
yaml_quote         = _common.yaml_quote
fetch_subscription = _common.fetch_subscription
extract_yaml_blocks = _common.extract_yaml_blocks
