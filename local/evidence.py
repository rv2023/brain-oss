"""Evidence archive writer (METHOD.md "Evidence capture").

Each metric run becomes an immutable folder `evidence/<YYYY-MM-DDTHHMMZ>-<metric>/` holding the hypothesis and bar as
written before the run, the exact command, the inputs (repo + commit, file hashes for untracked inputs), the environment,
the raw data, the summary, and `manifest.json` (sha256 of every file + the previous run's manifest hash: a hash chain).
A folder is never overwritten; a correction is a new run. Every run starts `public_safe: false` (a person decides later).
`python3 check.py evidence` verifies hashes and the chain (CI).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import check
from brainlib import BRAIN, lab_mention, load_layers

EVIDENCE = BRAIN / "evidence"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def runs(root: Path = EVIDENCE) -> list[Path]:
    """Run folders in chain order (manifest `sequence`; several runs can share one minute)."""
    if not root.is_dir():
        return []
    rs = [p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    return sorted(rs, key=lambda p: (json.loads((p / "manifest.json").read_text()).get("sequence", 0), p.name))


def last_manifest_hash(root: Path = EVIDENCE) -> str | None:
    rs = runs(root)
    return sha256_bytes((rs[-1] / "manifest.json").read_bytes()) if rs else None


def _safe(name: str, data: bytes, L) -> None:
    text = data.decode("utf-8", "replace")
    if check.has_twelve_digits(text):
        raise ValueError(f"{name}: contains a 12-digit number; redact before archiving")
    if lab_mention(L, text):
        raise ValueError(f"{name}: names a private path or ID; claude-only material stays in its own store")


def write_run(metric: str, files: dict[str, bytes | str], meta: dict, when: datetime | None = None,
              root: Path = EVIDENCE) -> Path:
    """files: relative name -> content. meta goes into manifest.json (metric, result, bar, sample size, commits)."""
    L = load_layers()
    when = when or datetime.now(timezone.utc)
    stamp = when.strftime("%Y-%m-%dT%H%MZ")
    if not re.fullmatch(r"[a-z0-9-]+", metric):
        raise ValueError("metric name: lowercase letters, digits and dashes")
    folder = root / f"{stamp}-{metric}"
    if folder.exists():
        raise FileExistsError(f"{folder} exists; evidence runs are never overwritten (wait a minute or use a new metric name)")
    blobs = {k: (v.encode() if isinstance(v, str) else v) for k, v in files.items()}
    for k, v in blobs.items():
        if k == "manifest.json" or k.startswith("/") or ".." in k:
            raise ValueError(f"bad file name {k}")
        _safe(k, v, L)
    prev = last_manifest_hash(root)
    seq = len(runs(root)) + 1
    manifest = {"metric": metric, "sequence": seq, "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "public_safe": False,
                **meta, "previous_manifest_sha256": prev,
                "files": {k: sha256_bytes(v) for k, v in sorted(blobs.items())}}
    mbytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    _safe("manifest.json", mbytes, L)
    folder.mkdir(parents=True)
    for k, v in blobs.items():
        (folder / k).parent.mkdir(parents=True, exist_ok=True)
        (folder / k).write_bytes(v)
    (folder / "manifest.json").write_bytes(mbytes)
    return folder


def write_index(root: Path = EVIDENCE) -> Path:
    rows = []
    for r in runs(root):
        m = json.loads((r / "manifest.json").read_text())
        rows.append("| {s} | {d} | {metric} | {res} | {bar} | {n} | [{f}]({f}/) | {c} | {ps} |".format(
            s=m.get("sequence", ""), d=m["created_at"], metric=m["metric"], res=m.get("result", ""), bar=m.get("bar", ""), n=m.get("sample_size", ""),
            f=r.name, c=m.get("brain_commit", "")[:10], ps="yes" if m.get("public_safe") else "no"))
    text = ("# Evidence index\n\n"
            "Generated from each run's `manifest.json` (do not edit by hand). One row per immutable run "
            "folder. `public_safe` is `no` until a person marks a run safe to publish. "
            "Verify: `python3 check.py evidence`.\n\n"
            "| # | Date (UTC) | Metric | Result | Bar | Sample size | Run folder | Brain commit | Public safe |\n"
            "|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
    p = root / "INDEX.md"
    p.write_text(text)
    return p
