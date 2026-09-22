#!/usr/bin/env bash
# Cut a release: one command, so it cannot be done by halves.
#
# *** EIGHT VERSIONS WERE BUMPED, COMMITTED AND PUSHED WITHOUT A TAG. ***
# 0.25 through 0.45 exist as commits and as nothing else. The release is tag-driven, so bumping
# `__version__` and pushing felt like releasing and published nothing -- and because PyPI only
# ever shows the LAST successful upload, the gap is invisible from the outside until somebody
# looks. A two-step ritual where the second step is optional gets skipped, so there is one step.
#
#   scripts/release.sh            # release whatever __version__ says
#   scripts/release.sh 0.47.0     # bump to this, commit the bump, then release
#
# It refuses rather than guesses: a dirty tree, a version already tagged, a red suite, a failed
# build. Nothing reaches PyPI that has not passed the same gates the workflow re-runs.
set -euo pipefail
cd "$(dirname "$0")/.."

die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
say() { printf '\033[2m%s\033[0m\n' "$*"; }

WANT="${1:-}"
if [ -n "$WANT" ]; then
  CUR=$(uv run python -c "import dbt_assay; print(dbt_assay.__version__)")
  if [ "$WANT" != "$CUR" ]; then
    say "bumping $CUR -> $WANT"
    # Both spellings, together. A version in two files is a version that disagrees with itself.
    sed -i.bak "s/^version = \"$CUR\"/version = \"$WANT\"/" pyproject.toml && rm -f pyproject.toml.bak
    sed -i.bak "s/^__version__ = \"$CUR\"/__version__ = \"$WANT\"/" src/dbt_assay/__init__.py \
      && rm -f src/dbt_assay/__init__.py.bak
    sed -i.bak "s/dbt-assay@v$CUR/dbt-assay@v$WANT/" README.md docs/OVERVIEW.md \
      && rm -f README.md.bak docs/OVERVIEW.md.bak
    git add -A
    git commit -q -m "$WANT"
  fi
fi

VER=$(uv run python -c "import dbt_assay; print(dbt_assay.__version__)")
PY_VER=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
[ "$VER" = "$PY_VER" ] || die "pyproject says $PY_VER and the package says $VER."

[ -z "$(git status --porcelain)" ] || die "the tree is dirty. Commit or stash first:
$(git status --short)"

git fetch --tags --quiet origin
if git rev-parse "v$VER" >/dev/null 2>&1 || \
   git ls-remote --tags --exit-code origin "refs/tags/v$VER" >/dev/null 2>&1; then
  die "v$VER is already tagged. PyPI is append-only: bump the version instead.
  scripts/release.sh <next version>"
fi

say "running the gates the release workflow will re-run..."
uv run pytest -q
uv run ruff check src tests
uv build >/dev/null
say "built $(ls dist | tr '\n' ' ')"

# *** THE COMMIT AND THE TAG GO TOGETHER OR NEITHER GOES. ***
# Pushing the commit first is how main ends up carrying a version nothing published.
git tag -a "v$VER" -m "$VER"
git push origin HEAD "v$VER"

printf '\n\033[32mreleased v%s\033[0m\n' "$VER"
say "the workflow publishes it: gh run watch \$(gh run list --workflow=release.yml -L1 --json databaseId -q '.[0].databaseId')"
