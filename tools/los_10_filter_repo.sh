#!/usr/bin/env bash
#
# los_10_filter_repo.sh — Step 6 mechanical extraction (LOS-10).
#
# Runs `git filter-repo --path-rename` and string substitution on a
# THROWAWAY CLONE of project-miru, producing a history that imports
# cleanly into Dreighto/LogueOS-Orchestrator.
#
# This script is invoked ONCE at Step 6 of the LOS-10 cutover. It must
# never run against the live project-miru working tree.
#
# USAGE:
#
#     # Dry-run (default): print what would happen, do not modify anything.
#     bash tools/los_10_filter_repo.sh --source-clone /tmp/project-miru-clone
#
#     # Real run: write the rewritten history to /tmp/los-10-import/.
#     bash tools/los_10_filter_repo.sh --source-clone /tmp/project-miru-clone --execute
#
# AFTER A SUCCESSFUL --execute, the output at /tmp/los-10-import/ is
# pushed to Dreighto/LogueOS-Orchestrator's `migration-import` branch
# (NEVER main directly). Main is updated via PR after manual review.
#
# REFUSAL CONDITIONS:
#
# - Source clone is not a git repo
# - Source clone has uncommitted changes
# - Source clone path resolves to the live D:\dev\miru tree
# - `git filter-repo` is not on PATH (install: pip install git-filter-repo)
# - Output directory already exists (must be a fresh path)
#
# WHAT THIS SCRIPT DOES NOT DO:
#
# - Push to GitHub (operator does this manually with explicit confirmation)
# - Sign commits (the imported history retains original signatures or lacks
#   them; new commits added post-import carry fresh signatures)
# - Touch the DGAS audit log (handled by tools/migrate_dgas_boundary.py at Step 4)
# - Change repo settings, branch protections, or webhooks
#
# See .miru/reference/los-10-rename-map.md for the canonical name list.

set -euo pipefail

EXECUTE=0
SOURCE_CLONE=""
OUTPUT_DIR="${LOS_10_OUTPUT_DIR:-/tmp/los-10-import}"

# The "live" project-miru tree. Identified by the common git dir of its
# checkout — that's the same identity for the main worktree AND every
# attached worktree (miru-w1, miru-w2, etc.). CR R3: the previous
# string-match on three literal path spellings missed attached worktrees,
# which share the same `.git` common dir and would let an operator
# accidentally rewrite history from a stale worker checkout.
#
# Resolution is done at runtime against the source_clone's git-common-dir
# (see refusal block below). The constant below is the path of the LIVE
# main worktree — its common-dir is computed once and compared by realpath
# against the source_clone's. Hardcoded fallback for clarity in error
# messages.
LIVE_REPO_MAIN_TREE="/d/dev/miru"

usage() {
    sed -n '2,40p' "$0" >&2
    exit 2
}

# Validate that an option flag has an accompanying value. Catches the
# easy mistake `--source-clone --output-dir /x` where source_clone would
# silently absorb the next flag as its value (CR R2). Under `set -u`,
# missing values otherwise produce a non-actionable "unbound variable"
# error rather than "X requires a value".
#
# CR R3: rejects ANY leading dash, not just `--`. Previously `--source-clone
# -h` consumed `-h` as a path and failed later with a misleading repo error
# instead of the intended usage failure.
require_value() {
    local flag="$1"
    local value="${2:-}"
    if [[ -z "$value" || "$value" == -* ]]; then
        echo "[los_10_filter_repo] ERROR: $flag requires a value" >&2
        usage
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-clone)
            require_value "$1" "${2:-}"
            SOURCE_CLONE="$2"
            shift 2
            ;;
        --output-dir)
            require_value "$1" "${2:-}"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --execute)
            EXECUTE=1
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "unknown arg: $1" >&2
            usage
            ;;
    esac
done

if [[ -z "$SOURCE_CLONE" ]]; then
    echo "[los_10_filter_repo] ERROR: --source-clone is required" >&2
    usage
fi

# Refusal: source must be a real git repo. CR R3: accept BOTH a .git
# directory (main worktree) AND a .git file (attached worktree gitlink).
# Use `git rev-parse --is-inside-work-tree` for the canonical check —
# it succeeds in either case.
if ! (cd "$SOURCE_CLONE" 2>/dev/null && [[ "$(git rev-parse --is-inside-work-tree 2>/dev/null)" == "true" ]]); then
    echo "[los_10_filter_repo] ERROR: $SOURCE_CLONE is not a git repository" >&2
    exit 1
fi

# Refusal: source must not be the live tree (we operate on clones only).
# CR R3: identity check, not path check. Compares git-common-dir (which
# is the same for the main worktree AND every attached worktree) so an
# operator can't accidentally invoke this from /d/dev/miru-w1 (or any
# worker checkout) and have it silently pass.
SOURCE_ABS="$(cd "$SOURCE_CLONE" && pwd)"
SOURCE_COMMON_DIR="$(cd "$SOURCE_CLONE" && git rev-parse --git-common-dir 2>/dev/null || true)"
if [[ -n "$SOURCE_COMMON_DIR" ]]; then
    # Resolve to absolute path for a reliable comparison
    SOURCE_COMMON_DIR="$(cd "$SOURCE_CLONE" && cd "$SOURCE_COMMON_DIR" && pwd)"
