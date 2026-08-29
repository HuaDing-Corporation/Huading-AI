# Task 12 Report — Paid Doubao ownership and free CosyVoice creation

## Status

Completed. The planned commit subject is `feat(voice): enforce paid ownership and free CosyVoice creation`.

## Implementation summary

- Centralized customer voice visibility and usability in `app/services/voices.py`. Owner-bound Doubao voices now require exact tenant/user ownership, ready/nondeleted state, a speaker ID, and `activated_at <= requested_at < expires_at`. Rights-less Doubao rows fail closed unless the exact configured ID also has an active official registry row and the caller is in the platform tenant. CosyVoice and other non-Doubao history retain tenant-shared behavior.
- Routed brand-voice list/detail/update/delete, the voice picker, video pricing and submission, batch submission, and worker revalidation through the shared predicate. Workers load `VideoTask.created_by_user_id` and validate at `task.created_at`, so a task accepted before expiry remains valid after queue delay.
- Replaced customer Doubao auto-provisioning with `DOUBAO_MANUAL_ORDER_REQUIRED` for omitted or Doubao providers before provider resolution, row creation, charging, or slot allocation. Removed customer-route Doubao allocation/release helpers.
- Changed delivered Doubao deletion to local HTTP 204 soft deletion only. It performs no provider deletion, registry release, refund, or wallet mutation; awaiting-renewal retention protection remains active. CosyVoice remote deletion behavior remains unchanged.
- Added `POST /brand-voices/estimate` and moved explicit CosyVoice creation through a signed fixed-policy zero-credit operation. The quote persists the Chinese `cosyvoice_tts_reference_rate` disclosure with copy version 1 and the live tenant `tts/character` rate provenance.
- CosyVoice submit creates the zero usage and commits before the provider call, settles zero on a valid ready result, releases zero on failure, attaches provider cost to the canonical usage, and replays terminal operations without another provider call.
- Added separate `order_status` and derived `delivery_status` fields while preserving provider `status`.

## Files changed

Production:

- `backend/app/api/v1/routes/brand_voices.py`
- `backend/app/api/v1/routes/voices.py`
- `backend/app/api/v1/routes/videos.py`
- `backend/app/api/v1/routes/batches.py`
- `backend/app/schemas/brand_voices.py`
- `backend/app/services/voices.py`
- `backend/app/services/batches.py`
- `backend/app/services/video_pricing.py`
- `backend/app/workers/avatar_talk.py`

Tests:

- `backend/tests/test_brand_voice_pipeline.py`
- `backend/tests/test_batch_prod_pipeline.py`
- `backend/tests/test_avatar_talk_worker.py`
- `backend/tests/test_video_pricing_contract.py`
- `backend/tests/test_brand_voice_audio_upload.py`
- `backend/tests/test_analytics_api.py`
- `backend/tests/test_vip_entitlement.py`

`videos.py` and `video_pricing.py` are required connection points because both acceptance and signed pricing must enforce the same owner/time predicate as picker, batch, and worker paths. The three additional regression files replace stale assumptions about customer Doubao auto-cloning with the Task 12 manual-order contract; the audio tests continue to verify upload and historic MIME compatibility through explicit CosyVoice estimate/submit.

`app/services/plan_access.py` did not need a change: Task 12 consumes its existing platform-tenant and Doubao-provider helpers without altering their contract.

## TDD evidence

Initial focused RED command:

```text
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py -q
```

The new cases failed against the prior tenant-shared predicate, current-clock worker validation, Doubao auto-clone/slot release behavior, and CosyVoice creation without a signed operation. Implementation proceeded in access, worker-time, delete, manual-create, and zero-price billing slices until the focused suite was green.

The first full-suite run then exposed five stale expectations for the removed Doubao auto-clone endpoint:

```text
tests/test_analytics_api.py::test_self_registered_admin_free_has_no_vip_or_analytics_access
tests/test_brand_voice_audio_upload.py::test_upload_audio_then_create_brand_voice_without_direct_db_audio_seed
tests/test_brand_voice_audio_upload.py::test_create_brand_voice_accepts_historical_codec_param_audio_asset
tests/test_vip_entitlement.py::test_creator_me_entitlement_tracks_live_plan_and_matches_doubao_gate
tests/test_vip_entitlement.py::test_platform_tenant_me_has_entitlements_without_subscription
```

