from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from app.config_loader import AppConfig
from app.llm_router import LLMRouter
from app.prompts import roles_logic_prompt, time_logic_prompt, verifier_prompt
from app.services.logic_parser import parse_json_array
from app.storage import SQLiteStore

logger = logging.getLogger(__name__)


class TeachService:
    def __init__(self, store: SQLiteStore, llm_router: LLMRouter, config: AppConfig) -> None:
        self.store = store
        self.llm_router = llm_router
        self.config = config

    def _build_message_payload(self, rows: list[dict[str, Any]]) -> str:
        payload = [
            {
                "message_id": row["id"],
                "timestamp": row["created_at"],
                "sender_id": row["sender_id"],
                "sender_name": row["sender_name"],
                "role": row["role"],
                "message": row["content"],
            }
            for row in rows
        ]
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _to_chinese_event(self, text: str) -> str:
        if self._contains_cjk(text):
            return text
        messages = [
            {
                "role": "system",
                "content": "将输入事件改写为简体中文事件描述。只输出一句中文，不要解释。",
            },
            {"role": "user", "content": text},
        ]
        zh = self.llm_router.summarizer.complete_sync(messages, stream=False).strip()
        return zh or text

    def _normalize_verifier_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not all(k in item for k in ("message_id", "confidence", "tone", "message")):
                continue
            normalized.append(
                {
                    "message_id": int(item["message_id"]),
                    "confidence": float(item["confidence"]),
                    "tone": str(item["tone"]),
                    "message": str(item["message"]),
                }
            )
        if not normalized:
            raise ValueError("verifier output format invalid")
        return normalized

    def _normalize_time_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        required = ("event_time", "actor_a_id", "actor_b_id", "event_zh", "confidence", "source_message_id")
        for item in items:
            if not all(k in item for k in required):
                continue
            event_zh = str(item["event_zh"]).strip()
            event_zh = self._to_chinese_event(event_zh)
            normalized.append(
                {
                    "event_time": str(item["event_time"]).strip() or "unknown",
                    "actor_a_id": str(item["actor_a_id"]),
                    "actor_b_id": str(item["actor_b_id"]),
                    "event_zh": event_zh,
                    "confidence": float(item["confidence"]),
                    "source_message_id": int(item["source_message_id"]),
                }
            )
        if not normalized:
            raise ValueError("time_logic output format invalid")
        return normalized

    def _normalize_role_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
        required = ("src_id", "relation", "dst_id", "confidence", "source_message_id")
        for item in items:
            if not all(k in item for k in required):
                continue
            src_id = str(item["src_id"])
            relation = str(item["relation"])
            dst_id = str(item["dst_id"])
            edge_key = (src_id, relation, dst_id)
            candidate = {
                "src_id": src_id,
                "relation": relation,
                "dst_id": dst_id,
                "confidence": float(item["confidence"]),
                "source_message_id": int(item["source_message_id"]),
            }
            prev = dedup.get(edge_key)
            if prev is None or candidate["confidence"] >= prev["confidence"]:
                dedup[edge_key] = candidate
        normalized = list(dedup.values())
        if not normalized:
            raise ValueError("roles_logic output format invalid")
        return normalized

    def _call_parse_with_retry(
        self,
        prompt_messages: list[dict[str, str]],
        retry_max: int,
        timeout_sec: int,
    ) -> list[dict[str, Any]]:
        started = time.monotonic()
        attempts = retry_max + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            if time.monotonic() - started > timeout_sec:
                break
            try:
                raw = self.llm_router.summarizer.complete_sync(prompt_messages, stream=False)
                return parse_json_array(raw)
            except Exception as exc:  # noqa: PERF203
                last_error = exc
                continue
        if last_error:
            raise last_error
        raise TimeoutError("parse timeout exceeded")

    def run_teach(
        self,
        session_key: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        def emit(msg: str) -> None:
            logger.info("teach session=%s %s", session_key, msg)
            if progress_callback:
                progress_callback(msg)

        run_id = self.store.create_teach_run(session_key, "running", "")
        try:
            rows = self.store.get_recent_message_rows(session_key, 24)
            if not rows:
                self.store.update_teach_run(run_id, "completed", "no messages")
                return {"status": "completed", "messages": 0, "time_events": 0, "role_edges": 0}

            raw_payload = self._build_message_payload(rows)
            verifier_raw = self.llm_router.verifier.complete_sync(
                verifier_prompt(raw_payload), stream=False
            )
            verifier_items = self._normalize_verifier_items(parse_json_array(verifier_raw))
            confidence_map: dict[int, float] = {}
            tone_map: dict[int, str] = {}
            for item in verifier_items:
                try:
                    msg_id = int(item.get("message_id"))
                except Exception:
                    continue
                confidence_map[msg_id] = float(item.get("confidence", 0.5))
                tone_map[msg_id] = str(item.get("tone", "neutral"))

            scored_payload = []
            for row in rows:
                msg_id = int(row["id"])
                scored_payload.append(
                    {
                        "message_id": msg_id,
                        "timestamp": row["created_at"],
                        "sender_id": row["sender_id"],
                        "message": row["content"],
                        "tone": tone_map.get(msg_id, "neutral"),
                        "confidence": confidence_map.get(msg_id, 0.5),
                    }
                )
            scored_json = json.dumps(scored_payload, ensure_ascii=False)

            emit("timelogic_start")
            time_items = self._call_parse_with_retry(
                time_logic_prompt(scored_json),
                retry_max=self.config.memory.time_logic_parse_retry_max,
                timeout_sec=self.config.memory.time_logic_parse_timeout_sec,
            )
            time_items = self._normalize_time_items(time_items)
            for item in time_items:
                self.store.add_time_logic_event(
                    session_key=session_key,
                    event_time=item["event_time"],
                    actor_a_id=item["actor_a_id"],
                    actor_b_id=item["actor_b_id"],
                    event_text=item["event_zh"],
                    confidence=item["confidence"],
                    source_message_id=item["source_message_id"],
                )
            emit(f"timelogic_done:{len(time_items)}")

            emit("roleslogic_start")
            role_items = self._call_parse_with_retry(
                roles_logic_prompt(scored_json),
                retry_max=self.config.memory.roles_logic_parse_retry_max,
                timeout_sec=self.config.memory.roles_logic_parse_timeout_sec,
            )
            role_items = self._normalize_role_items(role_items)
            for item in role_items:
                self.store.add_roles_logic_edge(
                    session_key=session_key,
                    src_id=item["src_id"],
                    relation=item["relation"],
                    dst_id=item["dst_id"],
                    confidence=item["confidence"],
                    source_message_id=item["source_message_id"],
                )
            emit(f"roleslogic_done:{len(role_items)}")

            detail = (
                f"messages={len(rows)}, time_events={len(time_items)}, role_edges={len(role_items)}"
            )
            self.store.update_teach_run(run_id, "completed", detail)
            return {
                "status": "completed",
                "messages": len(rows),
                "time_events": len(time_items),
                "role_edges": len(role_items),
            }
        except Exception as exc:
            self.store.update_teach_run(run_id, "failed", str(exc))
            return {"status": "failed", "error": str(exc)}
