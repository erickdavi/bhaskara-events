"""Reproduz nos testes o sys.path que a Lambda monta em runtime.

Cada funcao publicada recebe um zip proprio com os seus modulos na raiz — o
worker leva handler.py e calculator.py lado a lado, o producer leva handler.py
e generator.py. Por isso os imports dentro do codigo sao planos
("from calculator import calculate", "from generator import generate"), sem
prefixo de pacote.

Inserir aqui o diretorio compartilhado e o de cada handler faz os testes
exercitarem exatamente os mesmos imports que rodam na nuvem, em vez de um
caminho que so existe no repositorio.

A raiz do projeto entra junto para que "src.handlers.<nome>.handler" resolva
qualquer que seja a forma de invocar o pytest ("pytest" ou "python -m pytest").
Os dois handlers se chamam handler.py, entao os testes sempre os importam por
esse caminho completo — nunca por "import handler", que seria ambiguo.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
HANDLERS = os.path.join(ROOT, "src", "handlers")

PATHS = [ROOT, os.path.join(ROOT, "src", "shared")]

if os.path.isdir(HANDLERS):
    PATHS += [
        os.path.join(HANDLERS, name)
        for name in sorted(os.listdir(HANDLERS))
        if os.path.isdir(os.path.join(HANDLERS, name))
    ]

for path in PATHS:
    if path not in sys.path:
        sys.path.insert(0, path)
