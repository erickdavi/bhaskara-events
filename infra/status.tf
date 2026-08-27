resource "aws_cloudwatch_log_group" "status" {
  name              = local.status_log_group
  retention_in_days = var.log_retention_days
}

data "archive_file" "status" {
  type        = "zip"
  output_path = "${path.module}/build/${local.status_function_name}.zip"

  source {
    content  = file("${local.src_dir}/handlers/status/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${local.src_dir}/shared/api_auth.py")
    filename = "api_auth.py"
  }
}

resource "aws_lambda_function" "status" {
  function_name = local.status_function_name
  role          = aws_iam_role.status.arn

  filename         = data.archive_file.status.output_path
  source_code_hash = data.archive_file.status.output_base64sha256

  handler       = "handler.lambda_handler"
  runtime       = var.lambda_runtime
  architectures = [var.lambda_architecture]
  memory_size   = var.status_memory_size
  timeout       = var.status_timeout

  environment {
    variables = {
      ORDERS_QUEUE_URL  = aws_sqs_queue.orders.url
      RESULTS_QUEUE_URL = aws_sqs_queue.results.url
      DLQ_QUEUE_URL     = aws_sqs_queue.orders_dlq.url
      WORKER_LOG_GROUP  = aws_cloudwatch_log_group.worker.name
      API_KEY           = random_password.api_key.result
    }
  }

  depends_on = [
    aws_iam_role_policy.status,
    aws_cloudwatch_log_group.status,
  ]
}
