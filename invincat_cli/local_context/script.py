"""Bash local-context detection script assembly."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Context detection script
#
# Outputs markdown describing the current working environment. Each section
# is guarded so that missing tools or unsupported environments are silently
# skipped -- external tools like git, tree, python3, and node are checked
# with `command -v` before use.
#
# The script is built from section functions so each piece can be tested
# independently. Independent sections run as parallel background subshells;
# see build_detect_script() for the orchestration logic.
# ---------------------------------------------------------------------------


def _section_header() -> str:
    """CWD line and IN_GIT flag (used by other sections).

    Returns:
        Bash snippet that prints the header and sets `CWD` / `IN_GIT`.
    """
    return r"""CWD="$(pwd)"
echo "## Local Context"
echo ""
echo "**Current Directory**: \`${CWD}\`"
echo ""

# --- Check git once ---
IN_GIT=false
if command -v git >/dev/null 2>&1 \
    && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  IN_GIT=true
fi"""


def _section_project() -> str:
    """Language, monorepo, git root, virtual-env detection.

    Returns:
        Bash snippet (requires `CWD` / `IN_GIT` from header).
    """
    return r"""# --- Project ---
PROJ_LANG=""
[ -f pyproject.toml ] || [ -f setup.py ] && PROJ_LANG="python"
[ -z "$PROJ_LANG" ] && [ -f package.json ] && PROJ_LANG="javascript/typescript"
[ -z "$PROJ_LANG" ] && [ -f Cargo.toml ] && PROJ_LANG="rust"
[ -z "$PROJ_LANG" ] && [ -f go.mod ] && PROJ_LANG="go"
[ -z "$PROJ_LANG" ] && { [ -f pom.xml ] || [ -f build.gradle ]; } && PROJ_LANG="java"

MONOREPO=false
{ [ -f lerna.json ] || [ -f pnpm-workspace.yaml ] \
  || [ -d packages ] || { [ -d libs ] && [ -d apps ]; } \
  || [ -d workspaces ]; } && MONOREPO=true

ROOT=""
$IN_GIT && ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"

ENVS=""
{ [ -d .venv ] || [ -d venv ]; } && ENVS=".venv"
[ -d node_modules ] && ENVS="${ENVS:+${ENVS}, }node_modules"

HAS_PROJECT=false
{ [ -n "$PROJ_LANG" ] || { [ -n "$ROOT" ] && [ "$ROOT" != "$CWD" ]; } \
  || $MONOREPO || [ -n "$ENVS" ]; } && HAS_PROJECT=true

if $HAS_PROJECT; then
  echo "**Project**:"
  [ -n "$PROJ_LANG" ] && echo "- Language: ${PROJ_LANG}"
  [ -n "$ROOT" ] && [ "$ROOT" != "$CWD" ] && echo "- Project root: \`${ROOT}\`"
  $MONOREPO && echo "- Monorepo: yes"
  [ -n "$ENVS" ] && echo "- Environments: ${ENVS}"
  echo ""
fi"""


def _section_package_managers() -> str:
    """Python and Node package manager detection.

    Returns:
        Bash snippet (standalone).
    """
    return r"""# --- Package managers ---
PKG=""
if [ -f uv.lock ]; then PKG="Python: uv"
elif [ -f poetry.lock ]; then PKG="Python: poetry"
elif [ -f Pipfile.lock ] || [ -f Pipfile ]; then PKG="Python: pipenv"
elif [ -f pyproject.toml ]; then
  if grep -q '\[tool\.uv\]' pyproject.toml 2>/dev/null; then PKG="Python: uv"
  elif grep -q '\[tool\.poetry\]' pyproject.toml 2>/dev/null; then PKG="Python: poetry"
  else PKG="Python: pip"
  fi
elif [ -f requirements.txt ]; then PKG="Python: pip"
fi

