"""Guards for the narrowed gitleaks allowlist (#002-FIX-2).

Validates the allowlist logic in-process (no gitleaks binary needed, so this
runs in CI): integrity/hash strings are ignored, but a real token — even inside
a lockfile — is NOT, and lockfiles are not wholesale-allowlisted by path.
"""

import re
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / ".gitleaks.toml"


def _config() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_config_parses_and_has_sk_rule() -> None:
    data = _config()
    ids = [r.get("id") for r in data.get("rules", [])]
    assert "huading-sk-api-key" in ids


def test_no_wholesale_lockfile_path_allowlist() -> None:
    # A path-based allowlist for lockfiles would hide a real token committed to
    # one. The narrowed config must not allowlist lockfiles by path.
    paths = _config().get("allowlist", {}).get("paths", [])
    assert not any("lock" in p.lower() for p in paths)


def test_hash_shapes_ignored_but_real_token_caught() -> None:
    regexes = _config().get("allowlist", {}).get("regexes", [])
    compiled = [re.compile(r) for r in regexes]

    def ignored(value: str) -> bool:
        return any(c.search(value) for c in compiled)

    # Integrity / hash strings are known false positives -> ignored.
    assert ignored("sha512-AbCdEf0123456789AbCdEf0123456789AbCdEf0123456789AbCdEf01234567==")
    assert ignored("sha256:" + "a" * 64)
    # A provider-shaped token is NOT hash-shaped -> still reported (even in a
    # lockfile). Built from parts so this file never commits a secret-shaped
    # literal that the scanner (or this very config) would flag.
    sample_token = "sk-" + "x" * 20
    assert not ignored(sample_token)
