# A set of EKS managed add-ons. Call this module more than once to control ordering
# (e.g. CNI before node groups, CoreDNS after nodes exist).

data "aws_eks_addon_version" "this" {
  for_each = var.addons

  addon_name         = each.key
  kubernetes_version = var.kubernetes_version
  most_recent        = true
}

resource "aws_eks_addon" "this" {
  for_each = var.addons

  cluster_name                = var.cluster_name
  addon_name                  = each.key
  addon_version               = coalesce(each.value.version, data.aws_eks_addon_version.this[each.key].version)
  configuration_values        = each.value.configuration_values
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  dynamic "pod_identity_association" {
    for_each = each.value.pod_identity == null ? [] : [each.value.pod_identity]
    content {
      role_arn        = pod_identity_association.value.role_arn
      service_account = pod_identity_association.value.service_account
    }
  }

  tags = var.tags
}
