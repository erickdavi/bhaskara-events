# Fila de entrada do fluxo event-driven.
#
# Standard, nao FIFO: o que este projeto demonstra e paralelismo e vazao, e a
# ordem entre equacoes independentes nao tem significado. FIFO limitaria a
# 300 mensagens/s por grupo e encareceria sem beneficio algum aqui.
#
# Sem redrive_policy neste ciclo — a DLQ entra no Ciclo 3, junto com o
# tratamento de erro que a torna util. Ate la a retencao curta
# (queue_message_retention) e o que limita a janela de reentrega.
resource "aws_sqs_queue" "orders" {
  name = local.orders_queue_name

  visibility_timeout_seconds = var.queue_visibility_timeout
  message_retention_seconds  = var.queue_message_retention
  receive_wait_time_seconds  = var.queue_receive_wait_time

  # Criptografia em repouso com chave gerenciada pela propria SQS: sem custo e
  # sem KMS para administrar. Uma CMK do KMS so se justificaria se houvesse
  # exigencia de rotacao ou de politica de chave propria.
  sqs_managed_sse_enabled = true
}
