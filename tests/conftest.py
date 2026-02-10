import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class DummyAppScheduler:
    """Мок APScheduler для тестов: хранит id добавленных job'ов."""

    def __init__(self) -> None:
        self.job_ids: set[str] = set()

    def add_reminder_job(self, reminder_id: str, trigger_at) -> bool:
        self.job_ids.add(f"reminder:{reminder_id}")
        return True

    def remove_reminder_job(self, reminder_id: str) -> bool:
        self.job_ids.discard(f"reminder:{reminder_id}")
        return True

    def remove_job(self, job_id: str) -> bool:
        self.job_ids.discard(job_id)
        return True

    def get_jobs_by_name(self, name: str) -> list:
        return [name] if name in self.job_ids else []
