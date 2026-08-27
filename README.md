# Bhaskara Events

Arquitetura orientada a eventos (event-driven) para cálculo de equações do
segundo grau na AWS, com filas SQS, Lambdas assíncronas e infraestrutura
declarada em Terraform.

> **Estado atual: Ciclo 1 concluído — infraestrutura de mensageria.**
>
> A fila `orders` e o worker Lambda estão no ar e o caminho `SQS → Lambda`
> está comprovado ponta a ponta. O worker ainda **não calcula** — ele recebe,
> registra e confirma o consumo. O cálculo entra no Ciclo 2.

Este é o **Checkpoint 2** de um trabalho em duas partes. O Checkpoint 1 é uma
API serverless síncrona (`API Gateway → Lambda → resposta HTTP`) que vive em
[outro repositório](https://github.com/erickdavi/bhaskara-api), e continua no ar
sem alteração. Este projeto é independente dele: mesma regra de negócio, uma
arquitetura completamente diferente ao redor.

## Arquitetura

Destino do projeto, ao fim dos 7 ciclos:

```text
                      ┌───────────────────────┐
                      │     Web Dashboard     │
                      └───────────┬───────────┘
              POST /orders        │       GET /status
                {"quantity": N}   │
                      ┌───────────▼───────────┐
                      │  API Gateway HTTP API │
                      └─────┬───────────┬─────┘
                            │           │
                  ┌─────────▼──┐   ┌────▼────────┐
                  │  Producer  │   │   Status    │
                  │   Lambda   │   │   Lambda    │
                  └─────┬──────┘   └────┬────────┘
        SendMessageBatch │               │ GetQueueAttributes
                         ▼               │
                  ┌─────────────┐        │
                  │ SQS orders  │◄───────┘
                  └─────┬───────┘
                        │ event source mapping
                  ┌─────▼───────────────┐
                  │    Worker Lambda    │  usa shared/calculator.py
                  └─────┬───────────┬───┘
                sucesso │           │ falha → retry → maxReceiveCount
                        ▼           ▼
                 ┌─────────────┐  ┌────────────────┐
                 │ SQS results │  │ SQS orders-dlq │
                 └─────────────┘  └────────────────┘
```

O que existe **hoje**, no Ciclo 1:

```text
   aws sqs send-message
            │
            ▼
    ┌───────────────┐     event source      ┌────────────────┐
    │  SQS orders   │───────mapping────────►│ Worker Lambda  │
    └───────────────┘                       └───────┬────────┘
                                                    │
                                                    ▼
                                          ┌──────────────────┐
                                          │ CloudWatch Logs  │
                                          │  (uma linha JSON │
                                          │   por mensagem)  │
                                          └──────────────────┘
```

### Desenvolvimento incremental

| Ciclo | Escopo | Status |
| --- | --- | --- |
| 1 | Infraestrutura de mensageria: SQS orders, worker Lambda, event source mapping, IAM, logs | ✅ concluído |
| 2 | Bhaskara event-driven: worker passa a calcular usando `calculator.py` | ⬜ |
| 3 | Output e DLQ: fila `results`, DLQ, retry, tratamento de erro | ⬜ |
| 4 | Producer: gerar N mensagens a partir de uma única requisição | ⬜ |
| 5 | API de status: métricas do processamento | ⬜ |
| 6 | Web dashboard: disparar a carga e acompanhar visualmente | ⬜ |
| 7 | Polimento e entrega | ⬜ |

Cada ciclo termina em um estado funcional e testável, documentado em
[`docs/`](docs/).

## Estrutura do projeto

```text
bhaskara-events/
├── conftest.py                 # sys.path dos testes = sys.path da Lambda
├── run.sh                      # bootstrap do venv + testes
├── requirements.txt
├── src/
│   ├── shared/
│   │   └── calculator.py       # regra de negócio (reutilizada do Checkpoint 1)
│   └── handlers/
│       └── worker/
│           └── handler.py      # consome a fila orders
├── tests/
│   ├── test_calculator.py      # 18 casos, herdados do Checkpoint 1
│   └── test_worker_handler.py  # 10 casos, contrato com a SQS
├── infra/                      # Terraform
│   ├── versions.tf  main.tf  variables.tf  outputs.tf
│   ├── sqs.tf                  # fila orders
│   ├── iam.tf                  # role e política do worker
│   └── worker.tf               # Lambda, log group, event source mapping
├── scripts/
│   └── send-test-message.sh    # validação ponta a ponta do ciclo
└── docs/
    └── cycle-01.md
```

A separação segue quatro camadas independentes: **regra de negócio**
(`src/shared`), **processamento de eventos** (`src/handlers`), **infraestrutura**
(`infra`) e, a partir do Ciclo 6, **frontend** (`web/`).

### Sobre `calculator.py`

O arquivo é uma cópia **literal**, byte a byte, do
[Checkpoint 1](https://github.com/erickdavi/bhaskara-api) — junto com os seus 18
testes. É a única coisa reaproveitada, e de propósito: a regra matemática é a
mesma, o que muda é tudo ao redor dela. Ela é pura, usa só a biblioteca padrão e
tem um contrato de erro único (`ValueError` para `a = 0`, valores não finitos e
overflow), que é exatamente o que um worker precisa para classificar mensagem
boa e mensagem ruim.

## Executando os testes

Não precisa de credenciais AWS — os testes são todos locais.

```bash
./run.sh
```

```text
28 passed
```

Manualmente:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```

## Implantando na AWS

Pré-requisitos: Terraform >= 1.5, AWS CLI e credenciais ativas
(`aws sts get-caller-identity` confirma).

```bash
cd infra
terraform init
terraform apply
```

Os comandos de validação saem prontos nos outputs:

```bash
terraform output -raw orders_queue_url
terraform output -raw send_test_message
terraform output -raw tail_worker_logs
```

### Recursos criados (Ciclo 1)

| Recurso | Nome | Observação |
| --- | --- | --- |
| `aws_sqs_queue` | `bhaskara-events-dev-orders` | Standard, visibility 180 s, retenção 4 h, long polling 20 s, SSE gerenciado pela SQS |
| `aws_lambda_function` | `bhaskara-events-dev-worker` | Python 3.13, arm64, 128 MB, timeout 30 s |
| `aws_lambda_event_source_mapping` | — | batch 10, janela 0 s, `ReportBatchItemFailures` |
| `aws_iam_role` + `aws_iam_role_policy` | `bhaskara-events-dev-worker-role` | ARN restrito, sem `Resource: "*"` |
| `aws_cloudwatch_log_group` | `/aws/lambda/bhaskara-events-dev-worker` | retenção 7 dias, removido no `destroy` |

## Validando

O script faz o ciclo completo: publica, espera, confere que cada `MessageId`
publicado apareceu no log e mostra se a fila foi drenada.

```bash
./scripts/send-test-message.sh        # uma mensagem
./scripts/send-test-message.sh 10     # lote de 10
```

Manualmente:

```bash
QUEUE_URL="$(terraform -chdir=infra output -raw orders_queue_url)"

aws sqs send-message --queue-url "$QUEUE_URL" --message-body '{"ping":"cycle-1"}'
aws logs tail /aws/lambda/bhaskara-events-dev-worker --since 5m --format short
```

O worker emite uma linha JSON por mensagem:

```json
{"event": "message_received", "request_id": "3772...", "message_id": "bc96...", "receive_count": 1, "body": "{\"ping\":\"cycle-1\"}"}
```

JSON e não texto livre porque o painel do Ciclo 6 vai ler estes mesmos campos, e
o CloudWatch Logs Insights consulta JSON por campo sem precisar de parser.

## Decisões de infraestrutura

**Fila Standard, não FIFO.** O que este projeto demonstra é paralelismo e vazão,
e a ordem entre equações independentes não significa nada. FIFO limitaria a 300
mensagens/s por grupo e encareceria sem benefício.

**`ReportBatchItemFailures` desde o Ciclo 1.** O handler devolve
`{"batchItemFailures": []}` mesmo sem ter o que falhar ainda. Sem esse contrato,
no Ciclo 3 uma única mensagem ruim faria o lote inteiro (até 10) ser reentregue
e ir para a DLQ junto — inclusive as que já tinham sido processadas com sucesso.
Adotar depois significaria reescrever handler e testes.

**Visibility timeout de 180 s = 6× o timeout da função.** Recomendação da AWS:
evita que um retry do lote concorra com a execução ainda em andamento.

**Retenção de 4 horas na fila, deliberadamente baixa.** Enquanto não existir DLQ
(Ciclo 3), uma mensagem que falhe sempre é reentregue até expirar. A retenção
curta limita essa janela. Volta para o padrão de 4 dias quando a DLQ entrar.

**Política IAM inline em vez das managed policies.**
`AWSLambdaBasicExecutionRole` e `AWSLambdaSQSQueueExecutionRole` concedem acesso
sobre `"*"` — todos os log groups e todas as filas da conta. A inline aponta para
o log group e a fila exatos. O worker também **não** tem `sqs:SendMessage`: ele
consome, não publica.

**Log group declarado no Terraform.** Se não for declarado, quem o cria é a
própria Lambda no primeiro invoke — fora do state, sem retenção, e sobrevivendo
ao `terraform destroy`. Declarado, o destroy sai limpo.

**Sem concorrência reservada.** Esta conta tem limite total de 10 execuções
concorrentes (padrão da AWS para contas novas) e a AWS recusa qualquer reserva
que derrube a concorrência não reservada abaixo de 10 — inviabilizando até uma
reserva de 1. Na prática o teto de 10 da conta já cumpre o papel: limita o
estrago de um bug no producer e mantém a fila acumulando de forma visível na
demonstração.

**State local.** Adequado a um operador só, sem pipeline. `terraform.tfstate`
está no `.gitignore` porque guarda ARNs e o ID da conta. O backend S3 está
comentado em `versions.tf` para quando houver CI/CD.

## Custos

Nenhum recurso tem cobrança fixa — só por uso, e o uso deste projeto cabe
folgado no free tier permanente da AWS:

| Serviço | Free tier mensal | Uma demo de 1.000 mensagens |
| --- | --- | --- |
| SQS | 1M requests | ~3.000 requests (send + receive + delete) — **0,3%** |
| Lambda | 1M invocações + 400k GB-s | ~100 invocações × 128 MB × ~50 ms — **~0,0002%** |
| CloudWatch Logs | 5 GB de ingestão | ~200 KB — **0,004%** |

Ainda assim, o hábito recomendado é destruir ao fim de cada sessão de trabalho.

## Limpeza

```bash
cd infra
terraform destroy
```

Remove os seis recursos, log group incluído — não fica resíduo na conta.
Para conferir:

```bash
aws sqs list-queues
aws lambda list-functions --query "Functions[?starts_with(FunctionName, 'bhaskara-events')]"
```

## Segurança

Nada de credencial no repositório. As credenciais AWS vêm do ambiente
(`aws configure` ou variáveis de ambiente), e o `.gitignore` cobre
`*.tfstate*`, `*.tfvars`, `.terraform/`, `.env` e `*.zip`.
