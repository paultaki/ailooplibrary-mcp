# AGENT-NOTES

## 2026-10-02 · codex (MCP 2.1 reliability)

- **Did:** Mirrored MCP 2.1.0 from the site repository: protocol fidelity, lint negation, domain routing, transport/input validation, last-valid catalog recovery, structured output schemas, updated docs, regression tests, and Python 3.9/3.13 CI. Registry submission manifest versions now match.
- **Why:** Paul approved all recommendations from the MCP audit and publication through the existing site and GitHub distribution paths.
- **Next:** No outstanding release work. Code commit 13c1fe7 passed Python 3.9/3.13 CI, and v2.1.0 is published on GitHub with server.py, wheel, and source archive. Site distribution bytes match and live official-client calls pass. PyPI and official registry publication remain separate, unconfigured distribution channels.
- **Watch out:** Keep server.py, test_server.py, pyproject.toml and README.md byte-identical to site mcp/. Runtime remains stdlib only; jsonschema is test-only. Three Claude review rounds produced eight actionable findings, all fixed and covered by regression tests; one null-object compatibility finding was intentionally discarded and documented. Final review fixes passed tests without a fourth dispatch per the review cap, so no clean final reviewer verdict is claimed. Official Python MCP client verified all nine tools, six structured outputs and 69 resources against the full catalog.
