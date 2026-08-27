"""Testes do worker.

O que esta sob teste aqui e o contrato com a SQS, nao a matematica: o formato
do evento que o event source mapping entrega, o formato de retorno que o
ReportBatchItemFailures exige e os campos de log que o criterio de aceitacao do
ciclo verifica na nuvem.
"""

import json

import pytest

from calculator import calculate
from src.handlers.worker import handler as worker
from src.handlers.worker.handler import lambda_handler


class FakeContext:
    aws_request_id = "req-abc-123"


VALID_BODY = '{"a": 1, "b": -5, "c": 6}'


def sqs_record(message_id="msg-1", body=VALID_BODY, receive_count=1):
    """Monta um Record no formato que a SQS entrega a Lambda.

    Os campos seguem o evento real, incluindo o detalhe que mais engana quem
    escreve o handler: ApproximateReceiveCount chega como string.
    """
    return {
        "messageId": message_id,
        "receiptHandle": "handle-" + message_id,
        "body": body,
        "attributes": {
            "ApproximateReceiveCount": str(receive_count),
            "SentTimestamp": "1700000000000",
        },
        "messageAttributes": {},
        "eventSource": "aws:sqs",
        "awsRegion": "us-east-1",
    }


def logged(capsys):
    """Le as linhas de log emitidas, ja convertidas de JSON.

    Falha o teste se qualquer linha nao for JSON valido — o painel do Ciclo 6 e
    a validacao deste ciclo dependem disso.
    """
    lines = capsys.readouterr().out.strip().splitlines()

    return [json.loads(line) for line in lines if line]


def events_of(entries, name):
    return [entry for entry in entries if entry["event"] == name]


def test_returns_the_report_batch_item_failures_contract(capsys):
    result = lambda_handler({"Records": [sqs_record()]}, FakeContext())

    assert result == {"batchItemFailures": []}


def test_logs_one_line_per_message(capsys):
    records = [sqs_record(message_id="msg-%d" % i) for i in range(10)]

    lambda_handler({"Records": records}, FakeContext())

    received = events_of(logged(capsys), "message_received")

    assert [entry["message_id"] for entry in received] == [
        "msg-%d" % i for i in range(10)
    ]


def test_logs_the_batch_size(capsys):
    lambda_handler({"Records": [sqs_record(), sqs_record("msg-2")]}, FakeContext())

    batch = events_of(logged(capsys), "batch_received")

    assert len(batch) == 1
    assert batch[0]["batch_size"] == 2


def test_message_id_and_body_reach_the_log(capsys):
    # O criterio de aceitacao do ciclo casa o MessageId devolvido pelo
    # send-message com o que aparece no CloudWatch. Se este campo mudar de
    # nome, a validacao manual para de funcionar.
    lambda_handler(
        {"Records": [sqs_record(message_id="abc-123", body='{"ping": "x"}')]},
        FakeContext(),
    )

    entry = events_of(logged(capsys), "message_received")[0]

    assert entry["message_id"] == "abc-123"
    assert entry["body"] == '{"ping": "x"}'
    assert entry["request_id"] == "req-abc-123"


def test_receive_count_is_an_integer(capsys):
    # A SQS manda string. O criterio do ciclo exige receive_count == 1, entao a
    # comparacao precisa ser numerica e nao "1" == 1.
    lambda_handler({"Records": [sqs_record(receive_count=3)]}, FakeContext())

    entry = events_of(logged(capsys), "message_received")[0]

    assert entry["receive_count"] == 3


def test_missing_receive_count_does_not_break_the_batch(capsys):
    record = sqs_record()
    del record["attributes"]

    lambda_handler({"Records": [record]}, FakeContext())

    entry = events_of(logged(capsys), "message_received")[0]

    assert entry["receive_count"] is None


def test_empty_event_is_handled(capsys):
    # A Lambda nao deve quebrar com um invoke manual sem Records — util para
    # testar a funcao pelo console sem passar pela fila.
    result = lambda_handler({}, FakeContext())

    assert result == {"batchItemFailures": []}
    assert events_of(logged(capsys), "message_received") == []


def test_long_body_is_truncated_in_the_log(capsys):
    lambda_handler({"Records": [sqs_record(body="x" * 2000)]}, FakeContext())

    entry = events_of(logged(capsys), "message_received")[0]

    assert entry["body"].endswith("...[truncado]")
    assert len(entry["body"]) < 2000


