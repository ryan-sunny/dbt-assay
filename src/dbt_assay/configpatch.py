"""Add to `audit.yml` without rewriting it.

*** IT IS THEIR FILE, AND MOST OF WHAT IS IN IT IS THE COMMENTS. ***
`audit.yml` ships with forty lines of explanation around every block, people add their own, and it
lives in git. PyYAML cannot round-trip a comment: load-and-dump returns a file with the same
meaning and none of the reasons, reordered, with every block quote reflowed. A form that handed
back config and applied it that way would silently delete the part of the file that took the
longest to write.

So this edits LINES. It inserts a key under a key, it replaces a scalar in place, and it changes
nothing else -- ordering, comments, blank lines and quoting style all survive because nothing
touches them.

*** AND IT REFUSES RATHER THAN GUESSES. ***
A path it cannot place unambiguously comes back in `refused`, with the YAML to paste and the
reason. A config editor that writes something approximately where it belongs is worse than one
that says it could not, because the second one you can check.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Change:
    """One key to set, named by its path. `value` is a scalar or a mapping."""
    path: list
    value: object
    why: str = ""                     # what the person said, for the diff

    @property
    def dotted(self) -> str:
        return ".".join(str(p) for p in self.path)


@dataclass
class Result:
    text: str
    applied: list = field(default_factory=list)
    refused: list = field(default_factory=list)      # (Change, reason)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_blank(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def _render(value, indent: int) -> list:
    """A scalar or a mapping, as YAML lines at this indent. Strings are block-quoted when long."""
    pad = " " * indent
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            if isinstance(v, dict):
                out.append(f"{pad}{k}:")
                out += _render(v, indent + 2)
            else:
                out += _scalar(k, v, indent)
        return out
    return [f"{pad}{value}"]


def _scalar(key, value, indent: int) -> list:
    """`key: value`, folded when it is a sentence.

    A `means:` is prose somebody wrote and is routinely longer than a line. `>-` keeps it readable
    in the file rather than producing a 200-character line nobody will edit again.
    """
    pad = " " * indent
    text = str(value)
    if len(text) + len(key) + indent > 96 and "\n" not in text:
        wrapped = []
        line = ""
        for word in text.split():
            if len(line) + len(word) + 1 > 92 - indent:
                wrapped.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        wrapped.append(line)
        return [f"{pad}{key}: >-"] + [f"{pad}  {w}" for w in wrapped]
    if any(c in text for c in ':#"\'') or text.strip() != text:
        return [f'{pad}{key}: "{text}"']
    return [f"{pad}{key}: {text}"]


def _find_key(lines: list, key: str, indent: int, start: int, end: int) -> int:
    """The line index of `key:` at exactly this indent, inside [start, end), or -1."""
    want = f"{key}:"
    for i in range(start, min(end, len(lines))):
        line = lines[i]
        if _is_blank(line) or _indent(line) != indent:
            continue
        stripped = line.strip()
        if stripped == want or stripped.startswith(want + " "):
            return i
    return -1


def _block_end(lines: list, at: int, indent: int, end: int) -> int:
    """Where the block opened at `at` stops: the first later line indented no further.

    Trailing blanks and comments belong to whatever comes NEXT, not to this block, or every insert
    would land under somebody else's comment.
    """
    last = at + 1
    for i in range(at + 1, min(end, len(lines))):
        if _is_blank(lines[i]):
            continue
        if _indent(lines[i]) <= indent:
            return last
        last = i + 1
    return last


def apply(text: str, changes: list) -> Result:
    """Set each change, in place. Nothing else in the file moves."""
    lines = text.splitlines()
    applied, refused = [], []
    for ch in changes:
        try:
            lines, how = _one(lines, ch)
        except _Refused as e:
            refused.append((ch, str(e)))
            continue
        applied.append((ch, how))
    return Result("\n".join(lines) + ("\n" if text.endswith("\n") else ""), applied, refused)


class _Refused(RuntimeError):
    pass


def _one(lines: list, ch: Change) -> tuple:
    if not ch.path:
        raise _Refused("the change names no key")
    start, end, indent = 0, len(lines), 0
    parent_at = -1

    for depth, key in enumerate(ch.path):
        at = _find_key(lines, str(key), indent, start, end)
        last = depth == len(ch.path) - 1

        if at == -1:
            if parent_at == -1 and depth > 0:
                raise _Refused(f"`{ch.dotted}` has no parent block in this file")
            # *** THE TEMPLATE SHIPS `vocab: {}` AND THAT IS NOT A BLOCK. ***
            # Inserting a child under a flow mapping produces a file that does not parse, so the
            # empty flow is replaced by a block first. Any NON-empty flow mapping is somebody's
            # deliberate style and is refused rather than rewritten.
            if parent_at >= 0:
                head = lines[parent_at].strip()
                inline = head.split(":", 1)[1].strip()
                if inline in ("{}", "[]"):
                    lines[parent_at] = lines[parent_at].split(":", 1)[0] + ":"
                    end = _block_end(lines, parent_at, _indent(lines[parent_at]), len(lines))
                elif inline:
                    raise _Refused(
                        f"`{'.'.join(str(p) for p in ch.path[:depth])}` is written on one line "
                        f"as `{inline}`; assay will not rewrite it")
            block = ([f"{' ' * indent}{key}:"] + _render(ch.value, indent + 2)) if not last or \
                isinstance(ch.value, dict) else _scalar(key, ch.value, indent)
            if last and not isinstance(ch.value, dict):
                block = _scalar(key, ch.value, indent)
            elif last:
                block = [f"{' ' * indent}{key}:"] + _render(ch.value, indent + 2)
            else:
                raise _Refused(f"`{ch.dotted}` needs `{key}` to exist first")
            insert_at = end
            lines = lines[:insert_at] + block + lines[insert_at:]
            return lines, "added"

        if last:
            stop = _block_end(lines, at, _indent(lines[at]), end)
            block = (_scalar(key, ch.value, _indent(lines[at]))
                     if not isinstance(ch.value, dict)
                     else [f"{' ' * _indent(lines[at])}{key}:"]
                     + _render(ch.value, _indent(lines[at]) + 2))
            lines = lines[:at] + block + lines[stop:]
            return lines, "replaced"

        parent_at = at
        indent = _indent(lines[at]) + 2
        start, end = at + 1, _block_end(lines, at, _indent(lines[at]), end)
    raise _Refused("unreachable")


def diff(before: str, after: str) -> list:
    """The changed lines, for showing somebody before anything is written."""
    import difflib
    return [ln for ln in difflib.unified_diff(
        before.splitlines(), after.splitlines(), "audit.yml", "audit.yml", lineterm="", n=2)]
