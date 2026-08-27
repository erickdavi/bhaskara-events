"""Testes do gerador de equacoes.

O gerador existe para alimentar a demonstracao com uma amostra interessante, e
"interessante" aqui tem significado preciso: os tres desfechos possiveis de uma
equacao do segundo grau precisam aparecer, e as equacoes precisam ser
matematicamente corretas. Os dois pontos sao verificados contra o calculator.py,
que e a fonte da verdade.
"""

import json

import pytest

from calculator import calculate
from src.handlers.producer import generator
from src.handlers.producer.handler import lambda_handler as producer_handler  # noqa: F401
from src.handlers.worker import handler as worker


def bodies(quantity, **kwargs):
    return list(generator.generate(quantity, **kwargs))


def equations(quantity, **kwargs):
    return [json.loads(body) for body in bodies(quantity, **kwargs)]


def delta_of(equation):
    return equation["b"] ** 2 - 4 * equation["a"] * equation["c"]


def test_generates_the_requested_quantity():
    assert len(bodies(137)) == 137


def test_generates_nothing_for_zero():
    assert bodies(0) == []


def test_every_equation_has_the_three_coefficients():
    for equation in equations(200):
        assert set(equation) == {"a", "b", "c"}


def test_a_is_never_zero():
    # a = 0 nao e equacao do segundo grau: o worker recusaria toda a carga.
    for equation in equations(300):
        assert equation["a"] != 0


def test_all_three_outcomes_appear():
    # A razao de existir da construcao por desfecho. Sorteando a, b e c ao
    # acaso, delta = 0 exigiria b^2 == 4ac na igualdade e praticamente nunca
    # apareceria numa amostra deste tamanho.
    deltas = [delta_of(equation) for equation in equations(300, seed=1)]

    assert any(delta > 0 for delta in deltas)
    assert any(delta == 0 for delta in deltas)
    assert any(delta < 0 for delta in deltas)


def test_the_outcomes_are_reasonably_balanced():
    deltas = [delta_of(equation) for equation in equations(600, seed=2)]

    for predicate in (lambda d: d > 0, lambda d: d == 0, lambda d: d < 0):
        share = sum(1 for delta in deltas if predicate(delta)) / len(deltas)
        assert 0.15 < share < 0.55


def test_every_generated_equation_is_solvable():
    # Verificacao contra a fonte da verdade: nenhuma equacao gerada pode fazer
    # o calculator levantar erro.
    for equation in equations(300, seed=3):
        calculate(equation["a"], equation["b"], equation["c"])


def test_two_roots_equations_have_the_constructed_roots():
    # As equacoes de duas raizes sao montadas a partir da forma fatorada, entao
    # as raizes calculadas tem de ser inteiras e satisfazer soma e produto.
    for equation in equations(200, seed=4):
        if delta_of(equation) <= 0:
            continue

        result = calculate(equation["a"], equation["b"], equation["c"])
        x1, x2 = result["x1"], result["x2"]

        assert x1 + x2 == pytest.approx(-equation["b"] / equation["a"])
        assert x1 * x2 == pytest.approx(equation["c"] / equation["a"])


def test_double_root_equations_really_have_a_single_root():
    found = False

    for equation in equations(300, seed=5):
        if delta_of(equation) != 0:
            continue

        found = True
        result = calculate(equation["a"], equation["b"], equation["c"])

        assert result["x1"] == result["x2"]

    assert found, "nenhuma equacao de raiz dupla foi gerada"


def test_equations_without_real_roots_return_no_roots():
    found = False

    for equation in equations(300, seed=6):
        if delta_of(equation) >= 0:
            continue

        found = True
        assert calculate(equation["a"], equation["b"], equation["c"])["roots"] == []

    assert found, "nenhuma equacao sem raizes reais foi gerada"


def test_the_same_seed_produces_the_same_load():
    assert bodies(50, seed=42) == bodies(50, seed=42)


def test_different_seeds_produce_different_loads():
    assert bodies(50, seed=42) != bodies(50, seed=43)


def test_no_invalid_messages_by_default():
    # Gerar lixo sem que ninguem tenha pedido seria surpreendente.
    for body in bodies(300, seed=7):
        json.loads(body)


def test_invalid_ratio_of_one_makes_everything_invalid(capsys):
    for body in bodies(100, invalid_ratio=1.0, seed=8):
        assert_rejected_by_the_worker(body)


def test_invalid_ratio_is_roughly_respected():
    sample = bodies(1000, invalid_ratio=0.2, seed=9)
    invalid = sum(1 for body in sample if not is_valid_equation(body))

    assert 0.14 < invalid / len(sample) < 0.26


def test_every_invalid_category_is_actually_rejected(capsys):
    # O gerador e o worker precisam concordar sobre o que e invalido. Se o
    # worker aceitasse alguma dessas mensagens, a DLQ ficaria vazia na
    # demonstracao e ninguem entenderia por que.
    for body in bodies(400, invalid_ratio=1.0, seed=10):
        assert_rejected_by_the_worker(body)


def is_valid_equation(body):
    """Valida pela mesma regra do worker, e nao por uma reimplementacao.

    Uma checagem propria aqui erraria nos casos sutis — {"a": "1", ...} tem os
    tres campos e "a" diferente de zero, mas o worker a recusa por ser string.
    """
    try:
        a, b, c = worker.parse(body)
        calculate(a, b, c)
    except (worker.InvalidMessage, ValueError):
        return False

    return True


def assert_rejected_by_the_worker(body):
    with pytest.raises((worker.InvalidMessage, ValueError)):
        a, b, c = worker.parse(body)
        calculate(a, b, c)
