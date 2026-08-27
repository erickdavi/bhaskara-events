"""Worker do fluxo event-driven: consome mensagens da fila orders.

Ciclo 1 — o worker ainda nao calcula nada. Ele existe para provar o caminho
SQS -> Lambda ponta a ponta: recebe o lote, registra cada mensagem e confirma o
consumo. O calculo de Bhaskara entra no Ciclo 2, reaproveitando calculator.py.

O log sai como uma linha JSON por evento em vez de texto livre. A razao e a
validacao: o criterio de aceitacao do ciclo compara o messageId devolvido pelo
send-message com o que aparece no log, e o Ciclo 6 vai ler esses mesmos campos
para montar o painel. Texto livre exigiria parser; JSON o CloudWatch Logs
Insights ja consulta por campo.
"""

import json

# Corpo truncado no log. Em Ciclo 1 as mensagens sao minusculas, mas o producer
# do Ciclo 4 gera milhares delas — e ingestao de log e o unico item deste
# projeto que sai do free tier se alguem publicar payloads grandes por engano.
MAX_LOGGED_BODY = 512


def lambda_handler(event, context):
    records = event.get("Records") or []
    request_id = getattr(context, "aws_request_id", None)

    log(
        event="batch_received",
        request_id=request_id,
        batch_size=len(records),
    )

    for record in records:
        log(
            event="message_received",
            request_id=request_id,
            message_id=record.get("messageId"),
            receive_count=receive_count(record),
            body=truncate(record.get("body")),
        )

    # Contrato ReportBatchItemFailures, declarado no event source mapping.
    # Adotado desde o Ciclo 1 de proposito: e o que permite, no Ciclo 3, mandar
    # uma unica mensagem ruim para a DLQ sem arrastar o lote inteiro junto.
    # Introduzir esse contrato depois obrigaria a reescrever handler e testes.
    return {"batchItemFailures": []}


def receive_count(record):
    """Quantas vezes esta mensagem ja foi entregue.

    A SQS envia o valor como string. Convertido para int porque o criterio de
    aceitacao do ciclo exige receive_count == 1 (consumo na primeira entrega,
    sem reprocessamento silencioso).
    """
    raw = (record.get("attributes") or {}).get("ApproximateReceiveCount")

    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def truncate(body):
    if body is None or len(body) <= MAX_LOGGED_BODY:
        return body

    return body[:MAX_LOGGED_BODY] + "...[truncado]"


def log(**fields):
    # print em vez de logging: o runtime da Lambda prefixa as linhas do logging
    # com nivel, timestamp e requestId, o que quebraria o JSON puro que o
    # CloudWatch Logs Insights consulta por campo.
    print(json.dumps(fields, ensure_ascii=False, allow_nan=False))
