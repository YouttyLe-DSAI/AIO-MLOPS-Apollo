# bucket, region and dynamodb_table (state lock) come from infra/terraform/backend.hcl:
#   terraform init -backend-config=../../../backend.hcl
terraform {
  backend "s3" {
    key     = "apollo/dev/platform.tfstate"
    encrypt = true
  }
}
