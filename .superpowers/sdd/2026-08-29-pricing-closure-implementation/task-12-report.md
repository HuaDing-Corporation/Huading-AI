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

## Fix Round 2 — 2026-08-29

### Status and changes

The two open review items are closed without changing the already-approved ownership, timestamp, renewal, manual-provider, delete/finalize, or typed-result production behavior.

- The adapter no longer calls DashScope 1.25.21 `VoiceEnrollmentService.create_voice()`. That SDK method routes through its private retry loop and can repeat a non-idempotent create after a remotely committed response is lost. For the pinned real SDK service, the adapter now sends the same create payload through one direct `BaseApi.call()` transport attempt and reconstructs the SDK's normal response/error contract without any create retry.
- Before the one create attempt, the adapter snapshots the remote voice IDs returned for the supplier-compatible prefix. If the one attempt raises, recovery queries again and accepts only the unique new voice ID in the post-attempt set difference. No new voice re-raises the transport failure; multiple new voices fail closed as ambiguous.
- The ten-character DashScope prefix remains only a supplier-required label/filter. It is no longer treated as request identity and no pre-existing prefix match is reused. The adapter accepts an external identity only when it is the complete 64-character lowercase SHA-256 hash and caches successful mappings by that exact full key.
- The public route provides the durable, verifiable full mapping through `BillingOperation.request_hash`, scoped by tenant, payment user, and operation. A fresh HTTP idempotency key with an identical full request hash verifies its signed quote and replays the succeeded/in-progress canonical resource without resolving or calling the provider. A second full-hash lookup after reservation locking rolls back provisional BrandVoice/operation/usage rows if the initial lookup lost a race.
- `test_video_submit_persists_its_single_trusted_voice_gate_timestamp` received a test-only determinism correction after the local wall clock crossed its hard-coded Fix Round 1 expiry during verification. It now captures the test's current UTC instant and uses a one-hour validity window; no Finding 2 production code changed.

### Fix Round 2 RED evidence

Pinned SDK internal-retry reproduction:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_prevents_pinned_sdk_retry_after_remote_timeout
FAILED tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_prevents_pinned_sdk_retry_after_remote_timeout
AssertionError: assert 'bv11111111-remote-2' == 'bv11111111-remote-1'
1 failed, 1 warning in 0.49s
```

The test instantiates the real pinned `VoiceEnrollmentService`, replaces only `BaseApi.call`, and models the exact sequence: the first create records the remote voice and raises `TimeoutError`; the SDK retry loop then issues a second create before returning control to the adapter.

Old 32-bit-prefix collision reproduction:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_does_not_reuse_different_full_hash_with_same_old_prefix
FAILED tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_does_not_reuse_different_full_hash_with_same_old_prefix
AssertionError: assert 'bvdeadbeef-existing-other-request' == 'bvdeadbeef-new-request'
1 failed, 1 warning in 0.50s
```

The two request identities have different complete hashes but share the legacy first eight hex characters. The RED proves the prior prefix lookup incorrectly returned the other request's speaker without a create attempt.

Durable fresh-key and late-race reproduction:

```text
uv run pytest tests/test_brand_voice_pipeline.py::test_cosyvoice_new_http_key_reuses_stable_remote_request_identity
FAILED tests/test_brand_voice_pipeline.py::test_cosyvoice_new_http_key_reuses_stable_remote_request_identity
AssertionError: assert 2 == 1
1 failed, 1 warning in 0.78s
```

The test forces the second request's initial full-hash lookup to miss, representing requests that began together. Before the late recheck, two provider clone calls and two local resources were created.

### Fix Round 2 GREEN evidence

The two provider regressions after implementation:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_prevents_pinned_sdk_retry_after_remote_timeout tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_does_not_reuse_different_full_hash_with_same_old_prefix
2 passed, 1 warning in 0.14s
```

Public route durable/late replay:

```text
uv run pytest tests/test_brand_voice_pipeline.py::test_cosyvoice_new_http_key_reuses_stable_remote_request_identity
1 passed, 1 warning in 0.28s
```

Complete provider file:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py
10 passed, 1 warning in 0.24s
```

Complete provider plus brand-voice lifecycle files:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py tests/test_brand_voice_pipeline.py
60 passed, 1 warning in 8.12s
```

Task 12 focused regression suite:

```text
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py tests/test_video_pricing_contract.py tests/test_cosyvoice_voice_clone_provider.py
144 passed, 1 warning in 16.95s
```

Static verification:

```text
uv run ruff check app/providers/voice_clone/cosyvoice.py app/api/v1/routes/brand_voices.py tests/test_cosyvoice_voice_clone_provider.py tests/test_brand_voice_pipeline.py
All checks passed!

