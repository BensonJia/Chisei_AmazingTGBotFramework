from __future__ import annotations

from app.config_loader import MemoryConfig
from app.llm_router import LLMRouter
from app.storage import SQLiteStore


class MemoryManager:
    def __init__(self, store: SQLiteStore, llm_router: LLMRouter, config: MemoryConfig) -> None:
        self.store = store
        self.llm_router = llm_router
        self.config = config

    def maybe_compress_sync(self, conversation_key: str) -> None:
        total = self.store.count_messages(conversation_key)
        if total <= self.config.max_messages_per_conversation:
            return

        to_summarize = max(1, total - self.config.keep_recent_messages)
        old_messages = self.store.get_oldest_messages(conversation_key, to_summarize)
        if not old_messages:
            return

        previous_summary = self.store.get_latest_summary(conversation_key)
        transcript_lines = []
        for row in old_messages:
            transcript_lines.append(
                f"[{row['created_at']}] {row['sender_name']} ({row['role']}): {row['content']}"
            )
        transcript = "\n".join(transcript_lines)
        summary_input = (
            f"Previous summary:\n{previous_summary or '(none)'}\n\n"
            f"New transcript to compress:\n{transcript}"
        )
        summary_messages = [
            {"role": "system", "content": self.config.summary_prompt},
            {"role": "user", "content": summary_input},
        ]
        summary = self.llm_router.summarizer.complete_sync(summary_messages, stream=False)
        summary = summary.strip() or previous_summary or "(empty summary)"

        start_id = int(old_messages[0]["id"])
        end_id = int(old_messages[-1]["id"])
        self.store.insert_summary(conversation_key, summary, start_id, end_id)
        self.store.delete_messages_up_to(conversation_key, end_id)