def test_log_lines_are_valid_json(capsys):
    lambda_handler({"Records": [sqs_record()]}, FakeContext())

    # logged() ja faz json.loads em cada linha: se alguma nao for JSON valido,
    # este teste falha aqui.
    entries = logged(capsys)

    assert [entry["event"] for entry in entries] == [
        "batch_received",
        "message_received",
        "message_processed",
    ]


def test_context_without_request_id_is_tolerated(capsys):
    # Invocacoes de teste local passam context=None.
    lambda_handler({"Records": [sqs_record()]}, None)

    entry = events_of(logged(capsys), "message_received")[0]

    assert entry["request_id"] is None


# ---------------------------------------------------------------------------
# Ciclo 2 — o worker passa a calcular a equacao
# ---------------------------------------------------------------------------


def process_one(capsys, body):
    """Processa uma mensagem e devolve a linha de desfecho.

    Ha exatamente uma: message_processed ou message_rejected, nunca as duas.
    """
    lambda_handler({"Records": [sqs_record(body=body)]}, FakeContext())

    entries = logged(capsys)
    outcomes = [
        entry
        for entry in entries
        if entry["event"] in ("message_processed", "message_rejected")
    ]

    assert len(outcomes) == 1

    return outcomes[0]


def test_two_real_roots_are_calculated(capsys):
    outcome = process_one(capsys, '{"a": 1, "b": -5, "c": 6}')

    assert outcome["event"] == "message_processed"
    assert outcome["delta"] == 1
    assert outcome["x1"] == 3
    assert outcome["x2"] == 2


def test_single_root_when_delta_is_zero(capsys):
    outcome = process_one(capsys, '{"a": 1, "b": -4, "c": 4}')

    assert outcome["delta"] == 0
    assert outcome["x1"] == outcome["x2"] == 2


def test_no_real_roots(capsys):
    outcome = process_one(capsys, '{"a": 1, "b": 2, "c": 5}')

    assert outcome["event"] == "message_processed"
    assert outcome["delta"] == -16
    assert outcome["roots"] == []


def test_result_carries_the_coefficients_and_the_message_id(capsys):
    # O painel do Ciclo 6 correlaciona resultado com mensagem por este campo.
    lambda_handler(
        {"Records": [sqs_record(message_id="eq-1", body='{"a": 1, "b": -5, "c": 6}')]},
        FakeContext(),
    )

    outcome = events_of(logged(capsys), "message_processed")[0]

    assert outcome["message_id"] == "eq-1"
    assert (outcome["a"], outcome["b"], outcome["c"]) == (1, -5, 6)


def test_float_coefficients_are_accepted(capsys):
    outcome = process_one(capsys, '{"a": 0.5, "b": -1.5, "c": 1.0}')

    assert outcome["event"] == "message_processed"
    assert outcome["x1"] == 2
    assert outcome["x2"] == 1


def test_a_equal_to_zero_is_rejected(capsys):
    # Nao e uma equacao do segundo grau. calculate() ja recusa; o worker so
    # precisa nao transformar isso em erro de execucao.
    outcome = process_one(capsys, '{"a": 0, "b": 5, "c": 10}')

    assert outcome["event"] == "message_rejected"
    assert "a" in outcome["reason"]


@pytest.mark.parametrize(
    "body,expected_in_reason",
    [
        ("nao e json", "JSON"),
        ('{"a": 1, "b": -5}', "c"),
        ('{"b": -5, "c": 6}', "a"),
        ('{"a": "1", "b": -5, "c": 6}', "numero"),
        ('{"a": true, "b": -5, "c": 6}', "numero"),
        ('{"a": null, "b": -5, "c": 6}', "numero"),
        ("[1, -5, 6]", "objeto JSON"),
        ("42", "objeto JSON"),
        ('{"a": NaN, "b": -5, "c": 6}', "NaN"),
        ('{"a": 1, "b": Infinity, "c": 6}', "Infinity"),
        ('{"a": 1, "b": 1e200, "c": 1}', "representável"),
        ('{"a": 1e-320, "b": 1, "c": 1}', "representável"),
    ],
)
def test_invalid_messages_are_rejected(capsys, body, expected_in_reason):
    outcome = process_one(capsys, body)

    assert outcome["event"] == "message_rejected"
    assert expected_in_reason in outcome["reason"]


def test_boolean_is_not_treated_as_a_number(capsys):
    # isinstance(True, int) e True em Python: sem a checagem explicita de bool,
    # {"a": true} viraria a = 1 e a equacao seria resolvida com um coeficiente
    # que ninguem enviou.
    outcome = process_one(capsys, '{"a": true, "b": -5, "c": 6}')

    assert outcome["event"] == "message_rejected"
    assert "bool" in outcome["reason"]


