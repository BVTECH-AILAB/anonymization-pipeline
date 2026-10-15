#!/usr/bin/env sh
# Fails when a runtime dependency (direct or transitive, dev group excluded) has a licence
# outside the permissive allow-list. Used by `just licences` and by the CI lint job.
#
# MPL-2.0 is accepted only for the packages listed in MPL_PACKAGES, used unmodified
# (file-level copyleft): a new MPL dependency fails until it is reviewed and added here.
set -eu

ALLOWED="MIT;BSD;Apache;ISC;PSF;Python Software Foundation;HPND;MIT-CMU;Unlicense;CC0;0BSD;Zlib"
MPL_PACKAGES="certifi tqdm"

packages=$(uv export --no-dev --no-emit-project --no-hashes --format requirements-txt --quiet \
    | grep -E '^[A-Za-z0-9]' | cut -d'=' -f1 | cut -d';' -f1 | cut -d'[' -f1)

# shellcheck disable=SC2086
uv run pip-licenses --from=mixed --partial-match --allow-only="$ALLOWED" \
    --packages $packages --ignore-packages $MPL_PACKAGES
