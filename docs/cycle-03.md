# Ciclo 3 — Output e DLQ

**Concluído em:** 2026-08-27
**Objetivo:** fechar o fluxo `orders → worker → results`, com dead letter queue,
retry e tratamento de erro.

## Fluxo

```text
                    ┌─────────────────┐
   orders ─────────►│  Worker Lambda  │
      ▲             └────┬───────┬────┘
      │                  │       │
      │          sucesso │       │ inválida (erro permanente)
      │                  ▼       │
      │           ┌───────────┐  │
      │           │  results  │  │
      │           └───────────┘  │
      │                          │
      │  inesperada              │
      │  batchItemFailures       │
      └──── retry ×3 ────┐       │
                         ▼       ▼
                  ┌──────────────────┐
                  │   orders-dlq     │
                  └──────────────────┘
```

## Os dois caminhos de falha

A decisão central deste ciclo é que **falha permanente e falha inesperada não
compartilham o mesmo caminho**. A classificação veio pronta do Ciclo 2; aqui ela
vira roteamento.

| | Permanente | Inesperada |
| --- | --- | --- |
| **Exemplos** | JSON malformado, coeficiente ausente, `a = 0`, overflow | falha ao publicar, indisponibilidade da SQS, permissão revogada, bug |
| **Exceções** | `InvalidMessage`, `ValueError` | qualquer outra |
| **Reentregar ajuda?** | Não — o desfecho seria idêntico | Sim — pode passar na próxima |
| **O que o worker faz** | publica na DLQ e **confirma** a mensagem | devolve o `messageId` em `batchItemFailures` |
| **Como chega na DLQ** | direto, imediatamente | pelo `redrive_policy`, após `maxReceiveCount` |
| **Motivo da recusa** | anexado como message attribute | não existe (a SQS não sabe o motivo) |

### Por que não mandar tudo pelo caminho nativo

Seria mais simples: qualquer falha vira `batchItemFailures`, a SQS reentrega e o
`redrive_policy` faz o resto — um caminho só, zero código de roteamento.

Duas coisas se perdem, e as duas importam neste projeto:

1. **Tempo.** Com `visibility_timeout` de 60 s e `maxReceiveCount` de 3, uma
   mensagem inválida levaria ~3 minutos para chegar na DLQ. Multiplicado pelas
   centenas de mensagens do Ciclo 4, o painel do Ciclo 6 mostraria uma fila
   entupida de mensagens que já se sabia estarem erradas desde o primeiro
   milissegundo.
2. **Diagnóstico.** O redrive nativo entrega a mensagem na DLQ exatamente como
   ela era, sem dizer por que foi parar lá. Quem for investigar teria de
   reprocessar mentalmente cada payload. Publicando direto, o worker anexa
   `RejectionReason` e `SourceMessageId`.

O custo é um statement IAM a mais (`sqs:SendMessage` na DLQ) e ~15 linhas de
código. Vale.

### Por que não descartar as inválidas, como no Ciclo 2

Porque descartar é perda silenciosa. No Ciclo 2 era o menor dos males, já que
não havia DLQ e propagar a exceção causaria loop de reentrega até a retenção
expirar. Com a DLQ no lugar, a mensagem tem para onde ir.

## Recursos AWS

| Ação | Recurso | Observação |
| --- | --- | --- |
| criado | `aws_sqs_queue.results` | retenção 4 dias, SSE |
| criado | `aws_sqs_queue.orders_dlq` | retenção 14 dias, `redrive_allow_policy` restrita à `orders` |
| alterado | `aws_sqs_queue.orders` | `redrive_policy` (maxReceiveCount 3); retenção 4 h → 4 dias; visibility 180 s → 60 s |
| alterado | `aws_iam_role_policy.worker` | statement `sqs:SendMessage` restrito a `results` e à DLQ |
| alterado | `aws_lambda_function.worker` | código novo, env vars com as URLs, timeout 30 s → 10 s |

### Dependência circular entre as filas

A `orders` referencia a DLQ no `redrive_policy`, e a DLQ precisa referenciar a
`orders` no `redrive_allow_policy` — um ciclo no grafo do Terraform. Resolvido
montando o ARN da `orders` a partir do nome, que é determinístico:

```hcl
orders_queue_arn = "arn:${partition}:sqs:${region}:${account_id}:${local.orders_queue_name}"
```

### Timeout da função: 30 s → 10 s

Não é economia. O `visibility_timeout` é derivado do timeout da função (6×,
recomendação da AWS) e **também é o intervalo entre as tentativas de retry**.
Com 30 s de timeout, o ciclo até a DLQ levaria ~6 minutos; com 10 s, ~2 minutos.
Um lote de 10 equações processa em ~20 ms, então 10 s é folga de 500×.

## O interruptor de demonstração

