#!/usr/bin/env bash
# reorganize.sh — move loose root files into docs/audits/ in a single commit.
#
# Run from the repo root once before merging the SOLID reorganization branch:
#   bash scripts/reorganize.sh
#
# Idempotent: skips files that have already been moved.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

DEST="docs/audits"
mkdir -p "${DEST}"

FILES=(
    analysis.txt
    arch.txt
    c_standards_timeline.svg
    gaussian_file_sorter.html
    language_robustness_comparison.html
    teb2_1970s_audit.html
    teb2_architecture.html
    teb2_complete_file_relations.html
    teb2_enigma_audit.html
    teb2_file_schema.html
    teb2_ibm701_reality.html
    teb2_io_schema.html
    teb2_knr_replacements.html
    teb2_org_files.svg
    teb2_org_overview.svg
    teb_robust_replacements.html
)

moved=0
for f in "${FILES[@]}"; do
    if [ -f "${f}" ]; then
        git mv -k "${f}" "${DEST}/${f}"
        moved=$((moved + 1))
        echo "  moved: ${f} -> ${DEST}/${f}"
    elif [ -f "${DEST}/${f}" ]; then
        echo "  already moved: ${f}"
    else
        echo "  missing: ${f} (skip)"
    fi
done

if [ "${moved}" -eq 0 ]; then
    echo "\nNothing to do."
    exit 0
fi

echo "\nMoved ${moved} file(s). Stage the changes with:"
echo "  git status"
echo "  git commit -m 'Move root audit artifacts into docs/audits/'"
