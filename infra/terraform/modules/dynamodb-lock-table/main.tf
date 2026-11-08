# DynamoDB table for Terraform S3-backend state locking.
# Used instead of S3 native lockfiles where an org SCP denies s3:DeleteObject
# (the .tflock object could never be removed, leaving state permanently locked).

resource "aws_dynamodb_table" "this" {
  name                        = var.name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "LockID"
  deletion_protection_enabled = true

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}