`simulate_publish_failure` (padrão `false`) aponta `RESULTS_QUEUE_URL` para uma
fila inexistente, fazendo toda publicação falhar com `QueueDoesNotExist`.

Existe porque o caminho de retry só aparece quando algo dá errado de forma
inesperada — e o código, quando funciona, não dá errado. Sem um jeito de
provocar a falha, o retry ficaria documentado mas nunca demonstrado.

**Nenhuma linha do handler sabe que este interruptor existe.** O que muda é
apenas o valor de uma variável de ambiente. Não é chaos engineering embutido no
caminho de produção.

```bash
cd infra
terraform apply -var=simulate_publish_failure=true    # liga
terraform apply                                        # desliga (padrão)
```

## Critérios de aceitação e evidência

| # | Critério | Resultado |
| --- | --- | --- |
| 1 | **Mensagem bem-sucedida aparece na `results`** | ✅ ver abaixo |
| 2 | **Mensagem que falha vai para a DLQ** | ✅ pelos dois caminhos |
| 3 | DLQ carrega o motivo da recusa | ✅ `RejectionReason` + `SourceMessageId` |
| 4 | Retry acontece antes da DLQ | ✅ `receive_count` 1 → 2 → 3 |
| 5 | Uma mensagem ruim não afeta as demais do lote | ✅ coberto por teste |
| 6 | Falha ao arquivar não vira perda silenciosa | ✅ vira retry (teste) |
| 7 | IAM mínimo: consumo e publicação separados | ✅ sem `SendMessage` na `orders` |
| 8 | Testes | ✅ `63 passed` (eram 51) |

### Evidência — resultado na fila `results`

```json
{"a": 1, "b": -5, "c": 6, "delta": 1, "x1": 3.0, "x2": 2.0,
 "message_id": "2f096b93-13f5-4a6b-972d-eccc1029b21a"}
```

O `message_id` é o da mensagem de origem na `orders` — é o que permite ao painel
do Ciclo 6 casar pedido com resultado.

### Evidência — mensagem inválida na DLQ (caminho permanente)

```json
{
  "body":   "{\"a\":0,\"b\":5,\"c\":10}",
  "reason": "O valor de 'a' não pode ser zero.",
  "source": "77fa2ecf-5699-4065-851f-d9513b86acbb"
}
```

O corpo chegou **intacto** — a DLQ existe para inspecionar e, se for o caso,
reprocessar exatamente o que chegou. O motivo viaja como message attribute
justamente para não contaminar o payload.

### Evidência — retry (caminho inesperado)

Com `simulate_publish_failure=true`, uma mensagem **válida** falha ao publicar e
é reentregue:

```json
{"event": "message_failed", "message_id": "6d13c8a3-…", "receive_count": 1,
 "error_type": "QueueDoesNotExist", "error": "… The specified queue does not exist."}
{"event": "message_failed", "message_id": "6d13c8a3-…", "receive_count": 2,
 "error_type": "QueueDoesNotExist", "error": "… The specified queue does not exist."}
```

Na terceira entrega sem confirmação, o `redrive_policy` moveu a mensagem para a
DLQ:

```json
{"body": "{\"a\":1,\"b\":-5,\"c\":6}", "reason": null}
```

**O `reason: null` é a evidência mais interessante do ciclo.** Esta mensagem
chegou pelo redrive nativo, que entrega o payload exatamente como ele era e não
sabe dizer por que a mensagem falhou. Compare com a mensagem inválida da seção
anterior, publicada diretamente pelo worker, que chegou com
`"O valor de 'a' não pode ser zero."` anexado.

É precisamente essa diferença que justifica os dois caminhos: quando o worker
**sabe** o motivo, ele o anexa; quando o motivo é uma falha de infraestrutura
que só o operador pode diagnosticar, o mecanismo nativo faz o trabalho.

## Como reproduzir a validação

```bash
./scripts/send-test-message.sh              # 1 equação válida → results
./scripts/send-test-message.sh 10           # 10 equações variadas
./scripts/send-test-message.sh --invalid    # 5 inválidas → DLQ com motivo
```

O script termina mostrando amostras da `results` e da DLQ e a profundidade das
três filas.

Para o caminho de retry:

```bash
cd infra && terraform apply -var=simulate_publish_failure=true && cd ..
./scripts/send-test-message.sh
# aguardar ~3 min e inspecionar a DLQ
cd infra && terraform apply && cd ..
```

## Próximo passo — Ciclo 4

Criar o producer: uma Lambda atrás do API Gateway que recebe
`{"quantity": 1000}` e publica payloads variados na `orders`.

Duas restrições já conhecidas influenciam o desenho:

- A conta tem **10 execuções concorrentes no total**, compartilhadas entre
  producer e worker. O producer não pode ser uma invocação por mensagem.
- `SendMessageBatch` publica no máximo 10 mensagens por chamada, então 1.000
  mensagens são 100 chamadas — que precisam caber no timeout da função.
