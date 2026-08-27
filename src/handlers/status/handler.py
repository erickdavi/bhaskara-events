"""Status: metricas do processamento, para o painel do Ciclo 6.

    GET /status
    GET /status?events=50&since=<epoch_ms>&dlq=5

O caminho rapido sao tres chamadas de GetQueueAttributes, uma por fila. Os
eventos e a espiada na DLQ so acontecem quando pedidos, porque custam uma
chamada a mais cada e o painel vai consultar este endpoint a cada poucos
segundos.

De onde vem cada numero
-----------------------

    queued      profundidade da orders (mensagens visiveis)
    in_flight   mensagens da orders entregues e ainda nao confirmadas
    succeeded   profundidade da results
    failed      profundidade da DLQ

succeeded e failed sao contadores acumulados, e nao por execucao: nada consome
a results nem a DLQ, entao elas funcionam como o placar do que ja aconteceu
desde a ultima limpeza. O painel tira uma leitura no inicio da carga e subtrai —
manter o calculo do lado do cliente e o que permite a este endpoint ser sem
estado e responder em ~150 ms.

A alternativa seria agregar contadores em DynamoDB, o que daria numeros por
execucao e historico, ao custo de mais um componente e de uma escrita por
mensagem processada. Nao se justifica enquanto a profundidade das filas
responde a pergunta.
"""

import json
import os
import time

from api_auth import authorized

ORDERS_QUEUE_URL = os.environ.get("ORDERS_QUEUE_URL", "")
RESULTS_QUEUE_URL = os.environ.get("RESULTS_QUEUE_URL", "")
DLQ_QUEUE_URL = os.environ.get("DLQ_QUEUE_URL", "")
WORKER_LOG_GROUP = os.environ.get("WORKER_LOG_GROUP", "")
API_KEY = os.environ.get("API_KEY", "")

# Teto de eventos por chamada. O painel pagina pelo cursor; sem limite, uma
# consulta feita depois de uma carga de 5.000 mensagens tentaria devolver
# milhares de linhas numa resposta so.
MAX_EVENTS = 100

# Maximo aceito pelo ReceiveMessage.
MAX_DLQ_PEEK = 10

# Janela padrao dos eventos quando o cliente nao manda cursor.
DEFAULT_EVENTS_WINDOW_MS = 60_000

# Apenas os desfechos: sao eles que o painel conta e exibe. Filtrar aqui, e nao
# depois de receber, faz o trabalho acontecer no CloudWatch em vez de na Lambda.
OUTCOME_FILTER = (
    '{ $.event = "message_processed" || $.event = "message_rejected" '
    '|| $.event = "message_failed" }'
)

_sqs = None
_logs = None


class InvalidRequest(Exception):
    """Erro do cliente: vira 400, nao 500."""


def lambda_handler(event, context):
    if not authorized(event, API_KEY):
        return response(403, {"error": "Chave de API ausente ou invalida."})

    try:
        events_limit, since, dlq_limit = parse_query(event)
    except InvalidRequest as error:
        return response(400, {"error": str(error)})

    now_ms = int(time.time() * 1000)

    body = counters()
    body["checked_at"] = now_ms

    if events_limit:
        body["events"], body["events_cursor"] = recent_events(
            events_limit, since if since is not None else now_ms - DEFAULT_EVENTS_WINDOW_MS
        )

    if dlq_limit:
        body["dlq_messages"] = peek_dlq(dlq_limit)

    return response(200, body)


def counters():
    queued, in_flight = depth(ORDERS_QUEUE_URL, with_in_flight=True)
    succeeded, _ = depth(RESULTS_QUEUE_URL)
    failed, _ = depth(DLQ_QUEUE_URL)

    return {
        "queued": queued,
        "in_flight": in_flight,
        "succeeded": succeeded,
        "failed": failed,
        # Derivado, e nao medido: o painel mostra "processadas" e nao deveria
        # ter de somar duas colunas para saber quantas ja sairam da fila.
        "processed": succeeded + failed,
    }


