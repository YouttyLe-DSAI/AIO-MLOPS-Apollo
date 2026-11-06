# One-time stack: S3 bucket holding remote state for every environment/stack.
# State files are versioned (90 days of history) and the bucket refuses to be destroyed while
# it still contains state (force_destroy = false).

data "aws_caller_identity" "current" {}

module "tfstate_bucket" {
  source = "../modules/s3-bucket"

  name                               = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"
  force_destroy                      = false
  noncurrent_version_expiration_days = 90
}
