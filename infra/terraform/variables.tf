variable "aws_region" {
  type        = string
  description = "AWS deployment region"
  default     = "us-east-1"
}

variable "environment" {
  type        = string
  description = "Deployment environment (dev, staging, prod)"
  default     = "prod"
}

variable "app_name" {
  type        = string
  description = "Base application name"
  default     = "cachegate"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the VPC"
  default     = "10.0.0.0/16"
}

variable "container_port" {
  type        = number
  description = "Port the CacheGate application listens on"
  default     = 8000
}

variable "task_cpu" {
  type        = number
  description = "Fargate vCPU units (1024 = 1 vCPU)"
  default     = 1024
}

variable "task_memory" {
  type        = number
  description = "Fargate memory in MiB (e.g. 2048 for embedding model + gateway)"
  default     = 2048
}

variable "min_capacity" {
  type        = number
  description = "Minimum number of gateway tasks running"
  default     = 2
}

variable "max_capacity" {
  type        = number
  description = "Maximum number of gateway tasks under autoscaling"
  default     = 6
}

variable "image_tag" {
  type        = string
  description = "Docker image tag to deploy"
  default     = "latest"
}
