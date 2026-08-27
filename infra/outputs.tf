output "orders_queue_url" {
  description = "URL da fila orders, usada pelo aws sqs send-message."
  value       = aws_sqs_queue.orders.url
}

output "orders_queue_arn" {
  description = "ARN da fila orders."
  value       = aws_sqs_queue.orders.arn
}

output "worker_function_name" {
  description = "Nome da funcao worker, util para aws logs tail e para consultar metricas."
  value       = aws_lambda_function.worker.function_name
}

output "worker_log_group" {
  description = "Log group do worker, gerenciado pelo Terraform e removido no destroy."
  value       = aws_cloudwatch_log_group.worker.name
}

output "send_test_message" {
  description = "Comando pronto para validar o ciclo: publica uma mensagem na fila orders."
  value       = "aws sqs send-message --queue-url ${aws_sqs_queue.orders.url} --message-body '{\"a\":1,\"b\":-5,\"c\":6}'"
}

output "tail_worker_logs" {
  description = "Comando pronto para acompanhar o processamento em tempo real."
  value       = "aws logs tail ${aws_cloudwatch_log_group.worker.name} --follow --format short"
}

output "results_queue_url" {
  description = "URL da fila results, onde os calculos bem-sucedidos sao publicados."
  value       = aws_sqs_queue.results.url
}

output "dlq_queue_url" {
  description = "URL da dead letter queue."
  value       = aws_sqs_queue.orders_dlq.url
}

output "read_results" {
  description = "Comando pronto para ler um resultado da fila results."
  value       = "aws sqs receive-message --queue-url ${aws_sqs_queue.results.url} --max-number-of-messages 10"
}

output "read_dlq" {
  description = "Comando pronto para inspecionar a DLQ, com o motivo da recusa."
  value       = "aws sqs receive-message --queue-url ${aws_sqs_queue.orders_dlq.url} --max-number-of-messages 10 --message-attribute-names All"
}

output "api_base_url" {
  description = "URL base do HTTP API (stage $default, sem prefixo de stage)."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "producer_url" {
  description = "URL completa do endpoint que dispara a geracao de mensagens."
  value       = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}${local.route_path}"
}

output "api_key" {
  description = "Chave exigida no header x-api-key. Obtenha com 'terraform output -raw api_key'."
  value       = random_password.api_key.result
  sensitive   = true
}

output "producer_function_name" {
  description = "Nome da funcao producer."
  value       = aws_lambda_function.producer.function_name
}

output "producer_log_group" {
  description = "Log group do producer."
  value       = aws_cloudwatch_log_group.producer.name
}

output "generate_messages" {
  description = "Comando pronto para gerar 1.000 mensagens. A chave sai de 'terraform output -raw api_key'."
  value       = "curl -s -X POST '${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}${local.route_path}' -H \"x-api-key: $(terraform -chdir=infra output -raw api_key)\" -H 'Content-Type: application/json' -d '{\"quantity\":1000}'"
}
