"""Reports the size of the instruction files Claude Code loads automatically.

Claude Code loads `CLAUDE.md` into every session's context -- the root one
plus any nested one in a directory it is working in -- and warns past
150,000 characters. The root file reached ~394,000 characters by accumulating
one summary per feature until it was most of the context budget; the fix was
to move that reference material into `docs/architecture/` and keep only
cross-cutting rules in the root (see
`docs/architecture/claude-context-migration-report.md`).

This script exists so that regrowth is noticed early rather than by the
warning. Run it after editing instruction files:

    python scripts/check_instruction_size.py

Exit codes: 0 within budget, 1 over the soft target, 2 over Claude Code's
hard limit -- so it can gate a pre-commit hook or a CI step if that is ever
wanted. It reads files only; it changes nothing.

Prints to stdout by design -- this is a standalone CLI script meant to be
read as terminal output, the same convention `scripts/test_db_connection.py`
follows (see CLAUDE.md's coding standards).
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Claude Code's own warning threshold.
HARD_LIMIT = 150_000

#: The size the root file was deliberately rewritten to sit under. Well below
#: the hard limit on purpose: the root file competes with the actual task for
#: context, so "fits" is not the same as "right".
SOFT_TARGET = 60_000

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Read on demand, never loaded automatically -- reported for context only,
#: because their size is exactly what the root file no longer carries.
REFERENCE_DOCS = (
    "docs/architecture/context-map.md",
    "docs/architecture/overview.md",
    "docs/architecture/repository-map.md",
    "docs/architecture/features-analytics.md",
    "docs/architecture/features-governance-and-admin.md",
    "docs/architecture/features-frontend.md",
)


def char_count(path: Path) -> int:
    """Characters as Claude Code counts them -- decoded text, not bytes.

    Line endings are normalized to `\\n` first: a CRLF checkout would
    otherwise report one extra character per line (~6,000 on a file this
    size) and make the number disagree with the warning.
    """
    return len(path.read_text(encoding="utf-8").replace("\r\n", "\n"))


def find_auto_loaded() -> list[Path]:
    """Every `CLAUDE.md` in the repository, nearest-first.

    Skips the directories that hold third-party or generated content, which
    can contain a vendored `CLAUDE.md` that this project never loads.
    """
    skip = {".venv", "node_modules", ".git", "__pycache__", "dist", ".vs"}
    found = []
    for path in REPO_ROOT.rglob("CLAUDE.md"):
        if any(part in skip for part in path.parts):
            continue
        found.append(path)
    return sorted(found, key=lambda p: len(p.parts))


def main() -> int:
    auto_loaded = find_auto_loaded()
    if not auto_loaded:
        print("No CLAUDE.md found -- nothing is loaded automatically.")
        return 0

    print("Automatically loaded instruction files")
    print("-" * 72)
    total = 0
    worst = 0
    for path in auto_loaded:
        count = char_count(path)
        total += count
        worst = max(worst, count)
        rel = path.relative_to(REPO_ROOT).as_posix()
        if count > HARD_LIMIT:
            state = "OVER CLAUDE CODE'S LIMIT"
        elif count > SOFT_TARGET:
            state = "over the soft target"
        else:
            state = "ok"
        print(f"  {rel:<50} {count:>8,} chars  {state}")

    if len(auto_loaded) > 1:
        print(f"  {'TOTAL (if every one is in scope)':<50} {total:>8,} chars")
    print()
    print(f"  soft target per file: {SOFT_TARGET:>8,}")
    print(f"  Claude Code's limit:  {HARD_LIMIT:>8,}")

    present = [REPO_ROOT / rel for rel in REFERENCE_DOCS]
    present = [p for p in present if p.exists()]
    if present:
        print()
        print("Reference docs (read on demand, never loaded automatically)")
        print("-" * 72)
        reference_total = 0
        for path in present:
            count = char_count(path)
            reference_total += count
            print(f"  {path.relative_to(REPO_ROOT).as_posix():<50} {count:>8,} chars")
        print(f"  {'TOTAL kept out of automatic context':<50} {reference_total:>8,} chars")

    print()
    if worst > HARD_LIMIT:
        print("FAIL: an automatically loaded file is over Claude Code's 150k limit.")
        print("      Move per-feature detail into docs/architecture/ and leave a")
        print("      pointer -- see docs/architecture/context-map.md section 6.")
        return 2
    if worst > SOFT_TARGET:
        print("WARN: an automatically loaded file is over the soft target.")
        print("      Still within Claude Code's limit, but it is spending context")
        print("      that the actual task needs.")
        return 1
    print("OK: every automatically loaded instruction file is within budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
