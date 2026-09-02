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

- `ENVIRONMENT=production`
- `POSTGRES_PASSWORD`
- `JWT_SECRET_KEY` (32+ characters)
- `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`
- `ENGINE_APIMART_API_KEY`, `ENGINE_APIMART_IMAGE_MODEL`, and
  `ENGINE_APIMART_VIDEO_MODEL` for the default image and video_gen providers
- Volcengine, OpenAI fallback, and DeepSeek/LLM keys used by enabled features
- `ENGINE_DOUBAO_OFFICIAL_VOICE_IDS` from the platform owner's signed exact
  inventory. Keep the repository example empty; enter the real list only in a
  trusted production terminal and never paste it into ordinary logs or screenshots.
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

Existing deployments must set the corrected APIMart cost basis in their real
`infra/.env` before deploying this release:

```dotenv
ENGINE_APIMART_CREDIT_USD=0.10
ENGINE_USD_CNY_RATE=7.0
```

Updating an example does not overwrite an existing `infra/.env`. Confirm these
values on the production host instead of copying the example over secrets.

AIBRAIN user pricing has separate prompt-token tiers at `272000` tokens.
Existing production `infra/.env` files that already override the original six
rates will not pick up new example values automatically. Keep the complete
twelve-key set explicit before deployment:

```dotenv
ENGINE_AIBRAIN_LOW_INPUT_CREDITS_PER_1K=1.12
ENGINE_AIBRAIN_LOW_OUTPUT_CREDITS_PER_1K=6.72
ENGINE_AIBRAIN_LOW_ABOVE_272K_INPUT_CREDITS_PER_1K=2.24
ENGINE_AIBRAIN_LOW_ABOVE_272K_OUTPUT_CREDITS_PER_1K=10.08
ENGINE_AIBRAIN_MID_INPUT_CREDITS_PER_1K=2.80
ENGINE_AIBRAIN_MID_OUTPUT_CREDITS_PER_1K=16.80
ENGINE_AIBRAIN_MID_ABOVE_272K_INPUT_CREDITS_PER_1K=5.60
ENGINE_AIBRAIN_MID_ABOVE_272K_OUTPUT_CREDITS_PER_1K=25.20
ENGINE_AIBRAIN_HIGH_INPUT_CREDITS_PER_1K=5.60
ENGINE_AIBRAIN_HIGH_OUTPUT_CREDITS_PER_1K=33.60
ENGINE_AIBRAIN_HIGH_ABOVE_272K_INPUT_CREDITS_PER_1K=11.20
ENGINE_AIBRAIN_HIGH_ABOVE_272K_OUTPUT_CREDITS_PER_1K=50.40
```

Requests with up to `272000` prompt tokens use the original rates; requests
above that boundary use the `ABOVE_272K` rates. Do not replace the real env file
with an example because the real file also contains deployment credentials.

Overall generation waits in `infra/.env` must be `1500` seconds for Seedance,
OmniHuman, APIMart image/video, the image-provider wrapper, and OpenAI image.
Keep per-request HTTP timeouts and polling intervals at their shorter template
values. Existing deployments must update their real `infra/.env` manually;
copying a newer example does not overwrite that runtime file. The API checks
image-queue work every 60 seconds and marks tasks failed after 1800 seconds
without durable progress, releasing photo and video reverse-prompt
reservations. Keep `ENGINE_ORPHAN_TASK_STALE_SECONDS` above the 1500-second
provider wait ceiling.

Do not commit `infra/.env`.

## Existing Production: Pricing Closure Staged Release

Do not use `infra/deploy.sh` for the first pricing-closure rollout. That script
is intentionally restricted to an already-migrated, already-registered production
database and refuses the legacy state, so it cannot perform the required staged
gate between the legacy database check and migration.

Every pricing-closure readiness command below uses the dependency-free pricing
wrapper. It reserves stdout for exactly one redacted JSON document; invalid
configuration, import failures, runtime failures, and malformed implementation
output become a generic JSON failure without replaying captured stderr. Celery
drain, broker, and worker checks use the separate Celery release gate. Production
automatic migration defaults to off in both Compose and the environment example
and must remain off for routine and staged startup. The later worker and nginx
starts use `--no-deps` so Compose cannot reconsider or recreate the already-audited
backend.

Use one approved release SHA. Build all application images while the old stack is
still serving, then hold one uninterrupted write-free maintenance window from the
final backup through both registry audits. A backup or `preflight` captured while
an old API or worker can still write is stale and does not authorize migration.

1. Confirm the checkout is clean, fetch `develop`, and fast-forward only to the
   approved SHA. Verify `HEAD` equals that SHA before continuing.
2. Confirm the real `infra/.env` contains `ENVIRONMENT=production` and the signed
   exact official voice inventory. Do not print either the file or the inventory.