NODE_PKG=""
if [ -f bun.lockb ] || [ -f bun.lock ]; then NODE_PKG="Node: bun"
elif [ -f pnpm-lock.yaml ]; then NODE_PKG="Node: pnpm"
elif [ -f yarn.lock ]; then NODE_PKG="Node: yarn"
elif [ -f package-lock.json ] || [ -f package.json ]; then NODE_PKG="Node: npm"
fi
[ -n "$NODE_PKG" ] && PKG="${PKG:+${PKG}, }${NODE_PKG}"
[ -n "$PKG" ] && echo "**Package Manager**: ${PKG}" && echo ""
"""


def _section_runtimes() -> str:
    """Python and Node runtime version detection.

    Returns:
        Bash snippet (standalone).
    """
    return r"""# --- Runtimes ---
RT=""
if command -v python3 >/dev/null 2>&1; then
  PV="$(python3 --version 2>/dev/null | awk '{print $2}')"
  [ -n "$PV" ] && RT="Python ${PV}"
fi
if command -v node >/dev/null 2>&1; then
  NV="$(node --version 2>/dev/null | sed 's/^v//')"
  [ -n "$NV" ] && RT="${RT:+${RT}, }Node ${NV}"
fi
[ -n "$RT" ] && echo "**Runtimes**: ${RT}" && echo ""
"""


def _section_git() -> str:
    """Git branch, main branches, uncommitted changes.

    Returns:
        Bash snippet (requires `IN_GIT` from header).
    """
    return r"""# --- Git ---
if $IN_GIT; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  GT="**Git**: Current branch \`${BRANCH}\`"

  MAINS=""
  for b in $(git branch 2>/dev/null | sed 's/^[* ]*//'); do
    case "$b" in
      main) MAINS="${MAINS:+${MAINS}, }\`main\`" ;;
      master) MAINS="${MAINS:+${MAINS}, }\`master\`" ;;
    esac
  done
  [ -n "$MAINS" ] && GT="${GT}, main branch available: ${MAINS}"

  DC=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  if [ "$DC" -gt 0 ]; then
    if [ "$DC" -eq 1 ]; then GT="${GT}, 1 uncommitted change"
    else GT="${GT}, ${DC} uncommitted changes"
    fi
  fi

  echo "$GT"
  echo ""
fi"""


def _section_test_command() -> str:
    """Test command detection (make test / pytest / npm test).

    Returns:
        Bash snippet (standalone).
    """
    return r"""# --- Test command ---
TC=""
if [ -f Makefile ] && grep -qE '^tests?:' Makefile 2>/dev/null; then TC="make test"
elif [ -f pyproject.toml ]; then
  if grep -q '\[tool\.pytest' pyproject.toml 2>/dev/null \
      || [ -f pytest.ini ] || [ -d tests ] || [ -d test ]; then
    TC="pytest"
  fi
elif [ -f package.json ] \
    && grep -q '"test"' package.json 2>/dev/null; then
  TC="npm test"
fi
[ -n "$TC" ] && echo "**Run Tests**: \`${TC}\`" && echo ""
"""


def _section_files() -> str:
    """Directory listing (filtered, capped at 20).

    Returns:
        Bash snippet (standalone).
    """
    return r"""# --- Files ---
EXCL='node_modules|__pycache__|\.pytest_cache'
EXCL="${EXCL}|\.mypy_cache|\.ruff_cache|\.tox"
EXCL="${EXCL}|\.coverage|\.eggs|dist|build"
FILES=$(
  { ls -1 2>/dev/null; [ -e .invincat ] && echo .invincat; } |
  grep -vE "^(${EXCL})$" |
  sort -u
)
if [ -n "$FILES" ]; then
  TOTAL=$(echo "$FILES" | wc -l | tr -d ' ')
  SHOWN_FILES=$(echo "$FILES" | head -20)
  SHOWN=$(echo "$SHOWN_FILES" | wc -l | tr -d ' ')
  echo "**Files** (${SHOWN} shown):"
  echo "$SHOWN_FILES" | while IFS= read -r f; do
    if [ -d "$f" ]; then echo "- ${f}/"
    else echo "- ${f}"
    fi
  done
  [ "$SHOWN" -lt "$TOTAL" ] && echo "... ($((TOTAL - SHOWN)) more files)"
  echo ""
