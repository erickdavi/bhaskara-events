# Ciclo 5 — API de status

**Concluído em:** 2026-08-27
**Objetivo:** um endpoint que devolva as métricas do processamento, para o
painel do Ciclo 6 consumir.

```http
GET /status
GET /status?events=50&since=<cursor>&dlq=5
x-api-key: <chave>
```

```json
{"queued": 0, "in_flight": 0, "succeeded": 960, "failed": 40,
 "processed": 1000, "checked_at": 1787805030643}
```

## De onde vem cada número

| Campo | Origem |
| --- | --- |
| `queued` | `ApproximateNumberOfMessages` da `orders` |
| `in_flight` | `ApproximateNumberOfMessagesNotVisible` da `orders` |
| `succeeded` | profundidade da `results` |
| `failed` | profundidade da DLQ |
| `processed` | `succeeded + failed`, derivado |

`succeeded` e `failed` são **contadores acumulados, não por execução**. Nada
consome a `results` nem a DLQ, então elas funcionam como o placar do que já
aconteceu desde a última limpeza. O painel tira uma leitura no início da carga e
subtrai.

### Por que não agregar em DynamoDB

Seria a resposta "certa" para números por execução e histórico: o worker
incrementa contadores atômicos por desfecho e o status lê.

Custa mais um componente e uma escrita por mensagem processada — 5.000
mensagens viram 5.000 writes, ou 500 se agrupadas por invocação. E não resolve
nada que a profundidade das filas já não responda, porque **as filas de saída
não são consumidas**: elas já são o contador.

Manter o cálculo do delta no cliente é o que permite a este endpoint ser sem
estado e responder em ~100 ms. Se um dia um consumidor da `results` existir, a
premissa cai e o DynamoDB passa a se justificar.

## Caminho rápido por padrão

Sem parâmetros, a resposta sai de **três chamadas de `GetQueueAttributes`** e
nada mais. Eventos e espiada na DLQ custam uma chamada a mais cada e só
acontecem quando pedidos — o painel vai consultar este endpoint a cada poucos
segundos.

| Modo | Duração da função |
| --- | --- |
| Cold start | ~6 s (import do `boto3`) |
| Caminho rápido, aquecido | **94 ms** |
| Com `?events=20` | **135 ms** |

O cold start desaparece assim que o painel começa a fazer polling. Medido pelo
`REPORT` do CloudWatch; o tempo de parede a partir do Brasil acrescenta ~600 ms
de RTT e handshake TLS por chamada.

## O fluxo de eventos

`?events=50&since=<cursor>` devolve os desfechos registrados **após** o cursor,
do mais antigo ao mais novo, junto com `events_cursor` — o timestamp mais
recente visto. O painel guarda esse valor e pede só o que veio depois, montando
um fluxo contínuo em vez de rebuscar a mesma janela a cada poll.

Dois detalhes que evitam bugs visíveis:

- **`startTime = since + 1`.** Sem o `+1`, cada poll devolveria de novo o último
  evento do poll anterior e o painel mostraria duplicatas.
- **O filtro roda no CloudWatch, não na Lambda.** Uma carga de 5.000 mensagens
  gera outras 5.000 linhas de `message_received` que o painel não usa; filtrar
  na origem evita transferi-las.

Sem cursor, a janela padrão é o último minuto — um painel recém-aberto recebe o
passado recente em vez do log inteiro desde o início dos tempos.

## A espiada na DLQ

`?dlq=5` devolve até 10 mensagens com corpo, motivo e id de origem:

```json
{"body": "{\"a\": 0, \"b\": -6, \"c\": -2}",
 "reason": "O valor de 'a' não pode ser zero.",
 "source_message_id": "2249c093-058d-48ae-83e3-a2a72ecaea27"}
```

`VisibilityTimeout=0` devolve as mensagens à visibilidade imediatamente: a
espiada não esconde nada de ninguém nem impede reprocessamento. E a policy
**não tem `sqs:DeleteMessage`** — esta função consegue olhar a DLQ, nunca
esvaziá-la.

A SQS amostra os servidores que guardam a fila, então uma chamada pode não
devolver todas as mensagens nem sempre as mesmas. Serve para mostrar exemplos no
painel; para inventário existe o contador `failed`.

