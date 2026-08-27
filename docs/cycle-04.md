# Ciclo 4 — Producer

**Concluído em:** 2026-08-27
**Objetivo:** uma única solicitação HTTP gera N mensagens na fila `orders`.

```http
POST /orders
x-api-key: <chave>
Content-Type: application/json

{"quantity": 1000, "invalid_ratio": 0.05, "seed": 42}
```

```json
{"requested": 1000, "published": 1000, "batches": 100, "elapsed_ms": 4510}
```

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `quantity` | sim | inteiro entre 1 e 5.000 |
| `invalid_ratio` | não | proporção de mensagens propositalmente inválidas, 0 a 1 (padrão 0) |
| `seed` | não | torna a carga reproduzível: mesmo seed, mesmas equações |

Resposta **202 Accepted**, e não 200: as mensagens foram aceitas para
processamento, que acontece depois e em outro lugar. O resultado do cálculo não
está nesta resposta e nem poderia estar.

## As três restrições que moldaram o desenho

| Restrição | Consequência |
| --- | --- |
| `SendMessageBatch` publica no máximo **10 por chamada** | 1.000 mensagens são 100 chamadas de rede sequenciais |
| O HTTP API corta a integração em **30 s**, sem exceção | a função precisa responder antes disso, ou o cliente recebe erro sem saber o que foi publicado |
| A conta tem **10 execuções concorrentes no total**, compartilhadas com o worker | o producer é uma invocação por *requisição*, nunca uma por mensagem — do contrário consumiria sozinho toda a concorrência e travaria o próprio consumidor |

### O guarda de tempo

Antes de cada lote a função consulta `context.get_remaining_time_in_millis()`.
Com menos de 3 s de folga, ela para e responde:

```json
{"requested": 5000, "published": 3210, "batches": 321, "truncated": true,
 "detail": "A funcao parou antes do timeout. Reenvie a diferenca ..."}
```

Uma resposta honesta de "publiquei 3.210" é melhor que um timeout, que deixaria
o cliente sem saber quantas mensagens foram parar na fila.

### 256 MB no producer, contra 128 MB no worker

Na Lambda a CPU é proporcional à memória. O producer gera milhares de payloads e
faz centenas de chamadas de rede; mais memória termina antes e pode custar o
mesmo ou menos, já que a cobrança é GB × segundo.

## Geração das equações

As equações **não são coeficientes aleatórios soltos**. Sortear `a`, `b` e `c` ao
acaso produziria quase só dois desfechos — duas raízes ou nenhuma — e raiz dupla
(`delta = 0`) praticamente nunca apareceria, porque exige `b² == 4ac` na
igualdade exata. Um painel alimentado por esse sorteio mostraria uma
distribuição pobre.

Cada equação é construída **a partir do desfecho desejado**:

| Desfecho | Construção |
| --- | --- |
| Duas raízes reais | sorteia `r1 ≠ r2` e `a`, deriva `b = -a(r1+r2)` e `c = a·r1·r2` da forma fatorada |
| Raiz dupla | sorteia `r` e `a`, deriva `b = -2ar` e `c = ar²` |
| Sem raízes reais | sorteia `a > 0` e `b`, escolhe `c` acima de `b²/4a` com folga aleatória |

Os testes verificam a distribuição (cada desfecho entre 15% e 55% da amostra) e
conferem cada equação contra o `calculator.py`, que é a fonte da verdade.

### Mensagens inválidas

`invalid_ratio` é opcional e **zero por padrão** — gerar lixo sem que ninguém
tenha pedido seria surpreendente. Existe para alimentar a DLQ na demonstração.

As cinco categorias cobrem um caminho de recusa do worker cada: JSON malformado,
coeficiente ausente, coeficiente como string, coeficiente booleano e `a = 0`.
Um teste garante que o worker realmente recusa **todas** elas — se o gerador e o
worker discordassem sobre o que é inválido, a DLQ ficaria vazia na demonstração
e ninguém entenderia por quê.

## Segurança do endpoint

O endpoint **gera carga**: uma requisição vira até 5.000 mensagens, e cada
mensagem custa invocações de Lambda e requests de SQS. É diferente de um
endpoint que só faz uma conta e devolve — aqui, um endpoint aberto é um gerador
de custo para quem o encontrar.

**Chave de API no header `x-api-key`**, verificada dentro do próprio producer.

