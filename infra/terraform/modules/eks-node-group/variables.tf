variable "partition" {
  description = "AWS partition for managed policy ARNs (aws, aws-cn, aws-us-gov)."
  type        = string
  default     = "aws"
}

variable "cluster_name" {
  type = string
}

variable "name" {
  description = "Node group name, e.g. system or gpu-l4."
  type        = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "ami_type" {
  description = "EKS AMI type, e.g. AL2023_x86_64_STANDARD or AL2023_x86_64_NVIDIA."
  type        = string
  default     = "AL2023_x86_64_STANDARD"
}

variable "capacity_type" {
  type    = string
  default = "ON_DEMAND"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.capacity_type)
    error_message = "capacity_type must be ON_DEMAND or SPOT."
  }
}

variable "instance_types" {
  type = list(string)
}

variable "min_size" {
  type = number
}

variable "desired_size" {
  description = "Initial size only; later changes are ignored (see lifecycle in main.tf)."
  type        = number
}

variable "max_size" {
  type = number
}

variable "disk_size_gb" {
  type    = number
  default = 40
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "taints" {
  type = list(object({
    key    = string
    value  = string
    effect = string # NO_SCHEDULE | NO_EXECUTE | PREFER_NO_SCHEDULE
  }))
  default = []
}

variable "tags" {
  type    = map(string)
  default = {}
}
