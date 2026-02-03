import pytest

from app.core.result import OrchestratorResult
from app.core.tasks import InvalidPayloadError, get_task_registry


def test_task_upper() -> None:
    task = get_task_registry()["upper"]
    result = task.handler("hello")
    assert isinstance(result, OrchestratorResult)
    assert result.text == "HELLO"


def test_task_echo() -> None:
    task = get_task_registry()["echo"]
    result = task.handler("payload")
    assert isinstance(result, OrchestratorResult)
    assert result.text == "payload"


def test_task_json_pretty_valid() -> None:
    task = get_task_registry()["json_pretty"]
    result = task.handler('{"b": 1, "a": 2}')
    assert isinstance(result, OrchestratorResult)
    assert result.text == '{\n  "a": 2,\n  "b": 1\n}'


def test_task_json_pretty_invalid() -> None:
    task = get_task_registry()["json_pretty"]
    with pytest.raises(InvalidPayloadError):
        task.handler("not-json")
