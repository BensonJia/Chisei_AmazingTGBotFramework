from __future__ import annotations

import json


def build_chat_messages(
    system_prompt: str,
    summary_role: str,
    summary_text: str,
    recent_messages: list[dict[str, str]],
    relation_context: list[dict[str, str]] | None = None,
    event_context: list[dict[str, str]] | None = None,
    latest_user_text: str | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if summary_text.strip():
        messages.append(
            {
                "role": summary_role,
                "content": "[HISTORY INFO]Conversation summary for long-term memory:\n" + summary_text,
            }
        )
    if relation_context:
        messages.append(
            {
                "role": "system",
                "content": "[RELATION INFO]Relation graph context:\n" + json.dumps(relation_context, ensure_ascii=False),
            }
        )
    if event_context:
        messages.append(
            {
                "role": "system",
                "content": "[BACKGROUNDS] Time logic context:\n" + json.dumps(event_context, ensure_ascii=False),
            }
        )
    messages.extend(recent_messages)
    if latest_user_text:
        messages.append({"role": "user", "content": latest_user_text})
    return messages


def verifier_prompt(scored_messages_payload: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是严格的JSON生成器。只输出JSON数组，不要输出任何解释或Markdown。"
                "固定格式为："
                '[{"message_id":<Message_id>,"confidence":<置信度>,"tone":"neutral","message":"<消息内容>"}]。'
                "字段必须且只能包含 message_id/confidence/tone/message。"
            ),
        },
        {"role": "user", "content": scored_messages_payload},
    ]


def time_logic_prompt(scored_messages_payload: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是严格的JSON生成器。只输出JSON数组，不要输出任何解释或Markdown。"
                "固定格式为："
                '[{"event_time":"<事件时间,如2026-03-13 10:00:00>","actor_a_id":"<ID_A, the user A id>","actor_b_id":"<ID_A, the user A id>","event_zh":"<信息内容>","confidence":<置信度>,"source_message_id":<ID>}]。'
                "字段必须且只能包含 event_time/actor_a_id/actor_b_id/event_zh/confidence/source_message_id。"
                "event_zh 必须使用中文表达事件。"
            ),
        },
        {"role": "user", "content": scored_messages_payload},
    ]


def roles_logic_prompt(scored_messages_payload: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是严格的JSON生成器。只输出JSON数组，不要输出任何解释或Markdown。"
                "固定格式为："
                '[{"src_id":"<用户A的ID>","relation":<用户A对与用户B的关系>","dst_id":"<用户B的ID>","confidence":<置信度>,"source_message_id":<MsgID>}]。'
                "字段必须且只能包含 src_id/relation/dst_id/confidence/source_message_id。"
            ),
        },
        {"role": "user", "content": scored_messages_payload},
    ]
