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
  description = "Timeout do worker em segundos. Precisa cobrir o lote inteiro (batch_size mensagens), nao uma mensagem so."
  type        = number
  default     = 30
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
  description = "Tempo que a mensagem fica invisivel apos ser entregue. A AWS recomenda no minimo 6x o timeout da funcao consumidora, para que um retry do lote nao concorra com a execucao ainda em andamento."
  type        = number
  default     = 180
}

variable "queue_message_retention" {
  description = <<-EOT
    Retencao das mensagens na fila, em segundos.

    4 horas, deliberadamente baixo. Enquanto nao existir DLQ (Ciclo 3), uma
    mensagem que falhe sempre e reentregue ate expirar — a retencao curta
    limita essa janela. Volta para o padrao de 4 dias quando a DLQ entrar.
  EOT
  type        = number
  default     = 14400
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
