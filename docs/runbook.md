# CacheGate Operations Runbook

This runbook contains step-by-step procedures for deploying, maintaining, troubleshooting, and rolling back CacheGate in production.

---

## 1. Deployments

### 1.1 Automated Deployment (Recommended)
Deployments to production are triggered automatically via GitHub Actions upon tagging a commit:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The GitHub Actions workflow (`.github/workflows/deploy.yml`):
1. Authenticates to AWS using GitHub OIDC (no static AWS access keys).
2. Builds and scans the Docker image for vulnerabilities.
3. Pushes to AWS ECR tagged with the commit SHA and `latest`.
4. Updates the ECS task definition and performs a zero-downtime rolling update.
5. Verifies service stability and executes a smoke test against `/readyz`.

### 1.2 Infrastructure Provisioning (Terraform)

```bash
cd infra/terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

---

## 2. Rollback Procedure

If a bad deployment causes elevated 5xx errors or unexpected regressions:

### 2.1 Fast ECS Rollback via AWS CLI
Revert the ECS service to the previous known good task definition revision:

```bash
# 1. Identify previous revision number
CURRENT_REV=$(aws ecs describe-services --cluster cachegate-cluster-prod --services cachegate-service --query "services[0].taskDefinition" -o text)
echo "Current revision: $CURRENT_REV"

# 2. Update service to previous revision (e.g. revision 4)
aws ecs update-service \
  --cluster cachegate-cluster-prod \
  --service cachegate-service \
  --task-definition cachegate-task-prod:4 \
  --force-new-deployment

# 3. Monitor rollback progress
aws ecs wait services-stable --cluster cachegate-cluster-prod --services cachegate-service
```

---

## 3. Cache Management & Invalidation

CacheGate supports zero-downtime cache invalidation across exact and semantic tiers:

### 3.1 Flush Entire Cache
Flushes all cached completions across all tenants and models:

```bash
curl -X DELETE http://<GATEWAY_HOST>/admin/cache?all_entries=true \
  -H "Authorization: Bearer <ADMIN_API_KEY>"
```

### 3.2 Flush Specific Tenant
Flushes cached responses for an individual tenant without affecting others:

```bash
curl -X DELETE "http://<GATEWAY_HOST>/admin/cache?tenant_id=customer_42" \
  -H "Authorization: Bearer <ADMIN_API_KEY>"
```

### 3.3 Instant Invalidation via Schema Bump
To invalidate all cached entries globally (e.g. after changing embedding models or prompt normalization logic), bump `CACHE_SCHEMA_VERSION`:

```bash
# In task definition or environment variables
CACHE_SCHEMA_VERSION=v2
```
All old keys (`cg:v1:*`) are immediately bypassed and naturally expire via TTL.

---

## 4. Key Management & Rotation

### 4.1 Admin Key Rotation
1. Update `ADMIN_API_KEY` in AWS Secrets Manager:
   ```bash
   aws secretsmanager update-secret --secret-id cachegate-secrets-prod --secret-string '{"ADMIN_API_KEY":"new-secret"}'
   ```
2. Trigger an ECS rolling restart to pick up the new secret:
   ```bash
   aws ecs update-service --cluster cachegate-cluster-prod --service cachegate-service --force-new-deployment
   ```

### 4.2 Client Key Lifecycle
- **Create new key:**
  ```bash
  curl -X POST http://<GATEWAY_HOST>/admin/keys \
    -H "Authorization: Bearer <ADMIN_API_KEY>" \
    -H "Content-Type: application/json" \
    -d '{"name":"Team Alpha","tenant_id":"alpha","rate_limit_rps":20,"daily_budget_usd":50.0}'
  ```
- **Revoke compromised key:**
  ```bash
  curl -X DELETE http://<GATEWAY_HOST>/admin/keys/key_12345678 \
    -H "Authorization: Bearer <ADMIN_API_KEY>"
  ```

---

## 5. Cost Control & Teardown

To avoid unnecessary AWS charges when running ephemeral evaluation or test environments:

```bash
cd infra/terraform
terraform destroy -auto-approve
```

> **Note on AWS Cost Drivers:** NAT Gateways (~$32/mo each) and ALBs (~$16/mo) incur hourly charges regardless of traffic. Always destroy demo environments after capturing metrics and benchmarks.
