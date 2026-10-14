# CacheGate Terraform Infrastructure (AWS)

Production-ready Terraform configuration for deploying CacheGate on AWS with ECS Fargate, Application Load Balancer, Redis Stack, Autoscaling, and CloudWatch.

## Architecture

- **Networking:** VPC with 2 public subnets (ALB) and 2 private subnets (ECS tasks & Redis) across 2 Availability Zones, with an Internet Gateway and NAT Gateway.
- **Compute:** ECS Fargate Cluster with Container Insights enabled. Gateway tasks sized at 1 vCPU / 2GB memory for local embedding inference and async HTTP forwarding.
- **Storage & Caching:** Redis Stack container running in private subnet via AWS Cloud Map internal DNS (`redis.cachegate.local:6379`).
- **Load Balancing:** AWS Application Load Balancer (ALB) with health check target group routed to `/readyz` on port 8000.
- **Autoscaling:** AWS Application AutoScaling with target tracking at 60% CPU utilization and 500 requests/target, dynamically scaling between 2 and 6 tasks.
- **Observability:** CloudWatch log group with 14-day retention and CloudWatch alarms for 5XX errors, p95 latency > 2s, unhealthy hosts, and high CPU.
- **Secrets Management:** AWS Secrets Manager storing `ADMIN_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`.

## Deployment Instructions

### 1. Prerequisites
- AWS CLI configured with administrator or deployment credentials.
- Terraform >= 1.5.0 installed.
- Docker for building the container image.

### 2. Quick Deploy

```bash
cd infra/terraform

# 1. Initialize Terraform
terraform init

# 2. Plan and review changes
terraform plan -out=tfplan

# 3. Apply infrastructure
terraform apply tfplan
```

### 3. Build & Push Docker Image to ECR

```bash
# Obtain ECR URL from outputs
ECR_URL=$(terraform output -raw ecr_repository_url)
AWS_REGION=$(terraform output -raw aws_region 2>/dev/null || echo "us-east-1")

# Authenticate Docker to AWS ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_URL

# Build and push
docker build -t $ECR_URL:latest -f Dockerfile .
docker push $ECR_URL:latest

# Trigger ECS service deployment
CLUSTER_NAME=$(terraform output -raw ecs_cluster_name)
SERVICE_NAME=$(terraform output -raw ecs_service_name)
aws ecs update-service --cluster $CLUSTER_NAME --service $SERVICE_NAME --force-new-deployment
```

### 4. Verify & Smoke Test

```bash
ALB_URL=$(terraform output -raw alb_http_url)

# Health check
curl $ALB_URL/healthz
curl $ALB_URL/readyz

# Chat completion request
curl -X POST $ALB_URL/v1/chat/completions \
  -H "Authorization: Bearer <YOUR_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model":"mock-fast","messages":[{"role":"user","content":"Hello from AWS!"}]}'
```

### 5. Teardown (`terraform destroy`)

> **IMPORTANT COST NOTE:** Always destroy demo infrastructure when not in active use to avoid ongoing charges for NAT Gateways, ALBs, and Fargate tasks.

```bash
terraform destroy -auto-approve
```
