#!/bin/zsh
set -e
cd "${0:A:h}"
ATLAS_BUNDLE="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies"
ATLAS_PYTHON="${ATLAS_PYTHON:-$ATLAS_BUNDLE/python/bin/python3}"
ATLAS_NODE_DIR="${ATLAS_NODE_DIR:-$ATLAS_BUNDLE/node/bin}"
ATLAS_PNPM="${ATLAS_PNPM:-$ATLAS_BUNDLE/bin/fallback/pnpm}"
export PATH="$ATLAS_NODE_DIR:$PATH"
if [[ ! -d .venv ]]; then "$ATLAS_PYTHON" -m venv .venv; fi
.venv/bin/pip install -r requirements-lock.txt
cd frontend
"$ATLAS_PNPM" install --frozen-lockfile
"$ATLAS_PNPM" run build
cd ..
print 'Setup finished. Open Start.command to run Anesthesia Atlas.'