def depth(queue_url, with_in_flight=False):
    names = ["ApproximateNumberOfMessages"]

    if with_in_flight:
        names.append("ApproximateNumberOfMessagesNotVisible")

    attributes = sqs().get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=names
    ).get("Attributes", {})

    return (
        int(attributes.get("ApproximateNumberOfMessages", 0)),
        int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
    )


def recent_events(limit, since_ms):
    """Devolve os desfechos registrados apos `since_ms`, do mais antigo ao mais novo.

    A ordem crescente e o cursor existem para o painel: ele guarda o cursor da
    ultima consulta e pede so o que veio depois, montando um fluxo continuo em
    vez de rebuscar a mesma janela a cada poll.
    """
    if not WORKER_LOG_GROUP:
        return [], since_ms

    found = logs().filter_log_events(
        logGroupName=WORKER_LOG_GROUP,
        # +1 para nao repetir o ultimo evento ja entregue na consulta anterior.
        startTime=since_ms + 1,
        filterPattern=OUTCOME_FILTER,
        limit=limit,
    ).get("events", [])

    parsed = []
    cursor = since_ms

    for entry in found:
        cursor = max(cursor, entry.get("timestamp", cursor))

        try:
            message = json.loads(entry.get("message", ""))
        except json.JSONDecodeError:
            # Linha que nao e JSON nao interessa ao painel — e o filtro do
            # CloudWatch ja deveria te-la excluido.
            continue

        message["timestamp"] = entry.get("timestamp")
        parsed.append(message)

    return parsed, cursor


def peek_dlq(limit):
    """Espia a DLQ sem consumir nada.

    VisibilityTimeout=0 devolve as mensagens a visibilidade imediatamente, entao
    a espiada nao esconde nada de ninguem nem impede o reprocessamento.

    A SQS amostra os servidores que guardam a fila, entao uma chamada pode nao
    devolver todas as mensagens nem sempre as mesmas. Serve para mostrar
    exemplos no painel, e nao para inventariar a DLQ — para isso existe o
    contador `failed`.
    """
    if not DLQ_QUEUE_URL:
        return []

    messages = sqs().receive_message(
        QueueUrl=DLQ_QUEUE_URL,
        MaxNumberOfMessages=limit,
        VisibilityTimeout=0,
        WaitTimeSeconds=0,
        MessageAttributeNames=["All"],
    ).get("Messages", [])

    return [
        {
            "body": message.get("Body"),
            "reason": attribute(message, "RejectionReason"),
            "source_message_id": attribute(message, "SourceMessageId"),
        }
        for message in messages
    ]


def attribute(message, name):
    """Le um message attribute, tolerando a ausencia.

    Mensagens que chegaram pelo redrive nativo nao tem estes atributos: a SQS
    move o payload original e nao sabe dizer por que ele falhou. Devolver None
    e a resposta correta, e o painel a exibe como "sem motivo registrado".
    """
    return (message.get("MessageAttributes") or {}).get(name, {}).get("StringValue")


def parse_query(event):
    params = event.get("queryStringParameters") or {}

    return (
        integer(params, "events", 0, MAX_EVENTS),
        integer(params, "since", 0, None, default=None),
        integer(params, "dlq", 0, MAX_DLQ_PEEK),
    )


def integer(params, name, minimum, maximum, default=0):
    raw = params.get(name)

    if raw is None or raw == "":
        return default

    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise InvalidRequest("O parametro '%s' deve ser um numero inteiro." % name) from None

    if value < minimum:
        raise InvalidRequest("O parametro '%s' deve ser no minimo %d." % (name, minimum))

    if maximum is not None and value > maximum:
        raise InvalidRequest("O parametro '%s' deve ser no maximo %d." % (name, maximum))

    return value


def sqs():
    global _sqs

    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs")

    return _sqs


def logs():
    global _logs

    if _logs is None:
        import boto3

        _logs = boto3.client("logs")

    return _logs


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # O painel consulta este endpoint a cada poucos segundos: sem isto,
            # um cache intermediario poderia servir numeros velhos.
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False, allow_nan=False),
    }
