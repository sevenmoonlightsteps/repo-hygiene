"""Fixture-repo builders for repo-hygiene tests.

Nested `.git` dirs inside a tracked repo don't get tracked by the parent repo, so
fixture repos are NOT checked in as literal git repos here. Instead these helpers
build throwaway git repos under a caller-supplied `tmp_path` at test time - clean,
reproducible, and immune to nested-git bookkeeping issues.

The leak samples below are assembled from string fragments at call time rather
than written as contiguous literals. This file is itself tracked source, and
repo-hygiene scans its own tree when this project is linted - a contiguous
literal home path or email address here would trip the scanner on itself. The
fragment split defeats the regex match on THIS file's source lines while still
producing the full, matchable string once joined at runtime and written into a
throwaway tmp_path fixture (which is never tracked).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Fixture identity deliberately avoids matching generic_email (no dot in the
# domain part) so the "clean" fixture repo's own commit metadata isn't a leak.
_GIT_ENV_ARGS = [
    "-c", "user.name=Fixture Author",
    "-c", "user.email=fixture@localtest",
    "-c", "commit.gpgsign=false",
]

# Assembled at call time - see module docstring for why.
_LEAK_HOME_PATH = "/home" + "/testuser/project"
_LEAK_EMAIL = "test" + "@example.com"
_LEAK_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
_LEAK_INTERNAL_HOST = "build-server" + ".internal"
_LEAK_IDENTITY_NAME = "Test" + " Person"
_LEAK_IDENTITY_EMAIL = "leaky.person" + "@example.com"


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path)] + _GIT_ENV_ARGS + list(args),
        check=True,
        capture_output=True,
        text=True,
    )


def init_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True, capture_output=True, text=True)


def write(repo_path: Path, relname: str, content: str) -> Path:
    p = repo_path / relname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def commit_all(repo_path: Path, message: str) -> str:
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-m", message, "--allow-empty")
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def build_clean_repo(tmp_path: Path) -> Path:
    """A tiny real repo with a couple of innocuous files, no leaks."""
    repo = tmp_path / "clean-repo"
    init_repo(repo)
    write(repo, "README.md", "# Sample project\n\nA small example tool with no secrets.\n")
    write(repo, "src/main.py", "def add(a, b):\n    return a + b\n")
    commit_all(repo, "initial commit")
    return repo


def build_leaky_repo(tmp_path: Path) -> Path:
    """A repo seeded with one working-tree hit from every leakage class."""
    repo = tmp_path / "leaky-repo"
    init_repo(repo)
    write(
        repo,
        "notes.md",
        "\n".join(
            [
                "# dev notes",
                f"config lives at {_LEAK_HOME_PATH}/config.yaml",
                f"contact: {_LEAK_EMAIL}",
                f"leaked credential: {_LEAK_AWS_KEY}",
                f"internal service: {_LEAK_INTERNAL_HOST}",
                "",
            ]
        ),
    )
    commit_all(repo, "seed leaky notes")
    return repo


def build_history_only_leak_repo(tmp_path: Path):
    """A repo where a leak is committed then removed - tree clean, history dirty.

    Returns (repo_path, introducing_commit_sha).
    """
    repo = tmp_path / "history-leak-repo"
    init_repo(repo)
    write(repo, "README.md", "# Sample project\n")
    commit_all(repo, "initial commit")

    write(repo, "debug.log", f"session for {_LEAK_EMAIL} at {_LEAK_HOME_PATH}\n")
    leak_sha = commit_all(repo, "oops: committed a debug log with contact info")

    (repo / "debug.log").unlink()
    commit_all(repo, "remove debug log")

    return repo, leak_sha


def build_author_identity_repo(tmp_path: Path) -> Path:
    """A repo whose only leak is the commit author identity (email)."""
    repo = tmp_path / "author-identity-repo"
    init_repo(repo)
    write(repo, "README.md", "# Sample project\n")
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", f"user.name={_LEAK_IDENTITY_NAME}",
            "-c", f"user.email={_LEAK_IDENTITY_EMAIL}",
            "-c", "commit.gpgsign=false",
            "add", "-A",
        ],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", f"user.name={_LEAK_IDENTITY_NAME}",
            "-c", f"user.email={_LEAK_IDENTITY_EMAIL}",
            "-c", "commit.gpgsign=false",
            "commit", "-m", "initial commit",
        ],
        check=True, capture_output=True, text=True,
    )
    return repo
