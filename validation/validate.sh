#!/usr/bin/env bash
# Validazione dell'anonimizzazione sul dataset sintetico (validation/README.md).
#
# Uso: validation/validate.sh [--config FILE] [--profilo NOME]... [--tipo TIPO]...
#                             [--difficolta D]... [--documento ID]... [--url URL]
#                             [--concorrenza N] [--riprendi DIR] [--output DIR]
#
# Il servizio deve essere avviato (`just up`) con ANONPIP_ALLOW_TRANSCRIPT=true.
# Per i profili con mode: pseudonym esporta la chiave in ANONVAL_KEY.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv non trovato: installalo (https://docs.astral.sh/uv/)" >&2
    exit 2
fi
if [[ ! -f "$here/dataset/manifest.json" ]]; then
    echo "Dataset non generato: esegui prima \`just validation-dataset\`" >&2
    exit 2
fi

cd "$root"
PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" exec uv run python -m anonval.run "$@"