uv run python -m compileall -q app/providers/voice_clone/cosyvoice.py app/api/v1/routes/brand_voices.py
No output; exit 0.

git diff --check
No output; exit 0.
```

### Files changed

- `backend/app/providers/voice_clone/cosyvoice.py`
- `backend/app/api/v1/routes/brand_voices.py`
- `backend/tests/test_cosyvoice_voice_clone_provider.py`
- `backend/tests/test_brand_voice_pipeline.py`
- `.superpowers/sdd/2026-08-29-pricing-closure-implementation/task-12-report.md`

### Fix-round self-review and concerns

- Inspected the locked DashScope 1.25.21 source and `uv.lock`; only non-idempotent voice creation bypasses the SDK retry loop. Idempotent inventory queries retain the SDK behavior.
- Confirmed normal create responses, transport-timeout recovery, repeated exact full keys, colliding old prefixes, provider errors, runtime global restoration, delete, and TTS paths remain covered through the provider's public methods.
- Confirmed route replay is exact on the complete request hash and is scoped to tenant/user/operation. Failed prior operations are not treated as successful mappings, and provisional late-race rows/reservations are rolled back before provider resolution.
- Confirmed the provider rechecks and stores its exact full-key map while holding the serialized DashScope runtime lock, so concurrent calls sharing one provider instance cannot both pass the mapping check and create.
- The one-shot transport helper intentionally depends on private fields of the pinned DashScope 1.25.21 `VoiceEnrollmentService`; the real-SDK regression makes a future SDK incompatibility fail visibly. This should be re-audited when upgrading DashScope.
- If post-timeout inventory contains more than one previously unseen matching voice, recovery fails closed rather than risking cross-request association.
- Existing Starlette/httpx deprecation warning remains non-blocking. No subagent, network/provider call, push, merge, deploy, migration, production/shared database write, production configuration change, supplier registration write, Task 13 work, or external side effect was performed.

## Fix Round 3 — 2026-08-29

### Status and finding closed

The remaining concurrent timeout-recovery finding is closed without changing the approved billing, ownership, route replay, pinned-SDK transport, deletion, or Doubao behavior.

- A valid 64-character external request hash now produces a deterministic nine-character lowercase-alphanumeric supplier marker by hashing the complete request key and encoding the result into the maximum DashScope-compatible base-36 space. The old `bv` plus first-eight-hex marker is no longer used for request-bound recovery.
- Remote inventory snapshots and post-timeout set differences are therefore scoped to the full-request-derived marker. Requests with different complete hashes but the same old first eight characters no longer share recovery inventory.
- Recovery still accepts only one newly observed voice for the request-bound marker. No new voice re-raises the original transport error, and multiple new voices remain an explicit fail-closed ambiguity error.
- The deterministic regression uses two independent `CosyVoiceCloneProvider` instances and independent enrollment adapters with the runtime lock disabled to model separate processes. A shared fake supplier forces A's uncertain create to overlap B's successful create. B returns only B's speaker; A fails closed and cannot replay B from its exact-key cache.

### Fix Round 3 RED evidence

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_never_cross_binds_concurrent_request_hashes -q
FAILED tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_never_cross_binds_concurrent_request_hashes
AssertionError: assert False
where False = isinstance(None, TimeoutError)
Command exited 1.
```

Both requests used `bvdeadbeef` on RED. B committed its speaker while A's create had an uncertain non-commit outcome; A then treated B's sole new speaker as its own and returned successfully instead of failing closed.

### Fix Round 3 GREEN evidence

Finding-specific regression:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_never_cross_binds_concurrent_request_hashes -q
Command exited 0.
```

Pinned transport, same-full-request recovery, old-prefix collision, concurrent cross-bind, and durable/late route replay selection:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_prevents_pinned_sdk_retry_after_remote_timeout tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_recovers_remote_success_after_timeout_across_new_local_key tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_does_not_reuse_different_full_hash_with_same_old_prefix tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_never_cross_binds_concurrent_request_hashes tests/test_brand_voice_pipeline.py::test_cosyvoice_new_http_key_reuses_stable_remote_request_identity -q
5 passed, 1 warning
```

Complete provider file and provider plus brand-voice lifecycle files:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py -q
11 passed, 1 warning

uv run pytest tests/test_cosyvoice_voice_clone_provider.py tests/test_brand_voice_pipeline.py -q
61 passed, 1 warning
```

Task 12 focused regression suite:

```text
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py tests/test_video_pricing_contract.py tests/test_cosyvoice_voice_clone_provider.py
145 passed, 1 warning in 16.90s
```

Static verification:

```text
uv run ruff check app/providers/voice_clone/cosyvoice.py tests/test_cosyvoice_voice_clone_provider.py
All checks passed!

