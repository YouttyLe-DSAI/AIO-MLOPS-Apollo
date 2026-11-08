variable "name" {
  description = "IAM role name."
  type        = string
}

variable "cluster_name" {
  type = string
}

variable "namespace" {
  type    = string
  default = null
}

variable "service_account" {
  description = "null = create the role only (e.g. when an EKS add-on creates its own association)."
  type        = string
  default     = null
}

variable "managed_policy_arns" {
  type    = list(string)
  default = []
}

variable "inline_policies" {
  description = "Policy name => JSON document, attached inline to the role."
  type        = map(string)
  default     = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