3. Build every approved application image without restarting the old stack:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     build backend worker worker-image worker-video frontend
   ```

4. Enter the approved maintenance window. Stop `nginx` first so no new public
   request can arrive. Use the count-only gate to drain existing work, stop the
   backend with a 1,800-second grace period, and run the same drain again after
   the final enqueue source is gone. Only then stop all workers with the same
   grace period. Keep PostgreSQL, Redis, and MinIO running:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     stop nginx
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python -m scripts.ops.celery_release_gate drain \
     --expected-workers 3 --wait-seconds 1800 --poll-seconds 10
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     stop -t 1800 backend
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python -m scripts.ops.celery_release_gate drain \
     --expected-workers 3 --wait-seconds 1800 --poll-seconds 10
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     stop -t 1800 worker worker-image worker-video
   ```

   Each drain requires active, reserved, scheduled, logical broker queue, and
   unacknowledged counts to remain zero across consecutive samples. A nonzero or
   unavailable count aborts the release; do not shorten the grace period or kill
   work to continue. After all workers stop, run the broker-only boundary gate
   before any backend service restart:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python -m scripts.ops.celery_release_gate broker-only \
     --wait-seconds 300 --poll-seconds 10
   ```

   Continue only when the `default`, `avatar`, `image`, and `video` broker queues,
   every discovered unapproved non-empty logical queue, and the unacknowledged-message
   count remain zero across the gate's consecutive samples. Redis LIST discovery
   includes Kombu priority keys and the configured global key prefix; output exposes
   only safe aggregate counts, never queue or task names. A failure keeps nginx,
   backend, and all workers stopped and aborts the release; the one-off command does
   not restart the backend service.

   Verify these five services remain stopped for steps 5-9. Do not start a backend,
   worker, or ingress container between the final backup and the successful second
   audit.
5. Create the final write-free PostgreSQL custom-format backup in a restricted host
   directory. Verify it with `pg_restore --list` and record its SHA-256 checksum.
   This is the rollback artifact; an earlier online backup is not a substitute.
6. Run the legacy-schema-safe gate with the already-built backend image against the
   still-unmigrated production database:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python scripts/ops/pricing_closure_readiness_cli.py preflight
   ```

   Continue only when the command exits `0` with `ready: true`. `preflight` is
   read-only and is the only readiness mode permitted on the legacy `0031`
   schema. A full `audit` before migration must return `SCHEMA_NOT_READY` and is
   not a substitute for this gate.
7. After the verified backup, successful preflight, and release approval, run the
   migration as a one-off command while all application writers remain stopped.
   Do not use `up -d` here; normal backend startup would expose a healthy API before
   official registration is complete:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend alembic upgrade head
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend alembic current
   ```

8. Run the full `audit` once before registration, still inside the write-free
   maintenance window:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python scripts/ops/pricing_closure_readiness_cli.py audit
   ```

   This transitional audit must
   exit `2` with exactly one blocker, `OFFICIAL_DOUBAO_REGISTRY_MISMATCH`; it
   must also report zero unknown IDs and zero active official registry rows.
   Any other result stops the release. Customer Doubao routing remains fail
   closed during this state.
9. Run `register-official` exactly once. Its safe output reports only the number
   of registered rows, never provider IDs:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python scripts/ops/pricing_closure_readiness_cli.py register-official
   ```

   Do not retry after an error. Preserve the transaction result and return to
   the release approver for a decision.
   Immediately rerun the full `audit` as a one-off command:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python scripts/ops/pricing_closure_readiness_cli.py audit
   ```

   It must exit `0` with no blockers. Audit and registration output is deliberately
   provider-ID-redacted; retain it with the backup checksum and approved SHA.
10. Only after the second audit passes may the new API be started. Start backend
    and frontend without rebuilding and with startup migration disabled, verify
    backend health and revision, and rerun the full audit from the running backend
    before any queue or public ingress is reopened:

    ```bash
    sudo env BACKEND_AUTO_MIGRATE=0 \
      docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
      up -d --no-build backend frontend
    sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
      exec -T backend alembic current
    sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
      exec -T backend python scripts/ops/pricing_closure_readiness_cli.py audit
    ```

11. After the post-start audit is also exit `0`, start workers. Run the fixed
    three-worker release probe and require exit `0`, `ready: true`, and the
    approved queue topology (`{default, avatar}`, `{image}`, `{video}`) before
    opening public ingress last. The probe emits only redacted status fields and
    safe counts:

    ```bash
    sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
      up -d --no-build --no-deps worker worker-image worker-video
    sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
      exec -T backend \
      python -m scripts.ops.celery_release_gate workers \
      --expected-workers 3 --wait-seconds 300 --poll-seconds 10
    sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
      up -d --no-build --no-deps nginx
    ```

