#!/usr/bin/env bash
# Shim de despliegue del BE (story 4.1): invoca el instalador Python
# idempotente `wakemeup-install` con `uv`. Debe ejecutarse como root en el
# host de destino (nunca en la máquina de desarrollo). Ver deploy/README.md.
#
#   sudo backend/deploy/install.sh [--port 8080] [--app-dir /opt/wakemeup/backend]
#
# La lógica real vive en `src/wakemeup/install.py` (testeable); este script
# solo resuelve `uv` y pasa los argumentos.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: ejecuta el instalador como root (sudo $0)" >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
elif [[ -x /usr/local/bin/uv ]]; then
  UV=/usr/local/bin/uv
else
  echo "error: 'uv' no encontrado; instálalo (https://docs.astral.sh/uv/) o pasa --uv-path" >&2
  exit 1
fi

exec "$UV" run --project "${BACKEND_DIR}" wakemeup-install "$@"
