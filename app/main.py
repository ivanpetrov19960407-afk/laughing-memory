from __future__ import annotations

import asyncio
import logging
import sys
import time
import warnings
from collections import defaultdict, deque
from datetime import datetime, timezone

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.warnings import PTBUserWarning

from app.bot import actions, handlers, wizard
from app.core import calendar_store
from app.core.orchestrator import Orchestrator, load_orchestrator_config
from app.core.reminders import ReminderScheduler
from app.core.dialog_memory import DialogMemory
from app.core.memory_manager import MemoryManager, UserActionsLog, UserProfileMemory
from app.core.digest_scheduler import start_digest_scheduler, stop_digest_scheduler
from app.infra.access import AccessController
from app.infra.allowlist import AllowlistStore, extract_allowed_user_ids
from app.infra.actions_log_store import ActionsLogStore
from app.infra.config import StartupFeatures, load_settings, resolve_env_label, validate_startup_env
from app.infra.observability import load_observability_config
from app.infra.observability.http_server import start_observability_http
from app.infra.observability.metrics import MetricsCollector
from app.infra.observability.watchdog import run_watchdog_loop
from app.infra.user_profile_store import UserProfileStore
from app.infra.request_context import RequestContext, log_event
from app.infra.version import resolve_app_version
from app.infra.llm import OpenAIClient, PerplexityClient
from app.infra.rate_limit import RateLimiter as LLMRateLimiter
from app.infra.rate_limiter import RateLimiter
from app.infra.document_session_store import DocumentSessionStore
from app.infra.resilience import (
    CircuitBreakerRegistry,
    load_circuit_breaker_config,
    load_retry_policy,
    load_timeouts,
)
from app.infra.storage import TaskStorage
from app.infra.last_state_store import LastStateStore
from app.infra.trace_store import TraceStore
from app.infra.draft_store import DraftStore
from app.tools import NullSearchClient, PerplexityWebSearchClient
from app.storage.wizard_store import WizardStore


def _register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("ping", handlers.ping))
    application.add_handler(CommandHandler("tasks", handlers.tasks))
    application.add_handler(CommandHandler("task", handlers.task))
    application.add_handler(CommandHandler("last", handlers.last))
    application.add_handler(CommandHandler("ask", handlers.ask))
    application.add_handler(CommandHandler("summary", handlers.summary))
    application.add_handler(CommandHandler("search", handlers.search))
    application.add_handler(CommandHandler("search_sources", handlers.search_sources))
    application.add_handler(CommandHandler("trace", handlers.trace_command))
    application.add_handler(CommandHandler("facts_on", handlers.facts_on))
    application.add_handler(CommandHandler("facts_off", handlers.facts_off))
    application.add_handler(CommandHandler("digest_on", handlers.digest_on))
    application.add_handler(CommandHandler("digest_off", handlers.digest_off))
    application.add_handler(CommandHandler("context_on", handlers.context_on))
    application.add_handler(CommandHandler("context_off", handlers.context_off))
    application.add_handler(CommandHandler("context_clear", handlers.context_clear))
    application.add_handler(CommandHandler("context_status", handlers.context_status))
    application.add_handler(CommandHandler("memory_status", handlers.memory_status))
    application.add_handler(CommandHandler("memory_clear", handlers.memory_clear))
    application.add_handler(CommandHandler("memory", handlers.memory_command))
    application.add_handler(CommandHandler("profile", handlers.profile_command))
    application.add_handler(CommandHandler("profile_set", handlers.profile_set_command))
    application.add_handler(CommandHandler("remember", handlers.remember_command))
    application.add_handler(CommandHandler("forget", handlers.forget_command))
    application.add_handler(CommandHandler("history", handlers.history_command))
    application.add_handler(CommandHandler("history_find", handlers.history_search_command))
    application.add_handler(CommandHandler("allow", handlers.allow))
    application.add_handler(CommandHandler("deny", handlers.deny))
    application.add_handler(CommandHandler("allowlist", handlers.allowlist))
    application.add_handler(CommandHandler("menu", handlers.menu_command))
    application.add_handler(CommandHandler("cancel", handlers.cancel_command))
    application.add_handler(CommandHandler("image", handlers.image))
    application.add_handler(CommandHandler("check", handlers.check))
    application.add_handler(CommandHandler("rewrite", handlers.rewrite))
    application.add_handler(CommandHandler("explain", handlers.explain))
    application.add_handler(CommandHandler("calc", handlers.calc))
    application.add_handler(CommandHandler("calendar", handlers.calendar))
    application.add_handler(CommandHandler("caldav", handlers.caldav_settings))
    application.add_handler(CommandHandler("reminders", handlers.reminders))
    application.add_handler(CommandHandler("reminder_off", handlers.reminder_off))
    application.add_handler(CommandHandler("reminder_on", handlers.reminder_on))
    application.add_handler(CommandHandler("selfcheck", handlers.selfcheck))
    application.add_handler(CommandHandler("health", handlers.health))
    application.add_handler(CommandHandler("config", handlers.config_command))
    application.add_handler(CallbackQueryHandler(handlers.static_callback, pattern="^cb:"))
    application.add_handler(CallbackQueryHandler(handlers.action_callback))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handlers.document_upload))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.chat))
    application.add_handler(MessageHandler(filters.COMMAND, handlers.unknown_command))


