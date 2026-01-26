# Docker Base Services Startup (Local Test)

This guide starts **MySQL, Redis, MinIO, Elasticsearch, and Sandbox** using
`docker/docker-compose-base.yml`.

## Prereqs
- Docker daemon running
- `docker/.env` is the active config used by the compose file

## Optional: pull sandbox base images
```bash
docker pull infiniflow/sandbox-base-nodejs:latest
docker pull infiniflow/sandbox-base-python:latest
```

## Start services (with profiles)
```bash
docker compose -f docker/docker-compose-base.yml \
  --profile elasticsearch \
  --profile sandbox \
  up -d mysql redis minio es01 sandbox-executor-manager
```

## Check status
```bash
docker compose -f docker/docker-compose-base.yml ps
```

## Stop services
```bash
docker compose -f docker/docker-compose-base.yml down
```
