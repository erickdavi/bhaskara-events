# Ciclo 1 — Infraestrutura de mensageria

**Concluído em:** 2026-08-27
**Objetivo:** provar o caminho `SQS → Lambda` ponta a ponta.

O worker deste ciclo **não calcula nada**. Ele recebe o lote, registra cada
mensagem em JSON e confirma o consumo. O cálculo de Bhaskara entra no Ciclo 2,
reaproveitando `src/shared/calculator.py`. Manter os dois separados garante que,
se algo falhar no Ciclo 2, já se sabe que a mensageria não é a culpada.

## Escopo

| Entregue | Adiado |
| --- | --- |
| Fila SQS `orders` | Fila `results` (Ciclo 3) |
| Worker Lambda + event source mapping | DLQ e retry (Ciclo 3) |
| IAM mínimo (ARN restrito) | Producer e API (Ciclo 4) |
| CloudWatch Logs com retenção | Métricas agregadas (Ciclo 5) |
| Terraform completo | Frontend (Ciclo 6) |

## Contrato do handler

```python
def lambda_handler(event, context):
    for record in event["Records"]:
        log(event="message_received",
            message_id=record["messageId"],
            receive_count=int(record["attributes"]["ApproximateReceiveCount"]),
            body=record["body"])

    return {"batchItemFailures": []}
```

`ReportBatchItemFailures` entra já neste ciclo, mesmo sem nada a reportar. É o
que permite, no Ciclo 3, mandar *uma* mensagem ruim para a DLQ sem arrastar o
lote inteiro. Introduzido depois, obrigaria a reescrever handler e testes.

## Recursos AWS criados

| Recurso | Nome |
| --- | --- |
| `aws_sqs_queue.orders` | `bhaskara-events-dev-orders` |
| `aws_lambda_function.worker` | `bhaskara-events-dev-worker` |
| `aws_lambda_event_source_mapping.orders_to_worker` | — |
| `aws_iam_role.worker` | `bhaskara-events-dev-worker-role` |
| `aws_iam_role_policy.worker` | `bhaskara-events-dev-worker-policy` |
| `aws_cloudwatch_log_group.worker` | `/aws/lambda/bhaskara-events-dev-worker` |

## Critérios de aceitação e evidência

| # | Critério | Resultado |
| --- | --- | --- |
| 1 | `terraform fmt -check` e `validate` sem erro | ✅ `Success! The configuration is valid.` |
| 2 | `plan` cria exatamente 6 recursos, 0 destroys | ✅ `Plan: 6 to add, 0 to change, 0 to destroy.` |
| 3 | **Mensagem manual processada** — o `MessageId` publicado aparece no log | ✅ `bc968005-657d-4d3f-a716-0a32b08cdc43` publicado e encontrado no log |
| 4 | Consumo real, sem reentrega — fila zerada e `receive_count = 1` | ✅ `Visible = 0`, `NotVisible = 0`; nenhum evento com `receive_count != 1` |
| 5 | Lote de 10 mensagens processado | ✅ `10/10 encontrados`, fila drenada |
| 6 | Métrica `Errors` = 0 e nenhum traceback | ✅ `Invocations: 6, Errors: 0, Throttles: 0`; 0 linhas de erro |
| 7 | IAM mínimo, sem `Resource: "*"` | ✅ ARN restrito ao log group e à fila; sem `sqs:SendMessage` |
| 8 | Testes passando | ✅ `28 passed` (18 herdados + 10 novos) |
| 9 | `destroy` limpo, sem resíduo | ⏸️ pendente — a stack foi mantida no ar ao fim do ciclo; validar antes de encerrar a sessão de trabalho |
| 10 | Reprodutível de clone limpo, sem segredo versionado | ✅ `.gitignore` cobre state, tfvars, zip, `.env` |

### Evidência — mensagem única

```json
{"event": "batch_received",   "request_id": "37728530-cfcd-59ef-948a-e6397fa7fbd9", "batch_size": 1}
{"event": "message_received", "request_id": "37728530-cfcd-59ef-948a-e6397fa7fbd9",
 "message_id": "bc968005-657d-4d3f-a716-0a32b08cdc43", "receive_count": 1,
 "body": "{\"ping\":\"cycle-1\"}"}
```

```text
REPORT  Duration: 2.32 ms  Billed Duration: 86 ms  Memory Size: 128 MB  Max Memory Used: 35 MB
```

35 MB de 128 MB usados — a memória mínima da Lambda sobra para este workload.

### Evidência — lote de 10

As 10 mensagens foram distribuídas em 5 invocações concorrentes, com lotes de
tamanhos diferentes (1, 1, 2, ...). Isso é o event source mapping escalando
sozinho: ele não espera o lote encher porque
`maximum_batching_window_in_seconds = 0`.

```text
=== Cada MessageId publicado apareceu no log? ===
  10/10 encontrados

=== Fila drenada? ===
  ApproximateNumberOfMessages: 0    ApproximateNumberOfMessagesNotVisible: 0
```

### Política IAM efetiva

```json
[
  {"Sid": "WriteOwnLambdaLogs",  "Effect": "Allow",
   "Action": ["logs:PutLogEvents", "logs:CreateLogStream", "logs:CreateLogGroup"],
   "Resource": ["arn:aws:logs:us-east-1:<conta>:log-group:/aws/lambda/bhaskara-events-dev-worker",
                "arn:aws:logs:us-east-1:<conta>:log-group:/aws/lambda/bhaskara-events-dev-worker:*"]},
  {"Sid": "ConsumeOrdersQueue", "Effect": "Allow",
   "Action": ["sqs:ReceiveMessage", "sqs:GetQueueAttributes", "sqs:DeleteMessage"],
   "Resource": "arn:aws:sqs:us-east-1:<conta>:bhaskara-events-dev-orders"}
]
```

## Como reproduzir a validação

```bash
cd infra && terraform apply && cd ..

./scripts/send-test-message.sh        # uma mensagem
./scripts/send-test-message.sh 10     # lote de 10
```

## Incidente durante o apply

O primeiro `apply` falhou ao criar a Lambda:

```text
InvalidParameterValueException: Specified ReservedConcurrentExecutions for function
decreases account's UnreservedConcurrentExecution below its minimum value of [10].
```

**Causa.** A conta tem limite total de 10 execuções concorrentes — o padrão da
AWS para contas novas, elevado sob demanda — e a AWS recusa qualquer reserva que
derrube a concorrência não reservada abaixo de 10. Com um limite total de 10,
**nenhum** valor de `reserved_concurrent_executions` é aceitável, nem 1.

**Correção.** `worker_reserved_concurrency` passou de `5` para `-1` (sem
reserva). O efeito pretendido não se perdeu: o teto de 10 da conta já limita o
estrago de um bug no producer e mantém a fila acumulando de forma visível.

A Lambda chegou a ser criada antes da chamada de concorrência falhar, então o
Terraform a marcou como *tainted* e a recriou no `apply` seguinte.

**Consequência para os próximos ciclos:** com 10 execuções concorrentes na conta
inteira, o producer (Ciclo 4) e o status (Ciclo 5) vão disputar esse pool com o
worker. É administrável neste volume, mas precisa ser considerado no desenho do
Ciclo 4 — em particular, o producer deve usar `SendMessageBatch` e não uma
invocação por mensagem.

## Próximo passo — Ciclo 2

Adaptar o worker para receber `{"a": 1, "b": -5, "c": 6}` e calcular usando
`src/shared/calculator.py`. Nenhum recurso AWS novo: só código, testes e um
segundo bloco `source` no `archive_file` para levar `calculator.py` ao pacote.
