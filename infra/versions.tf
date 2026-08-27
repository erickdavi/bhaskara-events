terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # State local, adequado ao escopo de laboratorio deste projeto: um unico
  # operador, nenhum pipeline. terraform.tfstate esta no .gitignore porque
  # guarda ARNs e o ID da conta.
  #
  # Se entrar CI/CD, migrar para backend S3 — state local nao sobrevive a
  # pipeline:
  #
  # backend "s3" {
  #   bucket       = "SEU-BUCKET-TFSTATE"
  #   key          = "bhaskara-events/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}
