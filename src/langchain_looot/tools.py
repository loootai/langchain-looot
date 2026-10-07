"""LangChain tools for the looot REST API.

Five tools, one per operation: search the catalog, inspect an endpoint, run it, read a run,
read the balance. Search, inspect, get run and balance are free. Run spends prepaid credit.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Literal, Optional, Tuple, Type

from langchain_core.callbacks import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool, ToolException
from pydantic import BaseModel, ConfigDict, Field

from ._client import LoootClient, LoootError, path_segment

MAX_OUTPUT_CHARS = 20_000


def _render(data: Any) -> str:
    """Compact JSON, cut at MAX_OUTPUT_CHARS so one answer cannot fill the model's context."""
    text = json.dumps(data, separators=(",", ":"), default=str)
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f'... [truncated, {len(text) - MAX_OUTPUT_CHARS} more characters]'
    return text


class _LoootTool(BaseTool):
    """Shared plumbing. Subclasses define `_call` (the HTTP request) once; sync and async reuse it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: LoootClient = Field(exclude=True)
    handle_tool_error: bool = True  # a looot error becomes tool output the model can read

    def _spec(self, **kwargs: Any) -> Tuple[str, str, Dict[str, Any], Any]:
        raise NotImplementedError

    def _run(self, *args: Any, run_manager: Optional[CallbackManagerForToolRun] = None, **kwargs: Any) -> str:
        method, path, params, body = self._spec(**kwargs)
        try:
            return _render(self.client.request(method, path, params=params, json=body))
        except LoootError as e:
            raise ToolException(str(e)) from e

    async def _arun(
        self, *args: Any, run_manager: Optional[AsyncCallbackManagerForToolRun] = None, **kwargs: Any
    ) -> str:
        method, path, params, body = self._spec(**kwargs)
        try:
            return _render(await self.client.arequest(method, path, params=params, json=body))
        except LoootError as e:
            raise ToolException(str(e)) from e


class SearchInput(BaseModel):
    query: str = Field(description="What you want to do, in plain words, e.g. 'find the work email of a person at a company'")
    limit: int = Field(default=10, ge=1, le=50, description="Max number of rows")
    provider: Optional[str] = Field(default=None, description="Only this provider id")
    category: Optional[str] = Field(default=None, description="Only this category")
    prefer: Optional[Literal["balanced", "cheapest", "fastest", "reliable"]] = Field(
        default=None, description="How to rank the rows"
    )


class LoootSearchCatalog(_LoootTool):
    name: str = "looot_search_catalog"
    description: str = (
        "Search the looot catalog of 2,500+ data API endpoints (work emails, people and company search, "
        "Google results, web pages, news) by the task you want done. Free. Returns ranked rows with "
        "endpoint ids, prices and job ids. Call this first, then looot_inspect, then looot_run."
    )
    args_schema: Type[BaseModel] = SearchInput

    def _spec(self, **kw: Any) -> Tuple[str, str, Dict[str, Any], Any]:
        params = {k: kw.get(k) for k in ("provider", "category", "prefer")}
        params.update(q=kw["query"], limit=kw.get("limit", 10))
        return "GET", "/v1/catalog/search", params, None


class InspectInput(BaseModel):
    endpoint_id: str = Field(description="An endpoint id from a search row, e.g. 'serper-search'")


class LoootInspect(_LoootTool):
    name: str = "looot_inspect"
    description: str = (
        "Read the exact input fields, output shape and maximum price of one looot endpoint. Free. "
        "Use the field names it lists as the input of looot_run."
    )
    args_schema: Type[BaseModel] = InspectInput

    def _spec(self, **kw: Any) -> Tuple[str, str, Dict[str, Any], Any]:
        return "GET", f"/v1/operations/{path_segment(kw['endpoint_id'])}", {}, None


class RunInput(BaseModel):
    endpoint_id: str = Field(
        description="An endpoint id from search, or a job written as 'job:people.email.find' to let looot pick the provider"
    )
    input: Dict[str, Any] = Field(description="The input object, using the field names looot_inspect lists")
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Reuse the same key to retry without paying twice. Leave empty for a new key",
    )
    wait: int = Field(default=20, ge=0, le=60, description="Seconds to wait for the result. 0 returns a run id at once")


class LoootRun(_LoootTool):
    name: str = "looot_run"
    description: str = (
        "Run a looot endpoint or job and spend prepaid credit. A failed call costs nothing. "
        "Check the price with looot_inspect first. Returns the run: status, result, actualCost. "
        "If status is not 'completed', read looot_get_run later with the runId."
    )
    args_schema: Type[BaseModel] = RunInput
    fallback: bool = Field(default=True, exclude=True)
    """Try the next provider of the same job when one misses. Only works with 'job:' ids."""
    max_cost_usd: Optional[float] = Field(default=1.0, exclude=True)
    """Cap for the whole fallback route in USD. None means no extra cap."""

    def _spec(self, **kw: Any) -> Tuple[str, str, Dict[str, Any], Any]:
        body: Dict[str, Any] = {
            "endpointId": kw["endpoint_id"],
            "input": kw["input"],
            "idempotencyKey": kw.get("idempotency_key") or f"lc-{uuid.uuid4()}",
        }
        if self.fallback:
            body["fallback"] = {"enabled": True, "maxCostUsd": self.max_cost_usd} if self.max_cost_usd else True
        return "POST", "/v1/runs", {"wait": kw.get("wait", 20)}, body


class GetRunInput(BaseModel):
    run_id: str = Field(description="The runId returned by looot_run")


class LoootGetRun(_LoootTool):
    name: str = "looot_get_run"
    description: str = "Read one looot run: status, result, cost and error. Free."
    args_schema: Type[BaseModel] = GetRunInput

    def _spec(self, **kw: Any) -> Tuple[str, str, Dict[str, Any], Any]:
        return "GET", f"/v1/runs/{path_segment(kw['run_id'])}", {}, None


class BalanceInput(BaseModel):
    pass


class LoootBalance(_LoootTool):
    name: str = "looot_balance"
    description: str = "Read the available and reserved looot credit and the top-up link. Free."
    args_schema: Type[BaseModel] = BalanceInput

    def _spec(self, **kw: Any) -> Tuple[str, str, Dict[str, Any], Any]:
        return "GET", "/v1/balance", {}, None


class LoootToolkit(BaseModel):
    """Builds the five tools around one client.

    >>> tools = LoootToolkit(token="...").get_tools()
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    token: Optional[str] = None
    base_url: Optional[str] = None
    client: Optional[LoootClient] = None
    max_cost_usd: Optional[float] = 1.0

    def get_tools(self, *, include_run: bool = True) -> List[BaseTool]:
        """Return the tools. `include_run=False` gives a read-only set that cannot spend."""
        client = self.client or LoootClient(self.token, base_url=self.base_url)
        tools: List[BaseTool] = [
            LoootSearchCatalog(client=client),
            LoootInspect(client=client),
            LoootGetRun(client=client),
            LoootBalance(client=client),
        ]
        if include_run:
            tools.insert(2, LoootRun(client=client, max_cost_usd=self.max_cost_usd))
        return tools
