variable "aws_region" {
  description = "Regiao AWS. us-east-1 e a regiao de referencia de preco e a mais barata para Lambda e SQS."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Nome base do projeto, usado como prefixo em todos os recursos."
  type        = string
  default     = "bhaskara-events"
}

variable "environment" {
  description = "Ambiente logico (dev, stg, prd). Compoe o nome dos recursos."
  type        = string
  default     = "dev"
}

variable "lambda_runtime" {
  description = "Runtime Python das funcoes."
  type        = string
  default     = "python3.13"
}

variable "lambda_architecture" {
  description = "Arquitetura das funcoes. arm64 (Graviton) e ~20% mais barato por GB-segundo e seguro aqui porque o codigo usa apenas a stdlib."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.lambda_architecture)
    error_message = "lambda_architecture deve ser arm64 ou x86_64."
  }
}

variable "worker_memory_size" {
  description = "Memoria do worker em MB. 128 e o minimo e sobra para o processamento deste projeto."
  type        = number
  default     = 128
}

variable "worker_timeout" {
  description = <<-EOT
    Timeout do worker em segundos. Precisa cobrir o lote inteiro (batch_size
    mensagens), nao uma mensagem so.

    10 s com folga enorme: um lote de 10 equacoes leva ~20 ms mais o cold start
    de ~85 ms. O valor baixo tem proposito — o visibility timeout da fila e
    derivado dele (6x, recomendacao da AWS), e um visibility menor faz o ciclo
    de retry ate a DLQ levar ~2 minutos em vez de ~6, o que torna a
    demonstracao viavel.
  EOT
  type        = number
  default     = 10
}

variable "worker_reserved_concurrency" {
  description = <<-EOT
    Teto de execucoes simultaneas do worker. -1 significa sem reserva: a funcao
    concorre pelo pool da conta.

    O padrao e -1 por imposicao da conta, nao por preferencia. Esta conta tem
    limite total de 10 execucoes concorrentes (o padrao da AWS para contas
    novas, elevado sob demanda), e a AWS recusa qualquer reserva que derrube a
    concorrencia nao reservada abaixo de 10 — o que, com um limite de 10,
    inviabiliza ate uma reserva de 1.

    Na pratica o efeito desejado ja existe: o teto de 10 da conta limita o
    estrago de um bug no producer e mantem a fila acumulando de forma visivel
    na demonstracao. Se a conta tiver a cota elevada no futuro, um valor
    positivo aqui volta a fazer sentido para isolar o worker das demais
    funcoes.
  EOT
  type        = number
  default     = -1
}

variable "worker_batch_size" {
  description = "Quantas mensagens o event source mapping entrega por invocacao. Maximo 10 para filas standard sem janela de batching."
  type        = number
  default     = 10

  validation {
    condition     = var.worker_batch_size >= 1 && var.worker_batch_size <= 10
    error_message = "worker_batch_size deve estar entre 1 e 10."
  }
}

variable "queue_visibility_timeout" {
  description = "Tempo que a mensagem fica invisivel apos ser entregue. A AWS recomenda no minimo 6x o timeout da funcao consumidora, para que um retry do lote nao concorra com a execucao ainda em andamento. Tambem e o intervalo entre as tentativas de retry."
  type        = number
  default     = 60
}

variable "queue_message_retention" {
  description = <<-EOT
    Retencao das mensagens nas filas orders e results, em segundos.

    4 dias, o padrao da SQS. Nos Ciclos 1 e 2 este valor era 4 horas para
    limitar a janela de reentrega de uma mensagem que falhasse sempre — sem
    DLQ, ela ficaria em loop ate expirar. Com a DLQ no lugar, o loop tem fim
    (maxReceiveCount) e a retencao curta perdeu a razao de existir.
  EOT
  type        = number
  default     = 345600
}

variable "dlq_message_retention" {
  description = <<-EOT
    Retencao da dead letter queue, em segundos.

    14 dias, o maximo da SQS, contra os 4 dias das demais filas. Uma mensagem
    na DLQ e um problema a investigar, e o tempo de investigar costuma ser
    bem maior que o tempo de processar.
  EOT
  type        = number
  default     = 1209600
}

