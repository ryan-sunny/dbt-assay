"""assay's own config, read against assay's own store.

*** `description_contradicts_the_code`, OCCURRING IN THE CONFIG OF THE TOOL THAT SHIPS IT. ***
The field warehouse's `audit.yml` said "38 of 38 agreed" above `test_cannot_fail` while the store
held 90, and "nothing gates until a question clears min_adjudications, which none do" when one did.
Both are numbers about the store, written by hand, and both drifted -- and the drifted count was
then offered as evidence for a build gate. assay reads a project's prose against its SQL and never
read its own prose against its own store.

*** A PATTERN, NOT A JUDGMENT, AND IT SAYS WHAT IT CANNOT READ. ***
A comment asserting a count is checked the way a parser checks anything: the number it states
against the number the store holds. Only the shapes below are read. A claim qualified by a version
("0 of 10 agreed before 0.15.0") is history and is skipped rather than checked against the present,
and prose that asserts no number is not read at all -- silence here is "no checkable claim", never
"the comments are true".

A count matches if ANY reading of the store bears it out -- human verdicts, agent rulings, or both
-- because the comment does not say which it meant, and a finding that fires on an ambiguity is a
false positive in the file this tool asks people to trust.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .checks.structural import Finding

CHECK = "config_comment_contradicts_the_store"

_OF = re.compile(r"\b(\d[\d,]*) of (\d[\d,]*) agreed\b")
_ALL = re.compile(r"\b(\d[\d,]*) ruled, all agreed\b")
_AGREED_UNCLEAR = re.compile(r"\b(\d[\d,]*) agreed, (\d[\d,]*) unclear\b")
_SOURCE_TOTAL = re.compile(r"\b(\d[\d,]*) (agent|human) (?:rulings|verdicts)\b")
_NONE_CLEAR = re.compile(r"min_adjudications,? which none (?:do|of them do)\b")
# "before 0.15.0", "at 0.21.1", "until v2": a claim about a moment, not about now.
_DATED = re.compile(r"\b(?:before|after|until|at|as of|in)\s+v?\d+\.\d+")


@dataclass
class Claim:
    line: int
    question: str          # the `questions:` key the comment sits above, or "" for the file
    kind: str              # of | all | agreed_unclear | source_total | none_clear
    text: str
    numbers: tuple = ()
    source: str = ""


def _n(s: str) -> int:
    return int(s.replace(",", ""))


def claims(text: str) -> list[Claim]:
    """Every checkable claim in an audit.yml's comments, with the question it is about."""
    lines = text.splitlines()
    out: list[Claim] = []
    block: list[tuple[int, str]] = []
    section = ""
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            block.append((i, stripped.lstrip("#").strip()))
            continue
        if not stripped:
            continue
        indent = len(raw) - len(raw.lstrip())
        key = stripped.split(":", 1)[0].strip() if ":" in stripped else ""
        if indent == 0 and key:
            section = key
        about = key if (section == "questions" and indent == 2 and key) else ""
        if block:
            out += _read_block(block, about)
            block = []
    if block:
        out += _read_block(block, "")
    return out


def _read_block(block: list[tuple[int, str]], question: str) -> list[Claim]:
    out = []
    joined = " ".join(t for _i, t in block)
    first = block[0][0]

    def line_of(snippet: str) -> int:
        head = snippet.split()[0]
        return next((i for i, t in block if head in t), first)

    for sentence in re.split(r"(?<=[.;])\s+", joined):
        if _DATED.search(sentence):
            continue
        for m in _OF.finditer(sentence):
            out.append(Claim(line_of(m.group(0)), question, "of", m.group(0),
                             (_n(m.group(1)), _n(m.group(2)))))
        for m in _ALL.finditer(sentence):
            out.append(Claim(line_of(m.group(0)), question, "all", m.group(0), (_n(m.group(1)),)))
        for m in _AGREED_UNCLEAR.finditer(sentence):
            out.append(Claim(line_of(m.group(0)), question, "agreed_unclear", m.group(0),
                             (_n(m.group(1)), _n(m.group(2)))))
        for m in _SOURCE_TOTAL.finditer(sentence):
            out.append(Claim(line_of(m.group(0)), question, "source_total", m.group(0),
                             (_n(m.group(1)),), source=m.group(2)))
        for m in _NONE_CLEAR.finditer(sentence):
            out.append(Claim(line_of("min_adjudications"), question, "none_clear", m.group(0)))
    return out