fi

LIVE_COMMON_DIR=""
if [[ -d "$LIVE_REPO_MAIN_TREE" ]]; then
    LIVE_COMMON_DIR="$(cd "$LIVE_REPO_MAIN_TREE" && git rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "$LIVE_COMMON_DIR" ]]; then
        LIVE_COMMON_DIR="$(cd "$LIVE_REPO_MAIN_TREE" && cd "$LIVE_COMMON_DIR" && pwd)"
    fi
fi

if [[ -n "$SOURCE_COMMON_DIR" && -n "$LIVE_COMMON_DIR" && "$SOURCE_COMMON_DIR" == "$LIVE_COMMON_DIR" ]]; then
    echo "[los_10_filter_repo] ERROR: refusing to run on the live project-miru tree (or one of its attached worktrees)." >&2
    echo "    source_clone:   $SOURCE_ABS" >&2
    echo "    git-common-dir: $SOURCE_COMMON_DIR" >&2
    echo "    matches live:   $LIVE_COMMON_DIR" >&2
    echo "" >&2
    echo "    Clone to a throwaway path first:" >&2
    echo "    git clone --no-local $LIVE_REPO_MAIN_TREE /tmp/project-miru-clone" >&2
    exit 1
fi

# Fallback path-match (only fires if git rev-parse failed above, e.g.
# the LIVE_REPO_MAIN_TREE isn't on this machine). Keeps the prior
# defense-in-depth refusal for the literal path spellings.
LIVE_REPO_PATHS=(
    "/d/dev/miru"
    "D:/dev/miru"
    "D:\\dev\\miru"
)
for live in "${LIVE_REPO_PATHS[@]}"; do
    if [[ "$SOURCE_ABS" == "$live" ]]; then
        echo "[los_10_filter_repo] ERROR: refusing to run on the live tree ($live). Clone first:" >&2
        echo "    git clone --no-local $LIVE_REPO_MAIN_TREE /tmp/project-miru-clone" >&2
        exit 1
    fi
done

# Refusal: source must be clean. CR R1 fix: include untracked files too —
# `git diff` alone doesn't catch them, so a clone with random new files in
# the tree would silently pass and end up in the rewritten history.
if ! (cd "$SOURCE_CLONE" && [[ -z "$(git status --porcelain --untracked-files=all)" ]]); then
    echo "[los_10_filter_repo] ERROR: source clone has uncommitted changes or untracked files" >&2
    echo "    Run 'git status' in $SOURCE_CLONE and commit or remove them first." >&2
    exit 1
fi

# Refusal: git-filter-repo must be installed (only for actual execution).
# Dry-runs don't invoke filter-repo, so the install check shouldn't gate
# them — operators rehearsing the plan on a machine without the tool
# should still be able to see what would happen.
if [[ $EXECUTE -eq 1 ]] && ! command -v git-filter-repo >/dev/null 2>&1; then
    echo "[los_10_filter_repo] ERROR: git-filter-repo not on PATH" >&2
    echo "    Install: pip install git-filter-repo" >&2
    exit 1
fi

# Refusal: output dir must not exist when actually executing.
# CR R3: previously this fired unconditionally, blocking dry-runs on a
# leftover /tmp/los-10-import from a prior cutover attempt. Dry-runs
# don't write anything, so a stale output_dir shouldn't gate them.
if [[ $EXECUTE -eq 1 && -e "$OUTPUT_DIR" ]]; then
    echo "[los_10_filter_repo] ERROR: output dir already exists: $OUTPUT_DIR" >&2
    echo "    Pick a fresh path or delete the existing one." >&2
    exit 1
fi

# Path renames per .miru/reference/los-10-rename-map.md.
#
# Format: "source-glob:dest-path" — git-filter-repo --path-rename treats
# source as a prefix match. We list only the renames; identity paths
# (services/dispatch_listener/, tools/audit_chain.py, etc.) flow through
# unchanged.
PATH_RENAMES=(
    "tools/miru_mcp_gateway/:tools/logueos_mcp_gateway/"
)

# Paths to EXCLUDE from the import (Miru-specific business logic that
# does NOT go into LogueOS-Orchestrator).
PATH_EXCLUDES=(
    "miru_ai/"
    "pm/"
    "data/card_catalog_backup_*.db"
    "data/card_data.db"
    "tests/test_miru_ai_server.py"
    "tests/test_miru_project_sync.py"
    "tests/test_miru_learning_engine.py"
    "tests/test_miru_card_intel.py"
    "tests/test_miru_image_ingestion.py"
    "tests/test_miru_source_registry.py"
)

