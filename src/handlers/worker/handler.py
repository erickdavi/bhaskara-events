"""Worker do fluxo event-driven: consome orders, calcula e publica em results.

Ciclo 3 — o resultado deixa de existir apenas no log e passa a ser publicado na
fila results, e as mensagens problematicas ganham destino na DLQ.

    orders ──► worker ──► results
                 │
                 └── falha ──► DLQ

Ha dois caminhos de falha, e eles sao deliberadamente diferentes. A separacao
vem da classificacao feita no Ciclo 2:

  permanente   InvalidMessage / ValueError — JSON malformado, coeficiente
               ausente, a = 0, overflow. Reentregar nao muda o desfecho, entao
               o worker publica direto na DLQ e confirma a mensagem. Reentregar
               tres vezes uma mensagem que nunca vai funcionar so gastaria
               invocacoes e atrasaria em minutos a chegada na DLQ. De quebra, a
               mensagem chega la com o motivo da recusa anexado — coisa que o
               redrive nativo nao faz.

  inesperada   qualquer outra excecao: bug, indisponibilidade momentanea da
               SQS, permissao revogada. Aqui reentregar pode salvar a mensagem,
               entao o worker devolve o messageId em batchItemFailures e deixa
               o mecanismo nativo agir — a SQS reentrega ate maxReceiveCount e
               so entao move para a DLQ pelo redrive_policy.

O log sai como uma linha JSON por evento: a validacao dos ciclos compara campos
e o painel do Ciclo 6 vai ler esses mesmos campos. Texto livre exigiria parser;
JSON o CloudWatch Logs Insights ja consulta por campo.
"""

import json
import math
import os

from calculator import calculate

# Corpo truncado no log. As mensagens deste projeto sao minusculas, mas o
# producer do Ciclo 4 gera milhares delas — e ingestao de log e o unico item
# que sai do free tier se alguem publicar payloads grandes.
MAX_LOGGED_BODY = 512

COEFFICIENTS = ("a", "b", "c")

RESULTS_QUEUE_URL = os.environ.get("RESULTS_QUEUE_URL", "")
DLQ_QUEUE_URL = os.environ.get("DLQ_QUEUE_URL", "")

# Cliente criado sob demanda, e nao no import, por dois motivos: mantem o cold
# start fora do caminho de quem so importa o modulo, e permite que os testes
# substituam este objeto sem precisar do boto3 instalado localmente.
_sqs = None


class InvalidMessage(Exception):
    """Erro permanente: a mensagem nunca vai funcionar, reentregar nao ajuda."""


def lambda_handler(event, context):
    records = event.get("Records") or []
    request_id = getattr(context, "aws_request_id", None)

    log(
        event="batch_received",
        request_id=request_id,
        batch_size=len(records),
    )

    failures = []

    for record in records:
        message_id = record.get("messageId")

        log(
            event="message_received",
            request_id=request_id,
            message_id=message_id,
            receive_count=receive_count(record),
            body=truncate(record.get("body")),
        )

        try:
            process(record, request_id)
        except Exception as error:  # noqa: BLE001 - ver docstring do modulo
            # Erro inesperado: devolver o messageId faz a SQS reentregar apenas
            # esta mensagem. As demais do lote ja foram confirmadas e nao serao
            # reprocessadas — e para isso que serve o ReportBatchItemFailures.
            log(
                event="message_failed",
                request_id=request_id,
                message_id=message_id,
                receive_count=receive_count(record),
                error_type=type(error).__name__,
                error=str(error),
            )
            failures.append({"itemIdentifier": message_id})

    if failures:
        log(
            event="batch_partial_failure",
            request_id=request_id,
            failed=len(failures),
            total=len(records),
        )

    return {"batchItemFailures": failures}


