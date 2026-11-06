variable "cluster_name" {
  type = string
}

variable "kubernetes_version" {
  description = "Used to resolve the latest compatible add-on version when none is pinned."
  type        = string
}

variable "addons" {
  description = "Add-on name => settings. version = null picks the most recent build for kubernetes_version."
  type = map(object({
    version              = optional(string)
    configuration_values = optional(string)
    pod_identity = optional(object({
      role_arn        = string
      service_account = string
    }))
  }))
}

variable "tags" {
  type    = map(string)
  default = {}
}
