"""Roda o fluxo event-driven inteiro na sua maquina, sem AWS e sem credenciais.

    python3 local_simulator.py              200 equacoes, 10% invalidas
    python3 local_simulator.py 1000 25      1000 equacoes, 25% invalidas
    python3 local_simulator.py 50 0 --seed 7

O que ele faz e exercitar o MESMO codigo que roda na nuvem:

    generator.generate()   as equacoes que o producer publicaria
    worker.lambda_handler() o mesmo handler, recebendo eventos no formato
                            exato que o event source mapping da SQS entrega

O que ele substitui e apenas o transporte. As filas orders, results e a DLQ
viram listas em memoria, e o cliente SQS vira um dublê que registra o que teria
sido publicado. Nada sai da sua maquina, nenhuma credencial e lida, nenhum
recurso e criado.

Este arquivo fica na raiz de proposito: o archive_file empacota apenas o que
cada funcao precisa, entao nada daqui viaja para a nuvem.
"""

import argparse
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# src/shared entra no sys.path porque o worker importa de forma plana
# ("from calculator import calculate"): na Lambda os modulos ficam lado a lado
# na raiz do zip. Reproduzir isso e o que permite rodar o codigo de producao
# sem alterar uma linha.
sys.path.insert(0, os.path.join(ROOT, "src", "shared"))


def carregar(nome, *partes):
    """Carrega um modulo pelo caminho exato do arquivo.

    Um "import handler" simples seria ambiguo: o worker e o producer tem cada
    um o seu handler.py, e quem vencesse dependeria da ordem do sys.path — foi
    exatamente assim que a primeira versao deste script acabou chamando o
    handler errado.
    """
    caminho = os.path.join(ROOT, *partes)
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


generator = carregar("generator", "src", "handlers", "producer", "generator.py")
worker = carregar("worker", "src", "handlers", "worker", "handler.py")

RESULTS_QUEUE = "memoria://results"
DLQ_QUEUE = "memoria://orders-dlq"

BATCH_SIZE = 10


class FilaEmMemoria:
    """Dublê do cliente SQS: guarda o que o worker publicaria."""

    def __init__(self):
        self.mensagens = {RESULTS_QUEUE: [], DLQ_QUEUE: []}

    def send_message(self, **request):
        atributos = request.get("MessageAttributes") or {}

        self.mensagens[request["QueueUrl"]].append(
            {
                "body": request["MessageBody"],
                "motivo": atributos.get("RejectionReason", {}).get("StringValue"),
            }
        )


class Contexto:
    """O minimo do contexto da Lambda que o handler consulta."""

    aws_request_id = "simulacao-local"


def lotes(mensagens):
    """Divide em lotes de 10, como o event source mapping faz."""
    for inicio in range(0, len(mensagens), BATCH_SIZE):
        yield mensagens[inicio : inicio + BATCH_SIZE]


def evento_sqs(corpos, deslocamento):
    """Monta o evento no formato exato que a SQS entrega a Lambda."""
    return {
        "Records": [
            {
                "messageId": "msg-%04d" % (deslocamento + i),
                "receiptHandle": "handle-%04d" % (deslocamento + i),
                "body": corpo,
                "attributes": {
                    "ApproximateReceiveCount": "1",
                    "SentTimestamp": "1700000000000",
                },
                "messageAttributes": {},
                "eventSource": "aws:sqs",
                "awsRegion": "local",
            }
            for i, corpo in enumerate(corpos)
        ]
    }


def equacao(resultado):
    return "%gx² %s %gx %s %g = 0" % (
        resultado["a"],
        "-" if resultado["b"] < 0 else "+",
        abs(resultado["b"]),
        "-" if resultado["c"] < 0 else "+",
        abs(resultado["c"]),
    )


def raizes(resultado):
    if "roots" in resultado:
        return "sem raízes reais"

    return "x₁=%g  x₂=%g" % (resultado["x1"], resultado["x2"])


def numero(valor):
    return "%g" % valor if isinstance(valor, float) else str(valor)


