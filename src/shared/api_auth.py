"""Verificacao da chave de API, compartilhada pelos handlers HTTP.

O HTTP API do API Gateway nao tem API key nativa — isso e recurso do REST API
v1. A alternativa no gateway seria um Lambda authorizer: uma funcao, uma role e
um log group a mais para comparar duas strings. A verificacao acontece entao
dentro de cada handler, e este modulo existe para que ela seja a mesma nos dois.
"""

import hmac

HEADER = "x-api-key"


def authorized(event, expected_key):
    """Compara o header x-api-key com a chave configurada.

    Falha fechada: sem chave configurada, nenhuma requisicao passa. O contrario
    — liberar tudo quando a configuracao some — transformaria um erro de deploy
    em endpoint aberto sem ninguem perceber.

    compare_digest em vez de "==" para que o tempo da comparacao nao revele
    quantos caracteres iniciais da chave estao corretos.
    """
    if not expected_key:
        return False

    # No payload format 2.0 do HTTP API os nomes de header chegam minusculos.
    headers = event.get("headers") or {}
    provided = headers.get(HEADER) or ""

    return hmac.compare_digest(provided, expected_key)
