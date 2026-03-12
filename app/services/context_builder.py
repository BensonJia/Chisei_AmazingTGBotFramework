from __future__ import annotations

from collections import deque

from app.config_loader import AppConfig
from app.storage import SQLiteStore


class ContextBuilder:
    def __init__(self, store: SQLiteStore, config: AppConfig) -> None:
        self.store = store
        self.config = config

    def build_relation_context(self, session_key: str, start_user_id: str) -> list[dict[str, str]]:
        max_depth = self.config.bot.max_relation_depth
        seen = {start_user_id}
        q: deque[tuple[str, int]] = deque([(start_user_id, 0)])
        edges: list[dict[str, str]] = []

        while q:
            node, depth = q.popleft()
            if depth >= max_depth:
                continue
            neighbors = self.store.list_neighbors(session_key, node)
            if not neighbors:
                continue
            edge_rows = self.store.list_roles_edges_by_sources(session_key, [node])
            for edge in edge_rows:
                edges.append(
                    {
                        "src_id": edge["src_id"],
                        "relation": edge["relation"],
                        "dst_id": edge["dst_id"],
                    }
                )
                dst = edge["dst_id"]
                if dst not in seen:
                    seen.add(dst)
                    q.append((dst, depth + 1))
        return edges

    def build_time_context(self, session_key: str) -> list[dict[str, str]]:
        events = self.store.list_time_logic_events(session_key, self.config.bot.max_events_context)
        return [
            {
                "event_time": e["event_time"],
                "actor_a_id": e["actor_a_id"],
                "actor_b_id": e["actor_b_id"],
                "event": e["event_text"],
            }
            for e in events
        ]

