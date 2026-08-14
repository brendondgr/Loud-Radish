# LangGraph Structure

A clean LangGraph repository usually works best when you separate **graph wiring**, **business logic**, **state/schema**, and **infrastructure concerns** instead of putting everything into one large `agent.py`. LangChain’s own LangGraph application structure recommends a project with app code in one package plus `langgraph.json`, dependency files, and optional environment config, which is a good baseline to build on.

## Recommended layout

LangChain’s docs show a typical LangGraph app with a dedicated package for project code, utility modules for tools, nodes, and state, plus root-level deployment files such as `langgraph.json`, `requirements.txt` or `pyproject.toml`, and `.env`.  Building on that, a maintainable repo often looks like this:

```text
my-langgraph-app/
├── README.md
├── pyproject.toml
├── langgraph.json
├── .env.example
├── .gitignore
├── tests/
│   ├── unit/
│   ├── integration/
│   └── evals/
├── docs/
│   ├── architecture.md
│   ├── state-model.md
│   └── runbooks/
├── scripts/
│   ├── dev.py
│   └── seed_data.py
├── utils/
│   └── multi_agent/
│       ├── __init__.py
│       ├── main.py                 # app entrypoint / super-graph export
│       ├── config.py               # settings, env loading
│       ├── core_state/             # global state shared across agents
│       │   ├── __init__.py
│       │   ├── schemas.py          # TypedDict / Pydantic models
│       │   └── reducers.py
│       ├── shared_tools/
│       │   ├── __init__.py
│       │   ├── search.py
│       │   └── crm.py
│       ├── services/
│       │   ├── llm.py
│       │   ├── vectorstore.py
│       │   └── memory.py
│       ├── agents/                 # individual agents
│       │   ├── orchestrator/
│       │   │   ├── __init__.py
│       │   │   ├── graph.py
│       │   │   ├── nodes.py
│       │   │   ├── state.py
│       │   │   └── prompts.py
│       │   ├── planner/
│       │   │   ├── __init__.py
│       │   │   ├── graph.py
│       │   │   ├── nodes.py
│       │   │   ├── state.py
│       │   │   └── prompts.py
│       │   └── validator/
│       │       ├── __init__.py
│       │       ├── graph.py
│       │       ├── nodes.py
│       │       ├── state.py
│       │       └── prompts.py
│       └── observability/
│           ├── logging.py
│           └── tracing.py
└── notebooks/
    └── experiments.ipynb
```

## Folder roles

In a multi-agent setup, group each agent into its own folder under `agents/` (e.g., `orchestrator/`, `planner/`, `validator/`). Inside each agent folder, keep `graph.py` focused only on assembling nodes, edges, conditional routing, and compilation for that specific agent. Put actual node behavior in `nodes.py` (or a `nodes/` folder if complex), and agent-specific state in `state.py`. 

Keep global/shared state definitions isolated in `core_state/` (or similar), because state is one of the core concepts in LangGraph graph design and tends to become a hidden source of complexity if global and agent-specific states get mixed. 

Shared tool wrappers go in `shared_tools/`, and shared external integrations like model clients or vector stores in `services/`. Prompts should be kept in `prompts.py` or a `prompts/` directory within their respective agent folder instead of embedding them inside nodes, which makes prompt iteration safer and easier to review.

## Practical rules

A few conventions keep the repo easy to understand:

- One graph per file when possible; if a workflow grows large, split subgraphs into separate files and compose them from a top-level graph module. LangGraph supports one or more graphs configured via `langgraph.json`, so this scales cleanly.
- One node function per clear responsibility, for example `classify_intent`, `retrieve_context`, `draft_answer`, rather than a single `run_agent` node that does everything. This matches LangGraph’s node-and-edge model more naturally.
- Keep `main.py` or a single export module very thin; it should mostly expose the compiled graph referenced by `langgraph.json`. The docs describe the config file as the place where graph paths are registered for deployment.
- Put experiments in `notebooks/` or `experiments/`, not inside `utils/`, because the official examples repository contains many distinct example applications and patterns, which is useful for learning but can become noisy in a production repo if mixed with core app code.

## What to avoid

Avoid organizing by technical sprawl like `utils.py`, `helpers.py`, and `misc.py`, because those files become dumping grounds and make graph behavior harder to trace. The official example and docs structure is more explicit, naming modules around tools, nodes, state, and agent construction.

Also avoid mixing deployment files, prompt text, graph compilation, and provider-specific setup in one file. LangGraph is intentionally a low-level orchestration layer for long-running, stateful workflows, so cleanliness comes from making orchestration separate from implementation details.

## Minimal baseline

If you want the simplest version that still stays clean, start with this:

```text
utils/multi_agent/
├── main.py             # composes the agents into a main graph
├── shared_state.py
├── shared_tools.py
└── agents/
    ├── orchestrator.py
    ├── planner.py
    └── validator.py
```

This is enough for a small multi-agent project. As soon as individual agents grow complex, have their own multiple tools, or require separate prompt management, split into the larger package structure above so the repository remains readable.

A good rule of thumb is: if a new contributor cannot answer “where is the state defined, where is the graph wired, and where is the tool logic implemented?” in under a minute, the repo structure needs tightening.
