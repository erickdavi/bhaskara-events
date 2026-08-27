# Declarar o log group aqui e o que garante que o ciclo apply/destroy nao deixe
# residuo na conta. Sem este recurso, quem cria o log group e a propria Lambda
# no primeiro invoke — fora do state, sem retencao, e sobrevivendo ao
# terraform destroy.
resource "aws_cloudwatch_log_group" "worker" {
  name              = local.worker_log_group
  retention_in_days = var.log_retention_days
}

# O zip e montado a partir de arquivos explicitos, e nao de um source_dir, para
# que cada funcao leve apenas o que usa.
#
# Os dois arquivos vao para a raiz do pacote, lado a lado, porque e assim que a
# Lambda resolve o import: o handler faz "from calculator import calculate",
# sem prefixo de pacote. Um subdiretorio shared/ dentro do zip exigiria mexer
# no sys.path em runtime, sem ganho nenhum.
data "archive_file" "worker" {
  type        = "zip"
  output_path = "${path.module}/build/${local.worker_function_name}.zip"

  source {
    content  = file("${local.src_dir}/handlers/worker/handler.py")
    filename = "handler.py"
  }

  # Regra de negocio compartilhada, herdada do Checkpoint 1.
  source {
    content  = file("${local.src_dir}/shared/calculator.py")
    filename = "calculator.py"
  }
}

resource "aws_lambda_function" "worker" {
  function_name = local.worker_function_name
  role          = aws_iam_role.worker.arn

  filename         = data.archive_file.worker.output_path
  source_code_hash = data.archive_file.worker.output_base64sha256

  handler       = "handler.lambda_handler"
  runtime       = var.lambda_runtime
  architectures = [var.lambda_architecture]
  memory_size   = var.worker_memory_size
  timeout       = var.worker_timeout

  # -1 nesta conta: ver a explicacao em variables.tf (limite de 10 da conta
  # torna qualquer reserva invalida).
  reserved_concurrent_executions = var.worker_reserved_concurrency

  # As filas de destino chegam por ambiente, e nao codificadas no fonte: o
  # mesmo pacote sobe para qualquer environment sem alteracao.
  environment {
    variables = {
      # A fila inexistente do modo de demonstracao faz o send_message falhar
      # com QueueDoesNotExist — uma falha inesperada legitima, do ponto de
      # vista do worker, que exercita o caminho de retry.
      RESULTS_QUEUE_URL = var.simulate_publish_failure ? "${aws_sqs_queue.results.url}-inexistente" : aws_sqs_queue.results.url
      DLQ_QUEUE_URL     = aws_sqs_queue.orders_dlq.url
    }
  }

  # O log group precisa existir antes do primeiro invoke, senao a Lambda cria
  # um sem retencao e o proximo apply colide com ele.
  depends_on = [
    aws_iam_role_policy.worker,
    aws_cloudwatch_log_group.worker,
  ]
}

# A ligacao SQS -> Lambda. Quem faz o poll da fila e o servico de event source
# mapping, usando a role da funcao; nao ha nenhum codigo de polling no projeto.
resource "aws_lambda_event_source_mapping" "orders_to_worker" {
  event_source_arn = aws_sqs_queue.orders.arn
  function_name    = aws_lambda_function.worker.arn

  batch_size = var.worker_batch_size

  # Janela de batching em zero: a mensagem e entregue assim que chega, sem
  # esperar o lote encher. Isso mantem a demonstracao responsiva — o painel do
  # Ciclo 6 reage de imediato — ao custo de invocacoes menores.
  maximum_batching_window_in_seconds = 0

  # Contrato de falha parcial: o handler devolve batchItemFailures e apenas as
  # mensagens listadas voltam para a fila. Sem isso, uma unica mensagem ruim
  # faria o lote inteiro (ate 10) ser reentregue e, no Ciclo 3, ir para a DLQ
  # junto — inclusive as que ja tinham sido processadas com sucesso.
  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [aws_iam_role_policy.worker]
}
