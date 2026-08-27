# Ciclo 6 — Painel web

**Concluído em:** 2026-08-27
**Objetivo:** disparar a carga e acompanhar o processamento visualmente.

O painel é uma página estática servida pelo CloudFront a partir de um bucket S3
privado. Ele fala apenas com os dois endpoints já existentes: `POST /orders`
para gerar e `GET /status` para acompanhar.

## O que a página mostra

| Elemento | Fonte |
| --- | --- |
| 4 KPIs: aguardando, em processamento, sucessos, falhas | `GET /status` |
| Barra de progresso desta execução | contadores menos a leitura tirada no clique |
| Gráfico da fila ao longo do tempo | histórico dos polls, no cliente |
| Fluxo de eventos | `?events=100&since=<cursor>` |
| Mensagens na DLQ com o motivo | `?dlq=8` |

Os eventos aparecem como equações legíveis, não como JSON cru:

```text
02:00:15  ✓ resolvida   -2x² + 18x − 28 = 0 → x₁=2 x₂=7
02:00:15  ✓ resolvida    6x² + 36x + 54 = 0 → x₁=-3 x₂=-3
02:00:15  ✓ resolvida    5x² + 24x + 48 = 0 → sem raízes reais
          ! recusada     {"a": true, ...} — O coeficiente 'a' deve ser um numero, e nao bool.
```

## Polling, e não WebSocket

O enunciado permitia polling se isso mantivesse a implementação mais simples.
Mantém — e há uma razão a mais: **o fluxo de eventos vem do CloudWatch Logs, que
leva alguns segundos para tornar uma linha consultável.** Um WebSocket entregaria
a mesma informação com a mesma defasagem, ao custo de API Gateway WebSocket,
gerenciamento de conexões e uma tabela de sessões.

O intervalo é adaptativo: 2 s durante uma carga, 8 s parado. O throttling do
stage é de 5 rps — um poll a cada 2 s fica ordens de grandeza abaixo.

## Decisões de segurança

**O bucket nunca é público.** O acesso vem de um Origin Access Control, e a
bucket policy exige `AWS:SourceArn` igual ao ARN desta distribuição — sem essa
condição, qualquer distribuição CloudFront de qualquer conta poderia ler o
bucket. Verificado: acessar o objeto direto pelo S3 devolve `AccessDenied`.

**A chave de API não entra no bundle.** O `config.js` gerado pelo Terraform leva
**apenas a URL da API**. O bundle é público no CloudFront, e uma chave embutida
seria uma chave publicada. O operador cola a chave no painel e ela fica só no
`localStorage` daquele browser. Verificado: nenhum dos quatro arquivos
publicados contém a chave.

**CORS deixou de ser `"*"`.** Até o Ciclo 5 não havia origem conhecida para
nomear; agora há a distribuição. CORS não autoriza nada — quem autoriza é a
chave — mas fecha a porta para uma página de terceiros tentar usar a chave de um
operador logado.

**TTL de 60 s em vez do padrão de 24 h.** O `CachingOptimized` da AWS faria toda
correção na página exigir invalidação manual, um passo a mais para errar durante
a demonstração.

## Design da visualização

A paleta foi validada com o script do método, `PASS` nos seis checks em modo
claro e escuro (separação CVD ΔE 24,7 no par categórico, contra um piso de 8).

- **Os números grandes usam tokens de texto, nunca a cor da série.** A identidade
  fica no ponto colorido ao lado do rótulo.
- **Todo rótulo tem ícone além da cor** — ⏳ ⚙️ ✓ ! — para que a cor nunca seja o
  único portador do sentido.
- Sucessos e falhas usam a paleta de **status** (verde `#0ca30c`, vermelho
  `#d03b3b`); o gráfico e os estados neutros usam a **categórica** (azul,
  laranja). Status nunca é reaproveitado como cor de série.
- Os segmentos da barra têm 2 px de vão na cor da superfície, para que duas
  faixas adjacentes não se leiam como uma só.
- Tema claro e escuro declarados como tokens nos três escopos (`:root`, a media
  query e `[data-theme]`).

## Três defeitos encontrados no teste E2E

O teste com Playwright contra a página publicada encontrou três problemas reais.
Todos foram corrigidos e revalidados.

### 1. HTTP 503 sob carga

O painel exibia um banner de erro no meio da demonstração. Causa: **as três
funções dividem as 10 execuções concorrentes da conta**, e durante a rajada o
worker toma quase todos os slots.

```text
worker    Invocations=31  Throttles=17
status    Invocations=8   Throttles=1   ← virou HTTP 503 no browser
```