def test_missing_coefficients_are_all_named(capsys):
    outcome = process_one(capsys, '{"a": 1}')

    assert "b" in outcome["reason"]
    assert "c" in outcome["reason"]


def test_a_rejected_message_produces_no_result(capsys):
    lambda_handler({"Records": [sqs_record(body='{"a": 0, "b": 1, "c": 1}')]}, FakeContext())

    assert events_of(logged(capsys), "message_processed") == []


def test_one_bad_message_does_not_affect_the_others(capsys):
    # A garantia central do processamento em lote: uma mensagem ruim no meio do
    # lote nao pode impedir o calculo das demais.
    records = [
        sqs_record(message_id="ok-1", body='{"a": 1, "b": -5, "c": 6}'),
        sqs_record(message_id="bad", body="nao e json"),
        sqs_record(message_id="ok-2", body='{"a": 1, "b": -4, "c": 4}'),
    ]

    result = lambda_handler({"Records": records}, FakeContext())

    entries = logged(capsys)
    processed = events_of(entries, "message_processed")
    rejected = events_of(entries, "message_rejected")

    assert [entry["message_id"] for entry in processed] == ["ok-1", "ok-2"]
    assert [entry["message_id"] for entry in rejected] == ["bad"]

    # Ciclo 2 ainda descarta a invalida. O Ciclo 3 troca isto por batchItemFailures
    # e DLQ — e este assert e o que vai mudar la, deliberadamente.
    assert result == {"batchItemFailures": []}


def test_calculation_matches_the_business_rule(capsys):
    # Verificacao independente: o worker nao pode divergir do calculator.py,
    # que e a fonte da verdade herdada do Checkpoint 1.
    outcome = process_one(capsys, '{"a": 1, "b": 1e8, "c": 1}')

    expected = calculate(1, 1e8, 1)

    assert outcome["x1"] == expected["x1"]
    assert outcome["x2"] == expected["x2"]


# ---------------------------------------------------------------------------
# Ciclo 3 — publicacao em results, DLQ e retry
# ---------------------------------------------------------------------------

RESULTS_URL = "https://sqs.local/results"
DLQ_URL = "https://sqs.local/orders-dlq"


class FakeSQS:
    """Dubles do cliente SQS.

    Registra o que foi publicado e sabe falhar sob demanda numa fila
    especifica, que e como os testes simulam a falha inesperada — a categoria
    que deve virar retry em vez de descarte.
    """

    def __init__(self):
        self.sent = []
        self.fail_on = None

    def send_message(self, **request):
        if self.fail_on and self.fail_on in request["QueueUrl"]:
            raise RuntimeError("SQS indisponivel")

        self.sent.append(request)

    def to(self, queue_url):
        return [request for request in self.sent if request["QueueUrl"] == queue_url]


@pytest.fixture(autouse=True)
def sqs(monkeypatch):
    """Substitui o cliente SQS em todos os testes deste modulo.

    Autouse de proposito: nenhum teste deve tocar a AWS de verdade, e um teste
    que esquecesse a fixture publicaria na nuvem sem avisar.
    """
    client = FakeSQS()

    monkeypatch.setattr(worker, "_sqs", client)
    monkeypatch.setattr(worker, "RESULTS_QUEUE_URL", RESULTS_URL)
    monkeypatch.setattr(worker, "DLQ_QUEUE_URL", DLQ_URL)

    return client


def test_successful_result_is_published_to_results(sqs, capsys):
    lambda_handler(
        {"Records": [sqs_record(message_id="eq-1", body='{"a": 1, "b": -5, "c": 6}')]},
        FakeContext(),
    )

    published = sqs.to(RESULTS_URL)

    assert len(published) == 1

    result = json.loads(published[0]["MessageBody"])

    assert result["message_id"] == "eq-1"
    assert result["delta"] == 1
    assert (result["x1"], result["x2"]) == (3.0, 2.0)


def test_result_without_real_roots_is_also_published(sqs, capsys):
    lambda_handler({"Records": [sqs_record(body='{"a": 1, "b": 2, "c": 5}')]}, FakeContext())

    result = json.loads(sqs.to(RESULTS_URL)[0]["MessageBody"])

    assert result["roots"] == []


def test_invalid_message_goes_to_the_dlq(sqs, capsys):
    lambda_handler(
        {"Records": [sqs_record(message_id="bad-1", body='{"a": 0, "b": 5, "c": 10}')]},
        FakeContext(),
    )

    assert sqs.to(RESULTS_URL) == []

    dead = sqs.to(DLQ_URL)

    assert len(dead) == 1
    # O corpo original vai intacto: a DLQ existe para inspecionar e reprocessar
    # exatamente o que chegou.
    assert dead[0]["MessageBody"] == '{"a": 0, "b": 5, "c": 10}'


