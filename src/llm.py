"""Model access through Gravity's LLM endpoint only. Small prompts, typed outputs."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


class GatewayLLM:
    def __init__(self, base_url: str, token: str, model: str = "openai/gpt-6-luna"):
        self.base_url, self.token, self.model = base_url, token, model
        self._m = None

    def _model(self):
        if self._m is None:
            from langchain_openai import ChatOpenAI
            self._m = ChatOpenAI(model=self.model, base_url=self.base_url, api_key=self.token, timeout=90)
        return self._m

    async def structured(self, schema: type[BaseModel], system: str, payload: dict[str, Any]) -> BaseModel:
        res = await self._model().with_structured_output(schema).ainvoke(
            [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, default=str)}])
        return res if isinstance(res, schema) else schema.model_validate(res)