def main():
    argumentos = argparse.ArgumentParser(
        description="Simula o fluxo event-driven localmente, sem AWS."
    )
    argumentos.add_argument("quantidade", nargs="?", type=int, default=200)
    argumentos.add_argument("invalidas", nargs="?", type=int, default=10,
                            help="porcentagem de mensagens invalidas (0 a 100)")
    argumentos.add_argument("--seed", type=int, default=None,
                            help="torna a carga reproduzivel")
    opcoes = argumentos.parse_args()

    if opcoes.quantidade < 1:
        argumentos.error("a quantidade deve ser no minimo 1")

    if not 0 <= opcoes.invalidas <= 100:
        argumentos.error("a porcentagem de invalidas deve estar entre 0 e 100")

    fila = FilaEmMemoria()

    # Injeta o dublê no lugar do cliente boto3. O handler nao sabe a diferenca:
    # ele so chama send_message.
    worker._sqs = fila
    worker.RESULTS_QUEUE_URL = RESULTS_QUEUE
    worker.DLQ_QUEUE_URL = DLQ_QUEUE

    print()
    print("Bhaskara Events — simulação local")
    print("=" * 60)
    print()
    print("  Nenhuma credencial é lida e nenhum recurso é criado.")
    print("  As filas são listas em memória; o código do worker é o de produção.")
    print()

    print("Producer")
    print("  gerando %s equações (%s%% inválidas%s)" % (
        opcoes.quantidade,
        opcoes.invalidas,
        ", seed %d" % opcoes.seed if opcoes.seed is not None else "",
    ))

    orders = list(
        generator.generate(opcoes.quantidade, opcoes.invalidas / 100, opcoes.seed)
    )

    total_lotes = (len(orders) + BATCH_SIZE - 1) // BATCH_SIZE
    print("  publicadas na fila orders: %d mensagens" % len(orders))
    print()

    print("Worker")
    print("  consumindo em %d lotes de até %d mensagens" % (total_lotes, BATCH_SIZE))

    # O worker escreve uma linha JSON por evento. Aqui elas seriam ruido, entao
    # a saida padrao e silenciada durante o processamento — o relatorio abaixo e
    # montado a partir do que efetivamente foi publicado nas filas.
    devolvidas = 0
    stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")

    try:
        for indice, lote in enumerate(lotes(orders)):
            resposta = worker.lambda_handler(
                evento_sqs(lote, indice * BATCH_SIZE), Contexto()
            )
            devolvidas += len(resposta["batchItemFailures"])
    finally:
        sys.stdout.close()
        sys.stdout = stdout

    resultados = fila.mensagens[RESULTS_QUEUE]
    recusadas = fila.mensagens[DLQ_QUEUE]

    print("  concluído")
    print()

    print("Resultado")
    print("  %-38s %6d" % ("publicadas na fila results", len(resultados)))
    print("  %-38s %6d" % ("recusadas, enviadas à DLQ", len(recusadas)))
    print("  %-38s %6d" % ("devolvidas para retry", devolvidas))
    print("  " + "-" * 45)
    print("  %-38s %6d" % ("total", len(resultados) + len(recusadas) + devolvidas))
    print()

    if resultados:
        print("Amostra dos resultados")
        for bruto in resultados[:5]:
            resultado = json.loads(bruto["body"])
            print("  %-28s →  %s" % (equacao(resultado), raizes(resultado)))
        if len(resultados) > 5:
            print("  … e mais %d" % (len(resultados) - 5))
        print()

    if recusadas:
        print("Amostra das recusas (na nuvem iriam à DLQ com o motivo anexado)")
        vistos = set()
        for mensagem in recusadas:
            motivo = mensagem["motivo"] or "sem motivo"
            if motivo in vistos:
                continue
            vistos.add(motivo)
            corpo = mensagem["body"]
            print("  %-30s →  %s" % (corpo[:30], motivo))
        print()

    print("Para ver o mesmo fluxo rodando na AWS, com filas e Lambdas de verdade:")
    print("  cd infra && terraform apply")
    print()


if __name__ == "__main__":
    main()
