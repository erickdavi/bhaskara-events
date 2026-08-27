"""Worker do fluxo event-driven: consome a fila orders e calcula a equacao.

Ciclo 2 — o worker passa a interpretar o corpo da mensagem e a resolver a
equacao com a mesma implementacao usada pelo Checkpoint 1 (calculator.py, sem
uma linha de alteracao). O caminho SQS -> Lambda em si ja foi provado no
Ciclo 1 e continua valendo.

Formato esperado do corpo da mensagem:

    {"a": 1, "b": -5, "c": 6}

O log sai como uma linha JSON por evento em vez de texto livre: a validacao dos
ciclos compara campos (messageId, resultado) e o painel do Ciclo 6 vai ler esses
mesmos campos. Texto livre exigiria parser; JSON o CloudWatch Logs Insights ja
consulta por campo.

Sobre o destino das mensagens invalidas: neste ciclo elas sao registradas como
message_rejected e descartadas. Nao ha DLQ ainda — ela entra no Ciclo 3, junto
com o retry. Descartar e provisorio, mas e melhor do que a alternativa
disponivel hoje: propagar a excecao faria a SQS reentregar o lote inteiro em
loop ate a retencao de 4h expirar, sem nenhum lugar para a mensagem ruim
descansar. A classificacao entre erro permanente e erro inesperado, abaixo, e
justamente o que o Ciclo 3 vai usar para decidir o que vai para a DLQ e o que
merece nova tentativa.
"""

import json
import math

from calculator import calculate

# Corpo truncado no log. Em Ciclo 1 e 2 as mensagens sao minusculas, mas o
# producer do Ciclo 4 gera milhares delas — e ingestao de log e o unico item
# deste projeto que sai do free tier se alguem publicar payloads grandes.
MAX_LOGGED_BODY = 512

COEFFICIENTS = ("a", "b", "c")


class InvalidMessage(Exception):
    """Erro permanente: a mensagem nunca vai funcionar, reentregar nao ajuda.

    Separada de qualquer outra excecao de proposito. No Ciclo 3 esta e a classe
    que manda a mensagem direto para a DLQ, enquanto uma falha inesperada
    (bug, indisponibilidade momentanea) merece as tentativas de retry.
    """


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

        process(record, request_id)

    # Contrato ReportBatchItemFailures, declarado no event source mapping.
    # Continua vazio neste ciclo: nada volta para a fila ainda. O Ciclo 3
    # preenche esta lista com os messageIds que merecem nova tentativa.
    return {"batchItemFailures": []}


def process(record, request_id):
    message_id = record.get("messageId")

    try:
        a, b, c = parse(record.get("body"))
        result = calculate(a, b, c)
    except (InvalidMessage, ValueError) as error:
        # calculate() levanta ValueError para a = 0, valores nao finitos e
        # coeficientes que estouram o ponto flutuante — todos permanentes,
        # pela mesma razao que InvalidMessage: reentregar nao muda o desfecho.
        log(
            event="message_rejected",
            request_id=request_id,
            message_id=message_id,
            reason=str(error),
        )
        return

    log(
        event="message_processed",
        request_id=request_id,
        message_id=message_id,
        **result,
    )


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
        # Sem isso eles atravessariam a validacao de tipo abaixo — sao float —
        # e so seriam barrados la dentro do calculate, com uma mensagem menos
        # precisa sobre a origem do problema.
        payload = json.loads(body, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise InvalidMessage("Corpo da mensagem nao e JSON valido: %s" % error) from None

    if not isinstance(payload, dict):
        raise InvalidMessage("Corpo da mensagem deve ser um objeto JSON.")

    missing = [name for name in COEFFICIENTS if name not in payload]

    if missing:
        raise InvalidMessage(
            "Coeficientes ausentes: %s." % ", ".join(missing)
        )

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

    A SQS envia o valor como string. Convertido para int porque a validacao do
    Ciclo 1 exige receive_count == 1 (consumo na primeira entrega) e, a partir
    do Ciclo 3, e este numero que mostra o retry acontecendo.
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
    #
    # allow_nan=False e rede de seguranca: por padrao o json.dumps emite NaN e
    # Infinity, que nao sao JSON valido. Se um nao finito escapar da validacao,
    # e melhor a linha falhar alto do que gerar log que o painel nao parseia.
    print(json.dumps(fields, ensure_ascii=False, allow_nan=False))
