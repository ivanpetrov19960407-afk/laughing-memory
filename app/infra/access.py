from __future__ import annotations


class AccessController:
    def __init__(self, allowed_user_ids: set[int] | None) -> None:
        self._allowed_user_ids = allowed_user_ids

    def is_allowed(self, user_id: int) -> bool:
        if self._allowed_user_ids is None:
            return True
        return user_id in self._allowed_user_ids

    def is_restricted(self) -> bool:
        return self._allowed_user_ids is not None
