# Bhaskara Events

Arquitetura orientada a eventos (event-driven) para cálculo de equações do
segundo grau na AWS, com filas SQS, Lambdas assíncronas e infraestrutura
declarada em Terraform.

> **Estado atual: Ciclo 6 concluído — painel web.**
>
> O sistema está completo e demonstrável. Abra o painel, informe a quantidade,
> clique em **Gerar mensagens** e acompanhe: a fila enchendo e drenando, os
> contadores de sucesso e falha, as equações sendo resolvidas em tempo quase
> real e as mensagens que foram para a DLQ, com o motivo.
>
> ```bash
> terraform -chdir=infra output -raw dashboard_url   # abra no browser
> terraform -chdir=infra output -raw api_key         # cole no painel
> ```

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

O que existe **hoje** é a arquitetura completa do diagrama acima. O fluxo de
uma demonstração:

```text
   Painel (CloudFront + S3 privado)
   quantidade, % inválidas, [Gerar]
            │
            │ POST /orders  {"quantity": 1000}
            │ x-api-key: <chave>
            ▼
   ┌───────────────────────┐        GET /status
   │  API Gateway HTTP API │◄───────────────────┐
   └───────────┬───────────┘                    │
               ▼                     ┌──────────┴──────────┐
   ┌───────────────────────┐         │    Status Lambda    │
   │    Producer Lambda    │         │ lê as três filas +  │
   └───────────┬───────────┘         │  log do worker      │
               │                     └─────────────────────┘
               │ SendMessageBatch (10 por chamada)
               ▼
    ┌───────────────┐     event source      ┌────────────────┐
    │  SQS orders   │───────mapping────────►│ Worker Lambda  │
    └───────┬───────┘                       │  calculate()   │
            │                               └───┬────────┬───┘
            │ redrive_policy                    │        │
            │ maxReceiveCount = 3       sucesso │        │ inválida
            │                                   ▼        │
            │                          ┌─────────────┐   │
            │                          │ SQS results │   │
            │                          └─────────────┘   │
            │                                            │
            │  falha inesperada ──► batchItemFailures    │
            │         └── retry ──┘                      │
            ▼                                            ▼
    ┌──────────────────────────────────────────────────────┐
    │                  SQS orders-dlq                      │
    │   (mensagem inválida chega com RejectionReason)      │
    └──────────────────────────────────────────────────────┘
```

### Desenvolvimento incremental

| Ciclo | Escopo | Status |
| --- | --- | --- |
| 1 | Infraestrutura de mensageria: SQS orders, worker Lambda, event source mapping, IAM, logs | ✅ concluído |
| 2 | Bhaskara event-driven: worker passa a calcular usando `calculator.py` | ✅ concluído |
| 3 | Output e DLQ: fila `results`, DLQ, retry, tratamento de erro | ✅ concluído |
| 4 | Producer: gerar N mensagens a partir de uma única requisição | ✅ concluído |
| 5 | API de status: métricas do processamento | ✅ concluído |
| 6 | Web dashboard: disparar a carga e acompanhar visualmente | ✅ concluído |
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
│   │   ├── calculator.py       # regra de negócio (reutilizada do Checkpoint 1)
│   │   └── api_auth.py         # verificação da chave, usada pelos dois handlers HTTP
│   └── handlers/
│       ├── worker/
│       │   └── handler.py      # consome a fila orders
│       ├── producer/
│       │   ├── handler.py      # recebe POST /orders e publica em lotes
│       │   └── generator.py    # constrói as equações
│       └── status/
│           └── handler.py      # responde GET /status
├── tests/
│   ├── test_calculator.py         # 18 casos, herdados do Checkpoint 1
│   ├── test_worker_handler.py     # 45 casos, contrato com a SQS, cálculo e falhas
│   ├── test_generator.py          # 16 casos, variedade e correção das equações
│   ├── test_producer_handler.py   # 33 casos, requisição, lotes e chave de API
│   └── test_status_handler.py     # 30 casos, contadores, cursor e espiada na DLQ
├── infra/                      # Terraform
│   ├── versions.tf  main.tf  variables.tf  outputs.tf
│   ├── sqs.tf                  # filas orders, results e DLQ
│   ├── iam.tf                  # roles e políticas do worker e do producer
│   ├── worker.tf               # Lambda, log group, event source mapping
│   ├── producer.tf             # Lambda, log group, chave de API
│   ├── status.tf               # Lambda de métricas
│   ├── apigateway.tf           # HTTP API, rotas POST /orders e GET /status
│   └── dashboard.tf            # S3 privado + CloudFront do painel
├── web/                        # painel: publicado no S3 pelo Terraform
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── scripts/
│   ├── send-test-message.sh    # publica direto na fila (sem passar pela API)
│   └── generate-load.sh        # dispara carga pelo endpoint e acompanha as filas
└── docs/
    └── cycle-01.md
