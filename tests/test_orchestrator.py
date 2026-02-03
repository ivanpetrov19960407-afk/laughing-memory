from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.infra.storage import TaskStorage


def test_orchestrator_disabled_task(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    config = {"tasks": {"enabled": ["echo"]}}
    orchestrator = Orchestrator(config=config, storage=storage)

    result = orchestrator.execute_task(user_id=1, task_name="upper", payload="hello")

    assert result.status == "error"
    assert "disabled" in result.text


def test_orchestrator_records_last_execution(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    storage = TaskStorage(db_path)
    orchestrator = Orchestrator(config={}, storage=storage)

    result = orchestrator.execute_task(user_id=42, task_name="upper", payload="hello")

    record = storage.get_last_execution(42)
    assert record is not None
    assert record["task_name"] == "upper"
    assert record["status"] == ("success" if result.status == "ok" else "error")
