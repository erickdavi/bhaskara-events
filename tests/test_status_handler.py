"""Testes do endpoint de status.

O que esta sob teste e a traducao entre o que a AWS devolve e o que o painel
consome: profundidade de fila virando contadores, eventos do CloudWatch virando
um fluxo com cursor, e a espiada na DLQ que nao pode consumir nada.
"""

import json

import pytest

from src.handlers.status import handler as status

API_KEY = "chave-de-teste-123"

ORDERS = "https://sqs.local/orders"
RESULTS = "https://sqs.local/results"
DLQ = "https://sqs.local/orders-dlq"
LOG_GROUP = "/aws/lambda/worker"


class FakeSQS:
    def __init__(self):
        self.depths = {ORDERS: (0, 0), RESULTS: (0, 0), DLQ: (0, 0)}
        self.dlq_messages = []
        self.receive_calls = []

    def get_queue_attributes(self, QueueUrl, AttributeNames):  # noqa: N803
        visible, not_visible = self.depths[QueueUrl]
        attributes = {"ApproximateNumberOfMessages": str(visible)}

        if "ApproximateNumberOfMessagesNotVisible" in AttributeNames:
            attributes["ApproximateNumberOfMessagesNotVisible"] = str(not_visible)

        return {"Attributes": attributes}

    def receive_message(self, **request):
        self.receive_calls.append(request)

        return {"Messages": self.dlq_messages[: request["MaxNumberOfMessages"]]}


class FakeLogs:
    def __init__(self):
        self.events = []
        self.calls = []

    def filter_log_events(self, **request):
        self.calls.append(request)

        matching = [
            entry
            for entry in self.events
            if entry["timestamp"] >= request["startTime"]
        ]

        return {"events": matching[: request["limit"]]}


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    sqs = FakeSQS()
    logs = FakeLogs()

    monkeypatch.setattr(status, "_sqs", sqs)
    monkeypatch.setattr(status, "_logs", logs)
    monkeypatch.setattr(status, "ORDERS_QUEUE_URL", ORDERS)
    monkeypatch.setattr(status, "RESULTS_QUEUE_URL", RESULTS)
    monkeypatch.setattr(status, "DLQ_QUEUE_URL", DLQ)
    monkeypatch.setattr(status, "WORKER_LOG_GROUP", LOG_GROUP)
    monkeypatch.setattr(status, "API_KEY", API_KEY)

    return sqs, logs


def call(query=None, key=API_KEY):
    event = {"routeKey": "GET /status", "headers": {}, "queryStringParameters": query}

    if key is not None:
        event["headers"]["x-api-key"] = key

    result = status.lambda_handler(event, None)

    return result["statusCode"], json.loads(result["body"])


def log_entry(timestamp, event_name="message_processed", **fields):
    return {
        "timestamp": timestamp,
        "message": json.dumps(dict(fields, event=event_name)),
    }


def dlq_message(body, reason=None, source=None):
    attributes = {}

    if reason is not None:
        attributes["RejectionReason"] = {"StringValue": reason}

    if source is not None:
        attributes["SourceMessageId"] = {"StringValue": source}

    return {"Body": body, "MessageAttributes": attributes}


# --- contadores -------------------------------------------------------------


def test_reports_the_depth_of_each_queue(aws):
    sqs, _ = aws
    sqs.depths = {ORDERS: (347, 110), RESULTS: (640, 0), DLQ: (13, 0)}

    status_code, body = call()

    assert status_code == 200
    assert body["queued"] == 347
    assert body["in_flight"] == 110
    assert body["succeeded"] == 640
    assert body["failed"] == 13


def test_processed_is_the_sum_of_succeeded_and_failed(aws):
    sqs, _ = aws
    sqs.depths = {ORDERS: (0, 0), RESULTS: (640, 0), DLQ: (13, 0)}

    _, body = call()

    assert body["processed"] == 653


def test_empty_queues_report_zero(aws):
    _, body = call()

    assert body["queued"] == body["succeeded"] == body["failed"] == 0
    assert body["processed"] == 0


def test_counters_are_integers_not_strings(aws):
    # A SQS devolve os atributos como string; o painel faz aritmetica com eles.
    sqs, _ = aws
    sqs.depths = {ORDERS: (5, 2), RESULTS: (3, 0), DLQ: (1, 0)}

    _, body = call()

    for field in ("queued", "in_flight", "succeeded", "failed", "processed"):
        assert isinstance(body[field], int)


def test_the_fast_path_does_not_query_logs_or_the_dlq(aws):
    # O painel consulta a cada poucos segundos: sem parametros, a resposta sai
    # de tres chamadas de GetQueueAttributes e nada mais.
    sqs, logs = aws

    _, body = call()

    assert logs.calls == []
    assert sqs.receive_calls == []
    assert "events" not in body
    assert "dlq_messages" not in body


# --- eventos ----------------------------------------------------------------


def test_events_are_returned_when_requested(aws):
    _, logs = aws
    logs.events = [
        log_entry(1000, message_id="a"),
        log_entry(2000, message_id="b"),
    ]

    _, body = call({"events": "10", "since": "0"})

    assert [entry["message_id"] for entry in body["events"]] == ["a", "b"]


def test_events_carry_their_timestamp(aws):
    _, logs = aws
    logs.events = [log_entry(1500, message_id="a")]

    _, body = call({"events": "10", "since": "0"})

    assert body["events"][0]["timestamp"] == 1500


def test_the_cursor_is_the_newest_timestamp(aws):
    _, logs = aws
    logs.events = [log_entry(1000), log_entry(2500), log_entry(1800)]

    _, body = call({"events": "10", "since": "0"})

    assert body["events_cursor"] == 2500


