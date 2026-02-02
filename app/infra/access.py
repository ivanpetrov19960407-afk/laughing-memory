from __future__ import annotations

from collections.abc import Set

from app.infra.allowlist import AllowlistStore


class AccessController:
    def __init__(
        self,
        allowlist: AllowlistStore | Set[int] | None,
        admin_user_ids: set[int] | None = None,
    ) -> None:
        self._allowlist = allowlist
        self._admin_user_ids = admin_user_ids or set()

    def is_allowed(self, user_id: int) -> bool:
        if user_id in self._admin_user_ids:
            return True
        if self._allowlist is None:
            return True
        if isinstance(self._allowlist, AllowlistStore):
            return self._allowlist.is_allowed(user_id)
        return user_id in self._allowlist

    def is_restricted(self) -> bool:
        return self._allowlist is not None
