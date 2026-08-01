"""repo-hygiene engine - deterministic, report-only scan for personal-identity and
infrastructure leakage in a pre-publication repo.

Two layers:
    scan_tree(repo_path, compiled)     - the current working tree (tracked files only)
    scan_history(repo_path, compiled)  - the full git history (added blob content across
                                          every commit reachable from any ref, plus author/
                                          committer identity and commit-message text)

Never rewrites, never fixes, no network access - purely local `git` subprocess calls.

Pattern config lives in patterns.json (versioned, extensible). load_patterns() merges the
defaults with an optional per-repo --config file of the same shape (custom classes are
added; patterns on an existing class are appended; a severity override replaces the
default for that class).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

_THIS_DIR = Path(__file__).parent.resolve()
DEFAULT_PATTERNS_PATH = _THIS_DIR / "patterns.json"

_MAX_SNIPPET = 200

_COMMIT_RE = re.compile(r"^commit ([0-9a-f]{7,40})")
_AUTHOR_RE = re.compile(r"^Author:\s+(.*)$")
_COMMITTER_RE = re.compile(r"^Commit:\s+(.*)$")
_PLUS_FILE_RE = re.compile(r"^\+\+\+ (?:b/(.*)|(/dev/null))$")


class RepoHygieneError(RuntimeError):
    """Raised for unrecoverable scan errors (not a git repo, git not found, ...)."""


@dataclass
class Finding:
    layer: str  # "tree" | "history"
    cls: str
    severity: str
    pattern: str
    snippet: str
    file: Optional[str] = None
    line: Optional[int] = None
    commit: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── config loading ──


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_patterns(config_path: Optional[str] = None) -> Dict[str, dict]:
    """Return the merged {class_name: {"severity": str, "patterns": [str, ...]}} map."""
    defaults = _load_json(DEFAULT_PATTERNS_PATH)
    classes: Dict[str, dict] = {
        name: {"severity": meta["severity"], "patterns": list(meta["patterns"])}
        for name, meta in defaults["classes"].items()
    }

    if config_path:
        custom = _load_json(Path(config_path))
        for name, meta in custom.get("classes", {}).items():
            if name in classes:
                if "severity" in meta:
                    classes[name]["severity"] = meta["severity"]
                for pat in meta.get("patterns", []):
                    if pat not in classes[name]["patterns"]:
                        classes[name]["patterns"].append(pat)
            else:
                classes[name] = {
                    "severity": meta.get("severity", "low"),
                    "patterns": list(meta.get("patterns", [])),
                }

    return classes


def compile_patterns(classes: Dict[str, dict]) -> Dict[str, dict]:
    """Compile each class's pattern strings to case-insensitive regexes."""
    compiled = {}
    for name, meta in classes.items():
        compiled[name] = {
            "severity": meta["severity"],
            "regexes": [re.compile(pat, re.IGNORECASE) for pat in meta["patterns"]],
        }
    return compiled


# ── shared matching ──


def _trim(text: str) -> str:
    text = text.strip()
    if len(text) > _MAX_SNIPPET:
        return text[:_MAX_SNIPPET] + "...(trimmed)"
    return text


def _match_line(
    text: Optional[str],
    compiled: Dict[str, dict],
    layer: str,
    file: Optional[str] = None,
    line_no: Optional[int] = None,
    commit: Optional[str] = None,
) -> List[Finding]:
    findings: List[Finding] = []
    if not text:
        return findings
    for cls_name, meta in compiled.items():
        for rx in meta["regexes"]:
            if rx.search(text):
                findings.append(
                    Finding(
                        layer=layer,
                        cls=cls_name,
                        severity=meta["severity"],
                        pattern=rx.pattern,
                        snippet=_trim(text),
                        file=file,
                        line=line_no,
                        commit=commit,
                    )
                )
                break  # one finding per class per line is enough
    return findings


def _run_git(repo_path, args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path)] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def is_git_repo(repo_path) -> bool:
    proc = _run_git(repo_path, ["rev-parse", "--is-inside-work-tree"])
    return proc.returncode == 0 and proc.stdout.strip() == "true"


# ── tree layer ──


def _list_tracked_files(repo_path) -> List[str]:
    proc = _run_git(repo_path, ["ls-files", "-z"])
    if proc.returncode != 0:
        raise RepoHygieneError(f"git ls-files failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.split("\0") if p]


def scan_tree(repo_path, compiled: Dict[str, dict]) -> List[Finding]:
    findings: List[Finding] = []
    for rel in _list_tracked_files(repo_path):
        full = Path(repo_path) / rel
        try:
            data = full.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary file, skip
        text = data.decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), start=1):
            findings.extend(_match_line(line, compiled, layer="tree", file=rel, line_no=i))
    return findings


# ── history layer ──


def scan_history(repo_path, compiled: Dict[str, dict]) -> List[Finding]:
    # Trailing "-- ." pathspec scopes the walk to repo_path itself: `git log`
    # (unlike `git ls-files`) does NOT implicitly restrict to the -C cwd, so
    # without it a repo_path that is a subdirectory of a larger repo (e.g. a
    # project directory inside a monorepo) would walk the WHOLE enclosing
    # repo's history instead of just this subtree.
    proc = _run_git(
        repo_path,
        ["log", "--all", "-p", "--no-color", "--pretty=fuller", "-U0", "--", "."],
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        # No commits yet (or --all had nothing to walk) - empty history, not an error.
        return []

    findings: List[Finding] = []
    current_sha: Optional[str] = None
    current_file: Optional[str] = None
    in_diff = False

    for line in proc.stdout.splitlines():
        m = _COMMIT_RE.match(line)
        if m:
            current_sha = m.group(1)
            current_file = None
            in_diff = False
            continue

        m = _AUTHOR_RE.match(line)
        if m:
            findings.extend(
                _match_line(m.group(1), compiled, layer="history",
                            file="<commit-metadata:author>", commit=current_sha)
            )
            continue

        m = _COMMITTER_RE.match(line)
        if m:
            findings.extend(
                _match_line(m.group(1), compiled, layer="history",
                            file="<commit-metadata:committer>", commit=current_sha)
            )
            continue

        if line.startswith("diff --git"):
            in_diff = True
            continue

        m = _PLUS_FILE_RE.match(line)
        if m:
            current_file = m.group(1)  # None when it matched /dev/null
            continue

        if line.startswith("+++") or line.startswith("---"):
            continue

        if in_diff and line.startswith("+"):
            content = line[1:]
            findings.extend(
                _match_line(content, compiled, layer="history",
                            file=current_file, commit=current_sha)
            )
            continue

        if in_diff:
            continue  # other diff noise (index lines, @@ hunks, context) - not content

        # Between the commit header and the first "diff --git": commit message body.
        if current_sha and line.startswith("    "):
            findings.extend(
                _match_line(line.strip(), compiled, layer="history",
                            file="<commit-message>", commit=current_sha)
            )

    return findings


# ── verdict ──


def compute_verdict(tree_findings: List[Finding], history_findings: List[Finding]):
    """Return (verdict: str, exit_code: int). Tree findings outrank history-only ones."""
    if not tree_findings and not history_findings:
        return "CLEAN", 0
    if tree_findings:
        return "LEAKY-tree", 1
    return "LEAKY-history", 1


def summarize(findings: List[Finding]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.cls] = counts.get(f.cls, 0) + 1
    return counts
