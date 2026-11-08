variable "name" {
  type = string
}

variable "force_destroy" {
  description = "Allow destroy while objects still exist."
  type        = bool
  default     = false
}

variable "versioning_enabled" {
  description = "Set false where an org SCP denies s3:PutBucketVersioning."
  type        = bool
  default     = true
}

variable "noncurrent_version_expiration_days" {
  description = "Only used when versioning_enabled = true."
  type        = number
  default     = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
