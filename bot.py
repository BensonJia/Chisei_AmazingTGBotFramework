from __future__ import annotations

import argparse
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.ext import TypeHandler

from app.config_loader import load_app_config
from app.llm_router import LLMRouter
from app.orchestrator import TelegramBotService
from app.services import ContextBuilder, MemoryManager, SessionTaskDispatcher, TeachService
from app.storage import SQLiteStore
from app.telegram_adapter import TelegramAdapter


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(to_stdout: bool) -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
    if to_stdout:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram LLM Bot")
    parser.add_argument(
        "--log",
        action="store_true",
        help="Also print logs to terminal (default writes only to bot.log).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(to_stdout=args.log)

    config = load_app_config("config")
    if not config.bot.token:
        raise ValueError("Telegram bot token is empty. Set bot.token or bot.token_env.")
    if not config.llm.general.bearer_token:
        raise ValueError("General LLM bearer token is empty.")
    if not config.llm.summarizer.bearer_token:
        raise ValueError("Summarizer LLM bearer token is empty.")
    if not config.llm.verifier.bearer_token:
        raise ValueError("Verifier LLM bearer token is empty.")

    store = SQLiteStore(Path(config.memory.sqlite_path))
    llm_router = LLMRouter(config.llm)
    memory_manager = MemoryManager(store=store, llm_router=llm_router, config=config.memory)
    context_builder = ContextBuilder(store=store, config=config)
    teach_service = TeachService(store=store, llm_router=llm_router, config=config)
    dispatcher = SessionTaskDispatcher(max_workers=8)
    adapter = TelegramAdapter()
    service = TelegramBotService(
        config=config,
        adapter=adapter,
        store=store,
        llm_router=llm_router,
        memory_manager=memory_manager,
        context_builder=context_builder,
        teach_service=teach_service,
        dispatcher=dispatcher,
    )

    app = Application.builder().token(config.bot.token).build()
    app.add_handler(TypeHandler(Update, service.on_any_update_log), group=-2)
    app.add_handler(MessageHandler(filters.ALL, service.on_any_message_log), group=-1)
    app.add_handler(CommandHandler("start", service.on_start))
    app.add_handler(CommandHandler("RecordAll", service.on_record_all))
    app.add_handler(CommandHandler("teach", service.on_teach))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, service.on_message))

    async def on_shutdown(_: Application) -> None:
        await service.close()

    app.post_shutdown = on_shutdown
    app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
