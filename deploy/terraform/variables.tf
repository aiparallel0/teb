variable "app_name" {
  description = "Application name used for resource naming"
  type        = string
  default     = "teb"
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "container_image" {
  description = "Docker image for the teb application"
  type        = string
  default     = "teb:latest"
}

variable "task_cpu" {
  description = "CPU units for the ECS task (1024 = 1 vCPU)"
  type        = string
  default     = "256"
}

variable "task_memory" {
  description = "Memory in MiB for the ECS task"
  type        = string
  default     = "512"
}

variable "desired_count" {
  description = "Number of ECS task instances to run"
  type        = number
  default     = 2
}

variable "database_url" {
  description = "Database connection string"
  type        = string
  default     = "sqlite:///data/teb.db"
}

variable "jwt_secret" {
  description = "JWT signing secret (set via TF_VAR_jwt_secret or -var)"
  type        = string
  sensitive   = true
}

variable "log_level" {
  description = "Application log level"
  type        = string
  default     = "INFO"
}
