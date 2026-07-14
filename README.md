# AI Loop Library MCP server

A read-only MCP server that gives coding agents the [AI Loop Library](https://ailooplibrary.com):
63+ bounded, verifiable work loops with a trigger, one-change-per-round discipline, a
verification check, durable state, a stop condition, a budget, and human approval gates.

The point is judgment + packaging, not doc search: `pick_loop_for_goal` recommends the right
loop for a stated goal (with why), and `render_run_protocol` turns it into an executable
markdown protocol with a state-file skeleton, stop conditions, and a paste-ready prompt.

Single file, Python 3.9+ standard library only. No dependencies, no auth, no write tools.

## Install

From a clone of this repo:

```bash
python3 server.py --self-test   # verify: 21 offline checks
```

Or grab the single file straight from the live site:

```bash
mkdir -p ~/.ai-loop-library
curl -fsSL https://ailooplibrary.com/mcp/server.py -o ~/.ai-loop-library/server.py
python3 ~/.ai-loop-library/server.py --self-test
```

### Claude Code

```bash
claude mcp add ai-loop-library -- python3 ~/.ai-loop-library/server.py
```

### Cursor / generic MCP client

```json
{
  "mcpServers": {
    "ai-loop-library": {
      "command": "python3",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "AI_LOOP_LIBRARY_CATALOG_URL": "https://ailooplibrary.com/catalog.json"
      }
    }
  }
}
```

### Optional: pip install

```bash
pip install -e .             # installs the ai-loop-library-mcp console script
claude mcp add ai-loop-library -- ai-loop-library-mcp
```

## Catalog source

Resolution order:

1. `AI_LOOP_LIBRARY_CATALOG_PATH` — local JSON file (catalog.json or data/loops.json shape)
2. `AI_LOOP_LIBRARY_CATALOG_URL` — defaults to `https://ailooplibrary.com/catalog.json`
3. Repo-local fallback (`../catalog.json`, `../data/loops.json`) when the server runs inside
   the site repo; otherwise an embedded 2-loop sample keeps `--self-test` fully offline

Fetched catalogs are cached in memory for 5 minutes.

## Tools

| Tool | What it does |
| --- | --- |
| `search_loops(query, category?, limit?)` | Ranked loops with a one-line why-matched |
| `get_loop(id_or_slug)` | Full loop spec + canonical URL |
| `pick_loop_for_goal(goal, constraints?, limit?)` | Best loop for a goal, with why-this / why-not alternatives |
| `render_run_protocol(id_or_slug, goal?, risk_posture?)` | Executable markdown protocol: done contract, one-change-per-round, verification, state files, stop conditions, budget, risk-colored approval boundary, proof format |
| `list_categories()` | Category counts with library filter URLs |
| `catalog_stats()` | Loop count, featured loops, last_updated, catalog source |

Resources: `ailooplibrary://catalog` and `ailooplibrary://loop/{id}`.

Ranking is a transparent heuristic — weighted keyword overlap plus a goal-term-to-category
map plus a spec-completeness bonus. It is documented in `server.py` (`_score_loop`,
`CATEGORY_HINTS`) and labeled as such in tool output. No model, no magic.

## Design constraints

- Read-only. No write tools, no shell execution of user code, no posting, no auth, no PII.
- stdio transport only (newline-delimited JSON-RPC 2.0, MCP protocol 2024-11-05 through 2025-06-18).
- Errors from tools return `isError: true` with a plain-text explanation, never a crash.

## Related

- [ailooplibrary.com](https://ailooplibrary.com) — the library itself: loop specs, stop
  conditions, templates, and research
- [ailooplibrary.com/for-agents/](https://ailooplibrary.com/for-agents/) — install page,
  including the Claude Code skill and a zero-install path
- [Claude Code skill](https://ailooplibrary.com/agent-pack/claude-code/SKILL.md) — works
  without MCP by fetching catalog.json

## License

[MIT](LICENSE)