```

A separação segue quatro camadas independentes: **regra de negócio**
(`src/shared`), **processamento de eventos** (`src/handlers`), **infraestrutura**
(`infra`) e **frontend** (`web/`).

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
145 passed
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

### Recursos criados

| Recurso | Nome | Observação |
| --- | --- | --- |
| `aws_sqs_queue` | `bhaskara-events-dev-orders` | entrada; visibility 60 s, retenção 4 dias, long polling 20 s, SSE, redrive para a DLQ após 3 entregas |
| `aws_sqs_queue` | `bhaskara-events-dev-results` | saída; mesmos parâmetros |
| `aws_sqs_queue` | `bhaskara-events-dev-orders-dlq` | dead letter queue; retenção 14 dias |
| `aws_lambda_function` | `bhaskara-events-dev-worker` | Python 3.13, arm64, 128 MB, timeout 10 s |
| `aws_lambda_event_source_mapping` | — | batch 10, janela 0 s, `ReportBatchItemFailures` |
| `aws_iam_role` + `aws_iam_role_policy` | `bhaskara-events-dev-worker-role` | ARN restrito, sem `Resource: "*"` |
| `aws_lambda_function` | `bhaskara-events-dev-producer` | Python 3.13, arm64, 256 MB, timeout 30 s |
| `aws_lambda_function` | `bhaskara-events-dev-status` | Python 3.13, arm64, 128 MB, timeout 10 s |
| `aws_apigatewayv2_api` + rotas + stage | `bhaskara-events-dev` | HTTP API, `POST /orders` e `GET /status`, throttling 5 rps |
| `random_password` | — | chave de API, 40 caracteres, output `sensitive` |
| `aws_iam_role` + `aws_iam_role_policy` | `…-producer-role` | `sqs:SendMessage` apenas na `orders` |
| `aws_iam_role` + `aws_iam_role_policy` | `…-status-role` | só leitura: sem `SendMessage`, sem `DeleteMessage` |
| `aws_s3_bucket` + `aws_cloudfront_distribution` | `…-dashboard-<conta>` | bucket **privado**, servido só via CloudFront com Origin Access Control |
| `aws_cloudwatch_log_group` | `/aws/lambda/bhaskara-events-dev-{worker,producer,status}` | retenção 7 dias, removidos no `destroy` |

## Gerando carga

Uma única requisição gera milhares de equações:

```bash
./scripts/generate-load.sh              # 1.000 mensagens válidas
./scripts/generate-load.sh 1000 0.05    # 1.000 com 5% inválidas, para ver a DLQ
```

O script dispara a requisição e acompanha as três filas até a `orders` drenar.

Manualmente:

```bash
curl -s -X POST "$(terraform -chdir=infra output -raw producer_url)" \
  -H "x-api-key: $(terraform -chdir=infra output -raw api_key)" \
  -H 'Content-Type: application/json' \
  -d '{"quantity": 1000}'
```

```json
{"requested": 1000, "published": 1000, "batches": 100, "elapsed_ms": 4510}
```

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `quantity` | sim | inteiro entre 1 e 5.000 |
| `invalid_ratio` | não | proporção de mensagens inválidas, 0 a 1 (padrão 0) |
| `seed` | não | torna a carga reproduzível |

**A chave de API é obrigatória.** O endpoint gera carga — uma requisição vira
até 5.000 mensagens — e sem autenticação seria um gerador de custo para quem o
encontrasse. Sem a chave, a resposta é `403` e **nenhuma mensagem é gerada**.
Obtenha a chave com `terraform output -raw api_key`; ela nunca é versionada.
Detalhes em [`docs/cycle-04.md`](docs/cycle-04.md).

## O painel

```bash
terraform -chdir=infra output -raw dashboard_url   # abra no browser
terraform -chdir=infra output -raw api_key         # cole no campo do painel
```

Informe a quantidade e a proporção de mensagens inválidas, clique em **Gerar
mensagens** e acompanhe a fila enchendo e drenando, os contadores de sucesso e
falha, o fluxo de equações resolvidas e as mensagens da DLQ com o motivo.

> **A chave de API não está no bundle.** A página é pública no CloudFront, e uma
> chave embutida seria uma chave publicada. Ela é colada no painel e fica apenas
> no `localStorage` daquele browser. O bucket, por sua vez, nunca é público:
> todo acesso passa pelo CloudFront via Origin Access Control.

Detalhes de desenho e os três defeitos que o teste E2E encontrou estão em
[`docs/cycle-06.md`](docs/cycle-06.md).

## Acompanhando o processamento

```bash
STATUS="$(terraform -chdir=infra output -raw status_url)"
KEY="$(terraform -chdir=infra output -raw api_key)"

curl -s "$STATUS" -H "x-api-key: $KEY"
```

```json
{"queued": 347, "in_flight": 110, "succeeded": 640, "failed": 13,
 "processed": 653, "checked_at": 1787805030643}
