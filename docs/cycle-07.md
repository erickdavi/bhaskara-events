# Ciclo 7 — Polimento e entrega

**Concluído em:** 2026-08-27
**Objetivo:** revisar tudo e deixar o projeto reproduzível a partir de um clone
limpo.

Este ciclo não acrescentou nenhuma funcionalidade. Ele auditou o que existia,
corrigiu o que estava errado e escreveu o README definitivo.

## Auditoria

| Área | Resultado |
| --- | --- |
| Variáveis Terraform declaradas e não usadas | nenhuma |
| Locals órfãos | nenhum |
| Policies com `"Resource": "*"` | nenhuma |
| Imports Python não usados | nenhum |
| Segredos versionados | nenhum |
| Código morto no painel | 1 encontrado (`scoped`, nunca lido) — removido |

## Três correções

### 1. O lock do Terraform não era versionado

`.terraform.lock.hcl` estava no `.gitignore`. Isso contraria diretamente o
princípio de reprodutibilidade: sem o lock, cada clone resolve as versões de
provider que estiverem publicadas no dia, e um `apply` que funciona hoje pode
falhar amanhã por uma mudança de comportamento do provider.

O arquivo passou a ser versionado, com hashes para os quatro alvos comuns:

```bash
terraform providers lock \
  -platform=linux_amd64 -platform=darwin_amd64 \
  -platform=darwin_arm64 -platform=windows_amd64
```

Sem os hashes de múltiplas plataformas, um clone em macOS ou Windows falharia na
verificação de integridade.

### 2. O painel não tinha cabeçalhos de segurança

A página manuseia uma chave de API no `localStorage` e era servida sem CSP, sem
HSTS e sem `X-Content-Type-Options`. Adicionada uma
`aws_cloudfront_response_headers_policy`:

```text
content-security-policy: default-src 'none'; script-src 'self'; style-src 'self';
  img-src 'self' data:; connect-src https://*.execute-api.us-east-1.amazonaws.com;
  base-uri 'none'; form-action 'none'; frame-ancestors 'none'
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
```

A CSP é restritiva porque a página permite: todo o CSS e JS vem do próprio
bucket e não há script inline.

**O `connect-src` usa um curinga por região em vez da URL exata da API.** Nomear
a API fecharia um ciclo no grafo do Terraform — a CSP apontaria para a API, a
API aponta para a distribuição no CORS, e a distribuição aponta para esta
policy. Os três IDs só existem depois de criados, então não há lado do ciclo que
possa ser resolvido a mão. O curinga continua muito mais fechado que `"*"`, e a
autorização real é a chave de API, não a CSP.

### 3. A própria CSP quebrou a página

Ligada a CSP, o console acusou **8 violações** — todas de atributos `style=`
inline no HTML, que `style-src 'self'` bloqueia:

```text
Applying inline style violates the following Content Security Policy directive
'style-src 'self''. The action has been blocked.   ×8
```

Corrigido movendo todos os estilos para `styles.css` (classes `.card--spaced`,
`.flush`, `.hint`) e removendo o `onerror=` inline do `<script src="config.js">`
— que era redundante, porque o `app.js` já trata a ausência da configuração com
`window.BHASKARA_CONFIG || {}`.

Vale notar o que **não** precisou mudar: o `app.js` escreve
`elemento.style.width` para animar a barra de progresso, e a CSP permite isso.
`style-src` bloqueia o atributo `style=` no markup e blocos `<style>`, não a
manipulação do CSSOM por script.

Revalidado depois da correção: **zero erros no console** e a página funcional —
150 mensagens com 20% inválidas, `122 + 28 = 150 — concluído`.

### 4. Dois testes instáveis, encontrados pelo clone limpo

O `pytest` no clone recém-baixado falhou onde passava localmente:

```text
FAILED tests/test_status_handler.py::test_the_cursor_never_comes_back_below_the_window
1 failed, 144 passed
```

Os dois testes da janela de busca (introduzidos no Ciclo 6) mediam o piso
**depois** da chamada:

```python
call({"events": "10", "since": "0"})
floor = now_ms() - status.MAX_EVENTS_LOOKBACK_MS   # "agora" posterior ao do handler
assert body["events_cursor"] >= floor              # falha por milissegundos
```

