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

  orders_queue_name    = "${local.name_prefix}-orders"
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
