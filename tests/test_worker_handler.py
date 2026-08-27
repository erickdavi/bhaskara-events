"""Testes do worker do Ciclo 1.

O que esta sob teste aqui e o contrato com a SQS, nao a matematica: o formato
do evento que o event source mapping entrega, o formato de retorno que o
ReportBatchItemFailures exige e os campos de log que o criterio de aceitacao do
ciclo verifica na nuvem.
"""

import json

import pytest

from src.handlers.worker.handler import lambda_handler


class FakeContext:
    aws_request_id = "req-abc-123"


def sqs_record(message_id="msg-1", body='{"ping": "cycle-1"}', receive_count=1):
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
    lambda_handler({"Records": [sqs_record(body='{"a": 1, "b": -5, "c": 6}')]}, FakeContext())

    # logged() ja faz json.loads em cada linha: se alguma nao for JSON valido,
    # este teste falha aqui.
    entries = logged(capsys)

    assert len(entries) == 2
    assert {entry["event"] for entry in entries} == {
        "batch_received",
        "message_received",
    }


def test_context_without_request_id_is_tolerated(capsys):
    # Invocacoes de teste local passam context=None.
    lambda_handler({"Records": [sqs_record()]}, None)

    entry = events_of(logged(capsys), "message_received")[0]

    assert entry["request_id"] is None
