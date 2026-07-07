# Trax IO Inventory Optimizer — AWS Deployment Guide

**Audience:** DevOps Engineers, AWS Platform Architects, SREs  
**Last Updated:** 2026-07-07  
**Status:** Production-ready IaC; ECS/EKS deployment patterns for Phase 8+

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites & AWS Account Setup](#prerequisites--aws-account-setup)
3. [Container Images & ECR Registry](#container-images--ecr-registry)
4. [ECS Deployment (Recommended for v1)](#ecs-deployment-recommended-for-v1)
5. [EKS Deployment (Future, Higher Scale)](#eks-deployment-future-higher-scale)
6. [RDS Oracle Integration](#rds-oracle-integration)
7. [Kafka on MSK (Managed Streaming)](#kafka-on-msk-managed-streaming)
8. [Feature Store: DynamoDB + S3 Iceberg](#feature-store-dynamodb--s3-iceberg)
9. [Secrets Management & IAM](#secrets-management--iam)
10. [CI/CD Pipeline (CodePipeline/CodeBuild)](#cicd-pipeline-codepipelinecodebuild)
11. [Monitoring, Logging & Alarms](#monitoring-logging--alarms)
12. [Multi-Tenant Infrastructure](#multi-tenant-infrastructure)
13. [Disaster Recovery & Failover](#disaster-recovery--failover)
14. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

### Production AWS Deployment (All Regions)

```
┌─────────────────────────────────────────────────────────────┐
│                    AWS Account: TraxAi                      │
│                     Region: us-east-1                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐      ┌──────────────┐                    │
│  │  ALB / NLB   │      │  CloudFront  │                    │
│  │ (public)     │      │  (CDN)       │                    │
│  └──────┬───────┘      └──────┬───────┘                    │
│         │                     │                             │
│         ▼                     ▼                             │
│  ┌──────────────────────────────────────┐                 │
│  │  VPC: trax-io-vpc (10.0.0.0/16)     │                 │
│  │                                      │                 │
│  │  ┌─ Public Subnets (2 AZ) ─────────┐ │                 │
│  │  │ • NAT Gateway (high-availability)│ │                 │
│  │  │ • Bastion Host (for admin)       │ │                 │
│  │  └──────────────────────────────────┘ │                 │
│  │                                      │                 │
│  │  ┌─ Private Subnets (2 AZ) ────────┐ │                 │
│  │  │                                 │ │                 │
│  │  │  ┌──────────────────────────┐   │ │                 │
│  │  │  │ ECS Cluster / EKS        │   │ │                 │
│  │  │  │ (Fargate or EC2)         │   │ │                 │
│  │  │  │                          │   │ │                 │
│  │  │  │ • BFF (FastAPI)    [2x]  │   │ │                 │
│  │  │  │ • Writeback (Quarkus) [2]│   │ │                 │
│  │  │  │ • Web UI (nginx)   [2x]  │   │ │                 │
│  │  │  │ • Feature Store Jobs [1] │   │ │                 │
│  │  │  └──────────────────────────┘   │ │                 │
│  │  │                                 │ │                 │
│  │  │  ┌──────────────────────────┐   │ │                 │
│  │  │  │ Data Services            │   │ │                 │
│  │  │  │                          │   │ │                 │
│  │  │  │ • RDS Oracle 19c   [High AZ] │ │                 │
│  │  │  │ • DynamoDB (online layer)   │ │                 │
│  │  │  │ • MSK Kafka Cluster    [3]  │ │                 │
│  │  │  │ • S3 Iceberg Lake Bucket    │ │                 │
│  │  │  └──────────────────────────┘   │ │                 │
│  │  └──────────────────────────────────┘ │                 │
│  │                                      │                 │
│  └──────────────────────────────────────┘                 │
│                                                             │
│  ┌──────────────────────────────────────┐                 │
│  │ Observability & Compliance           │                 │
│  │ • CloudWatch (metrics, logs, alarms) │                 │
│  │ • X-Ray (distributed tracing)        │                 │
│  │ • CloudTrail (audit logs, 7-year)    │                 │
│  │ • Audit Manager (SOC 2 Type II)      │                 │
│  └──────────────────────────────────────┘                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Prerequisites & AWS Account Setup

### 1. AWS Account & Permissions

```bash
# You need an AWS Account with:
# - VPC creation rights
# - ECS/EKS/RDS/DynamoDB/S3 permissions
# - IAM role creation (for least-privilege policies)
# - KMS key management (per-tenant encryption)

# Recommended: Use AWS Organizations for multi-account structure
# Per-tenant account: Optional (v1 uses single "TraxAi" account with KMS isolation)
```

### 2. Install AWS CLI & Tools

```bash
# AWS CLI v2
brew install awscliv2  # macOS
# or download from https://aws.amazon.com/cli/

# Configure credentials
aws configure
# Enter:
#   AWS Access Key ID: [your key]
#   AWS Secret Access Key: [your secret]
#   Default region: us-east-1
#   Default output: json

# Verify
aws sts get-caller-identity

# Install other tools
brew install terraform  # or use CDK (Python)
brew install kubectl    # for EKS
brew install eksctl     # for EKS cluster creation
```

### 3. CDK Setup (Infrastructure as Code)

```bash
# CDK is written in Python; install dependencies
cd infra/feature-store
pip install -r requirements.txt  # or: uv sync

# Verify CDK
cdk --version  # Should be 2.x

# Bootstrap the AWS account (one-time, creates staging S3 for CDK)
cdk bootstrap aws://ACCOUNT_ID/us-east-1
```

### 4. Create S3 Bucket for Artifacts

```bash
# Create a central bucket for container images, terraform state, etc.
aws s3 mb s3://trax-io-deployment-artifacts-${ACCOUNT_ID} --region us-east-1

# Enable versioning (for rollback)
aws s3api put-bucket-versioning \
  --bucket trax-io-deployment-artifacts-${ACCOUNT_ID} \
  --versioning-configuration Status=Enabled

# Block public access
aws s3api put-public-access-block \
  --bucket trax-io-deployment-artifacts-${ACCOUNT_ID} \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

---

## Container Images & ECR Registry

### 1. Create ECR Repositories

```bash
# One ECR repo per service
for service in bff writeback-java web forecasting event-publisher; do
  aws ecr create-repository \
    --repository-name trax-io/${service} \
    --region us-east-1 \
    --image-tag-mutability MUTABLE \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=KMS
done

# Result: 
# 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/bff:latest
# 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/writeback-java:latest
# etc.
```

### 2. Build & Push Container Images

```bash
# Get ECR login token
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin 746251234567.dkr.ecr.us-east-1.amazonaws.com

# Build BFF service (FastAPI)
cd services/agent-spine
docker build -t trax-io-bff:latest -f deploy/bff.Dockerfile .
docker tag trax-io-bff:latest 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/bff:latest
docker push 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/bff:latest

# Build Writeback service (Quarkus/Java)
cd services/emro-writeback-java
docker build \
  -t trax-io-writeback-java:latest \
  -f Dockerfile \
  --build-arg BUILD_ENV=production .
docker tag trax-io-writeback-java:latest 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/writeback-java:latest
docker push 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/writeback-java:latest

# Build Web UI (nginx + React)
cd apps/web
docker build -t trax-io-web:latest -f Dockerfile .
docker tag trax-io-web:latest 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/web:latest
docker push 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/web:latest

# Verification
aws ecr describe-images --repository-name trax-io/bff --region us-east-1
```

### 3. Dockerfile Best Practices (Example: BFF)

```dockerfile
# deploy/bff.Dockerfile

# Multi-stage build: slim down final image
FROM python:3.14-slim AS builder

WORKDIR /build
COPY services/agent-spine/pyproject.toml .
COPY services/agent-spine/uv.lock .

# Install dependencies (non-editable)
RUN pip install uv && \
    uv pip install --python /usr/local/bin/python \
      --target /build/deps -r <(uv pip compile pyproject.toml)

# Final image
FROM python:3.14-slim

WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /build/deps /usr/local/lib/python3.14/site-packages

# Copy application code
COPY services/agent-spine /app

# Create non-root user (security best practice)
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Health check
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8001/health || exit 1

# Expose port
EXPOSE 8001

# Run with gunicorn
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8001", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--max-requests", "10000", \
     "--timeout", "120", \
     "trax_io_spine.bff.asgi:app"]
```

---

## ECS Deployment (Recommended for v1)

**Why ECS?** Simpler than EKS for v1 scale (3 main services + batch jobs). Fargate removes container host management.

### 1. Create ECS Cluster

```bash
# Create cluster (Fargate-only, no EC2 instances to manage)
aws ecs create-cluster \
  --cluster-name trax-io-prod \
  --region us-east-1 \
  --tags "key=Environment,value=production" "key=Project,value=TraxIO"

# Enable container insights (monitoring)
aws ecs put-cluster-capacity-providers \
  --cluster trax-io-prod \
  --capacity-providers FARGATE FARGATE_SPOT \
  --region us-east-1
```

### 2. Create IAM Task Execution Role

```bash
# Task execution role: allows ECS agent to pull images, write logs, etc.
cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
  --role-name trax-io-ecs-task-execution-role \
  --assume-role-policy-document file://trust-policy.json

# Attach managed policy
aws iam attach-role-policy \
  --role-name trax-io-ecs-task-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Add inline policy for ECR + Secrets Manager + KMS
cat > inline-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:/trax-io/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:DescribeKey"
      ],
      "Resource": "arn:aws:kms:us-east-1:ACCOUNT_ID:key/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:ACCOUNT_ID:log-group:/ecs/trax-io/*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name trax-io-ecs-task-execution-role \
  --policy-name trax-io-ecr-secrets \
  --policy-document file://inline-policy.json
```

### 3. Create Task Definition (BFF Service)

```bash
# Task definition: Docker image + CPU/memory + environment + logging
cat > bff-task-definition.json << 'EOF'
{
  "family": "trax-io-bff",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/trax-io-ecs-task-execution-role",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/trax-io-ecs-task-role",
  "containerDefinitions": [
    {
      "name": "bff",
      "image": "746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/bff:latest",
      "portMappings": [
        {
          "containerPort": 8001,
          "hostPort": 8001,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "ENVIRONMENT",
          "value": "production"
        },
        {
          "name": "LOG_LEVEL",
          "value": "INFO"
        }
      ],
      "secrets": [
        {
          "name": "PLANNER_RECS_FILE",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:/trax-io/bff/recs-file"
        },
        {
          "name": "EXTRACT_DIR",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:/trax-io/bff/extract-dir"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/trax-io/bff",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8001/health || exit 1"],
        "interval": 10,
        "timeout": 3,
        "retries": 3,
        "startPeriod": 30
      }
    }
  ]
}
EOF

# Register task definition
aws ecs register-task-definition \
  --cli-input-json file://bff-task-definition.json
```

### 4. Create ECS Service (BFF)

```bash
# Service: manages how many tasks to run, load balancing, rolling updates

cat > bff-service.json << 'EOF'
{
  "cluster": "trax-io-prod",
  "serviceName": "trax-io-bff",
  "taskDefinition": "trax-io-bff:1",
  "desiredCount": 2,
  "launchType": "FARGATE",
  "platformVersion": "LATEST",
  "networkConfiguration": {
    "awsvpcConfiguration": {
      "subnets": [
        "subnet-private-1a",
        "subnet-private-1b"
      ],
      "securityGroups": [
        "sg-bff-service"
      ],
      "assignPublicIp": "DISABLED"
    }
  },
  "loadBalancers": [
    {
      "targetGroupArn": "arn:aws:elasticloadbalancing:us-east-1:ACCOUNT_ID:targetgroup/trax-io-bff/abc123",
      "containerName": "bff",
      "containerPort": 8001
    }
  ],
  "deploymentConfiguration": {
    "maximumPercent": 200,
    "minimumHealthyPercent": 100,
    "deploymentCircuitBreaker": {
      "enable": true,
      "rollback": true
    }
  },
  "placementConstraints": [],
  "tags": [
    {
      "key": "Service",
      "value": "BFF"
    }
  ]
}
EOF

# Create service
aws ecs create-service --cli-input-json file://bff-service.json
```

### 5. Auto-Scaling

```bash
# Create scaling policy: scale up if CPU > 70%
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id service/trax-io-prod/trax-io-bff \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 2 \
  --max-capacity 10 \
  --region us-east-1

# CPU-based scaling
aws application-autoscaling put-scaling-policy \
  --policy-name bff-cpu-scaling \
  --service-namespace ecs \
  --resource-id service/trax-io-prod/trax-io-bff \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration \
    TargetValue=70.0,PredefinedMetricSpecification={PredefinedMetricType=ECSServiceAverageCPUUtilization},ScaleOutCooldown=60,ScaleInCooldown=300
```

---

## EKS Deployment (Future, Higher Scale)

**When to use EKS:** v2+ with 10,000+ parts/min throughput, custom scheduling, multi-region.

### 1. Create EKS Cluster

```bash
# Using eksctl (simplifies cluster creation)
eksctl create cluster \
  --name trax-io-prod \
  --version 1.30 \
  --region us-east-1 \
  --nodegroup-name trax-io-nodes \
  --node-type t3.xlarge \
  --nodes 3 \
  --nodes-min 3 \
  --nodes-max 10 \
  --enable-ssm \
  --with-oidc \
  --enable-cluster-logging api,audit,authenticator,controllerManager,scheduler \
  --tags Environment=production,Project=TraxIO

# Update kubeconfig
aws eks update-kubeconfig --region us-east-1 --name trax-io-prod
kubectl cluster-info
```

### 2. Helm Charts for Trax IO Services

```bash
# Install Helm (package manager for Kubernetes)
brew install helm

# Create Helm chart directory
mkdir -p deploy/helm/trax-io/{bff,writeback,web}

# Example: BFF Helm Chart values
cat > deploy/helm/trax-io/bff/values.yaml << 'EOF'
replicaCount: 3

image:
  repository: 746251234567.dkr.ecr.us-east-1.amazonaws.com/trax-io/bff
  tag: latest
  pullPolicy: IfNotPresent

resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: 1000m
    memory: 2Gi

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

service:
  type: ClusterIP
  port: 8001

ingress:
  enabled: true
  className: alb
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
  hosts:
    - host: bff.trax-io.internal
      paths:
        - path: /
          pathType: Prefix
EOF

# Deploy using Helm
helm install trax-io-bff deploy/helm/trax-io/bff \
  --namespace trax-io \
  --create-namespace
```

---

## RDS Oracle Integration

### 1. Create RDS Oracle Instance

```bash
# Security group for RDS
aws ec2 create-security-group \
  --group-name trax-io-rds-sg \
  --description "RDS Oracle access for Trax IO" \
  --vpc-id vpc-xxxxx

# Allow inbound from ECS/EKS
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxx \
  --protocol tcp \
  --port 1521 \
  --source-security-group-id sg-ecs-tasks

# Create RDS instance (Oracle 19c, High Availability)
aws rds create-db-instance \
  --db-instance-identifier trax-io-oracle \
  --db-instance-class db.r5.2xlarge \
  --engine oracle-ee \
  --engine-version 19.0.0.0.ru-2024-01.1 \
  --allocated-storage 500 \
  --storage-type gp3 \
  --storage-encrypted \
  --kms-key-id arn:aws:kms:us-east-1:ACCOUNT_ID:key/xxx \
  --master-username admin \
  --master-user-password [generated-strong-password] \
  --db-subnet-group-name trax-io-db-subnet-group \
  --vpc-security-group-ids sg-xxxxx \
  --multi-az \
  --backup-retention-period 30 \
  --enable-cloudwatch-logs-exports '["alert","audit","trace","listener"]' \
  --enable-iam-database-authentication \
  --deletion-protection \
  --tags "Key=Project,Value=TraxIO" "Key=Environment,Value=production"

# Get endpoint after creation
aws rds describe-db-instances --db-instance-identifier trax-io-oracle \
  --query 'DBInstances[0].Endpoint.Address'
# Result: trax-io-oracle.xxxxx.us-east-1.rds.amazonaws.com
```

### 2. Flyway Setup for Oracle Schema

```bash
# Store Flyway scripts in S3
aws s3 cp services/emro-writeback-java/src/main/resources/db/migration/ \
  s3://trax-io-deployment-artifacts-${ACCOUNT_ID}/flyway/ \
  --recursive

# Create ECS task to run Flyway migration
cat > flyway-task-definition.json << 'EOF'
{
  "family": "trax-io-flyway",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/trax-io-ecs-task-role",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/trax-io-ecs-task-execution-role",
  "containerDefinitions": [
    {
      "name": "flyway",
      "image": "flyway/flyway:latest",
      "environment": [
        {
          "name": "FLYWAY_URL",
          "value": "jdbc:oracle:thin:@trax-io-oracle.xxxxx.us-east-1.rds.amazonaws.com:1521/ORCL"
        },
        {
          "name": "FLYWAY_USER",
          "value": "admin"
        },
        {
          "name": "FLYWAY_BASELINE_ON_MIGRATE",
          "value": "true"
        },
        {
          "name": "FLYWAY_BASELINE_VERSION",
          "value": "0"
        }
      ],
      "secrets": [
        {
          "name": "FLYWAY_PASSWORD",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:/trax-io/rds/admin-password"
        }
      ],
      "mountPoints": [
        {
          "sourceVolume": "flyway-sql",
          "containerPath": "/flyway/sql"
        }
      ]
    }
  ],
  "volumes": [
    {
      "name": "flyway-sql",
      "efsVolumeConfiguration": {
        "fileSystemId": "fs-xxxxx",
        "rootDirectory": "/flyway"
      }
    }
  ]
}
EOF

# Run migration
aws ecs run-task \
  --cluster trax-io-prod \
  --task-definition trax-io-flyway:1 \
  --launch-type FARGATE \
  --network-configuration awsvpcConfiguration={subnets=['subnet-xxxxx'],securityGroups=['sg-xxxxx']}
```

---

## Kafka on MSK (Managed Streaming)

### 1. Create MSK Cluster

```bash
# Create MSK cluster (3 brokers, multi-AZ)
aws kafka create-cluster \
  --cluster-name trax-io-kafka \
  --broker-node-group-info \
      InstanceType=kafka.m5.large,\
      ClientSubnets=['subnet-private-1a','subnet-private-1b','subnet-private-1c'],\
      SecurityGroups=['sg-msk'] \
  --kafka-version 3.6.0 \
  --number-of-broker-nodes 3 \
  --encryption-info EbsStorageInfo={VolumeSize=100},InTransit={ClientBroker='TLS',Enabled=true},AtRest={DataVolumeKmsKeyId='arn:aws:kms:us-east-1:ACCOUNT_ID:key/xxx',Enabled=true} \
  --logging-info BrokerLogs={CloudWatchLogs={Enabled=true,LogGroup='/aws/msk/trax-io'},Firehose={Enabled=false},S3={Enabled=false}} \
  --tags Environment=production,Project=TraxIO

# Get bootstrap servers
aws kafka get-bootstrap-brokers --cluster-arn arn:aws:kafka:us-east-1:ACCOUNT_ID:cluster/trax-io-kafka/xxxxx
# Result: b-1.trax-io-kafka.xxxxx.kafka.us-east-1.amazonaws.com:9092, ...
```

### 2. Create Kafka Topics

```bash
# Get MSK broker list
BROKER_LIST=$(aws kafka get-bootstrap-brokers --cluster-arn arn:aws:kafka:us-east-1:ACCOUNT_ID:cluster/trax-io-kafka/xxxxx \
  --query 'BootstrapBrokerStringTls' --output text)

# Create topics (via ECS task or bastion host)
aws ssm start-session --target i-xxxxx  # SSH into bastion

# From bastion:
kafka-topics.sh \
  --create \
  --bootstrap-server ${BROKER_LIST} \
  --topic optimizer.writeback.v1 \
  --partitions 6 \
  --replication-factor 3 \
  --config retention.ms=604800000

kafka-topics.sh \
  --create \
  --bootstrap-server ${BROKER_LIST} \
  --topic optimizer.writeback.results.v1 \
  --partitions 3 \
  --replication-factor 3 \
  --config retention.ms=259200000

kafka-topics.sh \
  --create \
  --bootstrap-server ${BROKER_LIST} \
  --topic optimizer.writeback.dlq.v1 \
  --partitions 1 \
  --replication-factor 3 \
  --config retention.ms=2592000000
```

---

## Feature Store: DynamoDB + S3 Iceberg

### 1. DynamoDB Online Layer

```bash
# Create DynamoDB table (per tenant)
aws dynamodb create-table \
  --table-name trax-io-features-aircanada \
  --attribute-definitions \
      AttributeName=tenant_id,AttributeType=S \
      AttributeName=pn_location,AttributeType=S \
  --key-schema \
      AttributeName=tenant_id,KeyType=HASH \
      AttributeName=pn_location,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --sse-specification Enabled=true,SSEType=KMS,KMSMasterKeyId=arn:aws:kms:us-east-1:ACCOUNT_ID:key/xxx \
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true \
  --ttl AttributeName=ttl,Enabled=true \
  --tags "Key=Tenant,Value=aircanada" "Key=Project,Value=TraxIO"

# Global Secondary Index for queries by pn_location
aws dynamodb update-table \
  --table-name trax-io-features-aircanada \
  --attribute-definitions AttributeName=pn,AttributeType=S AttributeName=location,AttributeType=S \
  --global-secondary-indexes '[
    {
      "IndexName": "pn-location-index",
      "KeySchema": [
        {"AttributeName": "pn", "KeyType": "HASH"},
        {"AttributeName": "location", "KeyType": "RANGE"}
      ],
      "Projection": {"ProjectionType": "ALL"},
      "ProvisionedThroughput": {"ReadCapacityUnits": 100, "WriteCapacityUnits": 100}
    }
  ]'
```

### 2. S3 Iceberg Lake

```bash
# Create S3 buckets (data lake)
aws s3 mb s3://trax-io-lake-aircanada --region us-east-1

# Enable versioning
aws s3api put-bucket-versioning \
  --bucket trax-io-lake-aircanada \
  --versioning-configuration Status=Enabled

# Enable encryption
aws s3api put-bucket-encryption \
  --bucket trax-io-lake-aircanada \
  --server-side-encryption-configuration '{
    "Rules": [
      {
        "ApplyServerSideEncryptionByDefault": {
          "SSEAlgorithm": "aws:kms",
          "KMSMasterKeyID": "arn:aws:kms:us-east-1:ACCOUNT_ID:key/xxx"
        }
      }
    ]
  }'

# Create Glue database
aws glue create-database \
  --database-input Name=trax_io_lake_aircanada,Description="Trax IO data lake for Air Canada"

# Glue jobs will create Iceberg tables on this database
```

---

## Secrets Management & IAM

### 1. Store Secrets in Secrets Manager

```bash
# Database credentials
aws secretsmanager create-secret \
  --name /trax-io/rds/admin-password \
  --description "RDS Oracle admin password" \
  --secret-string '{"username":"admin","password":"GeneratedStrongPassword123!"}'

# Writeback service JWT key
aws secretsmanager create-secret \
  --name /trax-io/jwt-public-key \
  --description "JWT public key for writeback service" \
  --secret-string file://public.pem

# Kafka bootstrap servers
aws secretsmanager create-secret \
  --name /trax-io/kafka/bootstrap-servers \
  --secret-string "b-1.trax-io-kafka.xxxxx.kafka.us-east-1.amazonaws.com:9092,..."
```

### 2. IAM Task Role (Least Privilege)

```bash
# Create task role (for containers to assume)
cat > task-role-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
  --role-name trax-io-ecs-task-role \
  --assume-role-policy-document file://task-role-trust-policy.json

# Attach inline policy (DynamoDB, S3, Secrets Manager, KMS)
cat > task-role-inline-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem"
      ],
      "Resource": "arn:aws:dynamodb:us-east-1:ACCOUNT_ID:table/trax-io-features-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::trax-io-lake-*",
        "arn:aws:s3:::trax-io-lake-*/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:/trax-io/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:DescribeKey"
      ],
      "Resource": "arn:aws:kms:us-east-1:ACCOUNT_ID:key/*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name trax-io-ecs-task-role \
  --policy-name trax-io-permissions \
  --policy-document file://task-role-inline-policy.json
```

---

## CI/CD Pipeline (CodePipeline/CodeBuild)

### 1. CodeBuild Project (Build & Test)

```bash
# Create buildspec file (in repo root)
cat > buildspec.yml << 'EOF'
version: 0.2

phases:
  pre_build:
    commands:
      - echo "Logging in to Amazon ECR..."
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
      - REPOSITORY_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/trax-io
      - COMMIT_HASH=$(echo $CODEBUILD_RESOLVED_SOURCE_VERSION | cut -c 1-7)
      - IMAGE_TAG=${COMMIT_HASH:=latest}
  
  build:
    commands:
      - echo "Building and pushing Docker images..."
      
      # BFF
      - cd services/agent-spine
      - docker build -t $REPOSITORY_URI/bff:$IMAGE_TAG -f deploy/bff.Dockerfile .
      - docker push $REPOSITORY_URI/bff:$IMAGE_TAG
      - docker tag $REPOSITORY_URI/bff:$IMAGE_TAG $REPOSITORY_URI/bff:latest
      - docker push $REPOSITORY_URI/bff:latest
      - echo "[$REPOSITORY_URI/bff:$IMAGE_TAG]" > /tmp/bff-image.json
      - cd ../..
      
      # Writeback Java
      - cd services/emro-writeback-java
      - mvn clean package -DskipTests -Dnet.bytebuddy.experimental=true
      - docker build -t $REPOSITORY_URI/writeback-java:$IMAGE_TAG .
      - docker push $REPOSITORY_URI/writeback-java:$IMAGE_TAG
      - echo "[$REPOSITORY_URI/writeback-java:$IMAGE_TAG]" > /tmp/writeback-image.json
      - cd ../..
      
      # Web UI
      - cd apps/web
      - docker build -t $REPOSITORY_URI/web:$IMAGE_TAG -f Dockerfile .
      - docker push $REPOSITORY_URI/web:$IMAGE_TAG
      - echo "[$REPOSITORY_URI/web:$IMAGE_TAG]" > /tmp/web-image.json
      - cd ../..

  post_build:
    commands:
      - echo "Build completed on `date`"
      - printf '[{"name":"bff","imageUri":"%s"},{"name":"writeback-java","imageUri":"%s"},{"name":"web","imageUri":"%s"}]' $REPOSITORY_URI/bff:$IMAGE_TAG $REPOSITORY_URI/writeback-java:$IMAGE_TAG $REPOSITORY_URI/web:$IMAGE_TAG > imagedefinitions.json

artifacts:
  files: imagedefinitions.json
  name: BuildArtifact

cache:
  paths:
    - '/root/.m2/**/*'
    - '/root/.cache/**/*'
EOF

# Create CodeBuild project
aws codebuild create-project \
  --name trax-io-build \
  --service-role arn:aws:iam::ACCOUNT_ID:role/codebuild-role \
  --artifacts type=CODEPIPELINE \
  --environment type=LINUX_CONTAINER,image=aws/codebuild/standard:7.0,computeType=BUILD_GENERAL1_LARGE,environmentVariables='[{"name":"AWS_ACCOUNT_ID","value":"746251234567"},{"name":"AWS_DEFAULT_REGION","value":"us-east-1"}]' \
  --source type=CODEPIPELINE,buildspec=buildspec.yml \
  --logs-config cloudWatchLogs={status=ENABLED,groupName=/aws/codebuild/trax-io}
```

### 2. CodePipeline

```bash
# Pipeline: GitHub → Build → Deploy (ECS)
aws codepipeline create-pipeline \
  --cli-input-json '{
    "pipeline": {
      "name": "trax-io-pipeline",
      "roleArn": "arn:aws:iam::ACCOUNT_ID:role/codepipeline-role",
      "artifactStore": {
        "type": "S3",
        "location": "trax-io-deployment-artifacts-ACCOUNT_ID"
      },
      "stages": [
        {
          "name": "Source",
          "actions": [
            {
              "name": "SourceAction",
              "actionTypeId": {
                "category": "Source",
                "owner": "ThirdParty",
                "provider": "GitHub",
                "version": "1"
              },
              "configuration": {
                "Owner": "mas5464",
                "Repo": "trax-io-inventory-optimizer",
                "Branch": "main"
              },
              "outputArtifacts": [
                {"name": "SourceOutput"}
              ]
            }
          ]
        },
        {
          "name": "Build",
          "actions": [
            {
              "name": "Build",
              "actionTypeId": {
                "category": "Build",
                "owner": "AWS",
                "provider": "CodeBuild",
                "version": "1"
              },
              "inputArtifacts": [
                {"name": "SourceOutput"}
              ],
              "configuration": {
                "ProjectName": "trax-io-build"
              },
              "outputArtifacts": [
                {"name": "BuildOutput"}
              ]
            }
          ]
        },
        {
          "name": "Deploy",
          "actions": [
            {
              "name": "DeployToECS",
              "actionTypeId": {
                "category": "Deploy",
                "owner": "AWS",
                "provider": "ECS",
                "version": "1"
              },
              "inputArtifacts": [
                {"name": "BuildOutput"}
              ],
              "configuration": {
                "ClusterName": "trax-io-prod",
                "ServiceName": "trax-io-bff",
                "FileName": "imagedefinitions.json"
              }
            }
          ]
        }
      ]
    }
  }'
```

---

## Monitoring, Logging & Alarms

### 1. CloudWatch Logs & Dashboards

```bash
# Create log group
aws logs create-log-group --log-group-name /trax-io/ecs
aws logs put-retention-policy --log-group-name /trax-io/ecs --retention-in-days 30

# Create custom dashboard
aws cloudwatch put-dashboard \
  --dashboard-name TraxIOMetrics \
  --dashboard-body file://dashboard.json  # See below
```

**dashboard.json:**
```json
{
  "widgets": [
    {
      "type": "metric",
      "properties": {
        "metrics": [
          ["AWS/ECS", "CPUUtilization", {"stat": "Average"}],
          ["AWS/ECS", "MemoryUtilization", {"stat": "Average"}],
          ["AWS/DynamoDB", "ConsumedReadCapacityUnits"],
          ["AWS/DynamoDB", "ConsumedWriteCapacityUnits"]
        ],
        "period": 300,
        "stat": "Average",
        "region": "us-east-1",
        "title": "Service Health"
      }
    },
    {
      "type": "log",
      "properties": {
        "query": "fields @timestamp, @message | stats count() by @message | sort @timestamp desc",
        "region": "us-east-1",
        "title": "Error Logs"
      }
    }
  ]
}
```

### 2. CloudWatch Alarms

```bash
# CPU too high
aws cloudwatch put-metric-alarm \
  --alarm-name trax-io-ecs-cpu-high \
  --alarm-description "ECS CPU utilization > 80%" \
  --metric-name CPUUtilization \
  --namespace AWS/ECS \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:trax-io-alerts

# Memory too high
aws cloudwatch put-metric-alarm \
  --alarm-name trax-io-ecs-memory-high \
  --alarm-description "ECS Memory utilization > 85%" \
  --metric-name MemoryUtilization \
  --namespace AWS/ECS \
  --statistic Average \
  --period 300 \
  --threshold 85 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:trax-io-alerts

# RDS disk space
aws cloudwatch put-metric-alarm \
  --alarm-name trax-io-rds-disk-space \
  --alarm-description "RDS free storage space < 10%" \
  --metric-name FreeStorageSpace \
  --namespace AWS/RDS \
  --statistic Average \
  --period 300 \
  --threshold 50000000000 \
  --comparison-operator LessThanThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:trax-io-alerts
```

---

## Multi-Tenant Infrastructure

### Per-Tenant Architecture

```
AWS Account: TraxAi (shared for all tenants in v1)

For each tenant (e.g., "aircanada", "united", "delta"):

1. Secrets Manager
   └─ /trax-io/aircanada/db-credentials
   └─ /trax-io/aircanada/jwt-key
   └─ /trax-io/aircanada/kafka-servers

2. KMS Customer-Managed Key
   └─ arn:aws:kms:us-east-1:ACCOUNT_ID:key/aircanada-cmk
   └─ Used for: RDS encryption, S3 encryption, DynamoDB encryption

3. DynamoDB Table
   └─ trax-io-features-aircanada (partition key: tenant_id)

4. S3 Buckets
   └─ trax-io-lake-aircanada (Iceberg tables)
   └─ trax-io-landing-aircanada (nightly extracts)

5. Glue Database
   └─ trax_io_lake_aircanada

6. IAM Role (scoped to tenant)
   └─ Permissions restricted to aircanada resources only
```

**Tenant Context Enforcement:**

Every ECS task carries tenant context:

```
Environment:
  TENANT_ID=aircanada
  KMS_KEY_ID=arn:aws:kms:us-east-1:ACCOUNT_ID:key/aircanada-cmk
  S3_BUCKET=trax-io-lake-aircanada
  DYNAMODB_TABLE=trax-io-features-aircanada
```

Application layer asserts tenant match on all operations:
```python
# services/agent-spine/src/trax_io_spine/core.py
@dataclass
class TenantContext:
    tenant_id: str
    
    def assert_match(self, input_tenant_id: str):
        if self.tenant_id != input_tenant_id:
            raise TenantMismatchError(
                f"Context tenant {self.tenant_id} != input {input_tenant_id}"
            )
```

---

## Disaster Recovery & Failover

### 1. RDS High Availability (Multi-AZ)

```bash
# Already enabled in RDS creation above:
# --multi-az
# Automatic failover to standby in another AZ (< 2 min RTO)
```

### 2. ECS Blue-Green Deployment

```bash
# Rolling update (zero-downtime)
aws ecs update-service \
  --cluster trax-io-prod \
  --service trax-io-bff \
  --force-new-deployment \
  --deployment-configuration minimumHealthyPercent=100,maximumPercent=200

# Automatic rollback on failure
# (already configured in deploymentCircuitBreaker above)
```

### 3. Data Backup Strategy

```bash
# RDS automated backups (30-day retention)
# S3 Iceberg versions (immutable, 90-day recovery window)
# DynamoDB PITR (point-in-time recovery, 35 days)

# Manual snapshot before major changes
aws rds create-db-snapshot \
  --db-instance-identifier trax-io-oracle \
  --db-snapshot-identifier trax-io-oracle-pre-deployment-2026-07-07
```

---

## Troubleshooting

### ECS Task Won't Start

```bash
# Check task logs
aws logs tail /ecs/trax-io/bff --follow

# Inspect task details
aws ecs describe-tasks \
  --cluster trax-io-prod \
  --tasks arn:aws:ecs:us-east-1:ACCOUNT_ID:task/trax-io-prod/xxxxx

# Common issues:
# - Image not found in ECR → verify docker push succeeded
# - Secrets not readable → check IAM task role + KMS key perms
# - Memory/CPU exceeded → increase task definition allocation
```

### RDS Connection Timeout

```bash
# Check security group
aws ec2 describe-security-groups --group-ids sg-xxxxx

# Verify network path
aws ec2 describe-network-interfaces \
  --filters "Name=group-id,Values=sg-xxxxx"

# Test connectivity from bastion
mysql -h trax-io-oracle.xxxxx.us-east-1.rds.amazonaws.com -u admin -p
```

### Kafka Consumer Lag

```bash
# Check consumer group status
kafka-consumer-groups.sh \
  --bootstrap-server ${BROKER_LIST} \
  --group trax-io-writeback-consumer \
  --describe

# If lag is high:
# 1. Scale up writeback service (more replicas)
# 2. Check RDS/DynamoDB capacity (might be bottleneck)
# 3. Check CloudWatch metrics for errors
```

---

## Next Steps

1. **Customize for your AWS account:** Replace `ACCOUNT_ID`, region, tenant names
2. **Deploy infrastructure:** Run CDK or Terraform to provision AWS resources
3. **Configure secrets:** Populate Secrets Manager with real credentials
4. **Test end-to-end:** Smoke test via CodePipeline → ECS → RDS
5. **Set up monitoring:** Verify CloudWatch dashboards & alarms are firing
6. **Plan cutover:** Coordinate with customer for real eMRO connection

---

## References

- [AWS ECS Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/best_practices.html)
- [AWS RDS Oracle Guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html)
- [AWS MSK Documentation](https://docs.aws.amazon.com/msk/latest/developerguide/what-is-msk.html)
- [Trax IO Design Document § 8 (AWS Deployment)](./design/2026-04-14-trax-io-inventory-optimizer-design.md)
- [Trax IO AWS Infrastructure Guide](./guides-src/05-aws-infrastructure-guide.md)

