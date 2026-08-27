# Fila de entrada do fluxo event-driven.
#
# Standard, nao FIFO: o que este projeto demonstra e paralelismo e vazao, e a
# ordem entre equacoes independentes nao tem significado. FIFO limitaria a
# 300 mensagens/s por grupo e encareceria sem beneficio algum aqui.
resource "aws_sqs_queue" "orders" {
  name = local.orders_queue_name

  visibility_timeout_seconds = var.queue_visibility_timeout
  message_retention_seconds  = var.queue_message_retention
  receive_wait_time_seconds  = var.queue_receive_wait_time

  # Criptografia em repouso com chave gerenciada pela propria SQS: sem custo e
  # sem KMS para administrar.
  sqs_managed_sse_enabled = true

  # Retry nativo: apos maxReceiveCount entregas sem confirmacao, a SQS move a
  # mensagem para a DLQ sozinha. Este e o caminho das falhas inesperadas — o
  # worker devolve o messageId em batchItemFailures e deixa a SQS reentregar.
  #
  # Mensagens invalidas nao passam por aqui: o worker as publica direto na DLQ,
  # porque reentregar tres vezes algo que nunca vai funcionar so gastaria
  # invocacoes e atrasaria a chegada na DLQ. Ver o docstring do handler.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.orders_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Fila de saida: os resultados dos calculos bem-sucedidos.
#
# Existir como fila, e nao apenas como linha de log, e o que fecha o desenho
# event-driven — um consumidor futuro (relatorio, persistencia, notificacao)
# se conecta aqui sem que o worker precise saber que ele existe.
resource "aws_sqs_queue" "results" {
  name = local.results_queue_name

  visibility_timeout_seconds = var.queue_visibility_timeout
  message_retention_seconds  = var.queue_message_retention
  receive_wait_time_seconds  = var.queue_receive_wait_time

  sqs_managed_sse_enabled = true
}

# Dead letter queue: onde as mensagens problematicas descansam.
#
# Recebe por dois caminhos — o redrive nativo da orders (falha inesperada que
# esgotou as tentativas) e a publicacao direta do worker (mensagem invalida,
# que chega com o motivo da recusa anexado como message attribute).
#
# Retencao maxima de 14 dias, e nao os 4 dias das outras filas: uma mensagem
# aqui e um problema a investigar, e o tempo de investigar costuma ser bem
# maior que o tempo de processar.
resource "aws_sqs_queue" "orders_dlq" {
  name = local.dlq_queue_name

  message_retention_seconds = var.dlq_message_retention

  sqs_managed_sse_enabled = true

  # Restringe quais filas podem usar esta como destino de redrive. Sem isso,
  # qualquer fila da conta poderia apontar para ca.
  #
  # O ARN e montado a mao em vez de referenciar aws_sqs_queue.orders.arn: a
  # orders ja aponta para esta fila no redrive_policy, e a referencia de volta
  # fecharia um ciclo de dependencia no grafo do Terraform. O nome da fila e
  # deterministico, entao o ARN tambem e.
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [local.orders_queue_arn]
  })
}
