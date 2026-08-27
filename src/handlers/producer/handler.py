"""Producer: recebe uma solicitacao HTTP e publica N equacoes na fila orders.

    POST /orders   {"quantity": 1000}

Uma unica requisicao vira milhares de mensagens. O trabalho pesado nao esta em
gerar as equacoes — esta em publica-las dentro do orcamento de tempo que a
requisicao tem.

Tres restricoes moldam a implementacao:

  SendMessageBatch    publica no maximo 10 mensagens por chamada, entao 1.000
                      mensagens sao 100 chamadas de rede sequenciais.

  API Gateway         o HTTP API corta a integracao em 30 segundos, sem
                      excecao. A funcao precisa responder antes disso.

  concorrencia        a conta tem 10 execucoes simultaneas no total,
                      compartilhadas com o worker. O producer e uma invocacao
                      por requisicao, e nao uma por mensagem — do contrario
                      ele consumiria sozinho toda a concorrencia disponivel e
                      travaria o proprio consumidor.

Dai o guarda de tempo: antes de cada lote a funcao consulta quanto lhe resta e,
se estiver perto do fim, para e responde com o que conseguiu publicar. Uma
resposta honesta de 700 mensagens publicadas e melhor do que um timeout, que
deixaria o cliente sem saber quantas mensagens foram parar na fila.
"""

import json
import os
import time

from api_auth import authorized
from generator import generate

ORDERS_QUEUE_URL = os.environ.get("ORDERS_QUEUE_URL", "")

# Chave esperada no header x-api-key. A comparacao vive em shared/api_auth.py,
# compartilhada com o handler de status.
#
# O que se protege aqui e a amplificacao de custo: uma requisicao sem chave
# ainda invoca esta funcao, mas gera ZERO mensagens. O abuso cai de 5.000
# mensagens para uma invocacao de ~2 ms, e o throttling do stage limita ate
# isso.
#
# A chave vive numa variavel de ambiente da funcao, o que a expoe a quem tiver
# lambda:GetFunctionConfiguration na conta. Para um laboratorio e adequado; um
# sistema real guardaria isso no Secrets Manager.
API_KEY = os.environ.get("API_KEY", "")

# Teto de mensagens por requisicao. O endpoint e publico, e sem um limite uma
# unica requisicao com quantity gigante viraria custo e uma fila que o worker
# levaria muito tempo para drenar.
MAX_QUANTITY = int(os.environ.get("MAX_QUANTITY", "5000"))

# Maximo aceito pela API do SendMessageBatch.
BATCH_SIZE = 10

# Folga reservada antes do timeout para montar e devolver a resposta. Sem ela,
# a funcao gastaria ate o ultimo milissegundo publicando e morreria no meio da
# serializacao da resposta.
TIME_SAFETY_MARGIN_MS = 3000

_sqs = None


class InvalidRequest(Exception):
    """Erro do cliente: vira 400, nao 500."""


def lambda_handler(event, context):
    started = time.monotonic()

    if not authorized(event, API_KEY):
        log(event="request_unauthorized")
        return response(403, {"error": "Chave de API ausente ou invalida."})

    try:
        quantity, invalid_ratio, seed = parse_request(event)
    except InvalidRequest as error:
        log(event="request_rejected", reason=str(error))
        return response(400, {"error": str(error)})

    log(
        event="generation_requested",
        request_id=getattr(context, "aws_request_id", None),
        quantity=quantity,
        invalid_ratio=invalid_ratio,
    )

    published, failed, batches, truncated = publish(
        generate(quantity, invalid_ratio, seed), quantity, context
    )

    elapsed_ms = int((time.monotonic() - started) * 1000)

    body = {
        "requested": quantity,
        "published": published,
        "batches": batches,
        "elapsed_ms": elapsed_ms,
    }

    if failed:
        body["failed"] = failed

    if truncated:
        body["truncated"] = True
        body["detail"] = (
            "A funcao parou antes do timeout. Reenvie a diferenca em uma nova "
            "requisicao ou divida a carga em solicitacoes menores."
        )

    log(event="generation_finished", **body)

    # 202 e nao 200: as mensagens foram aceitas para processamento, que
    # acontece depois e em outro lugar. O resultado do calculo nao esta nesta
    # resposta e nem poderia estar.
    return response(202, body)


