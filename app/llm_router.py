from __future__ import annotations

from app.config_loader import LLMProfiles
from app.llm_client import LLMClient


class LLMRouter:
    def __init__(self, profiles: LLMProfiles) -> None:
        self.general = LLMClient(profiles.general)
        self.summarizer = LLMClient(profiles.summarizer)
        self.verifier = LLMClient(profiles.verifier)

    async def close(self) -> None:
        await self.general.close()
        await self.summarizer.close()
        await self.verifier.close()

