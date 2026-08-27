#!/usr/bin/env bash
#
# Bootstrap do ambiente de desenvolvimento e execucao dos testes.
#
#   ./run.sh          cria o venv, instala as dependencias e roda os testes
#
# Nao precisa de credenciais AWS: os testes sao todos locais, exercitando os
# handlers com eventos sinteticos.
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "Criando ambiente virtual em $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"

if ! "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then
  echo "Instalando dependencias ..."
  "$PYTHON" -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

exec "$PYTHON" -m pytest "$@"
