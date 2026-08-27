# Bhaskara Events

Arquitetura orientada a eventos para cálculo de equações do segundo grau na AWS.
Uma requisição gera milhares de equações numa fila; funções Lambda as resolvem
de forma assíncrona; um painel web mostra tudo acontecendo.

> **Checkpoint 2 — event-driven.** O [Checkpoint 1](https://github.com/erickdavi/bhaskara-api)
> é uma API serverless **síncrona** (`API Gateway → Lambda → resposta HTTP`) e
> vive em outro repositório, sem alteração. Este projeto é independente dele:
> a mesma regra de negócio, uma arquitetura completamente diferente ao redor.

```bash
git clone git@github.com:erickdavi/bhaskara-events.git
cd bhaskara-events
./run.sh                                            # 145 testes, sem AWS
cd infra && terraform init && terraform apply       # ~5 min
terraform output -raw dashboard_url                 # abra no browser
terraform output -raw api_key                       # cole no painel
```

## Índice

- [Arquitetura](#arquitetura)
- [Como funciona](#como-funciona)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Pré-requisitos](#pré-requisitos)
- [Executando os testes](#executando-os-testes)
- [Implantando na AWS](#implantando-na-aws)
- [Usando o painel](#usando-o-painel)
- [A API](#a-api)
- [Validando pela linha de comando](#validando-pela-linha-de-comando)
- [Recursos criados](#recursos-criados)
- [Segurança](#segurança)
- [Custos](#custos)
- [Limpeza](#limpeza)
- [Limitações conhecidas](#limitações-conhecidas)
- [Decisões de arquitetura](#decisões-de-arquitetura)
- [Histórico de desenvolvimento](#histórico-de-desenvolvimento)

## Arquitetura

```text
                    ┌───────────────────────────────┐
                    │   Painel  (CloudFront + S3)   │
                    │   quantidade · [Gerar]        │
                    └───────┬───────────────┬───────┘
        POST /orders        │               │        GET /status
        {"quantity": 1000}  │               │
                    ┌───────▼───────────────▼───────┐
                    │     API Gateway (HTTP API)    │   throttling 5 rps
                    └───────┬───────────────┬───────┘   chave em x-api-key
                            │               │
                  ┌─────────▼──────┐   ┌────▼───────────┐
                  │    Producer    │   │     Status     │
                  │ gera equações  │   │ lê as 3 filas  │
                  └─────────┬──────┘   └────┬───────────┘
         SendMessageBatch   │               │ GetQueueAttributes
              (10 por vez)  │               │ + FilterLogEvents
                            ▼               │
                     ┌─────────────┐        │
                     │ SQS orders  │◄───────┘
                     └──────┬──────┘
                            │ event source mapping (lote de 10)
                     ┌──────▼──────────────┐
                     │    Worker Lambda    │  usa shared/calculator.py
                     └──┬───────────────┬──┘
              sucesso   │               │   inválida (erro permanente)
                        ▼               │
                 ┌─────────────┐        │
                 │ SQS results │        │
                 └─────────────┘        │
                                        │
     falha inesperada → batchItemFailures → retry ×3 → redrive
                        │               │
                        ▼               ▼
                 ┌──────────────────────────────┐
                 │       SQS orders-dlq         │
                 └──────────────────────────────┘
```

## Como funciona

1. O painel envia `POST /orders {"quantity": 1000}`.
2. O **producer** gera mil equações e as publica na `orders` em 100 lotes de
   `SendMessageBatch`, respondendo `202` em poucos segundos — sem esperar o
   processamento.
3. O **event source mapping** entrega lotes de até 10 mensagens ao **worker**,
   escalando sozinho.
4. O worker resolve cada equação com `calculator.py` e publica o resultado na
   `results`. O que não dá para resolver vai para a **DLQ**, com o motivo.
5. O painel consulta `GET /status` a cada 2 s e mostra a fila enchendo,
   drenando, os contadores e as equações resolvidas.

### Os dois caminhos de falha

Falha permanente e falha inesperada **não compartilham o mesmo caminho**:

| | Permanente | Inesperada |
| --- | --- | --- |
| Exemplos | JSON malformado, coeficiente ausente, `a = 0`, overflow | falha ao publicar, indisponibilidade, permissão revogada |
| Reentregar ajuda? | Não — o desfecho seria idêntico | Sim |
| O que o worker faz | publica na DLQ e **confirma** a mensagem | devolve o `messageId` em `batchItemFailures` |
| Como chega na DLQ | direto, com o motivo anexado | pelo `redrive_policy`, após 3 entregas |

Uma mensagem que chega pela DLQ nativa não traz motivo — a SQS move o payload
original e não sabe por que ele falhou. É essa diferença que justifica os dois
caminhos.

## Estrutura do projeto

```text
bhaskara-events/
├── conftest.py                    # sys.path dos testes = sys.path da Lambda
├── run.sh                         # bootstrap do venv + testes
├── requirements.txt
├── src/
│   ├── shared/                    # o que mais de um handler usa
│   │   ├── calculator.py          # regra de negócio (cópia literal do CP1)
│   │   └── api_auth.py            # verificação da chave de API
│   └── handlers/                  # um diretório por função Lambda
│       ├── worker/handler.py      # consome orders, resolve, publica em results
│       ├── producer/
│       │   ├── handler.py         # recebe POST /orders, publica em lotes
│       │   └── generator.py       # constrói as equações
│       └── status/handler.py      # responde GET /status
├── tests/                         # 145 casos, nenhum toca a AWS
├── infra/                         # Terraform
│   ├── versions.tf  main.tf  variables.tf  outputs.tf
│   ├── sqs.tf                     # orders, results, DLQ
│   ├── iam.tf                     # as três roles
│   ├── worker.tf  producer.tf  status.tf
│   ├── apigateway.tf              # HTTP API e as duas rotas
│   └── dashboard.tf               # S3 privado + CloudFront
├── web/                           # painel, publicado no S3 pelo Terraform
│   ├── index.html  styles.css  app.js
├── scripts/
│   ├── send-test-message.sh       # publica direto na fila, sem passar pela API
│   └── generate-load.sh           # dispara pelo endpoint e acompanha as filas
└── docs/
    └── cycle-01.md … cycle-07.md  # uma nota por ciclo de desenvolvimento
```

Quatro camadas independentes: **regra de negócio** (`src/shared`),
**processamento de eventos** (`src/handlers`), **infraestrutura** (`infra`) e
**frontend** (`web`).

### Sobre `calculator.py`

Cópia **literal, byte a byte** do
[Checkpoint 1](https://github.com/erickdavi/bhaskara-api), junto com os seus 18
testes. É a única coisa reaproveitada, de propósito: a regra matemática é a
mesma, o que muda é tudo ao redor. Ela é pura, usa só a biblioteca padrão e tem
um contrato de erro único (`ValueError` para `a = 0`, valores não finitos e
overflow) — exatamente o que um worker precisa para classificar mensagem boa e
mensagem ruim.

### Empacotamento

Cada função tem seu próprio `data.archive_file`, montado a partir de arquivos
explícitos. Os módulos vão para a **raiz do zip**, lado a lado, porque é assim
que a Lambda resolve imports: o handler faz `from calculator import calculate`,
sem prefixo de pacote.

| Função | Conteúdo do zip |
| --- | --- |
| worker | `handler.py`, `calculator.py` |
| producer | `handler.py`, `generator.py`, `api_auth.py` |
| status | `handler.py`, `api_auth.py` |

O `conftest.py` reproduz esse mesmo `sys.path` nos testes, para que eles
exercitem os mesmos imports que rodam na nuvem.

## Pré-requisitos

| Ferramenta | Versão | Para quê |
| --- | --- | --- |
| Python | ≥ 3.9 | rodar os testes |
| Terraform | ≥ 1.5 | provisionar |
| AWS CLI | v2 | validar pela linha de comando |
| Credenciais AWS | — | `aws sts get-caller-identity` deve responder |

As permissões necessárias na conta: IAM, Lambda, SQS, API Gateway, CloudWatch
Logs, S3 e CloudFront.

## Executando os testes

**Não precisa de credenciais AWS** — nenhum teste toca a nuvem. Os clientes SQS
e CloudWatch são substituídos por dublês, via fixture `autouse`, para que um
teste que esquecesse a fixture não publicasse na fila de verdade.

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

| Arquivo | Casos | O que cobre |
| --- | --- | --- |
| `test_calculator.py` | 18 | a regra matemática: precisão, ordem das raízes, limites numéricos |
| `test_worker_handler.py` | 45 | contrato com a SQS, cálculo, DLQ e retry |
| `test_producer_handler.py` | 33 | requisição, lotes, orçamento de tempo, chave de API |
| `test_generator.py` | 16 | variedade e correção das equações geradas |
| `test_status_handler.py` | 33 | contadores, cursor de eventos, espiada na DLQ |

## Implantando na AWS

```bash
cd infra
terraform init
terraform apply
```

O `apply` leva **cerca de 5 minutos** — a distribuição CloudFront responde por
quase todo esse tempo. Não há passo de build: o `archive_file` empacota o código
durante o `plan`.

Os outputs entregam tudo o que se precisa:

```bash
terraform output -raw dashboard_url    # o painel
terraform output -raw api_key          # a chave (sensitive; nunca versionada)
terraform output -raw producer_url     # POST /orders
terraform output -raw status_url       # GET /status
terraform output -raw orders_queue_url
```

## Usando o painel

1. Abra a URL do `dashboard_url`.
2. Cole a chave do `api_key` no campo **Chave de API** e clique em **Salvar**.
   Ela fica apenas no `localStorage` daquele browser.
3. Informe a quantidade e a proporção de mensagens inválidas.
4. Clique em **Gerar mensagens**.

O painel mostra, atualizando a cada 2 segundos:

- **Aguardando** e **Em processamento** — a fila `orders`
- **Sucessos** e **Falhas** — as filas `results` e `orders-dlq`
- Barra de progresso desta execução
- Gráfico da fila ao longo do tempo
- Fluxo de eventos, como equações legíveis:
  `6x² + 36x + 54 = 0 → x₁=-3 x₂=-3`
- Mensagens da DLQ com o motivo da recusa

> **A chave não está no bundle.** A página é pública no CloudFront, e uma chave
> embutida seria uma chave publicada. O `config.js` gerado pelo Terraform leva
> apenas a URL da API.

## A API

Ambas as rotas exigem o header `x-api-key`. Sem ele, `403` e nenhuma carga é
gerada.

### `POST /orders` — gerar mensagens

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

Resposta **202 Accepted**: as mensagens foram aceitas para processamento, que
acontece depois e em outro lugar.

Se a função chegar perto do corte de 30 s do API Gateway, ela para e responde
`"truncated": true` com o que conseguiu publicar. Uma resposta honesta de
"publiquei 3.210" é melhor que um timeout.

### `GET /status` — acompanhar

```bash
curl -s "$(terraform -chdir=infra output -raw status_url)" \
  -H "x-api-key: $(terraform -chdir=infra output -raw api_key)"
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
função executa em ~100 ms.

> `succeeded` e `failed` são contadores **acumulados**, não por execução: nada
> consome a `results` nem a DLQ, então elas são o placar desde a última limpeza.
> O painel tira uma leitura antes de disparar a carga e subtrai.

## Validando pela linha de comando

```bash
./scripts/generate-load.sh              # 1.000 mensagens pelo endpoint
./scripts/generate-load.sh 1000 0.05    # com 5% inválidas, para ver a DLQ

./scripts/send-test-message.sh              # publica direto na fila
./scripts/send-test-message.sh 10           # dez equações variadas
./scripts/send-test-message.sh --invalid    # cinco inválidas
```

O `generate-load.sh` dispara a requisição e acompanha as três filas até a
`orders` drenar. O `send-test-message.sh` pula a API e publica direto na fila —
útil para testar o worker isoladamente.

Logs:

```bash
aws logs tail "$(terraform -chdir=infra output -raw worker_log_group)" --follow --format short
```

## Recursos criados

**37 recursos gerenciados.** Nenhum tem custo fixo.

| Recurso | Nome | Observação |
| --- | --- | --- |
| `aws_sqs_queue` | `…-orders` | entrada; visibility 60 s, retenção 4 dias, long polling 20 s, SSE, redrive após 3 entregas |
| `aws_sqs_queue` | `…-results` | saída dos cálculos bem-sucedidos |
| `aws_sqs_queue` | `…-orders-dlq` | dead letter queue; retenção 14 dias |
| `aws_lambda_function` | `…-worker` | Python 3.13, arm64, 128 MB, 10 s |
| `aws_lambda_function` | `…-producer` | Python 3.13, arm64, 256 MB, 30 s |
| `aws_lambda_function` | `…-status` | Python 3.13, arm64, 128 MB, 10 s |
| `aws_lambda_event_source_mapping` | — | batch 10, `ReportBatchItemFailures` |
| `aws_apigatewayv2_*` | `…-dev` | HTTP API, `POST /orders` e `GET /status`, throttling 5 rps |
| `aws_iam_role` ×3 + `policy` ×3 | `…-{worker,producer,status}-role` | ver [Segurança](#segurança) |
| `aws_cloudwatch_log_group` ×3 | `/aws/lambda/…` | retenção 7 dias, removidos no `destroy` |
| `aws_s3_bucket` + CloudFront | `…-dashboard-<conta>` | bucket **privado**, servido só via Origin Access Control |
| `random_password` | — | chave de API, 40 caracteres |

## Segurança

### Nada de credencial no repositório

O `.gitignore` cobre `*.tfstate*`, `*.tfvars`, `.terraform/`, `.env` e `*.zip`.
As credenciais AWS vêm do ambiente. A chave de API é gerada pelo Terraform, vive
no state (não versionado) e sai por `terraform output -raw api_key`.

O `.terraform.lock.hcl` **é** versionado, de propósito — é o que faz um clone
limpo resolver exatamente as mesmas versões de provider. Ele traz hashes para
Linux, macOS (Intel e ARM) e Windows.

### IAM: três papéis, nenhum com permissão do outro

Políticas inline com ARN restrito, em vez das managed policies
(`AWSLambdaBasicExecutionRole` e `AWSLambdaSQSQueueExecutionRole` concedem
acesso sobre `"*"` — todos os log groups e todas as filas da conta).

| | `orders` | `results` | DLQ | Logs |
| --- | --- | --- | --- | --- |
| **worker** | Receive, Delete, GetAttributes | Send | Send | escreve no próprio |
| **producer** | **Send** | — | — | escreve no próprio |
| **status** | GetAttributes | GetAttributes | GetAttributes, **Receive** | escreve no próprio; **lê o do worker** |

As roles são espelhadas: o producer publica na `orders` e não consome dela; o
worker consome e não publica nela. O status é o único que toca as três filas — e
o único **sem nenhum verbo de escrita em fila**: ele consegue olhar a DLQ, nunca
esvaziá-la (não tem `DeleteMessage`).

Nenhuma policy tem `"Resource": "*"`.

### O endpoint que gera carga

`POST /orders` transforma uma requisição em até 5.000 mensagens. Um endpoint
aberto seria um gerador de custo para quem o encontrasse. Três camadas:

1. **Chave de API** no header `x-api-key`, comparada com `hmac.compare_digest`
   para que o tempo não revele quantos caracteres iniciais estão corretos, e
   checada **antes do corpo** — responder `400` a um corpo inválido diria ao
   chamador anônimo que a chave estava certa. **Falha fechada**: sem chave
   configurada, nada passa.
2. **Throttling do stage**: 5 rps, burst 10.
3. **Teto de 5.000** mensagens por requisição.

O HTTP API não tem API key nativa (é recurso do REST API v1). A alternativa no
gateway seria um Lambda authorizer — uma função, uma role e um log group a mais
para comparar duas strings. Verificar no producer resolve o problema real: uma
requisição sem chave gera **zero mensagens**.

### O painel

- **Bucket privado.** Acesso só via Origin Access Control, e a bucket policy
  exige `AWS:SourceArn` igual ao ARN desta distribuição — sem isso, qualquer
  distribuição CloudFront de qualquer conta poderia ler o bucket. Acesso direto
  ao S3 devolve `AccessDenied`.
- **HTTPS obrigatório** (`redirect-to-https`).
- **A chave não está no bundle.** O `config.js` leva apenas a URL da API.
- **CORS restrito** à distribuição do painel, não `"*"`.
- **Cabeçalhos de segurança**: CSP (`default-src 'none'`, sem `unsafe-inline`),
  HSTS com um ano, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`.

### Criptografia em repouso

As três filas usam SSE gerenciado pela SQS; o bucket usa AES256. Sem KMS: uma
CMK só se justificaria com exigência de rotação ou de política de chave própria.

## Custos

**Nenhum recurso tem cobrança fixa.** Uma demonstração de 1.000 mensagens
consome:

| Serviço | Free tier mensal | Uso de uma demo | Fração |
| --- | --- | --- | --- |
| SQS | 1M requests | ~3.000 requests | 0,3% |
| Lambda | 1M invocações + 400k GB-s | ~110 invocações | ~0,0002% |
| CloudWatch Logs | 5 GB de ingestão | ~400 KB | 0,008% |
| S3 | 5 GB | ~25 KB | desprezível |
| CloudFront | 1 TB + 10M requests | alguns KB | desprezível |

Rodando a demonstração dezenas de vezes, a fatura fica em **zero** dentro do
free tier. Ainda assim, o hábito recomendado é destruir ao fim de cada sessão.

## Limpeza

```bash
cd infra
terraform destroy
```

Remove os 37 recursos, log groups incluídos — não fica resíduo na conta. Leva
**cerca de 5 minutos**: a distribuição CloudFront precisa ser desabilitada antes
de ser removida, e isso é o mais demorado.

Para conferir:

```bash
aws sqs list-queues
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'bhaskara-events')]"
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/bhaskara-events
```

O `force_destroy = true` no bucket faz o `destroy` remover os objetos junto —
sem ele, o bucket não vazio bloquearia a remoção.

## Limitações conhecidas

**Concorrência da conta em 10.** Esta conta AWS tem o limite padrão de contas
novas: 10 execuções Lambda simultâneas **no total**, divididas entre worker,
producer e status. Duas consequências:

- Nenhum valor de `reserved_concurrent_executions` é aceitável (a AWS recusa
  qualquer reserva que derrube a concorrência não reservada abaixo de 10), então
  as três funções ficam sem reserva.
- Sob carga, a função de status pode ser throttled e o painel recebe `503`. Ele
  trata isso: três tentativas com recuo exponencial e aviso discreto em vez de
  erro. Para elevar o limite: **Service Quotas → Lambda → Concurrent
  executions**.

**A chave de API vive numa variável de ambiente** da Lambda, o que a expõe a
quem tenha `lambda:GetFunctionConfiguration` na conta. Para um laboratório é
adequado. Um sistema real usaria o Secrets Manager — ao custo de ~US$ 0,40/mês
por segredo, uma chamada de API no cold start e uma permissão a mais em duas
roles. Fica registrado como decisão consciente, não como esquecimento.

**Contadores aproximados.** `ApproximateNumberOfMessages` é literal: os valores
são eventualmente consistentes e podem oscilar poucas unidades entre leituras. O
contador da DLQ atualiza mais devagar que o da `results`, então no fim de uma
carga a barra fica alguns segundos parada — o painel diz *"fila vazia,
contadores ainda assentando"* em vez de deixar parecer que travou.

**Eventos com alguns segundos de atraso.** O fluxo vem do CloudWatch Logs, que
leva alguns segundos para tornar uma linha consultável. É o que separa
"praticamente em tempo real" de "tempo real", e um WebSocket não eliminaria essa
defasagem.

**State local.** Adequado a um operador só, sem pipeline. O backend S3 está
comentado em `versions.tf` para quando houver CI/CD.

## Decisões de arquitetura

| Decisão | Por quê |
| --- | --- |
| Fila **Standard**, não FIFO | o projeto demonstra paralelismo e vazão; a ordem entre equações independentes não significa nada, e FIFO limitaria a 300 msg/s por grupo |
| `ReportBatchItemFailures` **desde o Ciclo 1** | sem esse contrato, uma única mensagem ruim faria o lote inteiro (até 10) ir para a DLQ, inclusive as já processadas com sucesso |
| Erro permanente vai **direto** para a DLQ | reentregar 3× algo que nunca vai funcionar gasta invocações e atrasa a chegada na DLQ; e o worker anexa o motivo, coisa que o redrive nativo não faz |
| Timeout do worker em **10 s** | o visibility timeout é derivado dele (6×, recomendação AWS) e também é o intervalo entre tentativas: 60 s em vez de 180 s faz o ciclo até a DLQ levar ~2 min em vez de ~6 |
| **256 MB** no producer, 128 MB nos demais | na Lambda a CPU é proporcional à memória; o producer faz centenas de chamadas de rede e termina antes, podendo custar o mesmo ou menos |
| Contadores da **profundidade das filas**, não DynamoDB | nada consome `results` nem a DLQ, então elas **já são** o contador; agregar custaria mais um componente e uma escrita por mensagem |
| **Polling**, não WebSocket | o fluxo vem do CloudWatch Logs, que já tem segundos de defasagem; um WebSocket entregaria a mesma informação com o mesmo atraso e muito mais complexidade |
| Equações **construídas a partir do desfecho** | sortear `a`, `b`, `c` ao acaso quase nunca produz `delta = 0`, que exige `b² == 4ac` exato |
| Log em **JSON**, não texto | a validação compara campos e o painel os lê; texto livre exigiria parser |
| Chave de API verificada **no producer** | o HTTP API não tem API key nativa; um Lambda authorizer custaria função, role e log group para comparar duas strings |

Duas dependências circulares apareceram no Terraform e foram quebradas montando
o valor a mão em vez de referenciar o recurso:

- `orders` aponta para a DLQ no `redrive_policy`, e a DLQ precisa apontar de
  volta no `redrive_allow_policy` → o ARN da `orders` é montado a partir do nome.
- A CSP nomearia a API, a API aponta para a distribuição no CORS e a
  distribuição aponta para a policy da CSP → o `connect-src` usa um curinga
  restrito à região.

## Histórico de desenvolvimento

Sete ciclos, cada um em um estado funcional e testável, documentado em
[`docs/`](docs/):

| Ciclo | Entrega | Nota |
| --- | --- | --- |
| 1 | Fila `orders`, worker, event source mapping, IAM, logs | [cycle-01](docs/cycle-01.md) |
| 2 | Worker resolve a equação com `calculator.py` | [cycle-02](docs/cycle-02.md) |
| 3 | Fila `results`, DLQ, retry, tratamento de erro | [cycle-03](docs/cycle-03.md) |
| 4 | Producer: N mensagens por requisição | [cycle-04](docs/cycle-04.md) |
| 5 | `GET /status` com as métricas | [cycle-05](docs/cycle-05.md) |
| 6 | Painel web | [cycle-06](docs/cycle-06.md) |
| 7 | Polimento e entrega | [cycle-07](docs/cycle-07.md) |

As notas de ciclo registram também o que **deu errado** — o limite de
concorrência que inviabilizou a reserva no Ciclo 1, o cursor de eventos travado
em zero que o teste E2E encontrou no Ciclo 6, e o contador da DLQ que parecia
travado mas só estava lento.
