terraform {
  backend "s3" {
    bucket               = "rag-app-shared-terraform-state-580118073904-us-west-1-an"
    region               = "us-west-1"
    key                  = "backend.tfstate"
    workspace_key_prefix = "rag-app-terraform/api-gateway"
    encrypt              = true
    use_lockfile         = true
  }
}