def _tallies(store, question: str) -> dict:
    """{source: {agree, disagree, unclear, accept, total}} for one question, or project-wide.

    Human verdicts are counted on the model-level rows, one per keypress. Agent rulings are filed
    per finding and have no model-level row, so those are what is counted for an agent.
    """
    out: dict = {}
    for source, where_subject in (("human", "subject not like '%::finding::%'"),
                                  ("agent", "1 = 1")):
        q = (f"select verdict, count(*) from (select *, row_number() over (partition by "
             f"subject, question order by decided_at desc) rn from adjudications "
             f"where source = ? and {where_subject}"
             + (" and question = ?" if question else "") + ") where rn = 1 group by 1")
        rows = dict(store.con.execute(q, [source, *([question] if question else [])])
                    .fetchall())
        rows["total"] = sum(rows.values())
        out[source] = rows
    both = {k: out["human"].get(k, 0) + out["agent"].get(k, 0)
            for k in ("agree", "disagree", "unclear", "accept", "total")}
    out["either"] = both
    return out


def _holds(c: Claim, t: dict) -> bool:
    agree = t.get("agree", 0) + t.get("accept", 0)
    total = t.get("total", 0)
    decided = total - t.get("unclear", 0)
    if c.kind == "of":
        n, m = c.numbers
        return agree == n and m in (total, decided)
    if c.kind == "all":
        return total == c.numbers[0] and agree == total
    if c.kind == "agreed_unclear":
        return agree == c.numbers[0] and t.get("unclear", 0) == c.numbers[1]
    return True


def _say(t: dict) -> str:
    agree = t.get("agree", 0) + t.get("accept", 0)
    bits = f"{agree} of {t.get('total', 0)} agreed"
    if t.get("unclear"):
        bits += f", {t['unclear']} unclear"
    return bits


def config_findings(config_path: str, store, cfg=None) -> list[Finding]:
    """Findings for every comment in audit.yml whose number the store does not bear out."""
    from .config import DEFAULT_FILENAMES
    base = Path(config_path or ".")
    path = next((base / n for n in DEFAULT_FILENAMES if (base / n).exists()), None)
    if path is None or store is None:
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    out = []
    for c in claims(text):
        if c.kind == "none_clear":
            floor = getattr(cfg, "min_adjudications", 20) if cfg is not None else 20
            cleared = sorted(f for f, n in store.adjudication_counts("human").items()
                             if n >= floor)
            if not cleared:
                continue
            store_says = (f"{len(cleared)} question(s) have cleared min_adjudications "
                          f"({floor}): {', '.join(cleared[:6])}")
        elif c.kind == "source_total":
            t = _tallies(store, c.question)[c.source]
            if t.get("total", 0) == c.numbers[0]:
                continue
            store_says = f"the store holds {t.get('total', 0)} {c.source} ruling(s)"
        else:
            t = _tallies(store, c.question)
            if any(_holds(c, t[src]) for src in ("human", "agent", "either")):
                continue
            store_says = (f"the store holds {_say(t['human'])} from people and "
                          f"{_say(t['agent'])} from an agent")
        about = f" about `{c.question}`" if c.question else ""
        out.append(Finding(
            check="config_comment_contradicts_the_store", subject="", subject_name=path.name, file=str(path), base=1,
            summary=f"{path.name} line {c.line} says \"{c.text}\"{about}, and the store does not",
            detail=(f"{store_says}. A comment in audit.yml that asserts a count is a claim about "
                    f"the store, and this one has drifted from it -- the same defect as a "
                    f"description that no longer matches its SQL. Update the number, or say "
                    f"when it was true (\"38 of 38 agreed at 0.37\"), which this check reads as "
                    f"history and leaves alone."),
            evidence={"line": c.line, "says": c.text, "question": c.question,
                      "store": store_says}))
    return out
