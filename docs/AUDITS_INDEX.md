# Audits & Working Documents

This index catalogs the audit/analysis artifacts that currently live at the repo
root. They were left at the root during active development; this file gives them
a navigation home until the SOLID reorganization moves them into `docs/`.

Do not delete these files without checking the SOLID_REORGANIZATION_PLAN first.

## C standards & language audits

| File | Type | What it covers |
| --- | --- | --- |
| `c_standards_timeline.svg` | diagram | Timeline of C language standards |
| `language_robustness_comparison.html` | report | Cross-language robustness comparison |
| `teb_robust_replacements.html` | report | Robust-replacement audit for the original `teb` codebase |
| `gaussian_file_sorter.html` | analysis | File-sorting analysis (Gaussian) |

## TEB2 reality / architecture audits

| File | Type | What it covers |
| --- | --- | --- |
| `teb2_1970s_audit.html` | audit | 1970s-era constraints applied to teb2 |
| `teb2_architecture.html` | architecture | High-level teb2 architecture |
| `teb2_complete_file_relations.html` | reference | Complete file-relations matrix |
| `teb2_enigma_audit.html` | audit | Enigma-influence audit |
| `teb2_file_schema.html` | reference | File schema |
| `teb2_ibm701_reality.html` | audit | IBM 701 era-realism audit |
| `teb2_io_schema.html` | reference | I/O schema |
| `teb2_knr_replacements.html` | audit | K&R-style replacement audit |
| `teb2_org_files.svg` | diagram | Organizational file map |
| `teb2_org_overview.svg` | diagram | Organizational overview |

## Loose plain-text working notes

| File | What it covers |
| --- | --- |
| `analysis.txt` | Free-form analysis notes |
| `arch.txt` | Free-form architecture notes |

## Why these are still at the root

Moving them via the GitHub API requires read+write+delete per file (~30 API
calls for this set), which is wasteful for a cosmetic change. The
`scripts/reorganize.sh` script (added alongside this doc) does the move with
`git mv` in one local commit; run it before merging the SOLID reorganization
branch.
