import pytest

from calculator import calculate


def test_two_real_roots():
    result = calculate(1, -5, 6)

    assert result["delta"] == 1
    assert result["x1"] == 3
    assert result["x2"] == 2


def test_one_real_root():
    result = calculate(1, -4, 4)

    assert result["delta"] == 0
    assert result["x1"] == 2
    assert result["x2"] == 2


def test_no_real_roots():
    result = calculate(1, 2, 5)

    assert result["delta"] == -16
    assert result["roots"] == []


def test_a_cannot_be_zero():
    with pytest.raises(ValueError):
        calculate(0, 5, 10)


def test_precision_when_b_dominates():
    # b² >> 4ac: a forma direta devolveria -7.45e-09 para x1, um erro de 25%
    # sobre a raiz correta. Ancora a forma numericamente estável.
    result = calculate(1, 1e8, 1)

    assert result["x1"] == pytest.approx(-1e-08, rel=1e-12)
    assert result["x2"] == pytest.approx(-1e08, rel=1e-12)


def test_roots_satisfy_the_equation():
    # Verificação independente da fórmula: substituir a raiz na equação
    # original tem de anular o resultado.
    #
    # O resíduo é comparado à escala dos próprios termos, não a zero absoluto:
    # para a raiz de magnitude 1e8 cada termo vale ~1e16, e nessa escala o
    # menor incremento representável em float64 já é ~2.
    a, b, c = 1, 1e8, 1

    result = calculate(a, b, c)

    for root in (result["x1"], result["x2"]):
        terms = (a * root ** 2, b * root, c)
        scale = max(abs(term) for term in terms)

        assert abs(sum(terms)) / scale < 1e-15


def test_root_order_follows_the_documented_contract():
    # x1 é a raiz de +√Δ e x2 a de -√Δ, independentemente do sinal de b.
    positive_b = calculate(1, 5, 6)

    assert positive_b["x1"] == -2
    assert positive_b["x2"] == -3

    negative_b = calculate(1, -5, 6)

    assert negative_b["x1"] == 3
    assert negative_b["x2"] == 2


def test_b_equal_to_zero():
    result = calculate(1, 0, -4)

    assert result["x1"] == 2
    assert result["x2"] == -2


def test_negative_zero_b_keeps_the_order():
    result = calculate(1, -0.0, -4)

    assert result["x1"] == 2
    assert result["x2"] == -2


def test_triple_zero_root():
    # Δ = 0 com b = 0: sem o tratamento dedicado de Δ = 0, a forma estável
    # dividiria por zero aqui.
    result = calculate(1, 0, 0)

    assert result["delta"] == 0
    assert result["x1"] == 0
    assert result["x2"] == 0


@pytest.mark.parametrize(
    "a,b,c",
    [
        (float("nan"), 1, 1),
        (1, float("nan"), 1),
        (1, 1, float("nan")),
        (float("inf"), 1, 1),
        (1, float("inf"), 1),
        (1, 1, float("-inf")),
    ],
)
def test_non_finite_values_are_rejected(a, b, c):
    with pytest.raises(ValueError):
        calculate(a, b, c)


@pytest.mark.parametrize(
    "a,b,c",
    [
        (1, 1e200, 1),      # b² estoura para infinito ao calcular o delta
        (1e-320, 1, 1),     # a divisão por 2a estoura ao calcular a raiz
    ],
)
def test_finite_coefficients_that_overflow_are_rejected(a, b, c):
    # Entradas finitas cujo resultado não é representável: precisam virar erro,
    # não uma resposta com Infinity — que nem sequer é JSON válido.
    with pytest.raises(ValueError):
        calculate(a, b, c)