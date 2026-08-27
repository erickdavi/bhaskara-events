"""Teste de fumaca do simulador local.

O simulador e o caminho que o avaliador percorre primeiro — ele precisa
funcionar num clone limpo, sem credenciais. Um teste que o executa de verdade
impede que ele apodreca silenciosamente quando o handler mudar.

Roda como subprocesso de proposito: e assim que a pessoa o executa, e e o unico
jeito de exercitar o carregamento de modulo por caminho, que ja errou uma vez
(o "import handler" pegava o handler do producer em vez do worker).
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def simular(*argumentos):
    resultado = subprocess.run(
        [sys.executable, os.path.join(ROOT, "local_simulator.py"), *argumentos],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert resultado.returncode == 0, resultado.stderr

    return resultado.stdout


def contagem(saida, rotulo):
    encontrado = re.search(rotulo + r"\s+(\d+)", saida)

    assert encontrado, "rotulo '%s' ausente na saida" % rotulo

    return int(encontrado.group(1))


def test_a_conta_fecha():
    saida = simular("120", "10", "--seed", "1")

    publicadas = contagem(saida, "publicadas na fila results")
    recusadas = contagem(saida, "recusadas, enviadas à DLQ")
    retry = contagem(saida, "devolvidas para retry")

    assert publicadas + recusadas + retry == 120
    assert contagem(saida, "total") == 120


def test_sem_invalidas_nada_vai_para_a_dlq():
    saida = simular("60", "0", "--seed", "2")

    assert contagem(saida, "publicadas na fila results") == 60
    assert contagem(saida, "recusadas, enviadas à DLQ") == 0


def test_tudo_invalido_nao_produz_resultado():
    saida = simular("40", "100", "--seed", "3")

    assert contagem(saida, "publicadas na fila results") == 0
    assert contagem(saida, "recusadas, enviadas à DLQ") == 40


def test_nenhuma_mensagem_e_devolvida_para_retry():
    # Retry so acontece em falha inesperada. Numa simulacao sem rede, qualquer
    # mensagem devolvida denunciaria um bug no worker.
    saida = simular("100", "20", "--seed", "4")

    assert contagem(saida, "devolvidas para retry") == 0


def test_a_mesma_seed_produz_a_mesma_carga():
    assert simular("30", "15", "--seed", "9") == simular("30", "15", "--seed", "9")


def test_a_saida_mostra_equacoes_e_motivos():
    saida = simular("100", "20", "--seed", "5")

    assert "Amostra dos resultados" in saida
    assert "= 0" in saida
    assert "Amostra das recusas" in saida


def test_quantidade_invalida_e_recusada():
    resultado = subprocess.run(
        [sys.executable, os.path.join(ROOT, "local_simulator.py"), "0"],
        capture_output=True, text=True, cwd=ROOT,
    )

    assert resultado.returncode != 0
