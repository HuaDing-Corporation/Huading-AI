# Huading Production Deploy

Target: one Hong Kong host, domain `huadingai.cn`, Docker Compose, HTTPS via Let's Encrypt.

## Prerequisites

1. Buy or prepare a Hong Kong server with public IPv4.
2. Install Docker Engine and the Docker Compose plugin.
3. Point the Alibaba Cloud DNS A record for `huadingai.cn` to the server public IP.
4. Open security group ports `80/tcp` and `443/tcp`. Do not open Postgres, Redis, or MinIO.

## Configure

```bash
git clone <repo-url> huading
cd huading
cp infra/.env.prod.example infra/.env
```

Edit `infra/.env` and fill real values for:

- `POSTGRES_PASSWORD`
- `JWT_SECRET_KEY` (32+ characters)
- `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`
- `ENGINE_APIMART_API_KEY`, `ENGINE_APIMART_IMAGE_MODEL`, and
  `ENGINE_APIMART_VIDEO_MODEL` for the default image and video_gen providers
- Volcengine, OpenAI fallback, and DeepSeek/LLM keys used by enabled features
- `ENGINE_SEEDANCE_MINI_MODEL` only if rolling video_gen back to the legacy Ark
  `seedance-mini` provider

Image edit/reference and video_gen i2v flows send APIMart presigned URLs built
from `ENGINE_S3_PUBLIC_ENDPOINT`; keep the production value at
`https://huadingai.cn`.
Generated object URLs use the bucket-root path, for example
`https://huadingai.cn/huading-videos/...`, so nginx preserves the signed object
path when forwarding to MinIO. Configure object storage CORS for public
`GET/HEAD` on generated media and BGM previews.
Video generation defaults to APIMart `doubao-seedance-2.0`; `seedance-mini`
remains registered only as a database rollback option.

Do not commit `infra/.env`.

## First Start

```bash
bash infra/deploy.sh
```

The backend service runs `alembic upgrade head` before starting Uvicorn.

Nginx creates a one-day self-signed placeholder certificate on first boot so the HTTPS server can start. Replace it with a real Let's Encrypt certificate:

```bash
# Equivalent certbot command inside the certbot container:
# certbot certonly --webroot -w /var/www/certbot -d huadingai.cn
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env run --rm certbot \
  certonly --webroot -w /var/www/certbot \
  -d huadingai.cn \
  --email <admin-email> --agree-tos --no-eff-email

docker compose -f infra/docker-compose.prod.yml --env-file infra/.env exec nginx nginx -s reload
```

The long-running `certbot` service renews certificates every 12 hours.

## Verify

```bash
curl -I http://huadingai.cn
curl -I https://huadingai.cn
curl -i https://huadingai.cn/api/v1/auth/me
curl -N https://huadingai.cn/api/v1/videos/example/events
```

Then open `https://huadingai.cn`, register a tenant, log in, and create a small test task.
For APIMart, run one text-to-image, one image-to-image, and one video_gen task
after setting a funded `ENGINE_APIMART_API_KEY`; no social publishing credentials
or raw provider keys are stored.

## Operations

```bash
bash infra/deploy.sh
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f nginx
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f backend
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f worker
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f worker-video
```

`infra/deploy.sh` runs `git pull --ff-only`, rebuilds the stack, restarts nginx as
a compatibility fallback, then prints `alembic current` and Compose `ps`. Nginx
also uses Docker DNS dynamic resolution for backend, frontend, and MinIO, so
container IP changes after rebuild do not require manual nginx restarts.

Persistent data lives in Docker volumes:

- `huading-prod_postgres-data`
- `huading-prod_redis-data`
- `huading-prod_minio-data`
- `huading-prod_certbot-etc`
- `huading-prod_certbot-www`

Back these up before destructive server maintenance.

## Notes

- Only nginx publishes ports `80` and `443`.
- Postgres, Redis, MinIO, backend, and frontend are internal Compose services.
- MinIO presigned media URLs use `https://huadingai.cn/huading-videos/...`; nginx and MinIO CORS allow cross-origin `GET/HEAD` for the BGM waveform player.
- `NEXT_PUBLIC_USE_MOCK=0` is baked into the frontend production image.