def test_dlq_message_carries_the_rejection_reason(sqs, capsys):
    lambda_handler(
        {"Records": [sqs_record(message_id="bad-1", body="nao e json")]},
        FakeContext(),
    )

    attributes = sqs.to(DLQ_URL)[0]["MessageAttributes"]

    assert "JSON" in attributes["RejectionReason"]["StringValue"]
    assert attributes["SourceMessageId"]["StringValue"] == "bad-1"


def test_permanent_error_is_not_retried(sqs, capsys):
    # Este e o ponto do caminho permanente: a mensagem foi para a DLQ e a
    # original e confirmada. Devolve-la em batchItemFailures faria a SQS
    # reentregar tres vezes algo que nunca vai funcionar.
    result = lambda_handler(
        {"Records": [sqs_record(body='{"a": 1, "b": -5}')]},
        FakeContext(),
    )

    assert result == {"batchItemFailures": []}


def test_unexpected_error_becomes_a_retry(sqs, capsys):
    # Falha ao publicar em results: pode ser indisponibilidade momentanea, e
    # reentregar tem chance real de salvar a mensagem.
    sqs.fail_on = RESULTS_URL

    result = lambda_handler(
        {"Records": [sqs_record(message_id="eq-1")]},
        FakeContext(),
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "eq-1"}]}

    failed = events_of(logged(capsys), "message_failed")[0]

    assert failed["message_id"] == "eq-1"
    assert failed["error_type"] == "RuntimeError"


def test_failing_to_archive_in_the_dlq_becomes_a_retry(sqs, capsys):
    # Falhar em arquivar nao pode virar perda silenciosa: se a publicacao na
    # DLQ falha, a mensagem tem de voltar para a fila.
    sqs.fail_on = DLQ_URL

    result = lambda_handler(
        {"Records": [sqs_record(message_id="bad-1", body="nao e json")]},
        FakeContext(),
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "bad-1"}]}


def test_missing_queue_configuration_becomes_a_retry(sqs, capsys, monkeypatch):
    # Erro de configuracao (variavel de ambiente ausente) nao pode descartar
    # mensagem: vira retry, e o operador ve message_failed no log.
    monkeypatch.setattr(worker, "RESULTS_QUEUE_URL", "")

    result = lambda_handler({"Records": [sqs_record(message_id="eq-1")]}, FakeContext())

    assert result == {"batchItemFailures": [{"itemIdentifier": "eq-1"}]}


def test_the_three_outcomes_coexist_in_one_batch(sqs, capsys):
    # O caso que justifica o ReportBatchItemFailures: num mesmo lote, uma
    # mensagem boa e confirmada, uma invalida vai para a DLQ e so a que falhou
    # de forma inesperada volta para a fila.
    sqs.fail_on = RESULTS_URL

    records = [
        sqs_record(message_id="ok-1", body='{"a": 1, "b": -5, "c": 6}'),
        sqs_record(message_id="bad-1", body="nao e json"),
    ]

    result = lambda_handler({"Records": records}, FakeContext())

    # A invalida foi arquivada e nao volta.
    assert len(sqs.to(DLQ_URL)) == 1
    # A boa falhou ao publicar e volta sozinha.
    assert result == {"batchItemFailures": [{"itemIdentifier": "ok-1"}]}


def test_partial_failure_is_logged(sqs, capsys):
    sqs.fail_on = RESULTS_URL

    lambda_handler(
        {"Records": [sqs_record(message_id="eq-1"), sqs_record(message_id="eq-2")]},
        FakeContext(),
    )

    summary = events_of(logged(capsys), "batch_partial_failure")[0]

    assert summary["failed"] == 2
    assert summary["total"] == 2


def test_no_partial_failure_event_when_everything_works(sqs, capsys):
    lambda_handler({"Records": [sqs_record()]}, FakeContext())

    assert events_of(logged(capsys), "batch_partial_failure") == []


def test_receive_count_is_visible_for_retried_messages(sqs, capsys):
    # O receive_count e o que mostra o retry acontecendo, e o que permite
    # prever quando a mensagem cairá na DLQ pelo redrive_policy.
    sqs.fail_on = RESULTS_URL

    lambda_handler(
        {"Records": [sqs_record(message_id="eq-1", receive_count=3)]},
        FakeContext(),
    )

    failed = events_of(logged(capsys), "message_failed")[0]

    assert failed["receive_count"] == 3
