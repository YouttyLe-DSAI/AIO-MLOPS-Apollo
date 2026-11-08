variable "name" {
  type = string
}

variable "image_tag_mutability" {
  type    = string
  default = "IMMUTABLE"
}

variable "scan_on_push" {
  type    = bool
  default = true
}

variable "keep_last_images" {
  type    = number
  default = 15
}

variable "force_delete" {
  description = "Allow destroy while images still exist."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
