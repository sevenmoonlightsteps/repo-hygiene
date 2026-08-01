#!/usr/bin/env python3
"""repo-hygiene - deterministic, report-only pre-publication leakage linter.

Scans a repo for personal-identity and infrastructure leakage across TWO
layers: the working tree (tracked files) and the full git history (added
blob content across every commit, plus author/committer identity and
commit-message text). Never rewrites, never fixes, no network access -
purely local `git`.

Usage:
    python3 cli.py <repo-path> [--json] [--config <path>]

Exit codes:
    0   CLEAN            - no findings in tree or history
    1   LEAKY-tree       - working tree has findings (fix before publishing)
    1   LEAKY-history     - tree is clean but history still has findings
                            (needs a history rewrite before publishing)
    2   usage/scan error - not a git repo, git missing, config unreadable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_THIS_DIR))

import engine  # noqa: E402


def _print_findings(title: str, findings) -> None:
    if not findings:
        return
    print(f"\n{title} ({len(findings)}):")
    for f in findings:
        loc = f"{f.file}:{f.line}" if f.line is not None else (f.file or "?")
        commit = f" [{f.commit[:12]}]" if f.commit else ""
        print(f"  [{f.severity:>6}] {f.cls:<20} {loc}{commit}")
        print(f"           {f.snippet}")


def _print_human(repo_path: Path, verdict: str, tree_findings, history_findings) -> None:
    print(f"repo-hygiene scan: {repo_path}")
    print(f"verdict: {verdict}")
    print(
        f"findings: tree={len(tree_findings)} history={len(history_findings)}"
    )

    if tree_findings:
        tree_summary = engine.summarize(tree_findings)
        print("tree findings by class: " + ", ".join(f"{k}={v}" for k, v in sorted(tree_summary.items())))
    if history_findings:
        hist_summary = engine.summarize(history_findings)
        print("history findings by class: " + ", ".join(f"{k}={v}" for k, v in sorted(hist_summary.items())))

    _print_findings("TREE FINDINGS", tree_findings)
    _print_findings("HISTORY FINDINGS", history_findings)

    if verdict == "LEAKY-history":
        print(
            "\nNOTE: working tree is clean but git history still carries this content. "
            "A history rewrite is required before publishing (fresh-init the repo from a "
            "clean tree, or use git-filter-repo to scrub the offending commits) - this "
            "linter is report-only and will not do that for you."
        )


def _print_json(repo_path: Path, verdict: str, tree_findings, history_findings) -> None:
    print(
        json.dumps(
            {
                "repo": str(repo_path),
                "verdict": verdict,
                "tree_findings": [f.to_dict() for f in tree_findings],
                "history_findings": [f.to_dict() for f in history_findings],
            },
            indent=2,
        )
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="repo-hygiene",
        description=(
            "Report-only pre-publication linter: flags personal-identity and "
            "infrastructure leakage in a repo's working tree AND full git "
            "history before it goes public."
        ),
    )
    parser.add_argument("repo", help="Path to the git repository to scan")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of a human summary")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a per-repo pattern-extension config (same 'classes' shape as patterns.json); merged into the defaults",
    )
    args = parser.parse_args(argv)

    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir() or not engine.is_git_repo(repo_path):
        print(f"ERROR: {repo_path} is not a git repository", file=sys.stderr)
        return 2

    try:
        classes = engine.load_patterns(args.config)
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not load pattern config: {exc}", file=sys.stderr)
        return 2

    compiled = engine.compile_patterns(classes)

    try:
        tree_findings = engine.scan_tree(repo_path, compiled)
        history_findings = engine.scan_history(repo_path, compiled)
    except engine.RepoHygieneError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    verdict, exit_code = engine.compute_verdict(tree_findings, history_findings)

    if args.json:
        _print_json(repo_path, verdict, tree_findings, history_findings)
    else:
        _print_human(repo_path, verdict, tree_findings, history_findings)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