uv run python -m compileall -q app/providers/voice_clone/cosyvoice.py
No output; exit 0.

git diff --check
No output; exit 0.
```

### Files changed

- `backend/app/providers/voice_clone/cosyvoice.py`
- `backend/tests/test_cosyvoice_voice_clone_provider.py`
- `.superpowers/sdd/2026-08-29-pricing-closure-implementation/task-12-report.md`

### Fix-round self-review and concerns

- Confirmed the marker change applies only to validated complete request hashes. The legacy safe-label fallback for calls without an external request key is unchanged.
- Confirmed the complete request key deterministically selects the same remote marker, while the two complete hashes in the regression share the old first eight characters but select different markers.
- Confirmed same-key timeout recovery after a remote commit, the pinned SDK one-shot create transport, exact-key in-process replay, and durable/late route replay remain green.
- Confirmed A's failed recovery stores no exact-key mapping: a second public clone call cannot return B's speaker.
- DashScope permits fewer than ten lowercase-alphanumeric prefix characters, so the remote recovery namespace is necessarily finite. Nine base-36 characters maximize that supplier field and use the complete request hash, but do not make cryptographic collisions mathematically impossible. Multiple observed candidates fail closed; the marker scheme should be revisited if DashScope adds a full idempotency key or request metadata.
- Existing Starlette/httpx deprecation warning remains non-blocking. No subagent, network/provider call, push, merge, deploy, migration, production/shared database write, provider-ID registration, configuration write, production routing change, Doubao routing change, Task 13 work, or external side effect was performed.

## Fix Round 4 — 2026-08-29

### Status and design

The finite nine-character marker collision finding is closed without a schema change or migration.

- Every request-bound CosyVoice supplier call now requires the ID of its already-committed `cosyvoice_brand_voice_create` `BillingOperation`. The adapter verifies that the durable operation's complete `request_hash` exactly matches the external request key before inventory, create, or timeout recovery can run.
- A dedicated recovery coordinator takes a PostgreSQL transaction advisory lock derived from the complete nine-character supplier marker. The namespace is global across tenants, users, operation states, and provider instances because the configured DashScope API credential/workspace and remote prefix inventory are global. A 64-bit advisory-key collision only over-serializes unrelated markers and cannot create an unsafe association.
- While holding that lock, the coordinator reads every committed CosyVoice-create request hash and recomputes its marker with the same adapter function. Any different complete hash with the current marker fails closed before the supplier is queried, even when it is the only foreign candidate. Exact same-hash operations remain allowed so existing durable/same-request replay semantics are unchanged.
- All historical operation outcomes reserve their marker. Failed operations are intentionally included because a timed-out supplier request can become visible after the local recovery query; reusing that marker could otherwise reintroduce a late cross-bind.
- The route still commits the zero-price operation and usage before provider work, adds `billing_operation_id` to the clone payload, and releases the provider-config resolver's read transaction before the coordinator opens its independent session. The coordinator holds only its advisory lock and read snapshot across supplier inventory/create/recovery, rolls back to release it, and only then does the route use the established `BillingOperation -> BrandVoice` finalization lock order.
- SDK create remains a single direct `BaseApi.call()` attempt. Normal response-loss recovery still accepts the unique marker-local inventory delta for the same durable request; a different request can no longer enter that recovery namespace.

### Fix Round 4 RED evidence

The required true-marker collision regression was written first. It forces two distinct valid 64-character hashes to the exact same nine-character `collision` marker, uses two independent providers and enrollment adapters, disables the process runtime lock, and overlaps A's uncertain non-commit with B's successful remote create:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_fails_closed_on_true_marker_collision_across_instances -q
FAILED tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_fails_closed_on_true_marker_collision_across_instances
AssertionError: assert {'a': {'speaker_id': 'collision-speaker-b', ...},
                        'b': {'speaker_id': 'collision-speaker-b', ...}} == {}
Command exited 1.
```

This reproduces the open finding exactly: A accepted B's sole newly visible voice and both requests returned B's speaker.

The subsequent vertical TDD slices also produced these RED results before their implementations:

```text
test_cosyvoice_recovery_collision_decision_fails_closed_for_one_foreign_candidate
ModuleNotFoundError: No module named 'app.services.cosyvoice_recovery'

test_cosyvoice_recovery_gate_uses_postgresql_transaction_advisory_lock
ImportError: cannot import name 'lock_cosyvoice_recovery_marker'

test_cosyvoice_recovery_claim_checks_durable_operation_scope_before_supplier_call
ImportError: cannot import name 'claim_cosyvoice_recovery_marker'

test_cosyvoice_create_is_signed_free_committed_and_idempotent
KeyError: 'billing_operation_id'

test_cosyvoice_external_request_key_requires_durable_operation_identity
Failed: DID NOT RAISE CosyVoiceCloneError
```

