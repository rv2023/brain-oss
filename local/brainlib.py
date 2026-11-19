"""Shared helpers for the local Brain slice: layers, git reads, redaction, privacy, and the in-memory graph.

Every source is read READ-ONLY. Tracked files are read at one pinned commit (`git show <sha>:<path>`), so a record's
location (file + line + commit) is reproducible. No database or psycopg import here (tests run without a DB).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

BRAIN = Path(__file__).resolve().parent.parent
# The source project: a git repo holding the decisions log, records/, plans/stage-map.yaml and logs/.
# Default: the example project shipped in example/. Point BRAIN_SOURCE at your own project's folder.
SOURCE = Path(os.environ.get("BRAIN_SOURCE", str(BRAIN / "example"))).expanduser().resolve()
SOURCE_STORE = os.environ.get("BRAIN_SOURCE_STORE", "example")       # the store name written into every location
DEFAULT_PROJECT = os.environ.get("BRAIN_PROJECT", "example")         # project for items that do not name one
GITHUB_ORG = os.environ.get("BRAIN_GITHUB_ORG", "example-org")       # for pull request links in review findings
# Optional compact pack format (a fold.py module with pack/unfold); without it the bridge prints plain JSON.
FOLD_DIR = Path(os.environ["BRAIN_FOLD"]).expanduser() if os.environ.get("BRAIN_FOLD") else None
OUT = Path(__file__).resolve().parent / "out"

sys.path.insert(0, str(BRAIN))
import check  # noqa: E402  (layers.yaml rules live in check.py's Layers class)

PRIVACY_ORDER = ["open", "owner", "consent", "claude-only"]
TWELVE = re.compile(r"(?<!\d)\d{12}(?!\d)")
SECRETS = [  # value shapes only; names of secrets are fine
    re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bwhsec_[A-Za-z0-9]{10,}"),
]


def load_layers() -> "check.Layers":
    return check.Layers(yaml.safe_load((BRAIN / "layers.yaml").read_text()))


# ------------------------------------------------------------------ git (read-only)
def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def show(repo: Path, sha: str, path: str) -> str:
    # "./" makes the path relative to `repo`, so a source folder inside a bigger git repo works too
    return subprocess.run(["git", "-C", str(repo), "show", f"{sha}:./{path}"], check=True, capture_output=True).stdout.decode("utf-8", "replace")


def ls_files(repo: Path, sha: str, *paths: str) -> list[str]:
    out = git(repo, "ls-tree", "-r", "--name-only", sha, "--", *paths)
    return [p for p in out.splitlines() if p]


def file_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# ------------------------------------------------------------------ safety
def redact(text: str) -> tuple[str, int]:
    """Remove 12-digit numbers (account numbers) and secret-looking values. Returns (text, removals)."""
    n = 0
    text, k = TWELVE.subn("[12-digit number removed]", text)
    n += k
    for rx in SECRETS:
        text, k = rx.subn("[secret removed]", text)
        n += k
    return text, n


def lab_mention(L, text: str) -> bool:
    paths, ids = check.lab_hits(L, text)
    return bool(paths or ids)


def is_lab_id(L, ident: str) -> bool:
    return any(L.is_lab_code(n) for n in L.match(str(ident)))


def privacy_of(L, projects: list[str], layer: str) -> str:
    vals = [L.privacy_of_layer(layer)] + [(L.projects.get(p) or {}).get("privacy", "open") for p in projects]
    return max(vals, key=PRIVACY_ORDER.index)


def one_line(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s.replace("**", "").replace("`", "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ------------------------------------------------------------------ graph
class Graph:
    """Nodes, links and pending references collected by the importers, resolved in one place."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.refs: list[dict] = []          # pending: {src, raw, type, tier, evidence, confidence, field, importer, reverse}
        self.links: dict[tuple, dict] = {}
        self.unresolved: list[dict] = []
        self.counts: dict[str, int] = {}
        self.pk_clashes: list[str] = []

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def add_node(self, node: dict, keep_existing: bool = False) -> bool:
        nid = node["id"]
        if nid in self.nodes:
            if keep_existing:
                old = self.nodes[nid]
                old["aliases"] = sorted(set(old["aliases"]) | set(node.get("aliases", [])))
                return False
            self.pk_clashes.append(nid)
            return False
        node.setdefault("aliases", [])
        node.setdefault("meta", {})
        node.setdefault("body", "")
        self.nodes[nid] = node
        return True

    def ref(self, src: str, raw: str, type_: str, tier: str, evidence: str, field: str, importer: str,
            confidence: float = 1.0, reverse: bool = False, commit: str | None = None) -> None:
        """reverse=True: the link reads raw -> src (e.g. a record replaces a log entry)."""
        self.refs.append(dict(src=src, raw=str(raw).strip(), type=type_, tier=tier, evidence=evidence, field=field,
                              importer=importer, confidence=confidence, reverse=reverse, commit=commit))

    def alias_index(self) -> dict[str, list[str]]:
        idx: dict[str, set[str]] = {}
        for nid, n in self.nodes.items():
            for a in n["aliases"]:
                if a != nid:
                    idx.setdefault(a, set()).add(nid)
        return {a: sorted(v) for a, v in idx.items()}

    def resolve(self, L) -> None:
        aliases = self.alias_index()
        self.alias_clashes = {a: v for a, v in aliases.items() if len(v) > 1}
        for r in self.refs:
            raw = r["raw"]
            if raw in self.nodes:
                targets = [raw]
            else:
                targets = aliases.get(raw, [])
            if len(targets) == 1:
                src, dst = (targets[0], r["src"]) if r["reverse"] else (r["src"], targets[0])
                if src == dst:
                    continue
                key = (src, dst, r["type"])
                if key not in self.links:
                    self.links[key] = dict(src=src, dst=dst, type=r["type"], tier=r["tier"], evidence=r["evidence"],
                                           confidence=r["confidence"], importer=r["importer"], read_at_commit=r["commit"])
                continue
            if is_lab_id(L, raw):
                self.count("refs_to_lab_skipped")
                continue
            if len(targets) > 1:
                reason = f"alias names {len(targets)} items"
            elif not L.match(raw):
                reason = "not an ID (matches no code)"
            else:
                reason = "no item with this ID imported"
            self.unresolved.append(dict(src=r["src"], raw_id=raw, field=r["field"], importer=r["importer"], reason=reason))
