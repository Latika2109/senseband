"""Signed over-the-air updates with automatic rollback.

Trust chain: a release manifest is signed with an Ed25519 key whose PUBLIC half is in the band's config. A download
is only unpacked if (1) the manifest signature verifies, (2) the tarball's SHA-256 matches the signed value, (3) the
archive passes the path-safety checks. The new version becomes `current` with one atomic symlink swap; if the service
does not report healthy within the timeout, the previous release is restored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..config import OtaCfg


class OtaError(Exception):
    pass


def signed_bytes(version: str, sha256: str) -> bytes:
    return f"senseband-ota\n{version}\n{sha256}".encode()


def parse_version(v: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(\.\d+){0,3}", v):
        raise OtaError(f"bad version '{v}'")
    return tuple(int(x) for x in v.split("."))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def verify_manifest(manifest: dict[str, Any], public_key_b64: str) -> None:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        version, sha, sig = manifest["version"], manifest["sha256"], base64.b64decode(manifest["signature"], validate=True)
    except (KeyError, ValueError, TypeError) as e:
        raise OtaError(f"malformed manifest or key: {e}") from e
    parse_version(version)
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise OtaError("bad sha256 in manifest")
    try:
        key.verify(sig, signed_bytes(version, sha))
    except InvalidSignature as e:
        raise OtaError("manifest signature does not verify") from e


def download(url: str, dest: Path, sha256: str, max_bytes: int, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
    """Stream to disk with a size cap, then check the hash in constant time."""
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1", "file://")):
        raise OtaError("updates must be fetched over https")
    h, n = hashlib.sha256(), 0
    with opener(url, timeout=60) as r, open(dest, "wb") as f:   # noqa: S310 - scheme checked above
        while chunk := r.read(1 << 20):
            n += len(chunk)
            if n > max_bytes:
                dest.unlink(missing_ok=True)
                raise OtaError("download larger than the allowed size")
            h.update(chunk)
            f.write(chunk)
    if not hmac.compare_digest(h.hexdigest(), sha256):
        dest.unlink(missing_ok=True)
        raise OtaError("downloaded file does not match the signed hash")


def safe_extract_tar(tar_path: Path, dest: Path, max_bytes: int = 600_000_000) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(tar_path, "r:*") as t:
        for m in t.getmembers():
            target = (dest / m.name).resolve()
            if dest != target and dest not in target.parents:
                raise OtaError(f"unsafe path in archive: {m.name!r}")
            if m.issym() or m.islnk() or m.isdev() or m.isfifo():
                raise OtaError(f"links and device files are not allowed: {m.name!r}")
            total += m.size
            if total > max_bytes:
                raise OtaError("archive expands too large")
        for m in t.getmembers():
            m.mode &= 0o755                                    # no setuid/setgid/world-writable
            t.extract(m, dest, filter="data")


class Releases:
    """<root>/releases/<version>, <root>/current -> releases/<v>, <root>/previous -> releases/<v-1>."""

    def __init__(self, root: Path, keep: int = 3):
        self.root, self.keep = root, keep
        (root / "releases").mkdir(parents=True, exist_ok=True)

    def _link(self, name: str) -> Path:
        return self.root / name

    def current_version(self) -> str | None:
        p = self._link("current")
        return p.resolve().name if p.is_symlink() else None

    def _point(self, name: str, target: Path) -> None:
        tmp = self.root / f".{name}.tmp"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(target)
        os.replace(tmp, self._link(name))                      # atomic

    def install(self, tarball: Path, version: str) -> Path:
        parse_version(version)
        final = self.root / "releases" / version
        if final.exists():
            raise OtaError(f"version {version} is already installed")
        stage = self.root / "releases" / f".{version}.partial"
        shutil.rmtree(stage, ignore_errors=True)
        try:
            safe_extract_tar(tarball, stage)
            if not (stage / "VERSION").exists() or (stage / "VERSION").read_text().strip() != version:
                raise OtaError("release does not contain a matching VERSION file")
            os.replace(stage, final)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        cur = self.current_version()
        if cur:
            self._point("previous", self.root / "releases" / cur)
        self._point("current", final)
        self.prune()
        return final

    def rollback(self) -> str:
        prev = self._link("previous")
        if not prev.is_symlink() or not prev.resolve().exists():
            raise OtaError("no previous release to roll back to")
        target = prev.resolve()
        bad = self.current_version()
        self._point("current", target)
        prev.unlink(missing_ok=True)
        if bad:
            shutil.rmtree(self.root / "releases" / bad, ignore_errors=True)
        return target.name

    def prune(self) -> None:
        keep = {self.current_version(), (self._link("previous").resolve().name if self._link("previous").is_symlink() else None)}
        vers = sorted((p for p in (self.root / "releases").iterdir() if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: parse_version(p.name), reverse=True)
        for p in vers[self.keep:]:
            if p.name not in keep:
                shutil.rmtree(p, ignore_errors=True)


def apply_update(cfg: OtaCfg, current_version: str, fetch_manifest: Callable[[str], dict[str, Any]],
                 opener: Callable[..., Any] = urllib.request.urlopen, restart: Callable[[list[str]], None] | None = None,
                 healthy: Callable[[str], bool] | None = None, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Check -> verify -> download -> install -> restart -> health gate (rollback on failure)."""
    if not cfg.enabled or not cfg.manifest_url or not cfg.public_key:
        raise OtaError("OTA is not configured (ota.enabled, manifest_url, public_key)")
    manifest = fetch_manifest(cfg.manifest_url)
    verify_manifest(manifest, cfg.public_key)
    version = manifest["version"]
    if not is_newer(version, current_version):
        return {"updated": False, "reason": "up to date", "version": current_version}
    rel = Releases(Path(cfg.root), cfg.keep)
    tmp = Path(cfg.root) / f".download-{version}.tar"
    try:
        download(manifest["url"], tmp, manifest["sha256"], min(cfg.max_bytes, int(manifest.get("size", cfg.max_bytes))), opener)
        rel.install(tmp, version)
    finally:
        tmp.unlink(missing_ok=True)
    run = restart or (lambda cmd: subprocess.run(cmd, check=True, timeout=60))     # noqa: S603 - fixed argv from config, no shell
    run(cfg.restart_cmd)
    t0 = time.monotonic()
    ok = False
    while healthy is not None and time.monotonic() - t0 < cfg.health_timeout_s:
        if healthy(version):
            ok = True
            break
        sleep(1.0)
    if healthy is not None and not ok:
        back = rel.rollback()
        run(cfg.restart_cmd)
        return {"updated": False, "rolled_back_to": back, "reason": "new version did not become healthy", "version": back}
    return {"updated": True, "version": version}


def fetch_json(url: str, opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    with opener(url, timeout=30) as r:                         # noqa: S310
        return json.loads(r.read(200_000).decode())


def main() -> int:  # pragma: no cover - runs on the band
    import argparse
    import sys

    from ..config import load_settings
    ap = argparse.ArgumentParser(description="SenseBand OTA update")
    ap.add_argument("--apply", action="store_true", help="download, verify, install and restart")
    a = ap.parse_args()
    cfg = load_settings()
    root = Path(cfg.ota.root)
    cur = (root / "current" / "VERSION").read_text().strip() if (root / "current" / "VERSION").exists() else "0.0.0"
    health = root / "healthy"
    try:
        res = apply_update(cfg.ota, cur, fetch_json,
                           healthy=lambda v: health.exists() and health.read_text().strip() == v) if a.apply else \
            {"current": cur, "manifest": fetch_json(cfg.ota.manifest_url)["version"]}
    except OtaError as e:
        print("update refused:", e, file=sys.stderr)
        return 2
    print(json.dumps(res))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
