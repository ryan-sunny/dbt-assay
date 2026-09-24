"""Write src/dbt_assay/lean/Sql/{Syntax,Lexer,Parser}.lean from their templates.

The kernel re-evaluates every definition it unfolds, and a `String` costs it UTF-8 work on every
comparison, so every keyword, symbol and name the grammar compares is a literal list of code
points. Written by hand they would be unreadable, so the templates say `⟪SELECT⟫` and this
writes `[83, 69, 76, 69, 67, 84]`. Run it after editing a template; a test fails when the
generated files are stale.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "dbt_assay" / "lean"


def expand(text: str) -> str:
    return re.sub(r"⟪(.*?)⟫", lambda m: "[" + ", ".join(str(ord(c)) for c in m.group(1)) + "]",
                  text)


def main() -> int:
    for name in ("Syntax", "Lexer", "Parser", "Semantics"):
        src = (ROOT / "templates" / f"{name}.lean.in").read_text()
        out = ("-- GENERATED from templates/" + name + ".lean.in by scripts/gen_lean_sql.py."
               " Edit the template.\n" + expand(src))
        (ROOT / "Sql" / f"{name}.lean").write_text(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
