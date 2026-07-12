# Billing and VIP operations

Run these commands from `backend/` on the production host. Every command requires an
existing `pg_dump` backup. Commands are dry-run by default; add `--apply` only after
reviewing the JSON summary.

Reset billing counters and grant the target account Huading admin access:

```powershell
uv run python -m scripts.ops.reset_billing_and_admin `
  --backup C:\backups\huading.dump `
  --tenant-slug huading-ai
```

Assign a purchased Doubao speaker slot to one Huading tenant:

```powershell
uv run python -m scripts.ops.reset_billing_and_admin assign-speaker-slot `
  --backup C:\backups\huading.dump `
  --tenant-slug customer-slug `
  --speaker-id S_xxx
```

The assignment is idempotent. It creates or updates only that tenant's
`doubao-voice-clone` `ProviderConfig`; the platform slot pool is not modified. Repeat
the reviewed command with `--apply` to commit it.
