# One-time stack: remote-state storage + locking for every environment/stack.
#
# This account's org SCP denies s3:PutBucketVersioning, s3:DeleteObject and s3:DeleteBucket, so:
#   - the state bucket is NOT versioned  -> run `terraform state pull > backup.tfstate` before risky changes
#   - locking uses DynamoDB, not S3 lockfiles (a .tflock object could never be deleted)

data "aws_caller_identity" "current" {}

module "tfstate_bucket" {
  source = "../modules/s3-bucket"

  name               = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"
  versioning_enabled = false
  force_destroy      = false
}

module "tflock_table" {
  source = "../modules/dynamodb-lock-table"

  name = "${var.project}-tflock"
}
