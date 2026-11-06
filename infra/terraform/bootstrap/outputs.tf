output "state_bucket" {
  value = module.tfstate_bucket.bucket
}

output "backend_hcl" {
  description = "terraform output -raw backend_hcl > ../backend.hcl"
  value       = <<-EOT
    bucket = "${module.tfstate_bucket.bucket}"
    region = "${var.region}"
  EOT
}