variable "max_receive_count" {
  description = <<-EOT
    Quantas entregas sem confirmacao a SQS tolera antes de mover a mensagem
    para a DLQ.

    3 = a entrega original mais duas tentativas. Vale apenas para falhas
    inesperadas, que o worker devolve em batchItemFailures: uma
    indisponibilidade momentanea costuma passar em segundos, e mais tentativas
    so atrasariam o diagnostico. Mensagens invalidas nao passam por aqui — vao
    direto para a DLQ, porque reentregar nao mudaria o desfecho.
  EOT
  type        = number
  default     = 3

  validation {
    condition     = var.max_receive_count >= 1
    error_message = "max_receive_count deve ser no minimo 1."
  }
}

variable "queue_receive_wait_time" {
  description = "Long polling em segundos. 20 e o maximo: reduz receives vazios, o que corta requests da SQS (custo) sem atrasar a entrega."
  type        = number
  default     = 20
}

variable "log_retention_days" {
  description = "Retencao dos logs em dias. Valores aceitos pelo CloudWatch: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653 ou 0 para reter indefinidamente."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Tags adicionais aplicadas a todos os recursos."
  type        = map(string)
  default     = {}
}

variable "simulate_publish_failure" {
  description = <<-EOT
    Aponta RESULTS_QUEUE_URL para uma fila inexistente, fazendo toda publicacao
    de resultado falhar.

    Existe para tornar demonstravel o caminho de retry nativo, que por
    definicao so aparece quando algo da errado de forma inesperada — e o
    codigo, quando funciona, nao da errado. Com o interruptor ligado, uma
    mensagem valida falha ao publicar, volta em batchItemFailures, e a SQS a
    reentrega ate maxReceiveCount antes de move-la para a DLQ pelo
    redrive_policy.

    Nao e chaos engineering embutido no caminho de producao: nenhuma linha do
    handler sabe que este interruptor existe. O que muda e apenas o valor de
    uma variavel de ambiente.

    Sempre false fora da demonstracao.
  EOT
  type        = bool
  default     = false
}

variable "producer_memory_size" {
  description = "Memoria do producer em MB. 256 e nao 128: a funcao gera milhares de payloads e faz centenas de chamadas de rede, e na Lambda a CPU e proporcional a memoria — mais memoria termina antes e pode custar o mesmo ou menos."
  type        = number
  default     = 256
}

variable "producer_timeout" {
  description = "Timeout do producer em segundos. 30 e o teto util: o HTTP API corta a integracao em 30 s de qualquer forma, entao passar disso so mascararia o problema."
  type        = number
  default     = 30
}

variable "producer_max_quantity" {
  description = "Teto de mensagens por requisicao. O endpoint dispara carga, entao sem limite uma unica chamada viraria custo e uma fila que o worker levaria muito tempo para drenar."
  type        = number
  default     = 5000
}

variable "api_route_key" {
  description = "Rota exposta pelo API Gateway, no formato 'METODO /caminho'."
  type        = string
  default     = "POST /orders"
}

variable "throttling_rate_limit" {
  description = "Requisicoes por segundo em regime permanente. Segunda linha de defesa depois da chave de API: limita quanto uma chave vazada consegue gerar."
  type        = number
  default     = 5
}

variable "throttling_burst_limit" {
  description = "Pico instantaneo de requisicoes aceitas pelo stage."
  type        = number
  default     = 10
}

variable "cors_allow_origins" {
  description = "Origens autorizadas a chamar a API pelo browser. O padrao '*' serve ao painel local do Ciclo 6; a chave de API continua sendo o que autoriza a chamada, CORS apenas diz de onde o browser pode tentar."
  type        = list(string)
  default     = ["*"]
}

variable "status_route_key" {
  description = "Rota de leitura das metricas, no formato 'METODO /caminho'."
  type        = string
  default     = "GET /status"
}

variable "status_memory_size" {
  description = "Memoria do status em MB. 128 basta: a funcao faz tres chamadas de API e serializa um JSON pequeno."
  type        = number
  default     = 128
}

variable "status_timeout" {
  description = "Timeout do status em segundos. O caminho rapido responde em ~150 ms; 10 s cobre com folga a consulta ao CloudWatch Logs quando o painel pede eventos."
  type        = number
  default     = 10
}
