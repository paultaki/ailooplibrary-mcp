# Registry submission checklist

Distribution plan: get the server discoverable everywhere agent developers look
for MCP servers. Everything below needs Paul's accounts — none of it is
automatable from this repo alone. Keep `server.json` version in lockstep with
`SERVER_VERSION` in server.py.

## 1. PyPI (prerequisite for the official registry entry)

```bash
python3 -m pip install --upgrade build twine
python3 -m build
python3 -m twine upload dist/*        # needs a PyPI account + API token
```

Publishes `ai-loop-library-mcp`; users then get the console script via
`pip install ai-loop-library-mcp` and register it with
`claude mcp add ai-loop-library -- ai-loop-library-mcp`.

## 2. Official MCP registry (registry.modelcontextprotocol.io)

Uses the `server.json` in this repo. Flow (see
https://github.com/modelcontextprotocol/registry for current docs):

```bash
brew install mcp-publisher            # or download the release binary
mcp-publisher login github            # proves ownership of io.github.paultaki/*
mcp-publisher publish                 # reads ./server.json
```

Re-publish on every version bump. If the schema URL in server.json has rotated,
`mcp-publisher` will say so — update to the current one from the registry docs.

## 3. Community directories (auto-index + manual submit)

- **Smithery** — https://smithery.ai — add via GitHub sign-in, point at this repo.
- **PulseMCP** — https://www.pulsemcp.com/submit — submission form.
- **mcp.so** — https://mcp.so — submit server (GitHub URL).
- **Glama** — https://glama.ai/mcp/servers — indexes GitHub automatically; claim
  the listing to add the description and website link.

## 4. Listing copy (paste-ready)

> **AI Loop Library** — read-only MCP server giving coding agents 68+ bounded,
> verifiable work loops (trigger, one change per round, same verification every
> round, durable state, stop conditions, budgets, human gates). Browse the
> whole catalog as a ~2k-token digest, shortlist loops for a goal with an
> honest confidence signal, render executable run protocols, lint any loop
> design against the anti-pattern rubric, or scaffold a new spec. One Python
> file, stdlib only, no auth, no write tools. `python3 server.py --self-test`
> = 43 offline checks; `--eval` = 20 golden ranking queries.
