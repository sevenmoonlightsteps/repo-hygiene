# repo-hygiene

Deterministic, report-only pre-publication leakage linter. Flags
personal-identity and infrastructure leakage in a repo before it goes public,
across **two layers**:

1. **tree** - the current working tree (tracked files only, binaries skipped)
2. **history** - the full git history: content ever added to any tracked file
   across every commit reachable from any ref, plus commit author/committer
   identity (name + email) and commit-message text

It never rewrites, never fixes, and makes no network calls - purely local
`git` subprocess calls. Use it as a publish-gate to scrub a repo before it
goes public.

## Usage

```bash
python3 cli.py <repo-path> [--json] [--config <path>]
```

- `--json` - emit structured findings instead of the human summary
- `--config <path>` - a per-repo pattern-extension file with the same
  `"classes"` shape as `patterns.json`. For an existing class, `patterns` are
  appended to the defaults (severity overrides if given); an unrecognized
  class name is added outright. Use this to add your own internal project
  codenames, extra secret shapes, or organization-specific identity markers
  (e.g. `ACME_INTERNAL`, `your-org`) without editing the shared default
  config.

Standalone invokable CLI (`cli.py` has a shebang and is executable) with no
external dependencies - stdlib + `git` only.

## What it detects

Pattern set lives in `patterns.json` (versioned, extensible - see
`--config` above). Default classes:

| Class | Severity | Examples |
|---|---|---|
| `home_paths` | high | `/home/<user>`, `/Users/<user>`, `C:\Users\<user>` |
| `secrets` | high | AWS access keys, PEM private-key headers, `api_key=`/`secret_key=`/`password=` assignments, GitHub/GitLab tokens |
| `generic_email` | medium | any local-part `@` domain.tld shaped string |
| `internal_hostnames` | low | generic internal-only hostnames (`*.internal`, `*.corp`, `*.local`) - seed pattern only, tune or extend via `--config` per repo |

Patterns are case-insensitive Python `re`. The `internal_hostnames` class is
deliberately broad and low-severity - expect false positives on legitimate
`.local` mentions; tighten or disable it with `--config` if it's too noisy
for your repo.

## Verdict and exit codes

| Verdict | Meaning | Exit |
|---|---|---|
| `CLEAN` | no findings, tree or history | 0 |
| `LEAKY-tree` | working tree has findings (fix before publishing) | 1 |
| `LEAKY-history` | tree is clean but history still has findings - requires a history rewrite (fresh re-init from a clean tree, or `git-filter-repo`) before publishing | 1 |

Tree findings outrank history-only findings: a repo with both is reported as
`LEAKY-tree`, since that is the more urgent state (it is at HEAD, right now).

## Scope

- Report-only. It never rewrites history, never edits files, never suggests a
  commit. Remediation is a human call.
- The `secrets` class covers a handful of common, easily-recognized shapes
  (cloud access keys, PEM headers, generic key/password assignments). It is
  not a substitute for a dedicated secret scanner - for thorough credential
  scanning, pair this with a tool built for that purpose.
- No network access; purely local git.

## Tests

```bash
python3 -m pytest test_repo_hygiene.py -v
```

Fixture repos are built programmatically in `tmp_path` at test time (see
`fixtures/builder.py`) rather than checked in as literal nested git repos,
since a nested `.git` directory does not get tracked by the parent repo.

## Contact

Built by an independent engineer - for consulting or contract work, open an
issue on this repo.
