variable "name" {
  description = "IAM role name."
  type        = string
}

variable "repository" {
  description = "GitHub repository allowed to assume the role, as owner/name."
  type        = string
}

variable "allowed_refs" {
  description = "Git refs whose workflows may assume the role, e.g. [\"refs/heads/main\"]."
  type        = list(string)
  default     = ["refs/heads/main"]
}

variable "create_oidc_provider" {
  description = "Create the account-wide GitHub OIDC provider (only one may exist per account)."
  type        = bool
  default     = true
}

variable "oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN when create_oidc_provider = false."
  type        = string
  default     = null
}

variable "inline_policies" {
  description = "Policy name => JSON document."
  type        = map(string)
  default     = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
