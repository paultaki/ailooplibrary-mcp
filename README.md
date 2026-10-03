# AI Loop Library MCP server

Version 2.1.0. A read-only, local MCP server for the
[AI Loop Library](https://ailooplibrary.com) and its 68 bounded work loops.
The calling agent uses its knowledge of the operator's project to choose a loop;
the server supplies searchable specs, runnable protocols, and design checks.

One Python file, Python 3.9+, standard library only at runtime. No write tools,
credentials, model calls, or hosted server to operate.

## Install

Download the single file:

```bash
mkdir -p ~/.ai-loop-library
curl -fsSL https://ailooplibrary.com/mcp/server.py -o ~/.ai-loop-library/server.py
python3 ~/.ai-loop-library/server.py --self-test
claude mcp add ai-loop-library -- python3 ~/.ai-loop-library/server.py
```

For Cursor or another stdio client:

```json
{
  "mcpServers": {
    "ai-loop-library": {
      "command": "python3",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

The [standalone repository](https://github.com/paultaki/ailooplibrary-mcp)
contains the same server. Run the following from its root, or from `mcp/` in
the site repository:

```bash
python3 server.py --self-test
python3 server.py --eval
pip install -e .
claude mcp add ai-loop-library -- ai-loop-library-mcp
```

Already installed? Download the file again or update your clone and reinstall,
then restart the MCP connection. Existing installations do not update themselves.

## Tools

| Tool | Result |
| --- | --- |
| `browse_catalog(category?)` | Compact catalog digest for the calling agent to judge |
| `search_loops(query, category?, limit?)` | Ranked matches with why-matched explanations |
| `get_loop(id_or_slug)` | Full spec, canonical URL, verifier classification, loop kind |
| `pick_loop_for_goal(goal, constraints?, limit?)` | Lexical shortlist and confidence signal; the calling agent chooses |
| `render_run_protocol(id_or_slug, goal?, risk_posture?, kind?, max_rounds?, max_minutes?)` | Markdown protocol and copyable prompt sharing the same stop conditions and approval boundaries |
| `critique_loop(loop_description)` | Text-based lint, per-check findings, critical missing checks, and related loops |
| `design_loop(goal, constraints?, cadence?, context?)` | New loop scaffold with a suggested domain verifier and explicit TODOs |
| `list_categories()` | Category counts and library links |
| `catalog_stats()` | Counts, catalog source, update metadata, and cache status |

Session protocols honor round and minute caps. Scheduled-tick protocols use
experiment logs, per-tick/total spend guidance, and a two-tick no-progress stop.
`max_rounds` and `max_minutes` apply only to session protocols. Strict posture
requires explicit approval for shared-surface actions, including in copied prompts.

The linter recognizes explicit gate language and flags common negations such as
"without human approval" or "approval is optional". Critical missing verification,
stop, budget, or approval checks prevent a positive overall grade. Text matching
cannot establish safety or grant permission, regardless of the numeric score.

Resources: `ailooplibrary://catalog` and `ailooplibrary://loop/{id}`.
All tools declare `readOnlyHint` and `idempotentHint`.

## Catalog loading and outages

Resolution order:

1. `AI_LOOP_LIBRARY_CATALOG_PATH`: an authoritative local JSON file, using either
   the `catalog.json` or `data/loops.json` shape. An invalid explicit path fails.
2. `AI_LOOP_LIBRARY_CATALOG_URL`, defaulting to `https://ailooplibrary.com/catalog.json`.
3. Local fallback: `catalog.json` beside the server, then `../catalog.json`, then
   `../data/loops.json`.

The server caches a validated catalog in memory for five minutes. Remote fetches
have a ten-second timeout and a 4 MiB response limit. Validation and search indexing
finish before a new snapshot replaces the current one.

If refresh and local fallback fail, the server retains its last validated snapshot
and waits 60 seconds before retrying. Tool responses include a stale-catalog warning
and `_meta["ai-loop-library/catalog"]` with source, age, and stale status;
`catalog_stats` also includes `catalog_status`. The cache is process-local and does
not survive restart. Changing the configured source invalidates the old snapshot.
An initial outage without a valid local catalog returns an error, never sample data.
The two-loop embedded sample is reserved for offline tests.

## Client compatibility

Supported protocol revisions: `2024-11-05`, `2025-03-26`, `2025-06-18`.
Transport: newline-delimited JSON-RPC 2.0 over stdio. Diagnostics go to stderr.
Malformed requests and invalid tool arguments return protocol errors without
terminating the server. Execution failures return `isError: true`.
Request `params` and tool `arguments` must be objects when supplied; omit them for
no-argument calls. Explicit `null` for these objects is intentionally rejected,
including for older clients. Nullable individual fields remain supported as declared
in each input schema. This validation is a deliberate tightening from 2.0.0.

With `2025-06-18`, the six JSON-returning tools advertise output schemas and return
`structuredContent` alongside identical JSON text. Earlier revisions retain the
text-only contract. Browse, design, and render tools continue to return Markdown.

Ranking uses IDF-weighted keyword overlap, light stemming, synonyms, and category
hints. It is a browsing aid, not a model judgment or constraint solver.

## Verification and development

From the directory containing `server.py`:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
python -m unittest -v test_server
python server.py --self-test
python server.py --eval
```

The runtime has no third-party dependencies. The optional test dependency,
`jsonschema`, independently validates advertised schemas and real tool results.
The 43 offline self-checks and 20 golden ranking queries remain separate from the
regression suite, which covers request recovery, argument validation, old-client
compatibility, protocol fidelity, lint negation, domain routing, and cache outages.
CI runs these gates on Python 3.9 and 3.13.

Release invariant: the site and standalone repositories must ship identical
`server.py`, `README.md`, `pyproject.toml`, and `test_server.py` files. Version metadata
must match `SERVER_VERSION`; the standalone `server.json` is a registry submission
manifest, not proof that a PyPI or registry publication has occurred.
