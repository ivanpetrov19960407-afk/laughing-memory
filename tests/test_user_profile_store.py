from __future__ import annotations

from app.core.user_profile import DEFAULT_TIMEZONE
from app.infra.user_profile_store import UserProfileStore


def test_profile_store_defaults_and_update(tmp_path) -> None:
    store = UserProfileStore(tmp_path / "profiles.db")

    profile = store.get(1)
    assert profile.user_id == 1
    assert profile.language == "ru"
    assert profile.timezone == DEFAULT_TIMEZONE
    assert profile.default_reminders.offset_minutes is None

    updated = store.update(
        1,
        {
            "language": "en",
            "timezone": "Europe/London",
            "verbosity": "short",
            "facts_mode_default": True,
            "default_reminders": {"enabled": False, "offset_minutes": 30},
        },
    )
    assert updated.language == "en"
    assert updated.timezone == "Europe/London"
    assert updated.verbosity == "short"
    assert updated.facts_mode_default is True
    assert updated.default_reminders.enabled is False
    assert updated.default_reminders.offset_minutes == 30


def test_profile_store_notes(tmp_path) -> None:
    store = UserProfileStore(tmp_path / "profiles.db")

    profile = store.add_note(2, "Любит короткие ответы")
    assert profile.notes

    note_id = profile.notes[0].id
    updated, removed = store.remove_note(2, note_id)
    assert removed is True
    assert not updated.notes


def test_profile_store_migration_fills_defaults(tmp_path) -> None:
    store = UserProfileStore(tmp_path / "profiles.db")
    store._connection.execute(
        "INSERT INTO user_profiles (user_id, schema_version, payload, updated_at) VALUES (?, ?, ?, ?)",
        (5, 0, "{}", "2024-01-01T00:00:00+00:00"),
    )
    store._connection.commit()

    profile = store.get(5)
    assert profile.timezone == DEFAULT_TIMEZONE


def test_profile_store_corrupted_json_returns_safe_defaults(tmp_path) -> None:
    """Повреждённый JSON в payload не ломает get(); применяются defaults и миграция."""
    store = UserProfileStore(tmp_path / "profiles.db")
    store._connection.execute(
        "INSERT INTO user_profiles (user_id, schema_version, payload, updated_at) VALUES (?, ?, ?, ?)",
        (99, 2, "{ invalid json ]", "2024-01-01T00:00:00+00:00"),
    )
    store._connection.commit()

    profile = store.get(99)
    assert profile.user_id == 99
    assert profile.timezone == DEFAULT_TIMEZONE
    assert profile.language in ("ru", "en")
