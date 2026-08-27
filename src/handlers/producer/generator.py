"""Geracao dos payloads publicados na fila orders.

Separado do handler de proposito: o handler cuida de HTTP e de SQS, este modulo
cuida de "como e uma equacao interessante". Sao dois motivos de mudanca
diferentes, e este e o unico dos dois que da para testar sem nenhuma nocao de
requisicao ou de fila.

As equacoes nao sao coeficientes aleatorios soltos. Sortear a, b e c ao acaso
produziria quase so dois desfechos — duas raizes reais ou nenhuma — e raiz
dupla (delta = 0) praticamente nunca apareceria, porque exige b^2 == 4ac exato.
Um painel alimentado por esse sorteio mostraria uma distribuicao pobre.

Em vez disso, cada equacao e construida a partir do desfecho desejado: sorteia-se
o tipo primeiro e depois coeficientes que o realizam. Os tres desfechos aparecem
em proporcoes parecidas.
"""

import json
import random

OUTCOMES = ("two_roots", "double_root", "no_real_roots")

# Categorias de mensagem invalida, uma para cada caminho de recusa do worker.
# Servem para demonstrar a DLQ com trafego realista, e nao para testar o worker
# — isso os testes ja fazem.
INVALID_KINDS = (
    "malformed_json",
    "missing_coefficient",
    "coefficient_as_string",
    "coefficient_as_boolean",
    "a_is_zero",
)


def generate(quantity, invalid_ratio=0.0, seed=None):
    """Produz `quantity` corpos de mensagem, ja serializados.

    Devolve strings, e nao dicionarios, porque parte das mensagens invalidas
    nao e JSON valido — nao haveria como representa-las de outro jeito.

    `seed` existe para tornar uma execucao reproduzivel: mesmo seed, mesma
    sequencia de equacoes. Sem ele, cada chamada gera uma carga diferente.
    """
    rng = random.Random(seed)

    for _ in range(quantity):
        if invalid_ratio > 0 and rng.random() < invalid_ratio:
            yield invalid_payload(rng)
        else:
            yield json.dumps(equation(rng))


def equation(rng):
    outcome = rng.choice(OUTCOMES)

    if outcome == "two_roots":
        return two_roots(rng)

    if outcome == "double_root":
        return double_root(rng)

    return no_real_roots(rng)


def two_roots(rng):
    """Constroi a equacao a partir de duas raizes inteiras distintas.

    Partindo das raizes r1 e r2, os coeficientes saem da forma fatorada
    a(x - r1)(x - r2): b = -a(r1 + r2) e c = a * r1 * r2. O delta e
    positivo por construcao, e as raizes sao numeros redondos — o que ajuda
    a conferir o resultado a olho na demonstracao.
    """
    r1 = rng.randint(-12, 12)
    r2 = rng.randint(-12, 12)

    while r2 == r1:
        r2 = rng.randint(-12, 12)

    a = nonzero(rng, -6, 6)

    return {"a": a, "b": -a * (r1 + r2), "c": a * r1 * r2}


def double_root(rng):
    """Raiz dupla: as duas raizes coincidem, entao delta = 0 exatamente.

    Este e o caso que o sorteio ingenuo de coeficientes praticamente nunca
    produz, porque depende de b^2 == 4ac na igualdade.
    """
    r = rng.randint(-12, 12)
    a = nonzero(rng, -6, 6)

    return {"a": a, "b": -2 * a * r, "c": a * r * r}


def no_real_roots(rng):
    """Delta negativo: escolhe c grande o bastante para que b^2 < 4ac.

    Com a > 0, qualquer c acima de b^2/(4a) garante delta < 0. A folga
    aleatoria evita que todas as equacoes fiquem no limite exato.

    O sinal dos tres coeficientes pode ser invertido no fim: (-a, -b, -c)
    descreve a mesma parabola refletida e tem o mesmo delta, entao o desfecho
    se mantem enquanto a amostra ganha variedade.
    """
    a = rng.randint(1, 6)
    b = rng.randint(-30, 30)
    c = b * b // (4 * a) + rng.randint(1, 20)

    if rng.random() < 0.5:
        return {"a": -a, "b": -b, "c": -c}

    return {"a": a, "b": b, "c": c}


def nonzero(rng, low, high):
    value = 0

    while value == 0:
        value = rng.randint(low, high)

    return value


def invalid_payload(rng):
    kind = rng.choice(INVALID_KINDS)

    if kind == "malformed_json":
        return "{isto nao e json"

    if kind == "missing_coefficient":
        missing = rng.choice("abc")
        payload = {"a": 1, "b": -5, "c": 6}
        del payload[missing]
        return json.dumps(payload)

    if kind == "coefficient_as_string":
        return json.dumps({"a": "1", "b": -5, "c": 6})

    if kind == "coefficient_as_boolean":
        return json.dumps({"a": True, "b": -5, "c": 6})

    return json.dumps({"a": 0, "b": rng.randint(-9, 9), "c": rng.randint(-9, 9)})