# Identifier renames inside file contents. Applied via
# git-filter-repo --replace-text. The replace-text file has one
# `old==>new` per line, NO leading whitespace.
#
# CR R2: single source of truth. The previous code duplicated this list
# between build_replace_text() and print_plan(). Drift between the two
# would silently mean the plan shows one set of renames while the
# execute path applies another. Now both paths iterate the same array.
REPLACEMENT_RULES=(
    "MIRU_ROUTING_KEY==>LOGUEOS_ROUTING_KEY"
    "MIRU_TRACE_ID==>LOGUEOS_TRACE_ID"
    "MIRU_MCP_GATEWAY_PORT==>LOGUEOS_MCP_GATEWAY_PORT"
    "MIRU_MCP_GATEWAY_HOST==>LOGUEOS_MCP_GATEWAY_HOST"
    "miru-gateway==>logueos-gateway"
    "miru-dispatch-listener==>logueos-dispatch-listener"
    "miru_mcp_gateway==>logueos_mcp_gateway"
)
REPLACE_TEXT_FILE="$OUTPUT_DIR/.filter-repo-replace-text"

build_replace_text() {
    mkdir -p "$OUTPUT_DIR"
    : > "$REPLACE_TEXT_FILE"
    for rule in "${REPLACEMENT_RULES[@]}"; do
        echo "$rule" >> "$REPLACE_TEXT_FILE"
    done
}

print_plan() {
    echo "==[ LOS-10 Step 6 filter-repo plan ]=================================="
    echo "Source clone:    $SOURCE_CLONE"
    echo "Output dir:      $OUTPUT_DIR"
    echo "Mode:            $( [[ $EXECUTE -eq 1 ]] && echo EXECUTE || echo DRY-RUN )"
    echo
    echo "Pass 1 — excludes (filter-repo --invert-paths --path-glob ...):"
    for r in "${PATH_EXCLUDES[@]}"; do echo "  $r"; done
    echo
    echo "Pass 2 — renames + replace-text (filter-repo --path-rename ... --replace-text):"
    for r in "${PATH_RENAMES[@]}"; do echo "  $r"; done
    echo
    echo "Identifier renames (replace-text, applied in pass 2):"
    for rule in "${REPLACEMENT_RULES[@]}"; do
        # Display "old → new" instead of the raw "old==>new" filter-repo format
        old="${rule%%==>*}"
        new="${rule##*==>}"
        echo "  $old → $new"
    done
    echo
    echo "Refusal: NO push to remote performed by this script. Operator pushes manually."
    echo "======================================================================"
}

print_plan

if [[ $EXECUTE -eq 0 ]]; then
    echo "[los_10_filter_repo] dry-run complete. Re-run with --execute to write to $OUTPUT_DIR." >&2
    exit 0
fi

# Real execution path:
# 1. Copy the clone to OUTPUT_DIR so filter-repo doesn't mangle the original
# 2. Pass 1: filter-repo with --invert-paths to EXCLUDE the Miru-specific
#    paths (this is the ONLY way to do exclusion in git-filter-repo: --path-glob
#    is inclusion-based, --invert-paths flips the selection to "everything
#    NOT matching these patterns"). CR R1 fix: previous implementation used
#    --path-glob "!path" which is treated as literal "!path", not as an
#    exclusion operator. See https://github.com/newren/git-filter-repo docs.
# 3. Pass 2: filter-repo with --path-rename + --replace-text. Per upstream
#    docs, the project recommends chaining separate runs rather than mixing
#    inclusion + exclusion in one invocation.
# 4. Print final size + commit-graph summary

echo "[los_10_filter_repo] copying $SOURCE_CLONE → $OUTPUT_DIR" >&2
cp -r "$SOURCE_CLONE" "$OUTPUT_DIR"
build_replace_text

cd "$OUTPUT_DIR"

# Pass 1: exclude Miru-specific paths via --invert-paths
EXCLUDE_ARGS=( --invert-paths )
for r in "${PATH_EXCLUDES[@]}"; do
    EXCLUDE_ARGS+=( --path-glob "$r" )
done

echo "[los_10_filter_repo] pass 1: excluding Miru-specific paths (this may take a few minutes)..." >&2
git filter-repo "${EXCLUDE_ARGS[@]}"

# Pass 2: apply path renames + replace-text
RENAME_ARGS=( --replace-text "$REPLACE_TEXT_FILE" )
for r in "${PATH_RENAMES[@]}"; do
    RENAME_ARGS+=( --path-rename "$r" )
done

echo "[los_10_filter_repo] pass 2: applying path renames + identifier replacements..." >&2
git filter-repo "${RENAME_ARGS[@]}"

echo
echo "==[ Filter-repo done ]=============================================="
echo "Output:           $OUTPUT_DIR"
echo "Commit count:     $(git rev-list --count HEAD)"
echo "Working dir size: $(du -sh .git 2>/dev/null | awk '{print $1}')"
echo
echo "Next:"
echo "  1. Inspect the result: cd $OUTPUT_DIR && git log --oneline | head"
echo "  2. Add LogueOS-Orchestrator as remote:"
echo "       git remote add origin https://github.com/Dreighto/LogueOS-Orchestrator.git"
echo "  3. Push to migration-import branch (NEVER main directly):"
echo "       git push -u origin HEAD:migration-import"
echo "  4. Open PR from migration-import → main on LogueOS-Orchestrator. Manual review required."
echo "===================================================================="
