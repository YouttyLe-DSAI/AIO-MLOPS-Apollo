resource "aws_budgets_budget" "this" {
  name         = var.name
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = format("%.1f", var.limit_usd)
  limit_unit   = "USD"
  metrics      = ["UnblendedCost"]

  dynamic "filter_expression" {
    for_each = length(var.regions) > 0 ? [1] : []
    content {
      dimensions {
        key    = "REGION"
        values = var.regions
      }
    }
  }

  dynamic "notification" {
    for_each = concat(
      [for t in var.actual_thresholds_pct : { type = "ACTUAL", threshold = t }],
      [for t in var.forecast_thresholds_pct : { type = "FORECASTED", threshold = t }],
    )
    content {
      notification_type          = notification.value.type
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = var.alert_emails
    }
  }

  tags = var.tags
}
