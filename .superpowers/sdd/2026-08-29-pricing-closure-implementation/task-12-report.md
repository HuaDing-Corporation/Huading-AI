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
