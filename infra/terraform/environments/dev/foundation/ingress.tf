# Static public IP for the platform's single ingress NLB (Traefik, created in ../platform).
# Hostnames are derived from it via sslip.io (e.g. mlflow.3-34-1-2.sslip.io), so links survive
# re-creating the load balancer. The NLB lives in one subnet (system_node_az), where Traefik runs.

resource "aws_eip" "ingress" {
  domain = "vpc"

  tags = {
    Name = "${var.cluster_name}-ingress"
  }
}

locals {
  ingress_base_domain = "${replace(aws_eip.ingress.public_ip, ".", "-")}.sslip.io"
}