def process(record, request_id):
    """Processa uma mensagem.

    Erros permanentes sao resolvidos aqui dentro (vao para a DLQ e a mensagem e
    confirmada). Qualquer outra excecao sobe para o lambda_handler, que a
    transforma em retry.
    """
    message_id = record.get("messageId")

    try:
        a, b, c = parse(record.get("body"))
        result = calculate(a, b, c)
    except (InvalidMessage, ValueError) as error:
        reject(record, request_id, str(error))
        return

    publish(
        RESULTS_QUEUE_URL,
        dict(result, message_id=message_id),
    )

    log(
        event="message_processed",
        request_id=request_id,
        message_id=message_id,
        **result,
    )


def reject(record, request_id, reason):
    """Manda a mensagem para a DLQ com o motivo anexado.

    O corpo original vai intacto: o objetivo da DLQ e permitir inspecionar e,
    se for o caso, reprocessar exatamente o que chegou. O motivo viaja como
    message attribute justamente para nao contaminar o payload.

    Se a publicacao falhar, a excecao sobe e a mensagem vira retry — o que e
    correto: falhar em arquivar nao pode virar perda silenciosa.
    """
    publish(
        DLQ_QUEUE_URL,
        record.get("body"),
        attributes={
            "RejectionReason": {"DataType": "String", "StringValue": reason[:256]},
            "SourceMessageId": {
                "DataType": "String",
                "StringValue": str(record.get("messageId")),
            },
        },
    )

    log(
        event="message_rejected",
        request_id=request_id,
        message_id=record.get("messageId"),
        reason=reason,
    )


def publish(queue_url, body, attributes=None):
    if not queue_url:
        raise RuntimeError("Fila de destino nao configurada no ambiente da funcao.")

    payload = body if isinstance(body, str) else json.dumps(body, allow_nan=False)

    request = {"QueueUrl": queue_url, "MessageBody": payload}

    if attributes:
        request["MessageAttributes"] = attributes

    sqs().send_message(**request)


def sqs():
    global _sqs

    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs")

    return _sqs


def parse(body):
    """Valida o corpo da mensagem e devolve os tres coeficientes.

    A validacao e estrita: o corpo e JSON, entao numero chega como numero. Uma
    string "1" no lugar de 1 indica um producer com defeito, e aceita-la
    silenciosamente esconderia esse defeito ate ele aparecer em outro lugar.
    """
    if body is None:
        raise InvalidMessage("Corpo da mensagem vazio.")

    try:
        # parse_constant intercepta os literais NaN, Infinity e -Infinity, que
        # o json.loads aceita por padrao apesar de a RFC 8259 nao os permitir.
        # Sem isso eles atravessariam a validacao de tipo abaixo — sao float.
        payload = json.loads(body, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise InvalidMessage("Corpo da mensagem nao e JSON valido: %s" % error) from None

    if not isinstance(payload, dict):
        raise InvalidMessage("Corpo da mensagem deve ser um objeto JSON.")

    missing = [name for name in COEFFICIENTS if name not in payload]

    if missing:
        raise InvalidMessage("Coeficientes ausentes: %s." % ", ".join(missing))

    return tuple(coefficient(payload[name], name) for name in COEFFICIENTS)


def coefficient(value, name):
    # isinstance(True, int) e True em Python: sem excluir bool explicitamente,
    # {"a": true} viraria a = 1 e a equacao seria resolvida com um coeficiente
    # que o remetente nunca quis enviar.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidMessage(
            "O coeficiente '%s' deve ser um numero, e nao %s."
            % (name, type(value).__name__)
        )

    if not math.isfinite(value):
        raise InvalidMessage("O coeficiente '%s' deve ser um numero finito." % name)

    return value


def reject_constant(name):
    raise InvalidMessage("Os coeficientes nao aceitam o literal %s." % name)


def receive_count(record):
    """Quantas vezes esta mensagem ja foi entregue.

    A SQS envia o valor como string. E este numero que mostra o retry
    acontecendo: 1 na primeira entrega, 2 e 3 nas seguintes, e entao a mensagem
    vai para a DLQ pelo redrive_policy.
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
