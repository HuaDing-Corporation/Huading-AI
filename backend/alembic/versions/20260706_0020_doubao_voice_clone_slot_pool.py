"""Keep Doubao voice clone speaker slots env-driven."""

from __future__ import annotations

revision = "20260706_0020"
down_revision = "20260706_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Slot ids are intentionally not seeded.

    Runtime allocation reads provider_configs.config.speaker_ids when an operator
    explicitly configures DB slots; otherwise it falls back to
    ENGINE_DOUBAO_VOICE_CLONE_SPEAKER_IDS. Writing the example value here would
    mask the deployment env and pin every tenant to a sample slot.
    """


def downgrade() -> None:
    """No-op; upgrade does not mutate persisted slot configuration."""
