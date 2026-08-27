# Painel web: bucket privado servido pelo CloudFront.
#
# O bucket nao e publico em momento algum. O acesso vem de um Origin Access
# Control, que assina as requisicoes do CloudFront para o S3 — a bucket policy
# so aceita o principal do CloudFront, e apenas desta distribuicao. E a
# diferenca entre "site estatico no S3" (bucket publico, HTTP puro) e este
# desenho: aqui nao existe caminho que alcance os objetos sem passar pelo TLS
# do CloudFront.

resource "aws_s3_bucket" "dashboard" {
  # O namespace de nomes de bucket e global; o id da conta o torna unico sem
  # depender de sorteio.
  bucket        = "${local.name_prefix}-dashboard-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "${local.name_prefix}-dashboard"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# TTL curto de proposito. O padrao gerenciado da AWS (CachingOptimized) guarda
# por 24 h, o que faria toda correcao na pagina exigir invalidacao manual — um
# passo a mais para errar durante a demonstracao. 60 s mantem o CDN util e o
# conteudo fresco.
resource "aws_cloudfront_cache_policy" "dashboard" {
  name        = "${local.name_prefix}-dashboard"
  default_ttl = 60
  min_ttl     = 0
  max_ttl     = 300

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}

resource "aws_cloudfront_distribution" "dashboard" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "Painel do Bhaskara Events"

  # PriceClass_100 (America do Norte e Europa) em vez de todas as regioes: o
  # trafego deste painel e de uma pessoa por vez, e servir do edge americano
  # custa a mesma latencia que ja se paga para falar com a API em us-east-1.
  price_class = "PriceClass_100"

  origin {
    origin_id                = "s3-dashboard"
    domain_name              = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard.id
  }

  default_cache_behavior {
    target_origin_id = "s3-dashboard"

    allowed_methods = ["GET", "HEAD"]
    cached_methods  = ["GET", "HEAD"]

    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    cache_policy_id = aws_cloudfront_cache_policy.dashboard.id
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "dashboard_bucket" {
  statement {
    sid    = "AllowCloudFrontRead"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.dashboard.arn}/*"]

    # Sem esta condicao, qualquer distribuicao CloudFront de qualquer conta
    # poderia ler o bucket.
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.dashboard.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  policy = data.aws_iam_policy_document.dashboard_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.dashboard]
}

# --- conteudo ---------------------------------------------------------------

locals {
  dashboard_files = {
    "index.html" = "text/html; charset=utf-8"
    "styles.css" = "text/css; charset=utf-8"
    "app.js"     = "application/javascript; charset=utf-8"
  }
}

resource "aws_s3_object" "dashboard" {
  for_each = local.dashboard_files

  bucket       = aws_s3_bucket.dashboard.id
  key          = each.key
  source       = "${path.module}/../web/${each.key}"
  content_type = each.value

  # Sem o etag, editar um arquivo nao republica nada: o Terraform compara
  # apenas o caminho e conclui que nada mudou.
  etag = filemd5("${path.module}/../web/${each.key}")
}

# Configuracao gerada: apenas a URL da API.
#
# A CHAVE NAO ENTRA AQUI. Este bundle e publico no CloudFront, e uma chave
# embutida seria uma chave publicada. O operador a cola no painel, que a guarda
# no localStorage do proprio browser.
resource "aws_s3_object" "dashboard_config" {
  bucket       = aws_s3_bucket.dashboard.id
  key          = "config.js"
  content_type = "application/javascript; charset=utf-8"

  content = "window.BHASKARA_CONFIG = ${jsonencode({
    apiBase = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  })};\n"
}