def test_the_cursor_excludes_the_last_delivered_event(aws):
    # Sem o +1 no startTime, cada poll devolveria de novo o ultimo evento do
    # poll anterior e o painel mostraria duplicatas.
    _, logs = aws
    logs.events = [log_entry(2000)]

    call({"events": "10", "since": "2000"})

    assert logs.calls[0]["startTime"] == 2001


def test_the_cursor_stays_put_when_nothing_new_happened(aws):
    _, body = call({"events": "10", "since": "5000"})

    assert body["events"] == []
    assert body["events_cursor"] == 5000


def test_only_outcome_events_are_requested(aws):
    # O filtro roda no CloudWatch, e nao na Lambda: uma carga de 5.000
    # mensagens gera o dobro de linhas de message_received, que o painel
    # nao usa.
    _, logs = aws

    call({"events": "10"})

    pattern = logs.calls[0]["filterPattern"]

    assert "message_processed" in pattern
    assert "message_rejected" in pattern
    assert "message_failed" in pattern
    assert "message_received" not in pattern


def test_events_default_to_a_recent_window(aws):
    # Sem cursor, o painel que acabou de abrir recebe o passado recente em vez
    # do log inteiro desde o inicio dos tempos.
    _, logs = aws

    call({"events": "10"})

    assert logs.calls[0]["startTime"] > 0


def test_lines_that_are_not_json_are_skipped(aws):
    _, logs = aws
    logs.events = [
        {"timestamp": 1000, "message": "START RequestId: abc"},
        log_entry(2000, message_id="b"),
    ]

    _, body = call({"events": "10", "since": "0"})

    assert [entry["message_id"] for entry in body["events"]] == ["b"]
    # O cursor avanca mesmo assim: a linha foi vista, so nao interessava.
    assert body["events_cursor"] == 2000


# --- espiada na DLQ ---------------------------------------------------------


def test_dlq_peek_returns_body_and_reason(aws):
    sqs, _ = aws
    sqs.dlq_messages = [
        dlq_message('{"a":0,"b":5,"c":10}', reason="a nao pode ser zero", source="m-1")
    ]

    _, body = call({"dlq": "5"})

    assert body["dlq_messages"] == [
        {
            "body": '{"a":0,"b":5,"c":10}',
            "reason": "a nao pode ser zero",
            "source_message_id": "m-1",
        }
    ]


def test_dlq_peek_does_not_consume_the_messages(aws):
    # VisibilityTimeout=0 devolve a mensagem a visibilidade imediatamente: a
    # espiada nao pode esconder nada de ninguem nem impedir reprocessamento.
    sqs, _ = aws
    sqs.dlq_messages = [dlq_message("{}")]

    call({"dlq": "5"})

    assert sqs.receive_calls[0]["VisibilityTimeout"] == 0


def test_dlq_peek_tolerates_messages_without_attributes(aws):
    # Mensagens que chegaram pelo redrive nativo nao tem RejectionReason: a
    # SQS move o payload original e nao sabe por que ele falhou.
    sqs, _ = aws
    sqs.dlq_messages = [dlq_message('{"a":1,"b":-5,"c":6}')]

    _, body = call({"dlq": "5"})

    assert body["dlq_messages"][0]["reason"] is None


def test_dlq_peek_respects_the_requested_limit(aws):
    sqs, _ = aws
    sqs.dlq_messages = [dlq_message("{}") for _ in range(10)]

    _, body = call({"dlq": "3"})

    assert len(body["dlq_messages"]) == 3


# --- validacao dos parametros ----------------------------------------------


@pytest.mark.parametrize(
    "query,expected_in_error",
    [
        ({"events": "abc"}, "inteiro"),
        ({"events": "-1"}, "minimo"),
        ({"events": "500"}, "maximo"),
        ({"dlq": "99"}, "maximo"),
        ({"dlq": "nao"}, "inteiro"),
        ({"since": "ontem"}, "inteiro"),
    ],
)
def test_invalid_parameters_are_rejected(aws, query, expected_in_error):
    status_code, body = call(query)

    assert status_code == 400
    assert expected_in_error in body["error"]


def test_empty_parameters_are_treated_as_absent(aws):
    # Um painel que monta a query string sem cuidado manda "events=".
    status_code, body = call({"events": "", "dlq": ""})

    assert status_code == 200
    assert "events" not in body


# --- chave de API -----------------------------------------------------------


def test_request_without_the_key_is_rejected(aws):
    status_code, _ = call(key=None)

    assert status_code == 403


def test_request_with_the_wrong_key_is_rejected(aws):
    status_code, _ = call(key="errada")

    assert status_code == 403


def test_unconfigured_key_fails_closed(aws, monkeypatch):
    monkeypatch.setattr(status, "API_KEY", "")

    status_code, _ = call()

    assert status_code == 403


def test_the_key_is_checked_before_the_parameters(aws):
    # Parametro invalido com chave errada tem de dar 403, e nao 400: responder
    # 400 diria ao chamador anonimo que a chave estava certa.
    status_code, _ = call({"events": "abc"}, key="errada")

    assert status_code == 403


# --- resposta ---------------------------------------------------------------


def test_response_is_json_and_uncacheable(aws):
    result = status.lambda_handler(
        {"routeKey": "GET /status", "headers": {"x-api-key": API_KEY}}, None
    )

    assert result["headers"]["Content-Type"] == "application/json"
    # O painel consulta a cada poucos segundos: um cache serviria numeros velhos.
    assert result["headers"]["Cache-Control"] == "no-store"


def test_response_carries_the_moment_it_was_measured(aws):
    _, body = call()

    assert isinstance(body["checked_at"], int)
