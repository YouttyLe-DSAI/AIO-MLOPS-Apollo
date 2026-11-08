variable "name" {
  type = string
}

variable "limit_usd" {
  type = number
}

variable "regions" {
  description = "Only count cost in these regions. Empty = whole account."
  type        = list(string)
  default     = []
}

variable "alert_emails" {
  type = list(string)
}

variable "actual_thresholds_pct" {
  type    = list(number)
  default = [50, 80, 100]
}

variable "forecast_thresholds_pct" {
  type    = list(number)
  default = [100]
}

variable "tags" {
  type    = map(string)
  default = {}
}
