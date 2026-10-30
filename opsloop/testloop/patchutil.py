"""Build git-style unified diffs from old/new file text (used by reference fixes and tests)."""

import difflib

NO_NEWLINE_MARKER = "\\ No newline at end of file\n"


def make_diff(path: str, old: str | None, new: str | None) -> str:
    """Diff for one file. old=None creates the file; new=None deletes it."""
    old_lines = (old or "").splitlines(keepends=True)
    new_lines = (new or "").splitlines(keepends=True)
    header = [f"diff --git a/{path} b/{path}\n"]
    if old is None:
        header.append("new file mode 100644\n")
    if new is None:
        header.append("deleted file mode 100644\n")
    body = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="/dev/null" if old is None else f"a/{path}",
            tofile="/dev/null" if new is None else f"b/{path}",
        )
    )
    for i, line in enumerate(body):
        if line and not line.endswith("\n"):
            body[i] = line + "\n" + NO_NEWLINE_MARKER
    return "".join(header + body)
