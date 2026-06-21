"""Replace Seed-TTS V1 voices with Seed-TTS 2.0 voices.

Revision ID: 20260621_0005
Revises: 20260621_0004
Create Date: 2026-06-21
"""

from alembic import op

revision = "20260621_0005"
down_revision = "20260621_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM voices
        WHERE provider = 'doubao-seed-tts'
          AND voice_code IN ('BV001_streaming', 'BV002_streaming')
        """
    )
    op.execute(
        """
        INSERT INTO voices (id, provider, voice_code, display_name, gender, language, is_active)
        VALUES
            (
                '00000000-0000-0000-0000-000000000207',
                'doubao-seed-tts',
                'zh_male_m191_uranus_bigtts',
                'Doubao Yunfan',
                'male',
                'zh-CN',
                true
            ),
            (
                '00000000-0000-0000-0000-000000000208',
                'doubao-seed-tts',
                'zh_female_xiaohe_uranus_bigtts',
                'Doubao Xiaohe',
                'female',
                'zh-CN',
                true
            ),
            (
                '00000000-0000-0000-0000-000000000209',
                'doubao-seed-tts',
                'zh_male_taocheng_uranus_bigtts',
                'Doubao Taocheng',
                'male',
                'zh-CN',
                true
            ),
            (
                '00000000-0000-0000-0000-000000000210',
                'doubao-seed-tts',
                'zh_female_vv_uranus_bigtts',
                'Doubao Vivi',
                'female',
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
          AND voice_code IN (
              'zh_male_m191_uranus_bigtts',
              'zh_female_xiaohe_uranus_bigtts',
              'zh_male_taocheng_uranus_bigtts',
              'zh_female_vv_uranus_bigtts'
          )
        """
    )
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
