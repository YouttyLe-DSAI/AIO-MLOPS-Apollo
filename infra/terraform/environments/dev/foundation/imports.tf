# The budget was first created by hand on 2026-11-06; this adopts it into state.
# Safe to delete after the first successful apply.
import {
  to = module.budget.aws_budgets_budget.this
  id = "${local.account_id}:${var.cluster_name}-${var.region}-monthly"
}
