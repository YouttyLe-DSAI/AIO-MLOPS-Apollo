variable "name" {
  type = string
}

variable "force_destroy" {
  description = "Allow destroy while objects still exist."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  type    = number
  default = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
