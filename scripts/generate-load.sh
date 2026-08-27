#!/usr/bin/env bash
#
# Dispara a geracao de mensagens pelo endpoint do producer e acompanha o
# processamento ate a fila drenar.
#
#   ./scripts/generate-load.sh                 1000 mensagens, todas validas
#   ./scripts/generate-load.sh 500             500 mensagens
#   ./scripts/generate-load.sh 1000 0.05       1000 mensagens, 5% invalidas
#
# Requer credenciais AWS ativas e um terraform apply ja aplicado.
set -euo pipefail

cd "$(dirname "$0")/.."

QUANTITY="${1:-1000}"
INVALID_RATIO="${2:-0}"

URL="$(terraform -chdir=infra output -raw producer_url)"
KEY="$(terraform -chdir=infra output -raw api_key)"
ORDERS="$(terraform -chdir=infra output -raw orders_queue_url)"
RESULTS="$(terraform -chdir=infra output -raw results_queue_url)"
DLQ="$(terraform -chdir=infra output -raw dlq_queue_url)"

depth() {
  aws sqs get-queue-attributes --queue-url "$1" \
    --attribute-names ApproximateNumberOfMessages \
    --query 'Attributes.ApproximateNumberOfMessages' --output text
}

in_flight() {
  aws sqs get-queue-attributes --queue-url "$1" \
    --attribute-names ApproximateNumberOfMessagesNotVisible \
    --query 'Attributes.ApproximateNumberOfMessagesNotVisible' --output text
}

echo "POST $URL"
echo "  quantity=$QUANTITY invalid_ratio=$INVALID_RATIO"
echo

# A chave vai pelo header e nunca aparece na linha de comando de outro processo.
RESPONSE="$(
  curl -s -X POST "$URL" \
    -H "x-api-key: $KEY" \
    -H 'Content-Type: application/json' \
    -d "{\"quantity\":$QUANTITY,\"invalid_ratio\":$INVALID_RATIO}"
)"

echo "Resposta do producer:"
echo "  $RESPONSE"
echo

echo "Acompanhando o processamento ..."
printf '  %-8s %-10s %-10s %-10s %s\n' "t(s)" "orders" "em voo" "results" "dlq"

START=$(date +%s)

for _ in $(seq 1 40); do
  Q="$(depth "$ORDERS")"
  F="$(in_flight "$ORDERS")"

  printf '  %-8s %-10s %-10s %-10s %s\n' \
    "$(( $(date +%s) - START ))" "$Q" "$F" "$(depth "$RESULTS")" "$(depth "$DLQ")"

  if [ "$Q" = "0" ] && [ "$F" = "0" ]; then
    break
  fi

  sleep 5
done

echo
echo "Fila orders drenada. results e dlq acima somam o total publicado."
