#!/usr/bin/env bash
#
# Validacao ponta a ponta: publica equacoes na fila orders e comprova, pelo
# CloudWatch, que o worker calculou (ou recusou) cada uma delas.
#
#   ./scripts/send-test-message.sh              1 equacao valida
#   ./scripts/send-test-message.sh 10           10 equacoes variadas
#   ./scripts/send-test-message.sh --invalid    lote de mensagens invalidas
#
# Requer credenciais AWS ativas e um terraform apply ja aplicado.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="valid"
COUNT=1

case "${1:-}" in
  --invalid) MODE="invalid" ;;
  "")        ;;
  *)         COUNT="$1" ;;
esac

QUEUE_URL="$(terraform -chdir=infra output -raw orders_queue_url)"
LOG_GROUP="$(terraform -chdir=infra output -raw worker_log_group)"

# Marco temporal: so interessa o que for gerado a partir daqui, e nao o log de
# execucoes anteriores.
START_MS=$(( $(date +%s) * 1000 ))

echo "Fila:  $QUEUE_URL"
echo "Modo:  $MODE ($COUNT mensagem(ns))"
echo

# As entradas do send-message-batch sao geradas aqui. No modo invalid, cada
# linha exercita um caminho de recusa diferente — e o que o Ciclo 3 vai usar
# para demonstrar a DLQ.
ENTRIES="$(
  python3 - "$MODE" "$COUNT" <<'PY'
import json
import sys

mode, count = sys.argv[1], int(sys.argv[2])

if mode == "invalid":
    bodies = [
        "nao e json",                       # JSON malformado
        '{"a": 1, "b": -5}',                # coeficiente ausente
        '{"a": 0, "b": 5, "c": 10}',        # nao e equacao do segundo grau
        '{"a": "1", "b": -5, "c": 6}',      # coeficiente como string
        '{"a": 1, "b": 1e200, "c": 1}',     # estoura o ponto flutuante
    ]
else:
    # Equacoes com raizes conhecidas, cobrindo os tres desfechos possiveis:
    # duas raizes reais, raiz dupla e nenhuma raiz real.
    catalog = [
        (1, -5, 6),     # x1=3,  x2=2
        (1, -4, 4),     # raiz dupla em 2
        (1, 2, 5),      # delta < 0, sem raizes reais
        (2, -7, 3),     # x1=3,  x2=0.5
        (1, 0, -4),     # x1=2,  x2=-2
    ]
    bodies = [
        json.dumps(dict(zip("abc", catalog[i % len(catalog)])))
        for i in range(count)
    ]

print(json.dumps([
    {"Id": "m%d" % i, "MessageBody": body} for i, body in enumerate(bodies)
]))
PY
)"

# A SQS aceita no maximo 10 mensagens por send-message-batch.
MESSAGE_IDS="$(
  python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)))" <<< "$ENTRIES" \
  | python3 -c "
import json, subprocess, sys

entries = json.load(sys.stdin)
url = sys.argv[1]
ids = []

for i in range(0, len(entries), 10):
    chunk = entries[i:i + 10]
    for n, entry in enumerate(chunk):
        entry['Id'] = 'm%d' % n
    out = subprocess.run(
        ['aws', 'sqs', 'send-message-batch', '--queue-url', url,
         '--entries', json.dumps(chunk), '--query', 'Successful[].MessageId',
         '--output', 'text'],
        capture_output=True, text=True, check=True,
    )
    ids.extend(out.stdout.split())

print('\n'.join(ids))
" "$QUEUE_URL"
)"

TOTAL=$(echo "$MESSAGE_IDS" | grep -c . || true)
echo "Publicadas: $TOTAL mensagem(ns)"
echo

echo "Aguardando o worker processar ..."
sleep 15

echo "=== Log do worker ==="
aws logs tail "$LOG_GROUP" --since 2m --format short \
  | grep -E '"event": "message_(processed|rejected)"' || true
echo

echo "=== Cada mensagem publicada teve um desfecho no log? ==="
OK=0
while read -r id; do
  [ -n "$id" ] || continue
  OUTCOME="$(
    aws logs filter-log-events \
      --log-group-name "$LOG_GROUP" \
      --start-time "$START_MS" \
      --filter-pattern "\"$id\"" \
      --query 'events[].message' --output text \
    | grep -oE 'message_(processed|rejected)' | tail -1
  )"
  if [ -n "$OUTCOME" ]; then
    echo "  $OUTCOME  $id"
    OK=$((OK + 1))
  else
    echo "  SEM DESFECHO  $id"
  fi
done <<< "$MESSAGE_IDS"
echo "  $OK/$TOTAL com desfecho registrado"
echo

echo "=== Fila drenada? (esperado: 0 e 0) ==="
aws sqs get-queue-attributes \
  --queue-url "$QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --query 'Attributes' --output table
