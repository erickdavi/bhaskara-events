import math

# Coeficientes finitos podem gerar resultados infinitos por overflow: b = 1e200
# estoura já em b², e a = 1e-320 estoura na divisão por 2a. Nos dois casos o
# resultado não existe em ponto flutuante — melhor recusar explicitamente do que
# devolver Infinity, que sequer é JSON válido.
OVERFLOW_ERROR = (
    "Os coeficientes informados produzem um resultado fora do intervalo "
    "representável."
)


def calculate(a, b, c):
    if a == 0:
        raise ValueError("O valor de 'a' não pode ser zero.")

    if not all(math.isfinite(value) for value in (a, b, c)):
        raise ValueError("Os valores de a, b e c devem ser números finitos.")

    # b ** 2 levanta OverflowError em vez de devolver infinito, ao contrário da
    # multiplicação. Os dois caminhos convergem para o mesmo ValueError para que
    # calculate() tenha um contrato único de erro.
    try:
        delta = b ** 2 - 4 * a * c
    except OverflowError:
        raise ValueError(OVERFLOW_ERROR) from None

    if not math.isfinite(delta):
        raise ValueError(OVERFLOW_ERROR)

    if delta < 0:
        return {
            "a": a,
            "b": b,
            "c": c,
            "delta": delta,
            "roots": []
        }

    if delta == 0:
        root = -b / (2 * a)

        if not math.isfinite(root):
            raise ValueError(OVERFLOW_ERROR)

        return {
            "a": a,
            "b": b,
            "c": c,
            "delta": delta,
            "x1": root,
            "x2": root
        }

    x1, x2 = _roots(a, b, c, delta)

    if not all(math.isfinite(root) for root in (x1, x2)):
        raise ValueError(OVERFLOW_ERROR)

    return {
        "a": a,
        "b": b,
        "c": c,
        "delta": delta,
        "x1": x1,
        "x2": x2
    }


def _roots(a, b, c, delta):
    """Calcula as raízes na forma numericamente estável.

    A forma direta, (-b ± √Δ) / 2a, sofre cancelamento catastrófico quando
    b² >> 4ac: um dos numeradores vira a subtração de dois números quase
    iguais e a precisão do resultado desaba. Para a = 1, b = 1e8, c = 1 ela
    devolve -7.45e-09 onde a raiz correta é -1e-08 — 25% de erro.

    A forma abaixo calcula primeiro a raiz de maior magnitude, onde os termos
    se somam em vez de se cancelarem, e obtém a outra pela relação entre as
    raízes e o produto (x1 * x2 = c / a), que não envolve subtração alguma.
    """
    sign = math.copysign(1.0, b)

    # Somar termos de mesmo sinal: nenhum cancelamento acontece aqui.
    q = -(b + sign * math.sqrt(delta)) / 2

    # q / a é a raiz de maior magnitude; c / q é a menor, derivada do produto.
    # A ordem segue o contrato documentado: x1 usa +√Δ, x2 usa -√Δ.
    if sign < 0:
        return q / a, c / q

    return c / q, q / a
