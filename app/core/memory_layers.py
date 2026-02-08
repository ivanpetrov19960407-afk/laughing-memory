from __future__ import annotations

from app.core.memory_manager import MemoryManager
from app.core.user_profile import UserProfile
from app.infra.request_context import RequestContext


async def build_memory_layers_context(
    request_context: RequestContext | None,
    *,
    memory_manager: MemoryManager | None,
    max_chars: int = 2000,
    dialog_limit: int = 10,
) -> str | None:
    if request_context is None or memory_manager is None:
        return None
    user_id = int(request_context.user_id or 0)
    chat_id = int(request_context.chat_id or 0)
    blocks: list[str] = []
    if memory_manager.dialog is not None and user_id and chat_id:
        if await memory_manager.dialog_enabled(user_id):
            messages = await memory_manager.get_dialog(user_id, chat_id, limit=dialog_limit)
            if messages:
                blocks.append(memory_manager.dialog.format_context(messages))
    if memory_manager.profile is not None and user_id and memory_manager.profile_is_persisted(user_id):
        profile = memory_manager.get_profile(user_id)
        if profile is not None:
            profile_text = _render_profile(profile, max_chars=max_chars)
            if profile_text:
                blocks.append(profile_text)
    if not blocks:
        return None
    combined = "\n\n".join(blocks).strip()
    if len(combined) <= max_chars:
        return combined
    return combined[: max(1, max_chars - 1)].rstrip() + "…"


def _render_profile(profile: UserProfile, *, max_chars: int) -> str | None:
    facts_label = "вкл" if profile.facts_mode_default else "выкл"
    blocks = [
        "Профиль пользователя:",
        f"• язык: {profile.language}",
        f"• режим фактов: {facts_label}",
    ]
    if profile.style:
        blocks.append(f"• стиль: {profile.style}")
    rendered = "\n".join(blocks)
    if not rendered.strip():
        return None
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max(1, max_chars - 1)].rstrip() + "…"
