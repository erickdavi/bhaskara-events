# Ciclo 2 — Bhaskara event-driven

**Concluído em:** 2026-08-27
**Objetivo:** o worker passa a interpretar o corpo da mensagem e a resolver a
equação com a implementação herdada do Checkpoint 1.

Nenhum recurso AWS novo. O que mudou foi o código do worker, o pacote publicado
(que agora leva o `calculator.py`) e a suíte de testes.

## Contrato da mensagem

```json
{"a": 1, "b": -5, "c": 6}
```

A validação é **estrita**: o corpo é JSON, então número chega como número. Uma
string `"1"` no lugar de `1` indica um producer com defeito, e aceitá-la
silenciosamente esconderia esse defeito até ele aparecer em outro lugar.

| Rejeitado | Motivo |
| --- | --- |
| `nao e json` | corpo não é JSON |
| `[1, -5, 6]`, `42` | corpo não é um objeto JSON |
| `{"a": 1, "b": -5}` | coeficiente ausente (a mensagem nomeia todos os que faltam) |
| `{"a": "1", ...}` | coeficiente como string |
| `{"a": true, ...}` | booleano — `isinstance(True, int)` é `True` em Python, então sem checagem explícita `true` viraria `a = 1` |
| `{"a": null, ...}` | coeficiente nulo |
| `{"a": NaN, ...}`, `Infinity` | `json.loads` aceita esses literais por padrão apesar de a RFC 8259 não os permitir; interceptados no `parse_constant` |
| `{"a": 0, ...}` | não é equação do segundo grau (regra do `calculator.py`) |
| `{"a": 1, "b": 1e200, "c": 1}` | resultado fora do intervalo representável em ponto flutuante |

## Classificação de erro

O worker separa duas naturezas de falha, e essa separação é o trabalho de
projeto que este ciclo entrega para o próximo:

| Classe | Exemplos | Retry ajuda? | Destino no Ciclo 3 |
| --- | --- | --- | --- |
| **Permanente** (`InvalidMessage` e `ValueError`) | JSON malformado, coeficiente ausente, `a = 0`, overflow | Não — o desfecho seria idêntico | DLQ, direto |
| **Inesperada** (qualquer outra exceção) | bug, indisponibilidade momentânea | Sim | `batchItemFailures` → retry → DLQ após `maxReceiveCount` |

### Limitação conhecida deste ciclo

Mensagens inválidas são registradas como `message_rejected` e **descartadas**.
Não há DLQ ainda — ela entra no Ciclo 3.

Descartar é provisório, mas é melhor do que a alternativa disponível hoje:
propagar a exceção faria a SQS reentregar o lote inteiro em loop até a retenção
de 4 h expirar, sem nenhum lugar para a mensagem ruim descansar. O teste
`test_one_bad_message_does_not_affect_the_others` fixa esse comportamento com um
comentário explícito de que o assert vai mudar no Ciclo 3 — de propósito.

## Eventos de log

Duas linhas por mensagem, mais uma por lote:

| Evento | Quando |
| --- | --- |
| `batch_received` | uma vez por invocação, com `batch_size` |
| `message_received` | recebimento, com `receive_count` e o corpo |
| `message_processed` | cálculo bem-sucedido, com `delta` e as raízes |
| `message_rejected` | recusa, com `reason` legível |

`message_processed` e `message_rejected` são mutuamente exclusivos — é isso que
permite ao painel do Ciclo 6 contar sucessos e falhas sem ambiguidade.

## Critérios de aceitação e evidência

| # | Critério | Resultado |
| --- | --- | --- |
| 1 | **Mensagem válida produz o resultado correto** | ✅ `{"a":1,"b":-5,"c":6}` → `delta: 1, x1: 3.0, x2: 2.0` |
| 2 | Os três desfechos matemáticos funcionam | ✅ duas raízes, raiz dupla (`delta: 0`) e sem raízes reais (`roots: []`) |
| 3 | Mensagens inválidas não derrubam a função | ✅ 5 recusas distintas, `Errors: 0` |
| 4 | Uma mensagem ruim não afeta as demais do lote | ✅ coberto por teste; validado na AWS com lote misto |
| 5 | Reutiliza `calculator.py` sem alteração | ✅ arquivo byte-idêntico ao do CP1, levado ao pacote pelo `archive_file` |
| 6 | Testes passando | ✅ `51 passed` (18 + 33) |
| 7 | Nenhum recurso AWS novo | ✅ `Plan: 0 to add, 1 to change, 0 to destroy` |

### Evidência — cálculo correto

```json
{"event": "message_processed", "message_id": "88a30371-…", "a": 1, "b": -5, "c": 6,
 "delta": 1, "x1": 3.0, "x2": 2.0}
```

### Evidência — os três desfechos, em lote de 10

```json
{"a": 1, "b": -5, "c": 6, "delta": 1,   "x1": 3.0, "x2": 2.0}
{"a": 1, "b": -4, "c": 4, "delta": 0,   "x1": 2.0, "x2": 2.0}
{"a": 1, "b":  2, "c": 5, "delta": -16, "roots": []}
{"a": 2, "b": -7, "c": 3, "delta": 25,  "x1": 3.0, "x2": 0.5}
{"a": 1, "b":  0, "c": -4, "delta": 16, "x1": 2.0, "x2": -2.0}
```

`10/10 com desfecho registrado`, fila drenada.

### Evidência — recusas, cada uma com motivo próprio

```json
{"event": "message_rejected", "reason": "Corpo da mensagem nao e JSON valido: Expecting value: line 1 column 1 (char 0)"}
{"event": "message_rejected", "reason": "Coeficientes ausentes: c."}
{"event": "message_rejected", "reason": "O valor de 'a' não pode ser zero."}
{"event": "message_rejected", "reason": "O coeficiente 'a' deve ser um numero, e nao str."}
{"event": "message_rejected", "reason": "Os coeficientes informados produzem um resultado fora do intervalo representável."}
```

`5/5 com desfecho registrado`, fila drenada, **`Errors: 0`** — recusa é um
desfecho tratado, não uma exceção.

## Como reproduzir a validação

```bash
./scripts/send-test-message.sh              # 1 equação válida
./scripts/send-test-message.sh 10           # 10 equações variadas
./scripts/send-test-message.sh --invalid    # lote de mensagens inválidas
```

## Próximo passo — Ciclo 3

Adicionar a fila `results`, a DLQ e o retry, transformando a classificação de
erro deste ciclo em roteamento real: permanente vai para a DLQ, inesperado
volta pela lista `batchItemFailures`. O worker ganha `sqs:SendMessage` na
`results` — e apenas nela.
