from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable

from telegram import Message, Update
from telegram.ext import ContextTypes

from app.config_loader import AppConfig
from app.llm_router import LLMRouter
from app.prompts import build_chat_messages
from app.services import ContextBuilder, MemoryManager, SessionTaskDispatcher, TeachService
from app.storage import MessageRow, SQLiteStore
from app.telegram_adapter import TelegramAdapter

logger = logging.getLogger(__name__)


class TelegramBotService:
    def __init__(
        self,
        config: AppConfig,
        adapter: TelegramAdapter,
        store: SQLiteStore,
        llm_router: LLMRouter,
        memory_manager: MemoryManager,
        context_builder: ContextBuilder,
        teach_service: TeachService,
        dispatcher: SessionTaskDispatcher,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.store = store
        self.llm_router = llm_router
        self.memory_manager = memory_manager
        self.context_builder = context_builder
        self.teach_service = teach_service
        self.dispatcher = dispatcher
        self._bot_username: str | None = None
        self._bot_id: int | None = None

    async def close(self) -> None:
        self.dispatcher.shutdown()
        await self.llm_router.close()
        self.store.close()

    async def on_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        await update.message.reply_text(
            f"你好，我是 {self.config.bot.name}。你可以直接给我发消息。"
        )

    async def on_any_message_log(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if message is None or update.effective_chat is None:
            return
        user = update.effective_user
        content = (message.text or message.caption or "").strip()
        if not content:
            content = f"<non-text:{type(message.effective_attachment).__name__}>"
        logger.info(
            "recv chat_type=%s chat_id=%s user_id=%s username=%s message_id=%s content=%r",
            update.effective_chat.type,
            update.effective_chat.id,
            user.id if user else None,
            user.username if user else None,
            message.message_id,
            content,
        )

    async def on_any_update_log(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(
            "update update_id=%s has_message=%s has_channel_post=%s has_edited_message=%s has_callback_query=%s",
            update.update_id,
            update.message is not None,
            update.channel_post is not None,
            update.edited_message is not None,
            update.callback_query is not None,
        )

    async def on_record_all(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None or update.message is None:
            return
        chat_type = str(update.effective_chat.type)
        if chat_type not in {"group", "supergroup"}:
            await update.message.reply_text("`/RecordAll` 仅支持群聊中使用。")
            return
        session_key = f"group:{update.effective_chat.id}"
        current = self.store.get_record_all(session_key)
        target = not current
        self.store.set_record_all(
            conversation_key=session_key,
            chat_id=update.effective_chat.id,
            chat_type=chat_type,
            enabled=target,
        )
        if target:
            await update.message.reply_text("Bot将记录全部群聊消息，如需对话请@Bot")
        else:
            await update.message.reply_text("Bot将仅在被@后记录消息并回复")

    async def on_teach(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        incoming = self.adapter.parse_message(update)
        if incoming is None or update.message is None:
            return
        session_key = incoming.conversation_key
        await update.message.reply_text("Teach任务已启动，正在分析最近24条消息。")
        logger.info("teach session=%s started", session_key)

        async def wait_and_report() -> None:
            loop = asyncio.get_running_loop()

            def on_progress(msg: str) -> None:
                def schedule_send() -> None:
                    if msg == "timelogic_start":
                        logger.info("teach session=%s TimeLogic started", session_key)
                        if self.config.bot.progress_feedback_enabled:
                            asyncio.create_task(
                                context.bot.send_message(
                                    chat_id=incoming.chat_id,
                                    text="Teach进度: TimeLogic总结启动。",
                                )
                            )
                    elif msg.startswith("timelogic_done:"):
                        count = msg.split(":", 1)[1]
                        logger.info("teach session=%s TimeLogic completed count=%s", session_key, count)
                        if self.config.bot.progress_feedback_enabled:
                            asyncio.create_task(
                                context.bot.send_message(
                                    chat_id=incoming.chat_id,
                                    text=f"Teach进度: TimeLogic总结完成，事件数={count}。",
                                )
                            )
                    elif msg == "roleslogic_start":
                        logger.info("teach session=%s RolesLogic started", session_key)
                        if self.config.bot.progress_feedback_enabled:
                            asyncio.create_task(
                                context.bot.send_message(
                                    chat_id=incoming.chat_id,
                                    text="Teach进度: RolesLogic总结启动。",
                                )
                            )
                    elif msg.startswith("roleslogic_done:"):
                        count = msg.split(":", 1)[1]
                        logger.info("teach session=%s RolesLogic completed count=%s", session_key, count)
                        if self.config.bot.progress_feedback_enabled:
                            asyncio.create_task(
                                context.bot.send_message(
                                    chat_id=incoming.chat_id,
                                    text=f"Teach进度: RolesLogic总结完成，关系数={count}。",
                                )
                            )

                loop.call_soon_threadsafe(schedule_send)

            result = await self.dispatcher.run(
                session_key,
                self.teach_service.run_teach,
                session_key,
                on_progress,
            )
            if result.get("status") == "completed":
                logger.info("teach session=%s completed result=%s", session_key, result)
                await context.bot.send_message(
                    chat_id=incoming.chat_id,
                    text=(
                        f"/teach 完成: messages={result['messages']}, "
                        f"time_events={result['time_events']}, role_edges={result['role_edges']}"
                    ),
                )
            else:
                logger.error("teach session=%s failed result=%s", session_key, result)
                await context.bot.send_message(
                    chat_id=incoming.chat_id,
                    text=f"/teach 失败: {result.get('error', 'unknown error')}",
                )

        asyncio.create_task(wait_and_report())

    def _decorate_answer(self, answer: str) -> str:
        style = self.config.bot.reply_style
        if not style.emoji_enabled or not style.emoji_pool:
            return answer
        return f"{random.choice(style.emoji_pool)} {answer}"

    async def _ensure_bot_identity(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._bot_username and self._bot_id is not None:
            return
        me = await context.bot.get_me()
        self._bot_username = (me.username or "").lower()
        self._bot_id = me.id

    def _extract_group_mention_text(self, update: Update) -> str | None:
        message = update.message
        if message is None:
            return None
        text = (message.text or "").strip()
        if not text:
            return None
        if not self._bot_username:
            return None

        mentioned = False
        for entity in message.entities or []:
            if entity.type == "mention":
                token = text[entity.offset : entity.offset + entity.length].lower()
                if token == f"@{self._bot_username}":
                    mentioned = True
                    break
            elif entity.type == "text_mention":
                if self._bot_id is not None and entity.user and entity.user.id == self._bot_id:
                    mentioned = True
                    break
        if not mentioned:
            return None

        cleaned = re.sub(rf"@{re.escape(self._bot_username)}\b", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or "请继续。"

    def _build_dialogue_payload(
        self,
        session_key: str,
        user_id: str,
        latest_user_text: str,
    ) -> tuple[list[dict[str, str]], int, int]:
        self.memory_manager.maybe_compress_sync(session_key)
        summary = self.store.get_latest_summary(session_key)
        recent_messages = self.store.get_recent_messages(
            session_key, self.config.memory.keep_recent_messages
        )
        relation_context = self.context_builder.build_relation_context(session_key, user_id)
        event_context = self.context_builder.build_time_context(session_key)
        llm_messages = build_chat_messages(
            system_prompt=self.config.bot.default_system_prompt,
            summary_role=self.config.memory.summary_role,
            summary_text=summary,
            recent_messages=recent_messages,
            relation_context=relation_context,
            event_context=event_context,
            latest_user_text=latest_user_text,
        )
        return llm_messages, len(relation_context), len(event_context)

    def _run_general_completion(self, llm_messages: list[dict[str, str]]) -> str:
        return self.llm_router.general.complete_sync(
            llm_messages, stream=self.config.llm.general.stream
        )

    def _run_general_streaming_with_callback(
        self,
        llm_messages: list[dict[str, str]],
        on_chunk: Callable[[str], None],
    ) -> str:
        parts: list[str] = []
        for chunk in self.llm_router.general.stream_sync_chunks(llm_messages):
            parts.append(chunk)
            on_chunk(chunk)
        return "".join(parts).strip()

    async def _stream_general_completion(
        self,
        session_key: str,
        llm_messages: list[dict[str, str]],
        update_partial: Callable[[str], Awaitable[None]],
        flush_interval_sec: float = 0.8,
        min_flush_chars: int = 24,
    ) -> str:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_chunk(chunk: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, chunk)

        producer_task = asyncio.create_task(
            self.dispatcher.run(
                session_key,
                self._run_general_streaming_with_callback,
                llm_messages,
                on_chunk,
            )
        )

        parts: list[str] = []
        last_text_len = 0
        last_flush_ts = time.monotonic()
        try:
            while True:
                if producer_task.done() and queue.empty():
                    break
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if chunk is None:
                    continue
                parts.append(chunk)
                current = "".join(parts)
                now = time.monotonic()
                if (len(current) - last_text_len) >= min_flush_chars or (
                    now - last_flush_ts
                ) >= flush_interval_sec:
                    await update_partial(current)
                    last_text_len = len(current)
                    last_flush_ts = now
            final_text = await producer_task
            if final_text and len(final_text) != last_text_len:
                await update_partial(final_text)
            return final_text
        except Exception:
            producer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer_task
            raise

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        incoming = self.adapter.parse_message(update)
        if incoming is None or update.message is None:
            return

        await self._ensure_bot_identity(context)
        if incoming.chat_type in {"group", "supergroup"}:
            original_text = incoming.text
            mention_text = self._extract_group_mention_text(update)
            if mention_text is None:
                if self.store.get_record_all(incoming.conversation_key):
                    self.store.append_message(
                        MessageRow(
                            conversation_key=incoming.conversation_key,
                            chat_id=incoming.chat_id,
                            chat_type=incoming.chat_type,
                            sender_id=incoming.sender_id,
                            sender_name=incoming.sender_name,
                            sender_is_bot=False,
                            role="user",
                            content=original_text,
                            tg_message_id=incoming.tg_message_id,
                        )
                    )
                    asyncio.create_task(
                        self.dispatcher.run(
                            incoming.conversation_key,
                            self.memory_manager.maybe_compress_sync,
                            incoming.conversation_key,
                        )
                    )
                return
            incoming.text = mention_text

        style = self.config.bot.reply_style
        if style.add_reaction and incoming.tg_message_id is not None:
            await self.adapter.set_reaction(
                context,
                incoming.chat_id,
                incoming.tg_message_id,
                style.processing_reaction,
            )

        self.store.append_message(
            MessageRow(
                conversation_key=incoming.conversation_key,
                chat_id=incoming.chat_id,
                chat_type=incoming.chat_type,
                sender_id=incoming.sender_id,
                sender_name=incoming.sender_name,
                sender_is_bot=False,
                role="user",
                content=incoming.text,
                tg_message_id=incoming.tg_message_id,
            )
        )

        typing_task = asyncio.create_task(
            self.adapter.typing_loop(context, incoming.chat_id, interval_seconds=4.0)
        )
        stream_message: Message | None = None
        try:
            llm_messages, relation_count, event_count = await self.dispatcher.run(
                incoming.conversation_key,
                self._build_dialogue_payload,
                incoming.conversation_key,
                str(incoming.sender_id or "unknown"),
                incoming.text,
            )
            logger.info(
                "dialogue session=%s context_ready relation_count=%s event_count=%s",
                incoming.conversation_key,
                relation_count,
                event_count,
            )
            if self.config.bot.progress_feedback_enabled:
                await self.adapter.reply_text(
                    update.message,
                    f"背景已加载完成（关系{relation_count}条，事件{event_count}条），正在生成回复...",
                    reply_to_message_id=incoming.tg_message_id,
                )
            stream_message = await self.adapter.reply_text(
                update.message,
                "正在生成回复...",
                reply_to_message_id=incoming.tg_message_id,
            )

            async def update_partial(text: str) -> None:
                content = text.strip() or "正在生成回复..."
                with contextlib.suppress(Exception):
                    await self.adapter.edit_text(stream_message, content)

            answer = await self._stream_general_completion(
                session_key=incoming.conversation_key,
                llm_messages=llm_messages,
                update_partial=update_partial,
            )
        except Exception:
            logger.exception("Dialogue task failed")
            await self.adapter.reply_text(update.message, "LLM 服务请求失败，请稍后重试。")
            return
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

        final_answer = self._decorate_answer(answer.strip() or "我现在没有整理出有效答案。")
        if stream_message is not None:
            sent = stream_message
            with contextlib.suppress(Exception):
                sent = await self.adapter.edit_text(stream_message, final_answer)
        else:
            sent = await self.adapter.reply_text(
                update.message,
                final_answer,
                reply_to_message_id=incoming.tg_message_id,
            )
        self.store.append_message(
            MessageRow(
                conversation_key=incoming.conversation_key,
                chat_id=incoming.chat_id,
                chat_type=incoming.chat_type,
                sender_id=sent.from_user.id if sent.from_user else None,
                sender_name=sent.from_user.username if sent.from_user else self.config.bot.name,
                sender_is_bot=True,
                role="assistant",
                content=final_answer,
                tg_message_id=sent.message_id,
            )
        )
        asyncio.create_task(
            self.dispatcher.run(
                incoming.conversation_key,
                self.memory_manager.maybe_compress_sync,
                incoming.conversation_key,
            )
        )

        if style.add_reaction and incoming.tg_message_id is not None:
            await self.adapter.set_reaction(
                context,
                incoming.chat_id,
                incoming.tg_message_id,
                style.done_reaction,
            )
