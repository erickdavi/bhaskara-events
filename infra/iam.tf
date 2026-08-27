data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${local.worker_function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Politica inline com ARN restrito, em vez das managed policies
# AWSLambdaBasicExecutionRole e AWSLambdaSQSQueueExecutionRole: as duas
# concedem acesso sobre "*" — todos os log groups e todas as filas da conta.
# Aqui cada acao aponta para o recurso exato que o worker precisa tocar.
data "aws_iam_policy_document" "worker" {
  statement {
    sid    = "WriteOwnLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${local.worker_log_group}",
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${local.worker_log_group}:*",
    ]
  }

  # As tres acoes exigidas pelo event source mapping: ele recebe o lote,
  # apaga as mensagens confirmadas e consulta os atributos da fila para
  # decidir como escalar. Nenhuma delas permite publicar na orders — o worker
  # so consome dela. Quem publica ali e o producer, no Ciclo 4, com role
  # propria.
  statement {
    sid    = "ConsumeOrdersQueue"
    effect = "Allow"

    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]

    resources = [aws_sqs_queue.orders.arn]
  }

  # Publicar e permitido apenas nas duas filas de saida, e apenas publicar: o
  # worker nao le nem apaga nada em results ou na DLQ. Se um dia um consumidor
  # de results existir, ele tera role propria.
  statement {
    sid    = "PublishResultsAndRejections"
    effect = "Allow"

    actions = ["sqs:SendMessage"]

    resources = [
      aws_sqs_queue.results.arn,
      aws_sqs_queue.orders_dlq.arn,
    ]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "${local.worker_function_name}-policy"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# --- producer ---------------------------------------------------------------

resource "aws_iam_role" "producer" {
  name               = "${local.producer_function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# O producer e a imagem espelhada do worker: publica na orders e nao consome
# dela. Nenhuma das duas roles tem as permissoes da outra — se o producer for
# comprometido, ele nao consegue ler nem apagar o que ja esta na fila, e nao
# alcanca results nem a DLQ.
data "aws_iam_policy_document" "producer" {
  statement {
    sid    = "WriteOwnLambdaLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${local.producer_log_group}",
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${local.producer_log_group}:*",
    ]
  }

  statement {
    sid    = "PublishToOrdersQueue"
    effect = "Allow"

    actions = ["sqs:SendMessage"]

    resources = [aws_sqs_queue.orders.arn]
  }
}

resource "aws_iam_role_policy" "producer" {
  name   = "${local.producer_function_name}-policy"
  role   = aws_iam_role.producer.id
  policy = data.aws_iam_policy_document.producer.json
}
