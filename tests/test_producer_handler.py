"""Testes do producer.

O producer tem duas responsabilidades e os testes seguem essa divisao: validar
a requisicao HTTP (que vira 400 quando o cliente erra) e publicar em lotes
dentro do orcamento de tempo (que vira uma resposta honesta quando o tempo
acaba).
"""

import json

import pytest

from src.handlers.producer import handler as producer

ORDERS_URL = "https://sqs.local/orders"
API_KEY = "chave-de-teste-123"


class FakeSQS:
    def __init__(self):
        self.batches = []
        self.fail_per_batch = 0

    def send_message_batch(self, QueueUrl, Entries):  # noqa: N803 - assinatura do boto3
        self.batches.append({"QueueUrl": QueueUrl, "Entries": Entries})

        failed = Entries[: self.fail_per_batch]
        succeeded = Entries[self.fail_per_batch :]

        return {
            "Successful": [{"Id": entry["Id"]} for entry in succeeded],
            "Failed": [
                {"Id": entry["Id"], "Message": "throttled"} for entry in failed
            ],
        }

    @property
    def messages(self):
        return [
            entry["MessageBody"]
            for batch in self.batches
            for entry in batch["Entries"]
        ]


class FakeContext:
    aws_request_id = "req-producer-1"

    def __init__(self, remaining_ms=30000):
        self._remaining = remaining_ms

    def get_remaining_time_in_millis(self):
        return self._remaining


@pytest.fixture(autouse=True)
def sqs(monkeypatch):
    """Substitui o cliente SQS em todos os testes deste modulo.

    Autouse: um teste que esquecesse a fixture publicaria na fila de verdade.
    """
    client = FakeSQS()

    monkeypatch.setattr(producer, "_sqs", client)
    monkeypatch.setattr(producer, "ORDERS_QUEUE_URL", ORDERS_URL)
    monkeypatch.setattr(producer, "MAX_QUANTITY", 5000)
    monkeypatch.setattr(producer, "API_KEY", API_KEY)

    return client


def request(payload):
    # Formato de evento do HTTP API (payload format 2.0): o corpo chega como
    # string, ja desserializado do envelope HTTP.
    return {
        "routeKey": "POST /orders",
        "headers": {"x-api-key": API_KEY},
        "body": payload if isinstance(payload, str) else json.dumps(payload),
    }


def call(payload, context=None):
    result = producer.lambda_handler(request(payload), context or FakeContext())

    return result["statusCode"], json.loads(result["body"])


def test_publishes_the_requested_quantity(sqs, capsys):
    status, body = call({"quantity": 25})

    assert status == 202
    assert body["requested"] == 25
    assert body["published"] == 25
    assert len(sqs.messages) == 25


def test_publishes_in_batches_of_ten(sqs, capsys):
    # SendMessageBatch aceita no maximo 10 por chamada: 25 mensagens sao 3
    # lotes (10, 10, 5).
    call({"quantity": 25})

    assert [len(batch["Entries"]) for batch in sqs.batches] == [10, 10, 5]


def test_reports_the_number_of_batches(sqs, capsys):
    _, body = call({"quantity": 1000})

    assert body["batches"] == 100
    assert body["published"] == 1000


def test_publishes_to_the_orders_queue(sqs, capsys):
    call({"quantity": 5})

    assert {batch["QueueUrl"] for batch in sqs.batches} == {ORDERS_URL}


def test_batch_ids_are_unique_within_each_batch(sqs, capsys):
    # A SQS recusa o lote inteiro se dois Ids se repetirem dentro dele.
    call({"quantity": 30})

    for batch in sqs.batches:
        ids = [entry["Id"] for entry in batch["Entries"]]
        assert len(ids) == len(set(ids))


def test_the_generated_messages_are_equations(sqs, capsys):
    call({"quantity": 20})

    for body in sqs.messages:
        payload = json.loads(body)
        assert set(payload) == {"a", "b", "c"}


def test_the_same_seed_publishes_the_same_load(sqs, capsys):
    call({"quantity": 20, "seed": 99})
    first = list(sqs.messages)

    sqs.batches.clear()
    call({"quantity": 20, "seed": 99})

    assert sqs.messages == first


def test_invalid_ratio_is_forwarded_to_the_generator(sqs, capsys):
    call({"quantity": 100, "invalid_ratio": 1.0, "seed": 5})

    # Com proporcao 1.0 nenhuma mensagem deve ser uma equacao valida.
    valid = 0

    for body in sqs.messages:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue

        if isinstance(payload, dict) and set(payload) == {"a", "b", "c"}:
            valid += 1

    assert valid < len(sqs.messages)


# --- validacao da requisicao ------------------------------------------------