Correção: `GET` passou a ter três tentativas com recuo exponencial (400, 800,
1600 ms) e o painel trata 429/5xx como transitório — mensagem discreta de
"serviço ocupado, reconectando" em vez de banner vermelho. **O `POST /orders`
não é repetido**: repetir publicaria a carga duas vezes.

### 2. O fluxo de eventos nunca aparecia

O painel iniciava o cursor em `0`. O `filter_log_events` com `startTime` muito
antigo varre o log desde o início e devolve **página vazia** junto de um
`nextToken` — então o painel recebia zero eventos e um cursor que nunca
avançava, travado para sempre.

```text
since=0     → eventos: 0   cursor: 0            (travado)
sem since   → eventos: 0   cursor: 1787806430724 (avança)
```

Correção em dois lugares: o painel omite `since` no primeiro poll, e o endpoint
passou a limitar a busca aos últimos 15 minutos, devolvendo um cursor nunca
anterior a esse piso. A correção no servidor é a que importa — ela torna a API
correta para qualquer cliente, não só para este painel.

Três testes do Ciclo 5 quebraram com essa mudança porque usavam timestamps de
1970 (`1000`, `2500`). Foram reescritos com instantes recentes: os valores
antigos nunca representaram um caso real.

### 3. A barra parecia travada no fim de cada carga

Sintoma: `357 de 400 processadas` com a fila vazia e nada mais acontecendo.
Investiguei instrumentando o `fetch` da página — e **não era bug de código**. O
trace mostrou o contador da DLQ pinado em `116` por quatro polls seguidos
enquanto `succeeded` subia, saltando para `147` só depois:

```text
GET /status  succeeded=1313  failed=116
GET /status  succeeded=1284  failed=116
GET /status  succeeded=1284  failed=116
GET /status  succeeded=1453  failed=147   ← só aqui
```

O `ApproximateNumberOfMessages` da DLQ atualiza em degraus e com atraso maior
que o da `results`. Não há mensagem perdida — a conta fecha, só depois.

Correção: o painel passou a dizer isso. Com a fila vazia e a conta ainda aberta,
o rótulo vira **"fila vazia, contadores ainda assentando"** em vez de deixar o
operador achando que travou. E a leitura de baseline se auto-corrige: como as
filas de saída só crescem, qualquer leitura menor que a baseline prova que a
baseline estava velha.

## Recursos AWS criados (11)

`aws_s3_bucket` + `public_access_block` + `sse` · `origin_access_control` ·
`cloudfront_distribution` · `cloudfront_cache_policy` · `bucket_policy` ·
`aws_s3_object` × 4. **Alterado (1):** o CORS do HTTP API.

A distribuição leva ~3 minutos para subir e outro tanto no `destroy`.

## Critérios de aceitação e evidência

| # | Critério | Resultado |
| --- | --- | --- |
| 1 | **Clicar no botão e acompanhar o processamento** | ✅ 400 mensagens, `357 + 43 = 400 — concluído` |
| 2 | Informar a quantidade | ✅ campo com teto de 5.000 |
| 3 | Gerar centenas ou milhares | ✅ 800 publicadas em 1.540 ms |
| 4 | Acompanhar aguardando | ✅ KPI + gráfico da fila |
| 5 | Acompanhar processadas / sucessos / falhas | ✅ KPIs + barra somando 100% |
| 6 | Visualizar mensagens da DLQ | ✅ corpo e motivo, sem consumi-las |
| 7 | Eventos quase em tempo real | ✅ 200 eventos com equações e raízes |
| 8 | Bucket privado | ✅ `AccessDenied` no acesso direto |
| 9 | Chave fora do bundle | ✅ ausente nos quatro arquivos |
| 10 | Testes | ✅ `145 passed` (eram 142) |

## Como usar

```bash
terraform -chdir=infra output -raw dashboard_url   # abra no browser
terraform -chdir=infra output -raw api_key         # cole no painel
```

A chave fica no `localStorage` daquele browser e não é enviada a mais ninguém.

## Próximo passo — Ciclo 7

Polimento e entrega: revisar Terraform, IAM, testes, README, instruções de
deploy e de teste, `.gitignore`, segurança, custos e limpeza dos recursos. O
README precisa permitir que outra pessoa reproduza o projeto a partir de um
clone limpo.

Itens já identificados para o Ciclo 7:

- **Response headers policy no CloudFront** (CSP, HSTS, `X-Content-Type-Options`).
  A página manuseia uma chave de API e hoje não tem esses cabeçalhos.
- **A chave vive numa variável de ambiente da Lambda**, visível a quem tenha
  `lambda:GetFunctionConfiguration`. Avaliar Secrets Manager e registrar a
  decisão.
- **Concorrência da conta em 10** — documentar como limitação conhecida e o
  caminho para elevá-la.
