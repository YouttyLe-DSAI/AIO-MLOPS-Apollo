variable "name" {
  description = "Name prefix for all network resources."
  type        = string
}

variable "cidr" {
  type = string
}

variable "azs" {
  description = "One public subnet is created per AZ."
  type        = list(string)
}

variable "subnet_newbits" {
  description = "Bits added to the VPC prefix for each subnet (/16 + 4 = /20)."
  type        = number
  default     = 4
}

variable "public_subnet_tags" {
  description = "Extra tags for public subnets, e.g. kubernetes.io/role/elb for NLB discovery."
  type        = map(string)
  default     = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
