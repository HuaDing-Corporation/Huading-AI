import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core import image_aspect_ratio


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1000, 1000, "1:1"),
        (1200, 900, "4:3"),
        (1500, 1000, "3:2"),
        (1600, 900, "16:9"),
        (2100, 900, "21:9"),
        (900, 1200, "3:4"),
        (1000, 1500, "2:3"),
        (900, 1600, "9:16"),
        (1790, 1000, "16:9"),
    ],
)
def test_closest_image_aspect_ratio(width: int, height: int, expected: str) -> None:
    assert image_aspect_ratio.closest_image_aspect_ratio(width, height) == expected


def test_auto_aspect_ratio_uses_input_dimensions_or_square_fallback() -> None:
    assert image_aspect_ratio.resolve_image_aspect_ratio("auto", width=900, height=1600) == "9:16"
    assert image_aspect_ratio.resolve_image_aspect_ratio("auto") == "1:1"
    assert image_aspect_ratio.resolve_image_aspect_ratio("21:9", width=900, height=1600) == "21:9"


@pytest.mark.parametrize(
    ("aspect_ratio", "expected_size"),
    [
        ("1:1", "1024x1024"),
        ("4:3", "1536x1024"),
        ("3:2", "1536x1024"),
        ("16:9", "1536x1024"),
        ("21:9", "1536x1024"),
        ("3:4", "1024x1536"),
        ("2:3", "1024x1536"),
        ("9:16", "1024x1536"),
    ],
)
def test_openai_image_size_mapping(aspect_ratio: str, expected_size: str) -> None:
    assert image_aspect_ratio.openai_image_size(aspect_ratio) == expected_size


@pytest.mark.parametrize(
    ("size", "expected_ratio"),
    [
        ("21:9", "21:9"),
        ("1536x1024", "3:2"),
        ("1024x1536", "2:3"),
        ("unexpected", "1:1"),
    ],
)
def test_provider_size_resolves_to_observable_ratio(size: str, expected_ratio: str) -> None:
    assert image_aspect_ratio.aspect_ratio_from_provider_size(size) == expected_ratio


def test_image_aspect_ratio_migration_upgrades_and_safely_downgrades() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260710_0023_image_aspect_ratios.py"
    )
    spec = importlib.util.spec_from_file_location("image_aspect_ratio_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE video_tasks ("
                "id VARCHAR(36) PRIMARY KEY, "
                "aspect_ratio VARCHAR(8) NOT NULL DEFAULT '9:16', "
                "CONSTRAINT ck_video_tasks_aspect_ratio "
                "CHECK (aspect_ratio IN ('9:16', '16:9', '1:1')))"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO video_tasks (id, aspect_ratio) "
                "VALUES ('wide', '21:9'), ('adaptive', 'auto')"
            )
        )
        upgraded = list(
            connection.scalars(sa.text("SELECT aspect_ratio FROM video_tasks ORDER BY id"))
        )
        migration.downgrade()
        downgraded = list(
            connection.scalars(sa.text("SELECT aspect_ratio FROM video_tasks ORDER BY id"))
        )

    assert upgraded == ["auto", "21:9"]
    assert downgraded == ["1:1", "1:1"]
