#!/usr/bin/env bash
# Turn a fresh copy of agenas-python-template into a new service.
#
#   cp -r agenas-python-template ../my-service && cd ../my-service
#   bash scripts/init_project.sh my-service MYSVC
#
# Arguments:
#   service-name  kebab-case, becomes project name, image name and package (my_service)
#   ENV_PREFIX    UPPER_CASE, environment variables become ENV_PREFIX_DATABASE_URL ...
set -euo pipefail

if [ $# -ne 2 ]; then
    echo "usage: $0 <service-name> <ENV_PREFIX>" >&2
    exit 1
fi

name="$1"
prefix="$2"
package="${name//-/_}"

[[ "$name" =~ ^[a-z][a-z0-9-]*[a-z0-9]$ ]] || { echo "service-name must be kebab-case: $name" >&2; exit 1; }
[[ "$prefix" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "ENV_PREFIX must be UPPER_CASE: $prefix" >&2; exit 1; }
[ -d src/agenas_service ] || { echo "src/agenas_service not found: run this once, from the root of a fresh copy" >&2; exit 1; }

git mv src/agenas_service "src/$package" 2>/dev/null || mv src/agenas_service "src/$package"

grep -rlI --exclude-dir=.git --exclude-dir=.venv --exclude=uv.lock --exclude=init_project.sh \
    -e agenas_service -e agenas-service -e TEMPLATE_ . \
    | while read -r file; do
        sed -i \
            -e "s/agenas_service/$package/g" \
            -e "s/agenas-service/$name/g" \
            -e "s/TEMPLATE_/${prefix}_/g" \
            "$file"
    done

# CLAUDE.md stays local in every AGENAS repo: never committed.
grep -qx "CLAUDE.md" .gitignore || printf '\n# Local only, never committed\nCLAUDE.md\n' >> .gitignore

rm -rf .venv
uv lock
rm -- "$0"

cat <<MSG
Done: $name (package $package, env prefix ${prefix}_).

Next steps:
  1. sonar-project.properties: replace CHANGEME with the SonarQube project key.
  2. migrations/env.py: change MIGRATION_LOCK_ID to a value unique to this service.
  3. uv sync && just test && docker compose up --build
  4. Replace the example "items" domain (model, migration, router, service, tests).
  5. Fill in docs/DEPLOY_HANDOVER.md before the first deploy.
MSG
