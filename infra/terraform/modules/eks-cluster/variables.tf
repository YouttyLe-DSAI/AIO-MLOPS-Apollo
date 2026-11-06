variable "name" {
  type = string
}

variable "kubernetes_version" {
  type = string
}

variable "subnet_ids" {
  description = "Subnets for the control-plane ENIs (at least two AZs)."
  type        = list(string)
}

variable "endpoint_public_access_cidrs" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}

variable "enabled_log_types" {
  description = "Control-plane log types sent to CloudWatch. Empty = off (no ingestion cost)."
  type        = list(string)
  default     = []
}

variable "support_type" {
  type    = string
  default = "STANDARD"

  validation {
    condition     = contains(["STANDARD", "EXTENDED"], var.support_type)
    error_message = "support_type must be STANDARD or EXTENDED."
  }
}

variable "admin_principal_arns" {
  description = "Extra IAM principals granted AmazonEKSClusterAdminPolicy. Do not include the cluster creator."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}