@pytest.mark.parametrize(
    "payload,expected_in_error",
    [
        ({}, "quantity"),
        ({"quantity": 0}, "minimo 1"),
        ({"quantity": -5}, "minimo 1"),
        ({"quantity": 1.5}, "inteiro"),
        ({"quantity": "10"}, "inteiro"),
        ({"quantity": True}, "inteiro"),
        ({"quantity": None}, "inteiro"),
        ({"quantity": 999999}, "maximo"),
        ({"quantity": 10, "invalid_ratio": 2}, "entre 0 e 1"),
        ({"quantity": 10, "invalid_ratio": -0.1}, "entre 0 e 1"),
        ({"quantity": 10, "invalid_ratio": "muito"}, "numero"),
    ],
)
def test_invalid_requests_are_rejected(sqs, capsys, payload, expected_in_error):
    status, body = call(payload)

    assert status == 400
    assert expected_in_error in body["error"]
    # Nada pode ter sido publicado numa requisicao recusada.
    assert sqs.batches == []


def test_malformed_json_body_is_rejected(sqs, capsys):
    status, body = call("{isto nao e json")

    assert status == 400
    assert "JSON" in body["error"]


def test_missing_body_is_rejected(sqs, capsys):
    result = producer.lambda_handler(
        {"routeKey": "POST /orders", "headers": {"x-api-key": API_KEY}},
        FakeContext(),
    )

    assert result["statusCode"] == 400


def test_body_that_is_not_an_object_is_rejected(sqs, capsys):
    status, body = call("[1, 2, 3]")

    assert status == 400
    assert "objeto JSON" in body["error"]


def test_the_maximum_is_accepted(sqs, capsys):
    status, body = call({"quantity": producer.MAX_QUANTITY})

    assert status == 202
    assert body["published"] == producer.MAX_QUANTITY


# --- orcamento de tempo -----------------------------------------------------


def test_stops_publishing_when_time_runs_out(sqs, capsys):
    # O HTTP API corta a integracao em 30 s. Melhor responder "publiquei 700"
    # do que estourar o timeout e deixar o cliente sem saber o que foi parar
    # na fila.
    status, body = call({"quantity": 1000}, context=FakeContext(remaining_ms=1000))

    assert status == 202
    assert body["truncated"] is True
    assert body["published"] == 0
    assert "detail" in body


def test_no_truncation_flag_when_everything_fits(sqs, capsys):
    _, body = call({"quantity": 50})

    assert "truncated" not in body


def test_missing_context_does_not_break_the_time_guard(sqs, capsys):
    # Invocacao local, sem contexto da Lambda: publica tudo.
    result = producer.lambda_handler(request({"quantity": 12}), None)

    assert json.loads(result["body"])["published"] == 12


# --- falhas parciais da SQS -------------------------------------------------


def test_partially_rejected_batches_are_counted(sqs, capsys):
    sqs.fail_per_batch = 2

    _, body = call({"quantity": 30})

    assert body["published"] == 24
    assert body["failed"] == 6


def test_a_partial_rejection_does_not_stop_the_rest(sqs, capsys):
    # Perder 3 de 1.000 mensagens nao justifica descartar as 997 restantes.
    sqs.fail_per_batch = 1

    _, body = call({"quantity": 100})

    assert body["batches"] == 10
    assert body["published"] == 90


def test_response_is_json_with_the_right_content_type(sqs, capsys):
    result = producer.lambda_handler(request({"quantity": 1}), FakeContext())

    assert result["headers"]["Content-Type"] == "application/json"
    json.loads(result["body"])


# --- chave de API -----------------------------------------------------------


def test_request_without_the_key_is_rejected(sqs, capsys):
    result = producer.lambda_handler(
        {"routeKey": "POST /orders", "body": json.dumps({"quantity": 1000})},
        FakeContext(),
    )

    assert result["statusCode"] == 403
    # O que se protege e a amplificacao: nenhuma mensagem foi gerada.
    assert sqs.batches == []


def test_request_with_the_wrong_key_is_rejected(sqs, capsys):
    result = producer.lambda_handler(
        {
            "routeKey": "POST /orders",
            "headers": {"x-api-key": "chave-errada"},
            "body": json.dumps({"quantity": 1000}),
        },
        FakeContext(),
    )

    assert result["statusCode"] == 403
    assert sqs.batches == []


def test_the_key_is_checked_before_the_body(sqs, capsys):
    # Um corpo invalido com chave errada tem de dar 403, e nao 400: responder
    # 400 diria ao chamador anonimo que a chave estava certa.
    result = producer.lambda_handler(
        {
            "routeKey": "POST /orders",
            "headers": {"x-api-key": "chave-errada"},
            "body": "{isto nao e json",
        },
        FakeContext(),
    )

    assert result["statusCode"] == 403


def test_unconfigured_key_fails_closed(sqs, capsys, monkeypatch):
    # Um erro de deploy que apague a variavel nao pode virar endpoint aberto.
    monkeypatch.setattr(producer, "API_KEY", "")

    status, _ = call({"quantity": 10})

    assert status == 403
    assert sqs.batches == []
