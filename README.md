# AI Loop Library MCP server

A read-only MCP server that gives coding agents the [AI Loop Library](https://ailooplibrary.com):
63+ bounded, verifiable work loops with a trigger, one-change-per-round discipline, a
verification check, durable state, a stop condition, a budget, and human approval gates.

The design premise: the calling agent is the best ranker available — it knows the
operator's repo, data, and constraints, and this server doesn't. So the tools hand the
agent clean, compact evidence instead of pretending to judge for it: `browse_catalog`
returns the whole library as a ~2k-token digest to judge from, `pick_loop_for_goal`
returns an honest lexically-ranked shortlist with a confidence signal (never a single
blind verdict), and `render_run_protocol` turns the chosen loop into an executable
markdown protocol with a state-file skeleton, stop conditions, and a paste-ready prompt.
`critique_loop` lints any loop design against the anti-pattern rubric, and `design_loop`
scaffolds a new spec when nothing in the catalog fits.

Single file, Python 3.9+ standard library only. No dependencies, no auth, no write tools.

## Install

From a clone of this repo:

```bash
python3 server.py --self-test   # verify: 43 offline checks
python3 server.py --eval        # 20 golden ranking queries vs the live catalog
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
| `browse_catalog(category?)` | The whole catalog as a ~2k-token digest (id, category, use_when, verifier strength) — one call, then the agent judges against operator context |
| `search_loops(query, category?, limit?)` | Ranked loops with a one-line why-matched |
| `get_loop(id_or_slug)` | Full loop spec + canonical URL, with verifier strength and loop kind |
| `pick_loop_for_goal(goal, constraints?, limit?)` | Lexically ranked shortlist (5 by default) with use_when, verification, and an honest confidence signal — the agent makes the final call |
| `render_run_protocol(id_or_slug, goal?, risk_posture?, kind?, max_rounds?, max_minutes?)` | Executable markdown protocol: done contract, one-change-per-round, verification, state files, stop conditions, budget, risk-colored approval boundary, proof format. Scheduled-tick business loops (SEO, ads, product metrics) get experiment logs, undo-losers discipline, and notify-the-human ticks |
| `critique_loop(loop_description)` | Deterministic lint against the anti-pattern rubric (verifier, stop condition, budget, one-change-per-round, state, MVL, risk gates…) — 0–10 score with per-check fixes |
| `design_loop(goal, constraints?, cadence?, context?)` | Scaffold a new loop spec from a stated bottleneck, with a domain-matched verifier suggestion and the nearest catalog loops |
| `list_categories()` | Category counts with library filter URLs |
| `catalog_stats()` | Loop count, featured loops, last_updated, catalog source |

All tools declare `readOnlyHint`. Resources: `ailooplibrary://catalog` and
`ailooplibrary://loop/{id}`.

Ranking is a transparent lexical heuristic — IDF-weighted keyword overlap (computed from
the catalog at load, so template boilerplate scores near zero) with light stemming, a
small documented synonym/expansion map, damped brand tokens, and a goal-term-to-category
map. It is documented in `server.py` (`_score_loop`, `SYNONYMS_RAW`, `CATEGORY_HINTS`)
and labeled as such in tool output. `--eval` holds it to 20 golden queries at a ≥85%
top-3 hit rate. No model, no magic — and when confidence is low, the output says so.

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