```

| Parâmetro | Descrição |
| --- | --- |
| `events=N&since=<cursor>` | até 100 desfechos posteriores ao cursor, com `events_cursor` para o próximo poll |
| `dlq=N` | até 10 mensagens da DLQ com corpo e motivo, **sem consumi-las** |

Sem parâmetros a resposta sai de três chamadas de `GetQueueAttributes` e a
função executa em ~100 ms — rápido o bastante para o painel do Ciclo 6 fazer
polling. Detalhes em [`docs/cycle-05.md`](docs/cycle-05.md).

> `succeeded` e `failed` são contadores **acumulados**, não por execução: nada
> consome a `results` nem a DLQ, então elas são o placar desde a última limpeza.
> Tire uma leitura antes de disparar a carga e subtraia.

## Validando

O script faz o ciclo completo: publica, espera, confere que cada mensagem
publicada teve um desfecho no log e mostra se a fila foi drenada.

```bash
./scripts/send-test-message.sh              # uma equação válida
./scripts/send-test-message.sh 10           # dez equações variadas
./scripts/send-test-message.sh --invalid    # lote de mensagens inválidas
```

Manualmente:

```bash
QUEUE_URL="$(terraform -chdir=infra output -raw orders_queue_url)"

aws sqs send-message --queue-url "$QUEUE_URL" --message-body '{"a":1,"b":-5,"c":6}'
aws logs tail /aws/lambda/bhaskara-events-dev-worker --since 5m --format short
```

O worker emite uma linha JSON por evento:

```json
{"event": "message_processed", "message_id": "88a3...", "a": 1, "b": -5, "c": 6, "delta": 1, "x1": 3.0, "x2": 2.0}
{"event": "message_rejected",  "message_id": "32e8...", "reason": "Coeficientes ausentes: c."}
```

JSON e não texto livre porque o painel do Ciclo 6 vai ler estes mesmos campos, e
o CloudWatch Logs Insights consulta JSON por campo sem precisar de parser.

### Contrato da mensagem

```json
{"a": 1, "b": -5, "c": 6}
```

A validação é estrita: o corpo é JSON, então número chega como número — uma
string `"1"` no lugar de `1` é recusada, porque aceitá-la esconderia um producer
com defeito. `a = 0`, valores não finitos e coeficientes que estouram o ponto
flutuante também são recusados, pelas regras do próprio `calculator.py`.

**Mensagens inválidas são registradas e descartadas neste ciclo** — a DLQ que
lhes dará destino entra no Ciclo 3. Ver [`docs/cycle-02.md`](docs/cycle-02.md).

## Decisões de infraestrutura

**Fila Standard, não FIFO.** O que este projeto demonstra é paralelismo e vazão,
e a ordem entre equações independentes não significa nada. FIFO limitaria a 300
mensagens/s por grupo e encareceria sem benefício.

**`ReportBatchItemFailures` desde o Ciclo 1.** O handler devolve
`{"batchItemFailures": []}` mesmo sem ter o que falhar ainda. Sem esse contrato,
no Ciclo 3 uma única mensagem ruim faria o lote inteiro (até 10) ser reentregue
e ir para a DLQ junto — inclusive as que já tinham sido processadas com sucesso.
Adotar depois significaria reescrever handler e testes.

**Dois caminhos de falha, deliberadamente diferentes.** Um erro **permanente**
(JSON malformado, coeficiente ausente, `a = 0`, overflow) faz o worker publicar
direto na DLQ e confirmar a mensagem: reentregar três vezes algo que nunca vai
funcionar só gastaria invocações e atrasaria a chegada na DLQ — e, de quebra, a
mensagem chega lá com o **motivo da recusa anexado**, coisa que o redrive nativo
não faz. Um erro **inesperado** (falha ao publicar, indisponibilidade) volta em
`batchItemFailures` e a SQS reentrega até `maxReceiveCount` antes de mover para
a DLQ. Ver [`docs/cycle-03.md`](docs/cycle-03.md).

**Visibility timeout de 60 s = 6× o timeout da função.** Recomendação da AWS:
evita que um retry do lote concorra com a execução ainda em andamento. Como o
visibility timeout também é o intervalo entre tentativas, o timeout da função
foi baixado de 30 s para 10 s — o worker processa um lote de 10 em ~20 ms, e o
valor menor faz o ciclo até a DLQ levar ~2 min em vez de ~6, o que torna a
demonstração viável.

**Retenção de 4 dias nas filas, 14 dias na DLQ.** Uma mensagem na DLQ é um
problema a investigar, e o tempo de investigar costuma ser bem maior que o tempo
de processar. (Nos Ciclos 1 e 2 a `orders` usava 4 horas, para limitar o loop de
reentrega enquanto não havia DLQ; com `maxReceiveCount` o loop tem fim.)

**Política IAM inline em vez das managed policies.**
`AWSLambdaBasicExecutionRole` e `AWSLambdaSQSQueueExecutionRole` concedem acesso
sobre `"*"` — todos os log groups e todas as filas da conta. A inline aponta para
os recursos exatos, e separa consumo de publicação: o worker consome **apenas**
da `orders` e publica **apenas** em `results` e na DLQ. Ele não tem
`sqs:SendMessage` na `orders` (quem publica ali é o producer do Ciclo 4, com role
própria) nem `ReceiveMessage` nas filas de saída.

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