def _build_startup_integrations(features: StartupFeatures) -> dict[str, bool]:
    base = {
        "caldav": features.caldav_enabled,
        "llm": features.llm_enabled,
    }
    return {key: value for key, value in base.items() if value}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",

    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("httpx").disabled = True
    logging.getLogger("httpcore").disabled = True
    logging.getLogger("httpx").propagate = False
    logging.getLogger("httpcore").propagate = False





    env_label = resolve_env_label()
    try:
        settings = load_settings()
    except RuntimeError as exc:
        logging.getLogger(__name__).exception("Startup failed: %s", exc)
        raise SystemExit(str(exc)) from exc
    startup_features = validate_startup_env(
        settings,
        env_label=env_label,
        logger=logging.getLogger(__name__),
    )
    config = load_orchestrator_config(settings.orchestrator_config_path)
    timeouts = load_timeouts(config)
    retry_policy = load_retry_policy(config)
    circuit_breakers = CircuitBreakerRegistry(config=load_circuit_breaker_config(config))
    if settings.facts_only_default is not None:
        config["facts_only_default"] = settings.facts_only_default
    storage = TaskStorage(settings.db_path)
    llm_client = None
    openai_client = None
    search_client = NullSearchClient()
    perplexity_client = None
    if settings.openai_api_key:
        openai_client = OpenAIClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=timeouts.llm_seconds,
            max_retries=0,
        )
        llm_client = openai_client
    elif settings.perplexity_api_key:
        perplexity_client = PerplexityClient(
            api_key=settings.perplexity_api_key,
            base_url=settings.perplexity_base_url,
            timeout_seconds=timeouts.llm_seconds,
            max_retries=0,
        )
        llm_client = perplexity_client
    if settings.perplexity_api_key and perplexity_client is not None:
        search_client = PerplexityWebSearchClient(
            perplexity_client,
            model=settings.perplexity_model,
            timeout_seconds=timeouts.external_api_seconds,
        )
    config_allowlist_ids = extract_allowed_user_ids(config)
    initial_allowlist_ids = settings.allowed_user_ids or config_allowlist_ids
    allowlist_store = AllowlistStore(
        path=settings.allowlist_path,
        initial_user_ids=initial_allowlist_ids,
    )
    asyncio.run(allowlist_store.load())
    admin_user_ids = settings.admin_user_ids or settings.allowed_user_ids or config_allowlist_ids
    access = AccessController(allowlist=allowlist_store, admin_user_ids=admin_user_ids)

    rate_limits = config.get("rate_limits", {}).get("llm", {})
    per_minute = settings.llm_per_minute
    if per_minute is None:
        per_minute = rate_limits.get("per_minute")
    per_day = settings.llm_per_day
    if per_day is None:
        per_day = rate_limits.get("per_day")
    rate_limiter = LLMRateLimiter(per_minute=per_minute, per_day=per_day)

    import app.infra.search_sources_store as search_sources_store
    orchestrator = Orchestrator(
        config=config,
        storage=storage,
        llm_client=llm_client,
        access=access,
        rate_limiter=rate_limiter,
        llm_history_turns=settings.llm_history_turns,
        llm_model=settings.openai_model if openai_client else settings.perplexity_model,
        search_client=search_client,
        feature_web_search=settings.feature_web_search,
        timeouts=timeouts,
        retry_policy=retry_policy,
        circuit_breakers=circuit_breakers,
        search_sources_store=search_sources_store,
    )
    dialog_memory = DialogMemory(
        settings.dialog_memory_path,
        max_turns=settings.context_max_turns,
    )
    asyncio.run(dialog_memory.load())
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    settings.document_texts_path.mkdir(parents=True, exist_ok=True)
    document_store = DocumentSessionStore(settings.document_sessions_path)
    document_store.load()
    profile_store = UserProfileStore(settings.db_path)
    actions_log_store = ActionsLogStore(settings.db_path)
    memory_manager = MemoryManager(
        dialog=dialog_memory,
        profile=UserProfileMemory(profile_store),
        actions=UserActionsLog(actions_log_store),
    )

    warnings.filterwarnings("ignore", message="No JobQueue set up", category=PTBUserWarning)
    application = Application.builder().token(settings.bot_token).build()
    reminder_scheduler = ReminderScheduler(
        application=application,
        max_future_days=settings.reminder_max_future_days,
    )
    application.bot_data["reminder_scheduler"] = reminder_scheduler
    application.bot_data["orchestrator"] = orchestrator
    application.bot_data["storage"] = storage
    application.bot_data["allowlist_store"] = allowlist_store
    application.bot_data["admin_user_ids"] = admin_user_ids
    application.bot_data["rate_limiter"] = RateLimiter(
        per_minute=settings.rate_limit_per_minute,
        per_day=settings.rate_limit_per_day,
    )
    application.bot_data["ui_rate_limiter"] = RateLimiter(
        per_minute=max(20, settings.rate_limit_per_minute * 3),
        per_day=max(200, settings.rate_limit_per_day * 3),
    )
    application.bot_data["history"] = defaultdict(lambda: deque(maxlen=settings.history_size))
    application.bot_data["history_size"] = settings.history_size
    application.bot_data["message_limit"] = settings.telegram_message_limit
    application.bot_data["settings"] = settings
    application.bot_data["resilience_timeouts"] = timeouts
    application.bot_data["resilience_retry_policy"] = retry_policy
    application.bot_data["circuit_breakers"] = circuit_breakers
    application.bot_data["openai_client"] = openai_client
    application.bot_data["llm_client"] = llm_client
    application.bot_data["start_time"] = time.monotonic()
    application.bot_data["dialog_memory"] = dialog_memory
    application.bot_data["profile_store"] = profile_store
    application.bot_data["actions_log_store"] = actions_log_store
    application.bot_data["memory_manager"] = memory_manager
    application.bot_data["document_store"] = document_store
    application.bot_data["last_state_store"] = LastStateStore(ttl_seconds=7 * 24 * 3600)
    application.bot_data["action_store"] = actions.ActionStore(
        ttl_seconds=settings.action_ttl_seconds,
        max_items=settings.action_max_size,
    )
    application.bot_data["draft_store"] = DraftStore(max_items=50, ttl_seconds=24 * 3600)
    application.bot_data["trace_store"] = TraceStore(max_items=20, ttl_seconds=86400)
    obs_config = load_observability_config()
    metrics_collector = MetricsCollector() if obs_config.obs_http_enabled else None
    obs_state = None
    if obs_config.obs_http_enabled:
        obs_state = {
            "init_complete": False,
            "start_time": time.monotonic(),
            "version": resolve_app_version(config.get("system_metadata", {})),
            "last_error_count": 0,
            "critical_error_count_last_n_minutes": 0,
            "metrics_collector": metrics_collector,
        }
    application.bot_data["obs_config"] = obs_config
    application.bot_data["obs_state"] = obs_state
    application.bot_data["obs_runner"] = None
    wizard_store = WizardStore(
        settings.wizard_store_path,
        timeout_seconds=settings.wizard_timeout_seconds,
    )
    application.bot_data["wizard_manager"] = wizard.WizardManager(
        wizard_store,
        reminder_scheduler=reminder_scheduler,
        settings=settings,
        profile_store=profile_store,
        memory_manager=memory_manager,
    )
    application.bot_data["search_sources_store"] = search_sources_store
    startup_context = RequestContext(
        correlation_id="startup",
        user_id=0,
        chat_id=0,
        message_id=0,
        timezone=None,
        ts=datetime.now(timezone.utc),
        env=env_label,
    )
    log_event(
        logging.getLogger(__name__),
        startup_context,
        component="startup",
        event="startup.check",
        status="ok",
        python_version=sys.version.split()[0],
        app_version=resolve_app_version(config.get("system_metadata", {})),
        timezone=calendar_store.BOT_TZ.key,
        integrations=_build_startup_integrations(startup_features),
    )
    if not application.job_queue:
        logging.getLogger(__name__).warning("JobQueue not configured; reminders will run without it.")

    async def _restore_reminders(app: Application) -> None:
        if not settings.reminders_enabled:
            logging.getLogger(__name__).info("Reminders disabled by config")
        else:
            await reminder_scheduler.restore_all()
        obs_cfg = app.bot_data.get("obs_config")
        obs_state = app.bot_data.get("obs_state")
        if obs_cfg and obs_cfg.obs_http_enabled and obs_state is not None:
            runner, _site = await start_observability_http(
                obs_cfg.obs_http_host,
                obs_cfg.obs_http_port,
                obs_state,
            )
            app.bot_data["obs_runner"] = runner
            obs_state["init_complete"] = True
            if obs_cfg.systemd_watchdog_enabled:
                asyncio.create_task(run_watchdog_loop(enabled=True, env=None))

    async def _post_init(app: Application) -> None:
        await _restore_reminders(app)
        if app.bot_data.get("digest_scheduler") is None:
            app.bot_data["digest_scheduler"] = start_digest_scheduler(app)

    async def _post_shutdown(app: Application) -> None:
        scheduler = app.bot_data.pop("digest_scheduler", None)
        stop_digest_scheduler(scheduler)

    application.post_init = _post_init
    application.post_shutdown = _post_shutdown

    _register_handlers(application)
    application.add_error_handler(handlers.error_handler)

    logging.getLogger(__name__).info("Bot started")
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    application.run_polling()


if __name__ == "__main__":
    main()