12. Run canary traffic, billing smoke tests, and reconciliation. Change the pricing
    workbench status to “已上线” only after every check passes. If any step from the
    migration through the post-start audit fails, keep ingress and application
    writers stopped and return to the release approver; do not retry registration
    or improvise database repairs. Any backend/frontend, worker, or nginx Compose start,
    revision check, post-start audit, or worker probe failure must
    explicitly stop nginx, backend, frontend, and all workers; cleanup continues if one stop
    command fails. The earlier drain failure path is deliberately different: keep
    the old workers available for controlled recovery and do not start the target.

For the exact backup, approval, smoke, and reconciliation checklist, follow
`docs/02-方案设计/定价闭环生产上线门禁.md`.

## First Start

A genuinely empty database uses a separate strict bootstrap gate. Do not run the
legacy `preflight`: it intentionally accepts only the approved `0031`-to-`0038`
release line. Never use `infra/deploy.sh` for first startup, an
existing `0031` database, or the first pricing-closure rollout.

1. Pin the clean checkout to the approved SHA, verify the production environment
   and signed official inventory without printing either, and build all application
   images exactly once.
2. Start only PostgreSQL, Redis, and MinIO. Keep nginx, backend, frontend, and all
   workers stopped. Run the same `broker-only --wait-seconds 300 --poll-seconds 10`
   boundary gate from staged step 4 and require the four broker queues and
   unacknowledged-message count to remain stably zero. Then create and verify a
   restricted custom-format empty-database backup as the rollback baseline.
3. Run the target image's dedicated empty gate. It exits `0` only when the database
   contains no tables, production mode is active, the official inventory is present
   and valid, approved pricing defaults match, and legacy slot writes are retired:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend \
     python scripts/ops/pricing_closure_readiness_cli.py bootstrap-empty-preflight
   ```

4. While every application writer and public ingress remains stopped, migrate with
   the already-built image and verify the single target revision:

   ```bash
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend alembic upgrade head
   sudo docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
     run --rm --no-deps backend alembic current
   ```

5. Follow steps 8-12 of the staged procedure above without alteration: accept only
   the documented pre-registration mismatch, run `register-official` exactly once,
   require the post-registration audit to pass, start backend/frontend and re-audit,
   then start workers and require the same fixed three-worker release probe from
   step 11 to pass before opening nginx. Empty bootstrap is not an exemption from
   registration, audit, smoke, reconciliation, or final approval.

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

The one-command shortcut below is for routine releases only after the
pricing-closure migration and registry gate have completed on this production
database and the post-registration audit has already passed. If the database is
still before `0038`, official registration is pending, or the workbench is not yet
“已上线”, use the write-free staged release procedure instead.

```bash
bash infra/deploy.sh --routine --release-sha <approved-sha>
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f nginx
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f backend
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f worker
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env logs -f worker-video
```

Routine mode first checks the currently running backend is at migration `0038`
and that the redacted pricing-closure audit exits successfully. It fetches only
the approved `develop` history, fast-forwards to the exact approved SHA, and
builds without restarting the live stack. Before downtime, the target image must
have exactly one Alembic head and it must equal the running database revision;
otherwise routine mode refuses the release instead of auto-migrating.
Routine mode also sets `BACKEND_AUTO_MIGRATE=0` for the recreated backend, so
its startup command cannot apply a migration after the revision checks pass.

After nginx closes, the script waits for all Celery active, reserved, scheduled,
and broker-queued work to reach zero. It stops the backend with a 1,800-second
grace period, repeats the count-only drain check to close enqueue races, and only
then stops workers. Before restarting backend/frontend, routine mode runs:

```bash
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
  run --rm --no-deps backend \
  python -m scripts.ops.celery_release_gate broker-only \
  --wait-seconds 300 --poll-seconds 10
```

The four approved broker queues, every discovered unapproved non-empty logical
queue, and the unacknowledged-message count must remain zero across the gate's
consecutive samples. With every application writer still stopped, routine mode
then runs the target image's full audit as another one-off command:

```bash
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env \
  run --rm --no-deps backend \
  python scripts/ops/pricing_closure_readiness_cli.py audit
```

Only after that target-image audit passes may the target backend/frontend start.
The running backend must then pass the revision check and a second full audit.
All three workers must answer the release probe and expose exactly the approved
queue topology before nginx opens last. Any backend/frontend, worker, or nginx
Compose start, revision check, post-start audit, or worker probe failure explicitly
stops nginx, backend, frontend, and all workers and keeps public ingress closed.
An earlier drain failure instead leaves the old workers available for recovery.
Calling the script without routine mode or an approved SHA is refused. Nginx also
uses Docker DNS dynamic resolution for backend, frontend, and MinIO.

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
