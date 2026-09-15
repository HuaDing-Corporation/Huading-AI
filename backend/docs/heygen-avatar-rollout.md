# HeyGen Avatar Candidate

## Release Gate

This is a candidate, not a production enablement or paid-media verification.
Deploy only the integrated backend/frontend revision after review, PostgreSQL
concurrency tests, migration checks and CI. Apply revision `20260915_0039` after
`20260829_0038`. The integrated health/readiness gate must explicitly accept and
validate the new schema. Do not deploy this migration with the old 0038-only gate.

Inject `HEYGEN_API_KEY` securely into the API and avatar workers. Configure both
`ENGINE_HEYGEN_AVATAR_IV_CNY_PER_SECOND` and
`ENGINE_HEYGEN_PRECISION_CNY_PER_SECOND` as positive CNY/second values. Neither has
a usable default. Missing credentials or the selected model's cost fails before
TTS. Costs are frozen before paid steps and labeled `configured_estimate`, not
observed USD charges. Public examples $3/minute and $4/minute at FX7 imply
0.35 and approximately 0.4666667 CNY/second; these are reference arithmetic only,
not enabled defaults or audited account charges. User sale rates are unchanged.

Production compose loads its backend/worker environment via `env_file`; confirm
both processes received the same configuration without printing secret values.
Keep the `avatar` queue subscribed. The API's existing periodic orphan recovery
loop also queues due avatar runs; it must be running for automatic reconnection.

`ENGINE_S3_PUBLIC_ENDPOINT` must be the externally reachable HTTPS endpoint.
Private/local MinIO endpoints cannot be submitted to HeyGen. Input signed URLs
are frozen once with a minimum one-hour lifetime. The exact endpoint, body and
idempotency key are retained in restricted database recovery data. Never print
these bodies/URLs or expose them in GET/list/SSE/telemetry. Media download uses
only exact `files.heygen.ai` and `files2.heygen.ai` hosts, forbids redirects, has a
size cap and a time bound; API credentials are not sent to media hosts.

## Recovery And Rollback

Existing tasks without a supplier snapshot stay explicitly OmniHuman. New photo
tasks freeze `heygen/avatar_iv`; video tasks freeze `heygen/lipsync_precision`.
Do not change the global avatar ProviderConfig row or its unique constraint.
No automatic fallback exists. Video input is locally looped/trimmed to external
TTS duration before one paid submission. Per-lease artifact paths prevent late
workers overwriting current outputs.

`HEYGEN_PENDING` means running with funds reserved; due runs rejoin the same task.
A known remote ID always uses GET. An uncertain POST uses the same stored request
and key, only within 24 hours and before input expiry. `HEYGEN_REVIEW_REQUIRED`
is a running, financially held task excluded from automatic re-submission.
Uncertain TTS without a committed audio checkpoint is also held, not synthesized
again. There is no public retry/refund command for these states.

For manual review, an authorized operator must reconcile the durable task and
supplier identity/account evidence before taking action. Do not reset the run,
clear its identity, create a fresh key, re-sign its frozen body, refund a pending
job, or claim a paid result failed based only on elapsed time. If an identity
cannot be recovered, retain the hold and escalate; any administrative mutation
requires a separately reviewed procedure and explicit authorization.

Before rollback, pause new submissions and drain/reconcile new runs with the
new worker; do not run old workers on frozen HeyGen jobs. Migration downgrade
rejects nonempty run tables to avoid discarding paid-job evidence. Do not clear
the table just to make rollback pass. Preserve usage/cost records after terminal
history deletion; run FK cascades with VideoTask, while held tasks are not terminal.

## Paid Acceptance Plan (Not Executed)

With separate explicit spend authorization, generate one photo-IV example and
one Precision example using a 3-10-second source and longer external TTS, plus
a shorter TTS example. Confirm response model, API acceptance, remote identity,
actual audio/video duration, lip sync, no unexpected provider captions, existing
subtitle and AIGC labels. Interrupt a GET and verify recovery on the same ID.
Compare supplier billing with the configured estimate, not with historical
Digital Twin/HeyGen-TTS probes. Do not rerun old paid probe scripts.
