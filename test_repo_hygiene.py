"""Tests for repo-hygiene: fixture repos are built at test time in tmp_path
(see fixtures/builder.py) to avoid nested-.git tracking issues."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_THIS_DIR))
sys.path.insert(0, str(_THIS_DIR / "fixtures"))

import engine  # noqa: E402
import builder  # noqa: E402

CLI = str(_THIS_DIR / "cli.py")

ALL_CLASSES = {"home_paths", "secrets", "generic_email", "internal_hostnames"}


def _compiled():
    return engine.compile_patterns(engine.load_patterns())


# ── B1: tree layer ──


def test_clean_repo_yields_zero_findings(tmp_path):
    repo = builder.build_clean_repo(tmp_path)
    compiled = _compiled()
    tree_findings = engine.scan_tree(repo, compiled)
    assert tree_findings == []


def test_clean_repo_cli_exits_0(tmp_path):
    repo = builder.build_clean_repo(tmp_path)
    result = subprocess.run([sys.executable, CLI, str(repo)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEAN" in result.stdout


def test_leaky_repo_catches_every_class_in_tree_layer(tmp_path):
    repo = builder.build_leaky_repo(tmp_path)
    compiled = _compiled()
    tree_findings = engine.scan_tree(repo, compiled)

    found_classes = {f.cls for f in tree_findings}
    assert ALL_CLASSES <= found_classes, f"missing classes: {ALL_CLASSES - found_classes}"
    assert all(f.layer == "tree" for f in tree_findings)
    assert all(f.file == "notes.md" for f in tree_findings)
    assert all(f.line is not None for f in tree_findings)


def test_leaky_repo_cli_exits_1(tmp_path):
    repo = builder.build_leaky_repo(tmp_path)
    result = subprocess.run([sys.executable, CLI, str(repo)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "LEAKY-tree" in result.stdout


def test_leaky_repo_json_output_has_findings(tmp_path):
    import json

    repo = builder.build_leaky_repo(tmp_path)
    result = subprocess.run([sys.executable, CLI, str(repo), "--json"], capture_output=True, text=True)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "LEAKY-tree"
    assert len(payload["tree_findings"]) > 0


def test_binary_file_is_skipped(tmp_path):
    repo = builder.build_clean_repo(tmp_path)
    # Fragment-split so this source line itself never contains a contiguous
    # matching path (see fixtures/builder.py module docstring for why).
    leak_bytes = b"\x00\x01" + b"/home" + b"/someone/leak" + b"\x00\x02"
    (repo / "blob.bin").write_bytes(leak_bytes)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=fixture@localtest", "-c", "user.name=Fixture",
         "-c", "commit.gpgsign=false", "add", "-A"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=fixture@localtest", "-c", "user.name=Fixture",
         "-c", "commit.gpgsign=false", "commit", "-m", "add binary"],
        check=True, capture_output=True, text=True,
    )
    compiled = _compiled()
    findings = engine.scan_tree(repo, compiled)
    assert findings == []


# ── B2: history layer + verdict ──


def test_history_only_leak_yields_leaky_history_with_correct_sha(tmp_path):
    repo, leak_sha = builder.build_history_only_leak_repo(tmp_path)
    compiled = _compiled()

    tree_findings = engine.scan_tree(repo, compiled)
    assert tree_findings == [], "tree should be clean - the leak was removed in HEAD"

    history_findings = engine.scan_history(repo, compiled)
    assert history_findings, "history should still carry the removed leak"
    shas = {f.commit for f in history_findings}
    assert leak_sha in shas
    assert all(f.layer == "history" for f in history_findings)

    verdict, exit_code = engine.compute_verdict(tree_findings, history_findings)
    assert verdict == "LEAKY-history"
    assert exit_code == 1


def test_history_only_leak_cli_prints_rewrite_note(tmp_path):
    repo, _ = builder.build_history_only_leak_repo(tmp_path)
    result = subprocess.run([sys.executable, CLI, str(repo)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "LEAKY-history" in result.stdout
    assert "history rewrite" in result.stdout.lower()


def test_author_identity_in_history_is_detected(tmp_path):
    repo = builder.build_author_identity_repo(tmp_path)
    compiled = _compiled()

    history_findings = engine.scan_history(repo, compiled)
    identity_hits = [f for f in history_findings if f.cls in ("operator_identity", "generic_email")]
    assert identity_hits, "author email/name should be flagged as history leakage"
    assert all(f.file.startswith("<commit-metadata:") for f in identity_hits)
    assert any(f.file == "<commit-metadata:author>" for f in identity_hits)
    assert all(f.commit for f in identity_hits)


def test_verdict_tree_outranks_history(tmp_path):
    # A repo with both a tree finding and (necessarily, since the tree finding is
    # committed) a history finding must report LEAKY-tree, not LEAKY-history.
    repo = builder.build_leaky_repo(tmp_path)
    compiled = _compiled()
    tree_findings = engine.scan_tree(repo, compiled)
    history_findings = engine.scan_history(repo, compiled)
    assert tree_findings and history_findings

    verdict, exit_code = engine.compute_verdict(tree_findings, history_findings)
    assert verdict == "LEAKY-tree"
    assert exit_code == 1


def test_compute_verdict_clean():
    verdict, exit_code = engine.compute_verdict([], [])
    assert verdict == "CLEAN"
    assert exit_code == 0


# ── config merging ──


def test_custom_config_extends_a_class(tmp_path):
    custom = tmp_path / "custom-patterns.json"
    custom.write_text(
        '{"classes": {"internal_hostnames": {"patterns": ["totally-custom-org-slug"]}}}',
        encoding="utf-8",
    )
    classes = engine.load_patterns(str(custom))
    assert "totally-custom-org-slug" in classes["internal_hostnames"]["patterns"]
    # defaults for that class are preserved, not replaced
    assert "\\b[A-Za-z0-9-]+\\.(?:internal|corp|local)\\b" in classes["internal_hostnames"]["patterns"]


def test_custom_config_adds_a_new_class(tmp_path):
    custom = tmp_path / "custom-patterns.json"
    custom.write_text(
        '{"classes": {"custom_thing": {"severity": "low", "patterns": ["widget-\\\\d+"]}}}',
        encoding="utf-8",
    )
    classes = engine.load_patterns(str(custom))
    assert "custom_thing" in classes
    assert classes["custom_thing"]["severity"] == "low"
