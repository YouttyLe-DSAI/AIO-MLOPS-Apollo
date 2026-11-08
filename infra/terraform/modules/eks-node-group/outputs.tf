output "node_group_name" {
  value = aws_eks_node_group.this.node_group_name
}

output "node_group_arn" {
  value = aws_eks_node_group.this.arn
}

output "iam_role_arn" {
  value = aws_iam_role.node.arn
}

output "iam_role_name" {
  value = aws_iam_role.node.name
}
