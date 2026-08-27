#!/usr/bin/env bash
#
# Validacao do Ciclo 1: publica mensagem(ns) na fila orders e comprova, pelo
# CloudWatch, que o worker recebeu e consumiu cada uma.
#
#   ./scripts/send-test-message.sh        1 mensagem
#   ./scripts/send-test-message.sh 10     lote de 10 (send-message-batch)
#
# Requer credenciais AWS ativas e um terraform apply ja aplicado.
set -euo pipefail

cd "$(dirname "$0")/.."

COUNT="${1:-1}"

QUEUE_URL="$(terraform -chdir=infra output -raw orders_queue_url)"
LOG_GROUP="$(terraform -chdir=infra output -raw worker_log_group)"

# Marco temporal para a consulta do log: so interessa o que for gerado a partir
# daqui, e nao mensagens de execucoes anteriores.
START_MS=$(( $(date +%s) * 1000 ))

echo "Fila:  $QUEUE_URL"
echo "Log:   $LOG_GROUP"
echo

if [ "$COUNT" -eq 1 ]; then
  MESSAGE_IDS="$(
    aws sqs send-message \
      --queue-url "$QUEUE_URL" \
      --message-body '{"ping":"cycle-1"}' \
      --query 'MessageId' --output text
  )"
else
  # A SQS aceita no maximo 10 mensagens por send-message-batch.
  ENTRIES="$(
    python3 -c "
import json, sys
n = int(sys.argv[1])
print(json.dumps([
    {'Id': 'm%d' % i, 'MessageBody': json.dumps({'ping': 'cycle-1', 'seq': i})}
    for i in range(n)
]))
" "$COUNT"
  )"

  MESSAGE_IDS="$(
    aws sqs send-message-batch \
      --queue-url "$QUEUE_URL" \
      --entries "$ENTRIES" \
      --query 'Successful[].MessageId' --output text | tr '\t' '\n'
  )"
fi

echo "Publicado(s):"
echo "$MESSAGE_IDS" | sed 's/^/  /'
echo

echo "Aguardando o worker processar ..."
sleep 15

echo "=== Log do worker ==="
aws logs tail "$LOG_GROUP" --since 2m --format short || true
echo

echo "=== Cada MessageId publicado apareceu no log? ==="
FOUND=0
TOTAL=0
while read -r id; do
  [ -n "$id" ] || continue
  TOTAL=$((TOTAL + 1))
  if aws logs filter-log-events \
       --log-group-name "$LOG_GROUP" \
       --start-time "$START_MS" \
       --filter-pattern "\"$id\"" \
       --query 'events[0].message' --output text | grep -q "$id"; then
    echo "  OK    $id"
    FOUND=$((FOUND + 1))
  else
    echo "  FALHA $id"
  fi
done <<< "$MESSAGE_IDS"
echo "  $FOUND/$TOTAL encontrados"
echo

echo "=== Fila drenada? (esperado: 0 e 0) ==="
aws sqs get-queue-attributes \
  --queue-url "$QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --query 'Attributes' --output table
