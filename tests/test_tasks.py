import pytest

from app.core.tasks import InvalidPayloadError, get_task_registry


def test_task_upper() -> None:
    task = get_task_registry()["upper"]
    assert task.handler("hello") == "HELLO"


def test_task_echo() -> None:
    task = get_task_registry()["echo"]
    assert task.handler("payload") == "payload"


def test_task_json_pretty_valid() -> None:
    task = get_task_registry()["json_pretty"]
    assert task.handler('{"b": 1, "a": 2}') == '{\n  "a": 2,\n  "b": 1\n}'


def test_task_json_pretty_invalid() -> None:
    task = get_task_registry()["json_pretty"]
    with pytest.raises(InvalidPayloadError):
        task.handler("not-json")
