# Cluster Autoscaler: starts the GPU node group from 0 when a pod requests nvidia.com/gpu
# (Triton, GPU runner, benchmark Jobs) and removes the node after it has been idle for
# `scale-down-unneeded-time`. Nobody needs AWS access or gpu-up.sh / gpu-down.sh any more.
#
# IAM: Pod Identity association kube-system/cluster-autoscaler (../foundation/iam.tf).
# Scale-from-zero hints: ASG tags written by modules/eks-node-group (autoscaler_node_template).
resource "helm_release" "cluster_autoscaler" {
  name       = "cluster-autoscaler"
  namespace  = "kube-system"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = var.chart_versions.cluster_autoscaler

  values = [yamlencode({
    image         = { tag = var.cluster_autoscaler_image_tag } # minor must match the cluster (1.36)
    cloudProvider = "aws"
    awsRegion     = var.region
    autoDiscovery = { clusterName = var.cluster_name }
    rbac          = { serviceAccount = { create = true, name = "cluster-autoscaler" } }
    nodeSelector  = { role = "system" }
    extraArgs = {
      "scale-down-unneeded-time"      = "10m" # idle GPU node is removed after 10 minutes
      "scale-down-delay-after-add"    = "5m"
      "max-node-provision-time"       = "10m" # give up on a launch (e.g. no L4 capacity) after 10 minutes
      "expander"                      = "least-waste"
      "skip-nodes-with-local-storage" = false
      "balance-similar-node-groups"   = false
    }
  })]

  wait    = true
  timeout = 600
}
