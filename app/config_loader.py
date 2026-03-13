from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class LLMConfig:
    base_url: str
    model: str
    bearer_token: str
    timeout_seconds: int = 120
    stream: bool = True


@dataclass
class LLMProfiles:
    general: LLMConfig
    summarizer: LLMConfig
    verifier: LLMConfig


@dataclass
class BotStyleConfig:
    emoji_enabled: bool
    emoji_pool: list[str]
    add_reaction: bool
    processing_reaction: str
    done_reaction: str


@dataclass
class BotConfig:
    token: str
    name: str
    default_system_prompt: str
    concurrent_updates: int
    dispatcher_max_workers: int
    max_relation_depth: int
    max_events_context: int
    progress_feedback_enabled: bool
    tg_stream_interval_sec: float
    tg_stream_retry: int
    tg_stream: bool
    reply_style: BotStyleConfig


@dataclass
class MemoryConfig:
    sqlite_path: str
    max_messages_per_conversation: int
    keep_recent_messages: int
    summary_role: str
    summary_prompt: str
    time_logic_parse_retry_max: int
    time_logic_parse_timeout_sec: int
    roles_logic_parse_retry_max: int
    roles_logic_parse_timeout_sec: int


@dataclass
class AppConfig:
    llm: LLMProfiles
    bot: BotConfig
    memory: MemoryConfig


def _resolve_secret(raw: dict, key: str, env_key: str) -> str:
    env_name = raw.get(env_key)
    if env_name:
        from_env = os.getenv(env_name, "").strip()
        if from_env:
            return from_env
    return str(raw.get(key, "")).strip()


def load_app_config(config_dir: str | Path = "config") -> AppConfig:
    base = Path(config_dir)
    service = yaml.safe_load((base / "service.yaml").read_text(encoding="utf-8"))
    bot_raw = yaml.safe_load((base / "bot.yaml").read_text(encoding="utf-8"))
    mem_raw = yaml.safe_load((base / "memory.yaml").read_text(encoding="utf-8"))

    llm_raw = service["llm"]
    bot_node = bot_raw["bot"]
    mem_node = mem_raw["memory"]

    bot_token = _resolve_secret(bot_node, "token", "token_env")

    def parse_llm(node: dict) -> LLMConfig:
        token = _resolve_secret(node, "bearer_token", "bearer_token_env")
        return LLMConfig(
            base_url=str(node["base_url"]),
            model=str(node["model"]),
            bearer_token=token,
            timeout_seconds=int(node.get("timeout_seconds", 120)),
            stream=bool(node.get("stream", True)),
        )

    if "general" in llm_raw:
        profiles = LLMProfiles(
            general=parse_llm(llm_raw["general"]),
            summarizer=parse_llm(llm_raw.get("summarizer", llm_raw["general"])),
            verifier=parse_llm(llm_raw.get("verifier", llm_raw["general"])),
        )
    else:
        # Backward compatibility for single llm profile.
        single = parse_llm(llm_raw)
        profiles = LLMProfiles(general=single, summarizer=single, verifier=single)

    bot = BotConfig(
        token=bot_token,
        name=str(bot_node["name"]),
        default_system_prompt=str(bot_node["default_system_prompt"]),
        concurrent_updates=max(1, int(bot_node.get("concurrent_updates", 8))),
        dispatcher_max_workers=max(1, int(bot_node.get("dispatcher_max_workers", 8))),
        max_relation_depth=int(bot_node.get("max_relation_depth", 2)),
        max_events_context=int(bot_node.get("max_events_context", 12)),
        progress_feedback_enabled=bool(bot_node.get("progress_feedback_enabled", True)),
        tg_stream_interval_sec=max(0.1, float(bot_node.get("tgStreamIntervalSec", 1))),
        tg_stream_retry=max(0, int(bot_node.get("tgStreamRetry", 2))),
        tg_stream=bool(bot_node.get("tgStream", True)),
        reply_style=BotStyleConfig(**bot_node["reply_style"]),
    )
    memory = MemoryConfig(
        sqlite_path=str(mem_node["sqlite_path"]),
        max_messages_per_conversation=int(
            mem_node.get(
                "max_messages_per_conversation",
                mem_node.get("max_messages_per_chat", 24),
            )
        ),
        keep_recent_messages=int(mem_node.get("keep_recent_messages", 12)),
        summary_role=str(mem_node.get("summary_role", "system")),
        summary_prompt=str(mem_node["summary_prompt"]),
        time_logic_parse_retry_max=int(mem_node.get("time_logic_parse_retry_max", 2)),
        time_logic_parse_timeout_sec=int(mem_node.get("time_logic_parse_timeout_sec", 30)),
        roles_logic_parse_retry_max=int(mem_node.get("roles_logic_parse_retry_max", 2)),
        roles_logic_parse_timeout_sec=int(mem_node.get("roles_logic_parse_timeout_sec", 30)),
    )
    return AppConfig(llm=profiles, bot=bot, memory=memory)
