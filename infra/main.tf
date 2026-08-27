provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  orders_queue_name  = "${local.name_prefix}-orders"
  results_queue_name = "${local.name_prefix}-results"
  dlq_queue_name     = "${local.name_prefix}-orders-dlq"

  # ARN da orders montado a mao para quebrar o ciclo de dependencia entre ela e
  # a DLQ: a orders referencia a DLQ no redrive_policy, e a DLQ precisa
  # referenciar a orders no redrive_allow_policy.
  orders_queue_arn = "arn:${data.aws_partition.current.partition}:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.orders_queue_name}"

  worker_function_name = "${local.name_prefix}-worker"
  worker_log_group     = "/aws/lambda/${local.worker_function_name}"

  # Codigo da aplicacao, relativo ao diretorio infra/.
  src_dir = "${path.module}/../src"

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    },
    var.tags,
  )
}
