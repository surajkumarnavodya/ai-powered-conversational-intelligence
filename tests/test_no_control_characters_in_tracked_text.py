"""No tracked text file contains a raw C0 control character.

UAT-readiness Phase 11 (`OPS-011`).

The first two commands a UAT operator is told to run did not run as
written. `docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md` §2.1 contained::

    alembic -c identity<BEL>lembic.ini upgrade head
    python scripts<BS>ootstrap_admin.py

The author meant ``identity\\alembic.ini`` and
``scripts\\bootstrap_admin.py``; the ``\\a`` and ``\\b`` were interpreted as
escape sequences on the way to disk, leaving a literal BEL (0x07) and
backspace (0x08) byte in the file. Both bytes are invisible in every
renderer this project's documentation is read in -- GitHub, VS Code's
preview and a terminal `cat` all show ``identitylembic.ini``, and a
terminal that honours BEL *beeps* instead. Phase 10 measured "449
documented command lines all run as written" and these two were among
them, because a reviewer reading the rendered page cannot see the
difference between a missing character and a missing escape.

The same slip reached code: `governance/domain_detection.py`'s docstring
described ``\\b`` word-boundary anchors in a non-raw docstring, so three
backspaces were embedded in prose a developer reads.

Scanning for the bytes is the only reliable check, so that is what this
does -- derived from `git ls-files` rather than a list, the same reason
`tests/test_declared_dependencies.py` and
`tests/test_ci_gate_coverage.py` derive theirs from the tree.

Scope, stated rather than implied:

* Binary files are skipped by detecting a NUL byte, which every image,
  font and archive this repository tracks contains and no text file
  does. That is deliberately cruder than a media-type table: a new
  binary extension must not be able to make this test fail, and a text
  file with a NUL in it is itself a defect worth failing on.
* TAB (0x09), LF (0x0A) and CR (0x0D) are legitimate in text and are not
  checked. Everything else below 0x20, plus DEL (0x7F), is not.
"""

from __future__ import annotations

import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Control bytes that have no legitimate place in a tracked text file.
#: TAB/LF/CR are excluded because they structure the text itself.
_FORBIDDEN = tuple(byte for byte in range(0x20) if byte not in (0x09, 0x0A, 0x0D)) + (0x7F,)

_NAMES = {
    0x07: "BEL (an escaped \\a)",
    0x08: "backspace (an escaped \\b)",
    0x0B: "vertical tab (an escaped \\v)",
    0x0C: "form feed (an escaped \\f)",
    0x1B: "escape (an escaped \\e)",
    0x7F: "DEL",
}


def _tracked_files() -> list[pathlib.Path]:
    """Every file git tracks, as absolute paths."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [_REPO_ROOT / rel for rel in listing.split("\0") if rel]


def _control_characters(data: bytes) -> dict[int, int]:
    """Forbidden control bytes in `data`, mapped to how many of each."""
    return {byte: data.count(bytes([byte])) for byte in _FORBIDDEN if bytes([byte]) in data}


def _offending_text_files() -> dict[str, dict[int, int]]:
    """Tracked, non-binary files holding a forbidden control byte."""
    offenders: dict[str, dict[int, int]] = {}
    for path in _tracked_files():
        try:
            data = path.read_bytes()
        except OSError:
            # A path git tracks but this checkout does not have (sparse
            # checkout, a symlink to nowhere) is not this test's concern.
            continue
        if b"\0" in data:
            continue  # binary -- see this module's docstring
        found = _control_characters(data)
        if found:
            offenders[str(path.relative_to(_REPO_ROOT)).replace("\\", "/")] = found
    return offenders


def test_the_scan_detects_the_bytes_it_is_looking_for():
    """Non-vacuity: the detector must see the exact corruption that
    reached `UAT_SCRIPTS_AND_SIGNOFF.md`, not merely return empty."""
    corrupted = b"alembic -c identity\x07lembic.ini\npython scripts\x08ootstrap_admin.py\n"
    assert _control_characters(corrupted) == {0x07: 1, 0x08: 1}
    assert _control_characters(b"tabs\tnewlines\r\nare fine\n") == {}


def test_binary_files_are_skipped_rather_than_reported():
    """The repository tracks PNG screenshots full of these bytes; the
    NUL test is what keeps them out of the result."""
    assert _control_characters(b"\x89PNG\r\n\x1a\n\x00\x07\x08") != {}
    offenders = _offending_text_files()
    assert not any(name.endswith(".png") for name in offenders), offenders


def test_no_tracked_text_file_contains_a_control_character():
    offenders = _offending_text_files()
    if offenders:
        lines = [
            f"  {name}: "
            + ", ".join(f"{count}x {_NAMES.get(byte, hex(byte))}" for byte, count in found.items())
            for name, found in sorted(offenders.items())
        ]
        raise AssertionError(
            "Tracked text files contain raw control characters -- almost "
            "always a backslash escape that was interpreted on the way to "
            "disk, which makes a documented command silently wrong "
            "(OPS-011):\n" + "\n".join(lines)
        )
