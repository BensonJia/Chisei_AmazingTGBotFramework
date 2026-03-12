from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from telegram import Message, ReactionTypeEmoji, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes


@dataclass
class IncomingMessage:
    conversation_key: str
    chat_id: int
    chat_type: str
    sender_id: int | None
    sender_name: str
    text: str
    tg_message_id: int | None


class TelegramAdapter:
    def parse_message(self, update: Update) -> IncomingMessage | None:
        message = update.message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None:
            return None

        text = (message.text or message.caption or "").strip()
        if not text:
            return None

        chat_type = str(chat.type)
        if chat_type == "private":
            conversation_key = f"private:{user.id if user else chat.id}"
        else:
            conversation_key = f"group:{chat.id}"

        sender_name = "unknown"
        sender_id = None
        if user:
            sender_id = user.id
            sender_name = (
                user.username
                or " ".join(v for v in [user.first_name, user.last_name] if v).strip()
                or str(user.id)
            )

        return IncomingMessage(
            conversation_key=conversation_key,
            chat_id=chat.id,
            chat_type=chat_type,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            tg_message_id=message.message_id,
        )

    async def reply_text(
        self, message: Message, text: str, reply_to_message_id: int | None = None
    ) -> Message:
        return await message.reply_text(text=text, reply_to_message_id=reply_to_message_id)

    async def edit_text(self, message: Message, text: str) -> Message:
        return await message.edit_text(text=text)

    async def send_typing(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: int
    ) -> None:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    async def typing_loop(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, interval_seconds: float = 4.0
    ) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.send_typing(context, chat_id)
            await asyncio.sleep(interval_seconds)

    async def set_reaction(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> None:
        with contextlib.suppress(Exception):
            await context.bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
