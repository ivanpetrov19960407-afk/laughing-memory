from __future__ import annotations

from app.core.orchestrator import Orchestrator
from app.core.result import OrchestratorResult, ensure_valid, error, ok, refused
from app.infra.request_context import RequestContext

BASE_SYSTEM_PROMPT = (
    "Ты аккуратный помощник. Не выдумывай, не добавляй источники без наличия, "
    "отмечай предположения. Если информации недостаточно, задавай вопросы. "
    "Если источники не были переданы инструментом sources, не используй ссылки, "
    "цитаты, номера источников ([1]) или фразы типа «согласно»/«по данным»."
)


async def llm_check(text: str, ctx: dict[str, object]) -> OrchestratorResult:
    system_prompt = (
        f"{BASE_SYSTEM_PROMPT}\n"
        "Задача: провести проверку текста. "
        "Ответ строго структурирован:\n"
        "1) Проблема/дыра\n"
        "2) Почему это проблема\n"
        "3) Как улучшить (конкретно)\n"
        "Не добавляй факты, которых нет в тексте."
    )
    return await _run_llm_tool(text, ctx, intent="utility_check", system_prompt=system_prompt)


async def llm_rewrite(mode: str, text: str, ctx: dict[str, object]) -> OrchestratorResult:
    mode = mode.strip().lower()
    system_prompt = (
        f"{BASE_SYSTEM_PROMPT}\n"
        "Задача: переписать текст согласно режиму.\n"
        "Режим simple: проще и яснее.\n"
        "Режим hard: прямой тон без оскорблений/ненависти.\n"
        "Режим short: сжать до ~800-1200 символов или 8-10 строк.\n"
        "Не добавляй новые факты."
    )
    prompt = f"Режим: {mode}\nТекст:\n{text}"
    return await _run_llm_tool(prompt, ctx, intent="utility_rewrite", system_prompt=system_prompt)


async def llm_explain(text: str, ctx: dict[str, object]) -> OrchestratorResult:
    system_prompt = (
        f"{BASE_SYSTEM_PROMPT}\n"
        "Задача: объяснить текст. Ответ:\n"
        "- простое объяснение (до 10 предложений)\n"
        "- 1 пример\n"
        "- 3 пункта «итог»\n"
        "Не добавляй новые факты."
    )
    return await _run_llm_tool(text, ctx, intent="utility_explain", system_prompt=system_prompt)


async def _run_llm_tool(
    prompt: str,
    ctx: dict[str, object],
    *,
    intent: str,
    system_prompt: str,
) -> OrchestratorResult:
    orchestrator = ctx.get("orchestrator")
    user_id = ctx.get("user_id")
    request_context = ctx.get("request_context")
    if not isinstance(orchestrator, Orchestrator) or not isinstance(user_id, int):
        return ensure_valid(
            error(
                "Ошибка конфигурации LLM инструмента.",
                intent=intent,
                mode="llm",
                debug={"reason": "invalid_context"},
            )
        )
    execution = await orchestrator.ask_llm(
        user_id,
        prompt,
        mode="ask",
        system_prompt=system_prompt,
        request_context=request_context if isinstance(request_context, RequestContext) else None,
    )
    if execution.status != "success":
        if "LLM не настроен" in execution.result:
            return ensure_valid(
                refused(
                    execution.result,
                    intent=intent,
                    mode="llm",
                    debug={"task_name": execution.task_name},
                )
            )
        return ensure_valid(
            error(
                execution.result,
                intent=intent,
                mode="llm",
                debug={"task_name": execution.task_name},
            )
        )
    return ensure_valid(
        ok(
            execution.result,
            intent=intent,
            mode="llm",
            debug={"task_name": execution.task_name},
        )
    )
