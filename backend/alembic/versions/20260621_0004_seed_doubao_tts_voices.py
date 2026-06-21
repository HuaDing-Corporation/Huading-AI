"""Seed Doubao Seed-TTS voices.

Revision ID: 20260621_0004
Revises: 20260616_0003
Create Date: 2026-06-21
"""

from alembic import op

revision = "20260621_0004"
down_revision = "20260616_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO voices (id, provider, voice_code, display_name, gender, language, is_active)
        VALUES
            (
                '00000000-0000-0000-0000-000000000205',
                'doubao-seed-tts',
                'BV001_streaming',
                'Doubao BV001',
                'female',
                'zh-CN',
                true
            ),
            (
                '00000000-0000-0000-0000-000000000206',
                'doubao-seed-tts',
                'BV002_streaming',
                'Doubao BV002',
                'male',
                'zh-CN',
                true
            )
        ON CONFLICT (provider, voice_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM voices
        WHERE provider = 'doubao-seed-tts'
          AND voice_code IN ('BV001_streaming', 'BV002_streaming')
        """
    )
