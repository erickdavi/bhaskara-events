#!/usr/bin/env bash
#
# Bootstrap do ambiente e execucao local. Nao precisa de credenciais AWS.
#
#   ./run.sh                 roda os testes
#   ./run.sh demo            simula o fluxo event-driven (200 equacoes)
#   ./run.sh demo 1000 25    1000 equacoes, 25% invalidas
#
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "Criando ambiente virtual em $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"

# O simulador usa apenas a biblioteca padrao; o venv existe para o pytest.
if [ "${1:-}" = "demo" ]; then
  shift
  exec "$PYTHON" local_simulator.py "$@"
fi

if ! "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then
  echo "Instalando dependencias ..."
  "$PYTHON" -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

exec "$PYTHON" -m pytest "$@"