O handler calcula o piso com o seu próprio `now_ms`, capturado antes. Qualquer
milissegundo decorrido entre os dois torna o piso do teste maior que o do
handler, e a asserção quebra. Localmente passava por sorte; no clone, a primeira
execução (criando o venv) foi lenta o bastante para expor a corrida.

Corrigido medindo o piso **antes** da chamada. Confirmado com 30 execuções
seguidas, zero falhas.

É exatamente o tipo de defeito que só o teste de clone limpo encontra — e a
razão de esse teste existir.

## Reprodutibilidade

O README foi reescrito do zero como guia de reprodução:

- caminho rápido de quatro comandos, do clone ao painel no ar
- índice navegável
- pré-requisitos com versões
- matriz IAM completa das três roles
- tabela de custos contra o free tier
- limitações conhecidas, com o caminho para resolver cada uma
- tabela de decisões de arquitetura com o porquê de cada uma

## Limitações registradas como decisão, não como esquecimento

**Concorrência da conta em 10.** Documentado o efeito (nenhuma reserva possível;
throttling do status sob carga) e o caminho para elevar (Service Quotas →
Lambda → Concurrent executions).

**Chave de API em variável de ambiente.** Exposta a quem tenha
`lambda:GetFunctionConfiguration`. O Secrets Manager custaria ~US$ 0,40/mês por
segredo, uma chamada no cold start e uma permissão a mais em duas roles. Para um
laboratório, a variável de ambiente é adequada — mas a escolha está registrada.

**Contadores aproximados e eventos com atraso.** Características da SQS e do
CloudWatch Logs, não defeitos. O painel trata as duas explicitamente.

## Critérios de aceitação

| # | Critério | Resultado |
| --- | --- | --- |
| 1 | Terraform sem variável órfã, sem `Resource: "*"` | ✅ |
| 2 | IAM revisado e documentado | ✅ matriz das três roles no README |
| 3 | Testes passando, sem instabilidade | ✅ `145 passed`, 30 execuções seguidas sem falha |
| 4 | README permite reprodução de clone limpo | ✅ clone do GitHub: `145 passed` + `terraform validate` OK |
| 5 | `.gitignore` correto, lock versionado | ✅ |
| 6 | Segurança revisada | ✅ CSP, HSTS, CORS, IAM, bucket privado |
| 7 | Custos documentados | ✅ tabela contra o free tier |
| 8 | `destroy` limpo, sem resíduo | ✅ executado e verificado — ver abaixo |


## O ciclo destroy → apply, executado

O último critério em aberto desde o Ciclo 1 — provar que o `destroy` não deixa
resíduo — foi fechado executando o ciclo completo contra a conta real.

### Destroy

```text
Destroy complete! Resources: 37 destroyed.
```

Inventário na conta, por categoria, antes e depois:

| Categoria | Antes | Depois |
| --- | --- | --- |
| Filas SQS | 3 | **0** |
| Funções Lambda | 3 | **0** |
| Log groups | 3 | **0** |
| Roles IAM | 3 | **0** |
| Buckets S3 | 1 | **0** |
| Distribuições CloudFront | 1 | **0** |
| APIs HTTP | 1 | **0** |

State vazio, **zero resíduo**. E o Checkpoint 1, em outra stack, continuou
respondendo normalmente durante todo o processo — a separação entre os dois
projetos vale também para o `destroy`.

O log group ser removido junto é consequência de tê-lo declarado no Terraform
desde o Ciclo 1. Se a Lambda o criasse sozinha no primeiro invoke, ele ficaria
fora do state e sobreviveria a cada `destroy`, acumulando resíduo.

### Apply do zero

```text
Apply complete! Resources: 37 added, 0 changed, 0 destroyed.
real  2m56s
```

Os 37 recursos reconstruídos em **menos de três minutos**, num único comando,
sem nenhum passo manual. Validação da stack nova:

| Verificação | Resultado |
| --- | --- |
| `POST /orders` sem chave | `403` |
| Acesso direto ao bucket S3 | `AccessDenied` |
| CSP servida pelo CloudFront | presente |
| Chave no `config.js` publicado | ausente |
| Carga de 600 mensagens com 10% inválidas | `543 + 57 = 600` |
| Painel no browser | carrega, console limpo |

A primeira leitura do `/status` durante a drenagem mostrou `396 sucessos, 0
falhas` — o contador da DLQ ainda assentando, exatamente o comportamento
documentado no Ciclo 6.

**Todos os critérios dos sete ciclos estão cumpridos.**
