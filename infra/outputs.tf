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
  value       = "aws sqs send-message --queue-url ${aws_sqs_queue.orders.url} --message-body '{\"ping\":\"cycle-1\"}'"
}

output "tail_worker_logs" {
  description = "Comando pronto para acompanhar o processamento em tempo real."
  value       = "aws logs tail ${aws_cloudwatch_log_group.worker.name} --follow --format short"
}
