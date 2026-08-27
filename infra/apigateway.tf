resource "aws_apigatewayv2_api" "this" {
  name          = local.name_prefix
  description   = "Dispara a geracao de equacoes na fila orders."
  protocol_type = "HTTP"

  # O painel do Ciclo 6 e uma pagina estatica servida de outra origem, entao
  # sem CORS o browser bloquearia a chamada antes mesmo de sair. x-api-key
  # precisa constar em allow_headers: nao e um header simples, e sem ele o
  # preflight falharia.
  # Origem restrita a distribuicao do painel, e nao "*". Ate o Ciclo 5 nao
  # havia origem conhecida para nomear; agora ha. CORS nao autoriza nada — quem
  # autoriza e a chave de API — mas fecha a porta para uma pagina de terceiros
  # tentar usar a chave de um operador logado.
  #
  # extra_cors_origins existe para o desenvolvimento local (por exemplo
  # http://localhost:8000), e vem vazio por padrao.
  cors_configuration {
    allow_origins = concat(
      ["https://${aws_cloudfront_distribution.dashboard.domain_name}"],
      var.extra_cors_origins,
    )
    allow_methods = ["POST", "GET", "OPTIONS"]
    allow_headers = ["content-type", "x-api-key"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "producer" {
  api_id = aws_apigatewayv2_api.this.id

  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.producer.invoke_arn

  # 2.0 e o formato nativo do HTTP API: entrega headers em minusculas e o corpo
  # como string, que e o que o handler espera.
  payload_format_version = "2.0"

  # O HTTP API corta a integracao em 30 s no maximo. Declarar explicitamente
  # deixa o limite visivel ao lado do timeout da funcao, que e o mesmo valor.
  timeout_milliseconds = 30000
}

resource "aws_apigatewayv2_route" "orders" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = var.api_route_key
  target    = "integrations/${aws_apigatewayv2_integration.producer.id}"
}

# Stage $default: a URL nao recebe prefixo de stage, entao o endpoint publicado
# fica em /orders direto.
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  # Segunda linha de defesa, depois da chave de API: limita quanto uma chave
  # vazada consegue gerar antes de alguem perceber.
  default_route_settings {
    throttling_rate_limit  = var.throttling_rate_limit
    throttling_burst_limit = var.throttling_burst_limit
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowInvokeFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.producer.function_name
  principal     = "apigateway.amazonaws.com"

  # Restrito a rota exata: qualquer stage, mas so este metodo e caminho.
  source_arn = "${aws_apigatewayv2_api.this.execution_arn}/*/${local.route_method}${local.route_path}"
}

resource "aws_apigatewayv2_integration" "status" {
  api_id = aws_apigatewayv2_api.this.id

  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.status.invoke_arn

  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "status" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = var.status_route_key
  target    = "integrations/${aws_apigatewayv2_integration.status.id}"
}

resource "aws_lambda_permission" "api_gateway_status" {
  statement_id  = "AllowInvokeFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.status.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_apigatewayv2_api.this.execution_arn}/*/${local.status_route_method}${local.status_route_path}"
}