They were updated to assert manual Doubao ordering or use the signed CosyVoice flow, then passed before the full-suite rerun.

## Verification

Focused Task 12 suite:

```text
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py -q
85 passed, 1 warning
```

Billing/order/registry/pricing regression suite:

```text
uv run pytest tests/test_billing_operations.py tests/test_brand_voice_orders.py tests/test_brand_voice_provider_registry.py tests/test_video_pricing_contract.py -q
175 passed, 1 warning
```

Additional video voice-contract selection:

```text
uv run pytest tests/test_video_pipeline_contract.py -k "brand_voice or cosyvoice or voice" -q
4 passed, 1 warning
```

Canonical full backend suite from `backend/`:

```text
uv run pytest -q
1826 collected; exit 0; no failures
```

Static verification across every changed Python file returned `All checks passed!`; `git diff --check` returned no output.

## Self-review

- Confirmed official Doubao requires both exact configuration and an active official registry row, remains platform-tenant-only, and receives no owner/date backfill or billing.
- Confirmed unknown rights-less Doubao is excluded from customer list/detail/update/delete/picker/submission/worker paths, while CosyVoice remains shared inside its tenant.
- Confirmed paid Doubao is isolated to the payer and expiry blocks only new acceptance; worker validation uses the immutable submitter and submission timestamp.
- Confirmed manual Doubao create rejection occurs before audio lookup, provider resolution, BrandVoice/operation/usage creation, wallet mutation, or slot mutation.
- Confirmed local Doubao soft deletion never invokes provider deletion or makes the registry ID reusable, and the awaiting-renewal guard still runs first.
- Confirmed CosyVoice reserves, settles, fails, and replays exactly one zero-credit operation/usage and attaches provider cost only to that canonical usage.
- Confirmed parent video billing remains the sole CosyVoice TTS user charge; creation adds no user fee.
- Audited high-churn files with whitespace-insensitive diff stats. `brand_voices.py` is +377/-257 versus +365/-245 ignoring whitespace; `test_brand_voice_pipeline.py` is +472/-604 versus +457/-589 ignoring whitespace. The large diff is semantic replacement plus removal of 14 obsolete slot tests, not whole-file formatting noise.

## Concerns and production freeze

- Existing Starlette/httpx TestClient and Alembic path-separator deprecation warnings remain non-blocking.
- Customer list serialization performs an order-status lookup per visible voice; this preserves correctness and can be optimized separately if catalog volume warrants it.
- No network/provider call, push, merge, deploy, production migration, production/shared database write, provider-ID registration, configuration write, or production routing change was performed during implementation or verification.

## Fix Round 1 — 2026-08-29

### Status and findings closed

All seven Important review findings are addressed:

1. Batch estimate and submit now resolve the selected narration voice through the same owner, activation/expiry, and Huading-plan gate. The exact paid-voice expiry boundary is `activated_at <= requested_at < expires_at`.
2. Video submit and batch submit each capture one trusted server timestamp. The same value drives acceptance, quote/rate resolution, every repeated voice check, and every created `VideoTask.created_at`; worker revalidation therefore continues to use the accepted submission instant.
3. Brand-voice reads associate both create fulfillment and renewals, and select the latest order by `created_at DESC, id DESC`. Awaiting, rejected, and fulfilled renewals override an older fulfilled create order, including equal-timestamp rows.
4. Omitted/Doubao providers are rejected as `DOUBAO_MANUAL_ORDER_REQUIRED` before optional billing-header parsing. Missing, partial, and malformed billing headers cannot mask the provider policy or create billing/provider side effects.
5. CosyVoice clone payloads carry the stable request hash as the provider idempotency identity. The adapter checks remote inventory before creation and reconciles a transport exception by checking again; a fresh HTTP/local key for the same payload therefore cannot create a second remote voice.
6. CosyVoice finalization and deletion use the same `BillingOperation -> BrandVoice` row-lock order. Delete returns `BRAND_VOICE_CREATION_IN_PROGRESS` while creation is in progress; after successful finalization, delete soft-deletes locally and releases the remote speaker exactly once.
7. Provider success is accepted only through strict `CosyVoiceCloneResult` validation: canonical provider, exact ready status, nonblank string speaker ID, strict optional telemetry types, and no extra fields. Safely typed provider cost is preserved on the canonical usage even when the result is rejected.