Mensagens que chegaram pelo redrive nativo não têm `RejectionReason` — a SQS
move o payload original e não sabe por que ele falhou. O campo vem `null`, e o
painel deve exibi-las como "sem motivo registrado".

## IAM: o papel mais restrito dos três

É o único que toca as três filas, e justamente por isso é o mais limitado no
verbo:

| Ação | Recurso |
| --- | --- |
| `sqs:GetQueueAttributes` | `orders`, `results`, DLQ |
| `sqs:ReceiveMessage` | **só** a DLQ |
| `logs:FilterLogEvents` | **só** o log group do worker |
| `logs:Create*`/`PutLogEvents` | só o próprio log group |

Sem `SendMessage`, sem `DeleteMessage`, sem acesso a nenhum outro log group.

## Recursos AWS

**Criados (7):** `aws_lambda_function.status`, `integration`, `route`,
`permission`, `aws_iam_role.status`, `aws_iam_role_policy.status`,
`aws_cloudwatch_log_group.status`.
**Alterado (1):** o pacote do producer, que passa a levar `api_auth.py`.

### `shared/api_auth.py`

A verificação da chave saiu do producer para `src/shared/`, agora que dois
handlers precisam dela. É o segundo módulo compartilhado, ao lado do
`calculator.py` — e a razão de `shared/` existir desde o Ciclo 1.

## Critérios de aceitação e evidência

| # | Critério | Resultado |
| --- | --- | --- |
| 1 | **Devolve as métricas do processamento** | ✅ `{"queued":0,"in_flight":0,"succeeded":960,"failed":40,"processed":1000}` |
| 2 | Os números batem com a carga real | ✅ 960 + 40 = 1.000, exatamente a carga do Ciclo 4 |
| 3 | Rápido o bastante para polling | ✅ 94 ms no caminho rápido |
| 4 | Fluxo de eventos sem duplicatas | ✅ 26 + 34 = 60 eventos, cada um uma vez só |
| 5 | Espiada na DLQ não consome | ✅ `VisibilityTimeout=0`, sem `DeleteMessage` na policy |
| 6 | Sem chave → 403 | ✅ verificado na AWS |
| 7 | Testes | ✅ `142 passed` (eram 112) |

### Evidência — o fluxo durante uma carga de 60 mensagens

```text
poll 1: queued=0 in_flight=0  succeeded=960  failed=40 | novos eventos=0
poll 2: queued=0 in_flight=0  succeeded=960  failed=43 | novos eventos=26  {processed:23, rejected:3}
poll 3: queued=0 in_flight=0  succeeded=1016 failed=44 | novos eventos=34  {processed:33, rejected:1}
poll 4: queued=0 in_flight=0  succeeded=1016 failed=40 | novos eventos=0
```

56 processadas + 4 recusadas = 60, e os 60 eventos chegaram distribuídos em dois
polls sem nenhuma repetição.

## Duas características que o painel precisa tratar

**Latência de ingestão do CloudWatch Logs.** O poll 1 veio vazio mesmo já
havendo mensagens processadas: uma linha de log leva alguns segundos até ficar
consultável. É o que separa "praticamente em tempo real" de "tempo real". Os
contadores, que vêm da SQS, não têm essa defasagem.

**Contadores aproximados.** Repare no `failed` oscilando 43 → 44 → 40 → 44 entre
polls. Os atributos da SQS são eventualmente consistentes: o nome
`ApproximateNumberOfMessages` é literal. O painel deve tratá-los como
indicadores, e não como um livro-caixa — e nunca alarmar por uma variação de
poucas unidades entre duas leituras.

## Como reproduzir

```bash
STATUS="$(terraform -chdir=infra output -raw status_url)"
KEY="$(terraform -chdir=infra output -raw api_key)"

curl -s "$STATUS" -H "x-api-key: $KEY"
curl -s "$STATUS?dlq=5" -H "x-api-key: $KEY"
curl -s "$STATUS?events=50&since=0" -H "x-api-key: $KEY"
```

## Próximo passo — Ciclo 6

O painel web. Todos os números que ele precisa já existem neste endpoint:
`queued`, `in_flight`, `succeeded`, `failed`, `processed`, o fluxo de eventos
com cursor e a espiada na DLQ. Falta a página que dispara a carga pelo
`POST /orders` e faz polling do `GET /status`.