O HTTP API do API Gateway **não tem API key nativa** — isso é recurso do REST
API v1. As alternativas eram um Lambda authorizer (uma função, uma role e um log
group a mais para comparar duas strings) ou trocar para REST API (~3,5× mais
caro). A verificação no producer resolve o problema real: uma requisição sem
chave ainda invoca a função, mas gera **zero mensagens** — o abuso cai de 5.000
mensagens para uma invocação de ~2 ms, e o throttling do stage limita até isso.

Três detalhes:

- **Falha fechada.** Sem `API_KEY` no ambiente, nenhuma requisição passa. O
  contrário transformaria um erro de deploy em endpoint aberto sem ninguém
  perceber.
- **`hmac.compare_digest`** em vez de `==`, para que o tempo da comparação não
  revele quantos caracteres iniciais estão corretos.
- **A chave é checada antes do corpo.** Responder 400 a um corpo inválido diria
  ao chamador anônimo que a chave estava certa.

A chave é gerada pelo Terraform (`random_password`, 40 caracteres) e sai por
`terraform output -raw api_key`. Ela vive numa variável de ambiente da função, o
que a expõe a quem tiver `lambda:GetFunctionConfiguration` na conta — adequado
para um laboratório; um sistema real usaria o Secrets Manager.

### Roles espelhadas

O producer publica na `orders` e **não consome** dela. O worker consome e **não
publica** nela. Nenhuma das duas roles tem as permissões da outra: se o producer
for comprometido, ele não lê nem apaga o que já está na fila, e não alcança
`results` nem a DLQ.

## Recursos AWS criados (10)

`aws_apigatewayv2_api` · `integration` · `route` · `stage` ·
`aws_lambda_function.producer` · `aws_lambda_permission` ·
`aws_iam_role.producer` · `aws_iam_role_policy.producer` ·
`aws_cloudwatch_log_group.producer` · `random_password.api_key`

**Zero alterações** nos 8 recursos anteriores.

## Critérios de aceitação e evidência

| # | Critério | Resultado |
| --- | --- | --- |
| 1 | **Uma solicitação gera quantidade significativa** | ✅ 1.000 mensagens em 100 lotes, 4,5 s |
| 2 | **As mensagens são processadas assincronamente** | ✅ ver abaixo |
| 3 | Contabilidade fecha | ✅ 960 `results` + 40 DLQ = 1.000 |
| 4 | Sem chave → sem carga gerada | ✅ 403, zero mensagens |
| 5 | Requisições inválidas recusadas | ✅ 400 com mensagem específica |
| 6 | Nenhum erro | ✅ `Errors: 0` nas duas funções |
| 7 | Testes | ✅ `112 passed` (eram 63) |

### Evidência — a assincronia

Imediatamente após a resposta do producer chegar:

```json
{"ApproximateNumberOfMessages": "890", "ApproximateNumberOfMessagesNotVisible": "110"}
```

**890 aguardando e 110 já em processamento.** O worker começou a consumir antes
de o producer terminar de publicar — que é precisamente o que a arquitetura
event-driven promete e o que o modelo síncrono do Checkpoint 1 não consegue
fazer.

Drenagem completa:

```text
t(s)   orders   em voo   results   dlq
5      890      110      960       4
17     890      100      960       40
28     0        0        960       40
```

### Evidência — throttling absorvido como backpressure

A métrica do worker registrou **44 throttles** durante a carga, efeito direto do
limite de 10 execuções concorrentes da conta. Isso poderia ser um problema:
se cada throttle queimasse uma tentativa de entrega, mensagens válidas chegariam
à DLQ por `maxReceiveCount` sem nunca terem falhado de verdade.

Não foi o que aconteceu:

```text
receive_count=1   1000
receive_count=2   0
receive_count=3   0
```

**As 1.000 mensagens foram entregues exatamente uma vez.** E as 40 da DLQ
carregam todas um `RejectionReason`, ou seja, vieram do caminho de recusa
direta — nenhuma veio de redrive. O throttling foi absorvido como backpressure
pelo event source mapping, que reduz o ritmo de polling em vez de descartar
trabalho.

## Como reproduzir

```bash
./scripts/generate-load.sh              # 1.000 mensagens válidas
./scripts/generate-load.sh 500          # 500 mensagens
./scripts/generate-load.sh 1000 0.05    # 1.000 com 5% inválidas
```

O script dispara a requisição e acompanha as três filas até a `orders` drenar.

## Próximo passo — Ciclo 5

Uma API de status que devolva as métricas do processamento. A decisão de
desenho já visível: `GetQueueAttributes` é barato e imediato, mas só informa
profundidade de fila — **não distingue sucessos de falhas nem guarda
histórico**. Os contadores por desfecho existem hoje apenas no CloudWatch Logs.
