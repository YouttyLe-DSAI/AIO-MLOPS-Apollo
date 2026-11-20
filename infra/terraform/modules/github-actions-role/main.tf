# IAM role that GitHub Actions assumes through OIDC (no long-lived AWS keys in GitHub).
# Trust is limited to the given repository and git refs (e.g. refs/heads/main).

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.oidc_provider_arn

  # Immutable subjects pin the numeric owner/repo IDs, so a renamed or re-created repository with
  # the same name cannot assume the role.
  owner_name     = split("/", var.repository)[0]
  repo_name      = split("/", var.repository)[1]
  subject_prefix = var.repository_ids == null ? "repo:${var.repository}" : "repo:${local.owner_name}@${var.repository_ids.owner}/${local.repo_name}@${var.repository_ids.repository}"
  subjects       = [for ref in var.allowed_refs : "${local.subject_prefix}:ref:${ref}"]
}

resource "aws_iam_role" "this" {
  name = var.name
  tags = var.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.oidc_provider_arn }
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = local.subjects }
      }
    }]
  })
}

resource "aws_iam_role_policy" "inline" {
  for_each = var.inline_policies

  name   = each.key
  role   = aws_iam_role.this.id
  policy = each.value
}