def parse_request(event):
    body = event.get("body")

    if not body:
        raise InvalidRequest("Corpo da requisicao ausente. Envie {\"quantity\": N}.")

    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        raise InvalidRequest("Corpo da requisicao nao e JSON valido.") from None

    if not isinstance(payload, dict):
        raise InvalidRequest("Corpo da requisicao deve ser um objeto JSON.")

    return (
        quantity_of(payload),
        invalid_ratio_of(payload),
        payload.get("seed"),
    )


def quantity_of(payload):
    if "quantity" not in payload:
        raise InvalidRequest("O campo 'quantity' e obrigatorio.")

    quantity = payload["quantity"]

    # isinstance(True, int) e True: sem excluir bool, {"quantity": true}
    # publicaria uma mensagem em vez de recusar a requisicao.
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidRequest("O campo 'quantity' deve ser um numero inteiro.")

    if quantity < 1:
        raise InvalidRequest("O campo 'quantity' deve ser no minimo 1.")

    if quantity > MAX_QUANTITY:
        raise InvalidRequest(
            "O campo 'quantity' deve ser no maximo %d." % MAX_QUANTITY
        )

    return quantity


def invalid_ratio_of(payload):
    """Proporcao de mensagens propositalmente invalidas, entre 0 e 1.

    Opcional e zero por padrao: gerar lixo sem que ninguem tenha pedido seria
    surpreendente. Existe para alimentar a DLQ na demonstracao, quando se quer
    ver o caminho de erro funcionando.
    """
    ratio = payload.get("invalid_ratio", 0)

    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise InvalidRequest("O campo 'invalid_ratio' deve ser um numero.")

    if not 0 <= ratio <= 1:
        raise InvalidRequest("O campo 'invalid_ratio' deve estar entre 0 e 1.")

    return float(ratio)


def publish(bodies, quantity, context):
    """Publica em lotes de 10, parando se o tempo acabar.

    Devolve (publicadas, falhas, lotes, truncado).
    """
    published = 0
    failed = 0
    batches = 0
    truncated = False

    for batch in chunks(bodies, BATCH_SIZE):
        if out_of_time(context):
            truncated = True
            break

        succeeded, rejected = send(batch, batches)

        published += succeeded
        failed += rejected
        batches += 1

    return published, failed, batches, truncated


def send(batch, batch_number):
    entries = [
        # O Id so precisa ser unico dentro do lote — nao viaja para a fila.
        {"Id": "m%d" % index, "MessageBody": body}
        for index, body in enumerate(batch)
    ]

    result = sqs().send_message_batch(QueueUrl=ORDERS_QUEUE_URL, Entries=entries)

    rejected = result.get("Failed") or []

    if rejected:
        # Falha parcial do lote: a SQS aceita algumas entradas e recusa outras.
        # Registrar em vez de levantar — perder 3 de 1.000 mensagens nao
        # justifica descartar as 997 que ja foram publicadas.
        log(
            event="batch_partially_rejected",
            batch=batch_number,
            failed=len(rejected),
            first_error=rejected[0].get("Message"),
        )

    return len(result.get("Successful") or []), len(rejected)


def out_of_time(context):
    if context is None:
        return False

    remaining = getattr(context, "get_remaining_time_in_millis", None)

    if remaining is None:
        return False

    return remaining() < TIME_SAFETY_MARGIN_MS


def chunks(iterable, size):
    batch = []

    for item in iterable:
        batch.append(item)

        if len(batch) == size:
            yield batch
            batch = []

    if batch:
        yield batch


def sqs():
    global _sqs

    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs")

    return _sqs


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False, allow_nan=False),
    }


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, allow_nan=False))