### Fix Round 4 GREEN evidence

Finding-specific collision regression:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_fails_closed_on_true_marker_collision_across_instances -q
1 passed, 1 warning
```

Complete provider and route lifecycle files:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py -q
16 passed, 1 warning

uv run pytest tests/test_brand_voice_pipeline.py -q
51 passed, 1 warning
```

Pinned one-shot transport, same-request response-loss recovery, old-prefix isolation, distinct-marker concurrency, true-marker collision, durable identity enforcement, and route durable/late replay selection:

```text
uv run pytest tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_prevents_pinned_sdk_retry_after_remote_timeout tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_recovers_remote_success_after_timeout_across_new_local_key tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_clone_does_not_reuse_different_full_hash_with_same_old_prefix tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_never_cross_binds_concurrent_request_hashes tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_timeout_recovery_fails_closed_on_true_marker_collision_across_instances tests/test_cosyvoice_voice_clone_provider.py::test_cosyvoice_external_request_key_requires_durable_operation_identity tests/test_brand_voice_pipeline.py::test_cosyvoice_new_http_key_reuses_stable_remote_request_identity tests/test_brand_voice_pipeline.py::test_cosyvoice_route_fails_closed_before_provider_on_durable_true_marker_collision -q
8 passed, 1 warning
```

Task 12 focused suite:

```text
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py tests/test_video_pricing_contract.py tests/test_cosyvoice_voice_clone_provider.py -q
151 tests collected; 100%; exit 0; no failures
```

Full backend suite, run once after focused suites were green:

```text
uv run pytest -q
1867 tests collected; 100%; exit 0; no failures
```

Static verification:

```text
uv run ruff check app/providers/voice_clone/cosyvoice.py app/services/cosyvoice_recovery.py app/api/v1/routes/brand_voices.py tests/test_cosyvoice_voice_clone_provider.py tests/test_brand_voice_pipeline.py
All checks passed!

uv run python -m compileall -q app/providers/voice_clone/cosyvoice.py app/services/cosyvoice_recovery.py app/api/v1/routes/brand_voices.py
No output; exit 0.

git diff --check
No output; exit 0.
```

### Files changed

- `backend/app/services/cosyvoice_recovery.py`
- `backend/app/providers/voice_clone/cosyvoice.py`
- `backend/app/api/v1/routes/brand_voices.py`
- `backend/tests/test_cosyvoice_voice_clone_provider.py`
- `backend/tests/test_brand_voice_pipeline.py`
- `.superpowers/sdd/2026-08-29-pricing-closure-implementation/task-12-report.md`

### Fix-round self-review and concerns

- Confirmed the collision decision is based on complete durable request hashes and exact nine-character markers, not probability, old prefixes, process locks, or candidate count. The SQLite route regression proves one foreign candidate prevents a second supplier create.
- Confirmed committed-operation ordering plus the marker advisory lock handles true concurrency: if B commits while A owns the marker lock, B waits and then sees A; if B is already committed before A's locked snapshot, A sees B. At most one different full request can enter the supplier call, and commonly both fail closed.
- Confirmed a provider call with an external request key but no durable operation identity now fails before inventory or create. The route regression confirms the committed operation ID is passed through.
- Confirmed same-full-request remote success plus lost response, exact-key adapter replay, route full-hash replay/late-race rollback, strict provider result validation, deletion/finalization ordering, zero-price settlement, and parent-video billing remain green.
- Confirmed the one-shot pinned SDK transport path is unchanged and the SDK's non-idempotent create retry remains bypassed.
- No PostgreSQL service is available in the local focused environment. Executable local tests cover the exact collision decision, durable operation/hash verification, SQLite route behavior, stable signed advisory key, and emitted `pg_advisory_xact_lock` statement without skips. A real PostgreSQL two-process concurrency execution is explicitly left to Task 18 as directed.
- The global collision scan selects only request hashes but is linear in historical CosyVoice-create operations because no marker column/index exists. This favors correctness and avoids an unapproved migration; volume-driven indexing can be evaluated separately.
- Existing Starlette/httpx and Alembic path-separator warnings remain non-blocking. No subagent, network/provider call, push, merge, deploy, migration, production/shared database write, provider-ID registration, configuration write, production routing change, Doubao routing change, Task 13 work, or external side effect was performed.
