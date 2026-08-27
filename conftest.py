"""Reproduz nos testes o sys.path que a Lambda monta em runtime.

No pacote publicado, calculator.py e handler.py ficam lado a lado na raiz do
zip — por isso o handler importa "from calculator import calculate", sem
prefixo de pacote. Inserir src/shared aqui faz os testes exercitarem exatamente
o mesmo import que roda na nuvem, em vez de um caminho que so existe no repo.

A raiz do projeto entra junto para que "src.handlers.<nome>.handler" resolva
qualquer que seja a forma de invocar o pytest ("pytest" ou "python -m pytest").
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

for path in (ROOT, os.path.join(ROOT, "src", "shared")):
    if path not in sys.path:
        sys.path.insert(0, path)