The file-boundary concern is also closed. `backend/tests/test_video_pipeline_quota.py` now submits real public parent videos and inspects persisted canonical billing rows: CosyVoice has exactly one `video/second` row plus one `tts/character` row, Doubao has only the video row, and operation credits equal the canonical usage sum without a duplicate character fee. The Doubao case also proves same-tenant non-owner and expired-owner rejection. `app/services/plan_access.py` remains unchanged because the existing `uses_doubao_voice_clone()` to `require_doubao_voice_clone_access()` path is already the authoritative gate reused by video and batch estimate/submit; changing it would add no behavior.

### Fix Round 1 RED evidence

Each production change followed a failing behavior test. These are the exact focused commands and observations recorded before the corresponding implementation:

```text
uv run pytest tests/test_batch_prod_pipeline.py::test_batch_estimate_rejects_another_users_paid_doubao_voice -q
FAILED: the other payer's voice was accepted (HTTP 200 instead of 404).

uv run pytest tests/test_batch_prod_pipeline.py::test_batch_submit_uses_one_timestamp_for_gate_and_every_created_task -q
FAILED: the second current-clock voice check crossed expiry and returned HTTP 404 instead of accepting the request at the first trusted timestamp.

uv run pytest tests/test_brand_voice_pipeline.py::test_video_submit_persists_its_single_trusted_voice_gate_timestamp -q
FAILED: the task persisted an ORM-generated time rather than the route-captured voice-gate timestamp.

uv run pytest tests/test_brand_voice_pipeline.py::test_brand_voice_delivery_status_uses_latest_awaiting_renewal_order -q
FAILED: the API returned the older fulfilled create order instead of the latest awaiting renewal.

uv run pytest tests/test_brand_voice_pipeline.py::test_legacy_doubao_create_requires_manual_order_without_side_effects -q
9 failed, 3 passed: partial/malformed header combinations returned BILLING_HEADERS_REQUIRED or INVALID_IDEMPOTENCY_KEY before DOUBAO_MANUAL_ORDER_REQUIRED.

uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_recovers_remote_success_after_timeout_across_new_local_key -q
FAILED: the adapter propagated TimeoutError after the supplier had already created the remote voice.

uv run pytest tests/test_brand_voice_pipeline.py::test_cosyvoice_delete_loses_to_in_progress_finalize_without_orphan -q
FAILED: DELETE returned HTTP 204 while the operation was in progress; the outer finalizer then succeeded, producing the forbidden deleted-local/live-remote combination.

uv run pytest tests/test_brand_voice_pipeline.py::test_cosyvoice_create_rejects_result_without_explicit_canonical_provider -q
FAILED: a provider result with no explicit canonical provider was accepted with HTTP 201.
```

The renewal RED was subsequently expanded and renamed to the three-state deterministic matrix `test_brand_voice_delivery_status_uses_latest_renewal_order`. The strict-result RED was expanded and renamed to the eight-case matrix `test_cosyvoice_create_rejects_invalid_typed_provider_result`.

### Fix Round 1 GREEN evidence

Finding-specific reruns after implementation:

```text
uv run pytest tests/test_batch_prod_pipeline.py::test_batch_estimate_rejects_another_users_paid_doubao_voice tests/test_batch_prod_pipeline.py::test_batch_estimate_uses_exact_paid_voice_expiry_boundary
4 passed, 1 warning in 0.59s

uv run pytest tests/test_batch_prod_pipeline.py::test_batch_submit_uses_one_timestamp_for_gate_and_every_created_task tests/test_brand_voice_pipeline.py::test_video_submit_persists_its_single_trusted_voice_gate_timestamp tests/test_avatar_talk_worker.py::test_worker_uses_submission_time_for_paid_voice_expiry
3 passed, 1 warning in 0.46s

uv run pytest tests/test_brand_voice_pipeline.py::test_brand_voice_delivery_status_uses_latest_renewal_order
3 passed, 1 warning in 0.48s

uv run pytest tests/test_brand_voice_pipeline.py::test_legacy_doubao_create_requires_manual_order_without_side_effects
12 passed, 1 warning in 1.75s

uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_recovers_remote_success_after_timeout_across_new_local_key tests/test_brand_voice_pipeline.py::test_cosyvoice_new_http_key_reuses_stable_remote_request_identity
2 passed, 1 warning in 0.38s

uv run pytest tests/test_brand_voice_pipeline.py::test_cosyvoice_delete_loses_to_in_progress_finalize_without_orphan tests/test_brand_voice_pipeline.py::test_cosyvoice_delete_after_finalize_releases_remote_once
2 passed, 1 warning in 0.43s

uv run pytest tests/test_brand_voice_pipeline.py::test_cosyvoice_create_rejects_invalid_typed_provider_result
8 passed, 1 warning in 1.44s

uv run pytest tests/test_video_pipeline_quota.py::test_cosyvoice_parent_video_reserves_one_character_usage_without_duplicate_fee tests/test_video_pipeline_quota.py::test_doubao_parent_video_has_no_character_usage_and_enforces_payer_time_gate
2 passed, 1 warning in 0.42s
```

Combined review-specific selection:

```text
uv run pytest tests/test_batch_prod_pipeline.py::test_batch_estimate_uses_exact_paid_voice_expiry_boundary tests/test_batch_prod_pipeline.py::test_batch_submit_uses_one_timestamp_for_gate_and_every_created_task tests/test_brand_voice_pipeline.py::test_brand_voice_delivery_status_uses_latest_renewal_order tests/test_brand_voice_pipeline.py::test_cosyvoice_new_http_key_reuses_stable_remote_request_identity tests/test_brand_voice_pipeline.py::test_cosyvoice_delete_loses_to_in_progress_finalize_without_orphan tests/test_brand_voice_pipeline.py::test_cosyvoice_delete_after_finalize_releases_remote_once tests/test_brand_voice_pipeline.py::test_cosyvoice_create_rejects_invalid_typed_provider_result tests/test_cosyvoice_voice_clone_provider.py tests/test_video_pipeline_quota.py::test_cosyvoice_parent_video_reserves_one_character_usage_without_duplicate_fee tests/test_video_pipeline_quota.py::test_doubao_parent_video_has_no_character_usage_and_enforces_payer_time_gate
28 passed, 1 warning in 3.58s
```

### Verification

```text
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py tests/test_video_pricing_contract.py tests/test_cosyvoice_voice_clone_provider.py
142 passed, 1 warning in 16.66s

uv run pytest tests/test_video_pipeline_contract.py -k "brand_voice or cosyvoice or voice" -q
4 passed, 1 warning

uv run pytest
1804 passed, 54 skipped, 3 warnings in 173.34s (0:02:53)

uv run ruff check app/api/v1/routes/batches.py app/api/v1/routes/brand_voices.py app/api/v1/routes/videos.py app/providers/voice_clone/cosyvoice.py app/schemas/brand_voices.py app/services/batches.py tests/test_batch_prod_pipeline.py tests/test_brand_voice_pipeline.py tests/test_cosyvoice_voice_clone_provider.py tests/test_video_pipeline_quota.py
All checks passed!

git diff --check
No output; exit 0.
```

### Fix-round self-review and concerns

- Rechecked both batch routes, every video creation branch, and queued/failed batch rows for the single trusted timestamp; no worker-facing task retains an acceptance-time default.
- Rechecked the provider gate ordering across all twelve omitted/Doubao header combinations and confirmed zero BrandVoice, operation, usage, wallet, slot, and provider side effects.
- Rechecked both delete/finalize orderings and the failure/invalid-result finalizers under the shared lock order; terminal succeeded operations remain terminal after later deletion.
- Rechecked stable provider recovery at both adapter and public-route boundaries. Different HTTP idempotency keys can create distinct local operation/history rows, but the stable external request identity resolves to one remote speaker and only one supplier create call.
- Rechecked strict provider-result rejection for missing/wrong provider, missing/wrong status, missing/wrong speaker, wrong provider type, and extra fields.
- Existing Starlette/httpx and Alembic path-separator warnings remain non-blocking. The previously deferred brand-voice list N+1 order-status lookup is unchanged.
- No subagent, network/provider call, push, merge, deploy, migration, production/shared database write, Task 13 change, configuration write, or external side effect was performed.
