# bucket + region come from infra/terraform/backend.hcl:
#   terraform init -backend-config=../../../backend.hcl
terraform {
  backend "s3" {
    key          = "apollo/dev/foundation.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
