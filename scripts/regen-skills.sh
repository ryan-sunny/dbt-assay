#!/usr/bin/env bash
# Rewrite the checked-in skill copies from `skilltext`, the way `assay onboard --agent` would.
# test_docs_cover_the_tool fails until they match, so a skill change is one command, not a ritual.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -c "
from pathlib import Path
from dbt_assay import skilltext
for n, t in (('dbt-assay', skilltext.SKILL_MD), ('assay-review', skilltext.REVIEW_SKILL_MD)):
    Path(f'.claude/skills/{n}/SKILL.md').write_text(t)
"
