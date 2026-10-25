#!/usr/bin/env python3
"""Compares a "before" `.secrets.baseline` snapshot against the current one
and fails (exit 1) if anything beyond the `generated_at` timestamp differs --
i.e. `detect-secrets scan --baseline FILE` (run just before this script, see
`.github/workflows/ci.yml`'s `secret-scan` job) found a genuinely new,
un-reviewed finding. A naive `git diff --exit-code .secrets.baseline` would
fail on *every* run even with zero new secrets, since `--baseline` always
rewrites the file's own `generated_at` field -- this strips that field from
both sides before comparing, so the step only fails on a real new finding
(verified: a planted fake PEM private key in a new file is correctly caught
by this exact comparison).

**Why this is its own script, not an inline `python -c "..."` in the
workflow YAML (where it used to live):** the message printed on a real
finding names the remediation command, `` `detect-secrets audit
.secrets.baseline` ``, in backticks -- inside a `run: |` block's `python -c
"..."` argument, those backticks are **not** inert Python syntax to bash;
bash performs command substitution on any unescaped backtick pair inside a
double-quoted string, unconditionally, while constructing the string that
becomes `-c`'s argument, regardless of where that text falls inside the
Python source's own (unrelated) control flow. In practice this meant
`detect-secrets audit .secrets.baseline` -- an interactive, TTY-expecting
command -- was being executed as a subshell substitution on every single
run of that step, and its own output (not valid Python) was spliced into
the middle of the `print(...)` call, corrupting the script into a syntax
error. A real, confirmed, deterministic bug (reproduced locally via
`bash -x`, which shows the substitution firing), not a hypothetical --
found and fixed by moving this logic into a real `.py` file, where a
backtick is never anything but a literal character.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: One finding, reduced to what identifies it independently of where in the
#: file it sits. `hashed_secret` is a content hash, so two findings sharing
#: one are the *same* secret even at different line numbers.
Finding = tuple[str, int, str]


def _normalize(results: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Canonicalizes a baseline's `results` keys to forward slashes.

    2026-10-25, UAT-readiness Phase 2 (`OPS-005`). `detect-secrets` writes
    **OS-native** path separators, so a scan run on Windows produces
    `tests\\test_x.py` where the committed baseline (generated on Linux, as
    CI's ubuntu-latest runner does) has `tests/test_x.py`. Comparing those
    raw made every entry look new, which cost this audit a wrong conclusion
    before the cause was found: the separator difference was initially read
    as the reason the gate fails, when the real reason is staleness.

    Normalizing cannot hide a real difference -- no filesystem holds both
    `a/b.py` and `a\\b.py` -- so this only ever removes a false one, and it
    makes a developer's local run agree with CI's verdict.
    """
    return {key.replace("\\", "/"): value for key, value in results.items()}


def _signature(findings: list[dict[str, Any]]) -> set[Finding]:
    return {(f["type"], f["line_number"], f["hashed_secret"]) for f in findings}


def _describe_delta(
    before: dict[str, list[dict[str, Any]]],
    after: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Says exactly what changed, grouped by what it means.

    The point is that the three groups need different responses, and the
    bare "new finding(s) detected" this script used to print made them
    indistinguishable:

    * **a new secret hash** is the only one that might be a real leak;
    * **a file newly carrying a finding** is usually a new test fixture or
      a new Alembic revision, i.e. the already-triaged false-positive
      classes;
    * **the same hash at a new line** is pure code movement and needs
      nothing but a baseline refresh.

    A gate that fails without distinguishing these is a gate nobody can
    act on, which is how this one came to be red without being noticed.
    """
    lines: list[str] = []

    new_hashes: list[str] = []
    moved: list[str] = []
    for path in sorted(set(before) & set(after)):
        sig_before, sig_after = _signature(before[path]), _signature(after[path])
        if sig_before == sig_after:
            continue
        hashes_before = {h for _, _, h in sig_before}
        hashes_after = {h for _, _, h in sig_after}
        if hashes_before == hashes_after:
            moved.append(path)
        else:
            added = sorted(h[:12] for h in hashes_after - hashes_before)
            new_hashes.append(f"{path}: {added}")

    added_files = sorted(set(after) - set(before))
    removed_files = sorted(set(before) - set(after))

    if new_hashes:
        lines.append(f"  secret values not in the baseline ({len(new_hashes)} file(s)) -- REVIEW:")
        lines.extend(f"    {entry}" for entry in new_hashes)
    if added_files:
        lines.append(f"  files newly carrying a finding ({len(added_files)}):")
        lines.extend(
            f"    {path}: {[(f['type'], f['line_number']) for f in after[path]]}"
            for path in added_files
        )
    if removed_files:
        lines.append(f"  files no longer carrying a finding ({len(removed_files)}):")
        lines.extend(f"    {path}" for path in removed_files)
    if moved:
        lines.append(f"  same secret at a different line, i.e. code movement ({len(moved)}):")
        lines.extend(f"    {path}" for path in moved)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "before_snapshot",
        type=Path,
        help="Path to the baseline file as it was before `detect-secrets scan --baseline` ran.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(".secrets.baseline"),
        help="Path to the current baseline file, after the scan ran (default: .secrets.baseline).",
    )
    args = parser.parse_args(argv)

    before = json.loads(args.before_snapshot.read_text(encoding="utf-8"))
    after = json.loads(args.baseline.read_text(encoding="utf-8"))
    before.pop("generated_at", None)
    after.pop("generated_at", None)
    before["results"] = _normalize(before.get("results", {}))
    after["results"] = _normalize(after.get("results", {}))

    if before != after:
        print(
            "New, un-baselined finding(s) detected -- review with "
            "`detect-secrets audit .secrets.baseline` and commit the result."
        )
        for line in _describe_delta(before["results"], after["results"]):
            print(line)
        if before.keys() != after.keys() or any(
            key != "results" and before[key] != after[key] for key in before
        ):
            # Everything outside `results` -- the plugin list, the filters,
            # the detect-secrets version. A change here alters what the scan
            # even looks for, so it is reported rather than folded in with
            # the findings.
            print("  baseline metadata outside `results` also differs (plugins/filters/version)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