fi"""


def _section_tree() -> str:
    """`tree -L 3` output.

    Returns:
        Bash snippet (standalone).
    """
    return r"""# --- Tree ---
if command -v tree >/dev/null 2>&1; then
  TREE_EXCL='node_modules|.venv|__pycache__|.pytest_cache'
  TREE_EXCL="${TREE_EXCL}|.git|.mypy_cache|.ruff_cache"
  TREE_EXCL="${TREE_EXCL}|.tox|.coverage|.eggs|dist|build"
  T=$(tree -L 3 --noreport --dirsfirst \
    -I "$TREE_EXCL" 2>/dev/null | head -22)
  if [ -n "$T" ]; then
    echo "**Tree** (3 levels):"
    echo '```text'
    echo "$T"
    echo '```'
    echo ""
  fi
fi"""


def _section_makefile() -> str:
    """First 20 lines of Makefile (falls back to git root in monorepos).

    Returns:
        Bash snippet (requires `ROOT` from `_section_project` and `CWD` from header).
    """
    return r"""# --- Makefile ---
MK=""
if [ -f Makefile ]; then
  MK="Makefile"
elif [ -n "$ROOT" ] && [ "$ROOT" != "$CWD" ] && [ -f "${ROOT}/Makefile" ]; then
  MK="${ROOT}/Makefile"
fi
if [ -n "$MK" ]; then
  echo "**Makefile** (\`${MK}\`, first 20 lines):"
  echo '```makefile'
  head -20 "$MK"
  TL=$(wc -l < "$MK" | tr -d ' ')
  [ "$TL" -gt 20 ] && echo "... (truncated)"
  echo '```'
fi"""


def build_detect_script() -> str:
    """Concatenate all section functions into the full detection script.

    Independent sections run as parallel background jobs writing to temp
    files, then results are concatenated in the original display order.
    The header (CWD / IN_GIT) and project section (sets ROOT) run first
    because later sections depend on their variables.

    Returns:
        Complete bash heredoc ready for `backend.execute()`.
    """
    # Header + project run synchronously (set CWD, IN_GIT, ROOT for others)
    serial_prefix = f"{_section_header()}\n{_section_project()}"

    # These sections are independent — run them in parallel.
    # Subshells inherit parent variables (IN_GIT, ROOT, CWD) via fork.
    # Individual exit codes are not tracked because sections legitimately
    # exit non-zero when they have nothing to report (e.g. no runtimes).
    parallel_sections = [
        ("02_pkgmgr", _section_package_managers()),
        ("03_runtimes", _section_runtimes()),
        ("04_git", _section_git()),
        ("05_testcmd", _section_test_command()),
        ("06_files", _section_files()),
        ("07_tree", _section_tree()),
        ("08_makefile", _section_makefile()),
    ]

    # Build parallel wrapper: each section runs in a subshell writing to a
    # temp file. Stderr is captured per-section to prevent noise leakage.
    parallel_setup = "_DCT=$(mktemp -d) || exit 1\ntrap 'rm -rf \"$_DCT\"' EXIT"
    parallel_block = "\n".join(
        f'(\n{body}\n) > "$_DCT/{name}" 2>"$_DCT/{name}.err" &'
        for name, body in parallel_sections
    )
    cat_line = "cat " + " ".join(f'"$_DCT/{name}"' for name, _ in parallel_sections)

    body = f"{serial_prefix}\n{parallel_setup}\n{parallel_block}\nwait\n{cat_line}"
    return f"bash <<'__DETECT_CONTEXT_EOF__'\n{body}\n__DETECT_CONTEXT_EOF__\n"


DETECT_CONTEXT_SCRIPT = build_detect_script()

