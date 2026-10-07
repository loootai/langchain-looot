# langchain-looot

LangChain tools for [looot](https://looot.ai). looot gives an agent one token and one prepaid balance for 2,500+ data API endpoints: work emails, people and company search, Google results, web pages, news. The agent searches the catalog, sees the price before it runs, and pays per call. A failed call costs nothing.

This package wraps the looot REST API as five `BaseTool` subclasses from `langchain-core`.

| Tool | REST call | Cost |
| --- | --- | --- |
| `looot_search_catalog` | `GET /v1/catalog/search?q=` | free |
| `looot_inspect` | `GET /v1/operations/{endpointId}` | free |
| `looot_run` | `POST /v1/runs?wait=` | spends credit |
| `looot_get_run` | `GET /v1/runs/{runId}` | free |
| `looot_balance` | `GET /v1/balance` | free |

## Install

Not on PyPI yet. Install from GitHub:

```bash
pip install "langchain-looot @ git+https://github.com/loootai/langchain-looot.git@v0.1.0"
```

Python 3.10 or newer, `langchain-core` 1.6.6 or newer (below 2).

## Use

Create an agent token at https://looot.ai (Settings, Agent tokens) with the scopes `catalog.read`, `runs.read`, `runs.execute` and `usage.read`, and top up your balance (from $5).

```python
import os
from langchain_looot import LoootToolkit

os.environ["LOOOT_TOKEN"] = "..."   # or LoootToolkit(token="...")

tools = LoootToolkit(max_cost_usd=0.50).get_tools()

# Hand them to any LangChain agent (needs `pip install langchain langchain-anthropic`):
from langchain.agents import create_agent
agent = create_agent("anthropic:claude-sonnet-4-5", tools=tools)
agent.invoke({"messages": [{"role": "user", "content":
    "Find the work email of the CEO of example.com. Check the price first."}]})
```

The tools do not need an agent. You can call one directly:

```python
tools[0].invoke({"query": "verify an email address", "limit": 5})
```

### Read-only tools

`get_tools(include_run=False)` returns four tools and leaves out `looot_run`. That agent can search, inspect and read the balance, and cannot spend.

### Spending limits

`looot_run` sends `fallback: {"enabled": true, "maxCostUsd": <max_cost_usd>}`, so a `job:` run that moves from provider to provider stays under one cap. The default is $1.00. Pass `max_cost_usd=None` for no extra cap. Your prepaid balance is the hard limit either way.

Every run needs an idempotency key. The tool makes one (`lc-<uuid>`) unless the model passes `idempotency_key`; the same key with the same input never charges twice.

### Jobs

`endpoint_id` can be an endpoint from a search row or a job such as `job:people.email.find`, where looot picks the provider and maps the shared input names (`first_name`, `last_name`, `domain`, `email`, `url`, `linkedin_url`...). See https://docs.looot.ai/concepts/jobs.

### Errors

A looot error such as `insufficient_balance` or `unauthorized` is raised as `ToolException` and returned to the model as tool output (`handle_tool_error=True`), so the agent can react instead of crashing. Answers longer than 20,000 characters are cut with a marker.

Sync (`invoke`) and async (`ainvoke`) both work.

## Develop

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.lock
pip install -e . --no-deps
pytest                      # 11 tests, mocked HTTP through httpx.MockTransport
python -m build             # wheel and sdist in dist/, build backend hatchling==1.32.4
bash scripts/leak-scan.sh . # before every push
```

## Publish (maintainers)

`python -m build && python -m twine upload dist/*` with a PyPI token for the `langchain-looot` project. Do this from a clean checkout of the tag.

## License

MIT. Docs: https://docs.looot.ai. Support: https://looot.ai/contact.
