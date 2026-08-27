# Chave de API do endpoint. Gerada pelo Terraform e nunca versionada: fica no
# state (que o .gitignore cobre) e sai por `terraform output -raw api_key`.
#
# special = false porque a chave viaja num header HTTP — restringir ao conjunto
# alfanumerico evita qualquer questao de escaping no caminho.
resource "random_password" "api_key" {
  length  = 40
  special = false
}

resource "aws_cloudwatch_log_group" "producer" {
  name              = local.producer_log_group
  retention_in_days = var.log_retention_days
}

# Os dois modulos vao para a raiz do zip, lado a lado: o handler importa
# "from generator import generate", sem prefixo de pacote, que e como a Lambda
# resolve imports dentro do proprio pacote.
#
# calculator.py nao entra aqui: o producer gera equacoes, nao as resolve.
data "archive_file" "producer" {
  type        = "zip"
  output_path = "${path.module}/build/${local.producer_function_name}.zip"

  source {
    content  = file("${local.src_dir}/handlers/producer/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${local.src_dir}/handlers/producer/generator.py")
    filename = "generator.py"
  }
}

resource "aws_lambda_function" "producer" {
  function_name = local.producer_function_name
  role          = aws_iam_role.producer.arn

  filename         = data.archive_file.producer.output_path
  source_code_hash = data.archive_file.producer.output_base64sha256

  handler       = "handler.lambda_handler"
  runtime       = var.lambda_runtime
  architectures = [var.lambda_architecture]
  memory_size   = var.producer_memory_size
  timeout       = var.producer_timeout

  environment {
    variables = {
      ORDERS_QUEUE_URL = aws_sqs_queue.orders.url
      MAX_QUANTITY     = tostring(var.producer_max_quantity)
      API_KEY          = random_password.api_key.result
    }
  }

  depends_on = [
    aws_iam_role_policy.producer,
    aws_cloudwatch_log_group.producer,
  ]
}
