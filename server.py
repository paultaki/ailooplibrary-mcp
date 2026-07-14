#!/usr/bin/env python3
"""AI Loop Library MCP server (stdio, read-only, zero dependencies).

Gives coding agents a library of bounded, verifiable work loops — trigger,
one-change-per-round, same verification every round, durable state, stop
condition, budget, and human approval gates — then picks the right loop for a
stated goal and renders a runnable protocol.

This is judgment + packaging, not doc search:
  search_loops          find loops by job-to-be-done / category / keywords
  get_loop              full loop spec + canonical URL
  pick_loop_for_goal    best loop for a goal, with why-this / why-not-that
  render_run_protocol   executable markdown protocol + state-file skeleton
  list_categories       category counts
  catalog_stats         counts, featured loops, last_updated

Ranking is a transparent keyword + category heuristic (see _score_loop), not a
model. The server is read-only: no write tools, no auth, no shell execution.

Run:            python3 mcp/server.py
Self-test:      python3 mcp/server.py --self-test        (offline, local catalog)
Catalog source: AI_LOOP_LIBRARY_CATALOG_PATH (local file) or
                AI_LOOP_LIBRARY_CATALOG_URL (default https://ailooplibrary.com/catalog.json),
                falling back to the repo's catalog.json / data/loops.json.

Requires Python 3.9+. Newline-delimited JSON-RPC 2.0 over stdio per the MCP spec.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

SERVER_NAME = "ai-loop-library"
SERVER_VERSION = "1.0.0"
DEFAULT_CATALOG_URL = "https://ailooplibrary.com/catalog.json"
BASE_URL = "https://ailooplibrary.com"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}
LATEST_PROTOCOL = "2025-06-18"
CACHE_TTL_SECONDS = 300

STOPWORDS = {
    "a", "an", "and", "the", "for", "of", "to", "in", "on", "with", "my",
    "our", "your", "is", "it", "that", "this", "loop", "loops", "ai", "agent",
    "i", "we", "me", "up", "at", "by", "or", "be", "do", "make", "get",
}

# Goal keywords -> category boost. Transparent heuristic, documented on purpose.
CATEGORY_HINTS = {
    "Engineering": ["ci", "test", "tests", "build", "deploy", "code", "repo",
                    "pr", "prs", "bug", "refactor", "lint", "pipeline", "flaky",
                    "coverage", "merge", "commit", "docs", "dependency", "release",
                    "speed", "slow", "error", "errors", "logging", "debug"],
    "Knowledge": ["notes", "research", "source", "sources", "capture", "obsidian",
                  "knowledge", "retrieval", "memory", "reading", "summarize"],
    "Content": ["publish", "blog", "post", "article", "content", "seo", "copy",
                "newsletter", "draft", "writing", "fact", "claims"],
    "Growth": ["growth", "outreach", "leads", "funnel", "audience", "traffic",
               "distribution", "citation", "buyers", "customers"],
    "Operations": ["ops", "incident", "monitor", "monitoring", "production",
                   "handoff", "queue", "process", "toolchain", "sweep"],
    "Evaluation": ["eval", "evaluation", "regression", "quality", "review",
                   "judge", "rubric", "benchmark", "qa", "browser"],
    "Security": ["security", "vulnerability", "vulnerabilities", "secrets",
                 "audit", "permissions", "exposure", "leak"],
    "Design": ["design", "ui", "ux", "visual", "polish", "accessibility",
               "screenshot", "layout"],
    "Personal Ops": ["email", "inbox", "calendar", "personal", "follow",
                     "followup", "promise", "refund", "subscription", "admin"],
    "Strategy": ["strategy", "positioning", "roadmap", "bets", "planning"],
}


def log(message):
    sys.stderr.write("[ai-loop-library] %s\n" % message)
    sys.stderr.flush()


def slugify(value):
    value = re.sub(r"[^a-z0-9]+", "-", str(value).lower().strip())
    return value.strip("-")


def tokenize(text):
    return [t for t in re.findall(r"[a-z0-9]+", str(text).lower()) if t not in STOPWORDS]


# ---------------------------------------------------------------------------
# Catalog loading + normalization
# ---------------------------------------------------------------------------

DEFAULTS = {
    "stop_condition": ("Stop when the verifier passes, the budget is exhausted, "
                       "no progress is made, a blocker appears, or approval is required."),
    "budget": "Set a time, turn, token, retry, file, or dollar cap before running the loop.",
    "approval_boundary": ("Human approval required before public, destructive, financial, "
                          "legal, account, or production-impacting actions."),
}


def _normalize_loop(raw):
    """Map either a catalog.json spec entry or a data/loops.json entry to one shape."""
    url = raw.get("url", "")
    loop_id = raw.get("id") or ""
    if not loop_id and url:
        match = re.search(r"/loops/([^/]+)/?", url)
        if match:
            loop_id = match.group(1)
    title = raw.get("title") or raw.get("name") or loop_id
    if not loop_id:
        loop_id = slugify(title)
    if not url:
        url = "%s/loops/%s/" % (BASE_URL, loop_id)
    return {
        "id": loop_id,
        "title": title,
        "category": raw.get("category", ""),
        "tags": raw.get("tags", []),
        "difficulty": raw.get("difficulty", ""),
        "trigger": raw.get("trigger") or raw.get("cadence", ""),
        "use_when": raw.get("use_when") or raw.get("useWhen", ""),
        "summary": raw.get("summary") or raw.get("objective", ""),
        "steps": raw.get("steps") or raw.get("allowed_actions", []),
        "verification": raw.get("verification", ""),
        "stop_condition": raw.get("stop_condition") or DEFAULTS["stop_condition"],
        "budget": raw.get("budget") or DEFAULTS["budget"],
        "approval_boundary": raw.get("approval_boundary") or DEFAULTS["approval_boundary"],
        "safe_output": raw.get("safe_output", ""),
        "prompt": raw.get("prompt", ""),
        "source": raw.get("source", ""),
        "featured": bool(raw.get("featured")),
        "url": url,
        "state_file_suggestion": raw.get("state_file_suggestion")
        or "docs/loops/%s/progress.md" % loop_id,
    }


# Minimal embedded subset so a standalone download of this file can still pass
# --self-test offline. The serving path never uses it: a running server with no
# catalog is a misconfiguration and should fail loudly instead.
EMBEDDED_SAMPLE = [
    {
        "id": "ci-optimization", "title": "CI Optimization", "category": "Engineering",
        "tags": ["CI", "performance", "testing"], "difficulty": "Intermediate",
        "cadence": "Monthly or when CI is painful",
        "useWhen": "CI is slow or flaky and you want bounded rounds of one fix at a time.",
        "summary": "Attack one CI bottleneck at a time until the target is met.",
        "steps": ["Profile the slowest CI stage.", "Make one bounded change.",
                  "Re-run and compare timings.", "Keep or revert, then record."],
        "verification": "CI p50/p95 improves against the same workflow without weakening tests.",
        "prompt": "Profile CI, fix one bottleneck per round, verify timings each round.",
    },
    {
        "id": "prepublish-source-check", "title": "Pre-Publish Source Check",
        "category": "Content", "tags": ["fact check", "sources"], "difficulty": "Beginner",
        "cadence": "Before publishing factual work",
        "useWhen": "A draft makes checkable claims and publishing is reputationally risky.",
        "summary": "Make every checkable claim source-backed or flagged before publish.",
        "steps": ["List checkable claims.", "Map each claim to a source.",
                  "Flag unsupported claims.", "Stop at the publish approval gate."],
        "verification": "Every claim maps to a source or an explicit uncertainty note.",
        "prompt": "Build a claim-to-source table for this draft; publishing stays human-gated.",
    },
]


class Catalog(object):
    def __init__(self, allow_network=True):
        self.allow_network = allow_network
        self._loops = []
        self._meta = {}
        self._source = ""
        self._loaded_at = 0.0

    def _repo_candidates(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return [os.path.join(root, "catalog.json"), os.path.join(root, "data", "loops.json")]

    def _ingest(self, payload, source):
        if isinstance(payload, dict):
            raw_loops = payload.get("loops", [])
            self._meta = {k: v for k, v in payload.items() if k != "loops"}
        else:
            raw_loops = payload
            self._meta = {}
        self._loops = [_normalize_loop(item) for item in raw_loops]
        self._source = source
        self._loaded_at = time.time()
        log("loaded %d loops from %s" % (len(self._loops), source))

    def load(self, force=False):
        if self._loops and not force and time.time() - self._loaded_at < CACHE_TTL_SECONDS:
            return
        path = os.environ.get("AI_LOOP_LIBRARY_CATALOG_PATH")
        if path:
            with open(path, "r", encoding="utf-8") as handle:
                self._ingest(json.load(handle), path)
            return
        if self.allow_network:
            url = os.environ.get("AI_LOOP_LIBRARY_CATALOG_URL", DEFAULT_CATALOG_URL)
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "%s-mcp/%s" % (SERVER_NAME, SERVER_VERSION)})
                with urllib.request.urlopen(request, timeout=10) as response:
                    self._ingest(json.loads(response.read().decode("utf-8")), url)
                return
            except Exception as exc:  # fall back to local files
                log("catalog fetch failed (%s); trying local fallback" % exc)
        for candidate in self._repo_candidates():
            if os.path.exists(candidate):
                with open(candidate, "r", encoding="utf-8") as handle:
                    self._ingest(json.load(handle), candidate)
                return
        if not self.allow_network:  # offline self-test on a standalone download
            log("no local catalog found; using embedded %d-loop sample" % len(EMBEDDED_SAMPLE))
            self._ingest(EMBEDDED_SAMPLE, "embedded-sample")
            return
        raise RuntimeError(
            "No catalog available. Set AI_LOOP_LIBRARY_CATALOG_PATH or "
            "AI_LOOP_LIBRARY_CATALOG_URL, or run from a clone of the site repo.")

    @property
    def loops(self):
        self.load()
        return self._loops

    @property
    def meta(self):
        self.load()
        return self._meta

    @property
    def source(self):
        self.load()
        return self._source

    def find(self, id_or_slug):
        needle = slugify(id_or_slug)
        for loop in self.loops:
            if loop["id"] == needle or slugify(loop["title"]) == needle:
                return loop
        for loop in self.loops:  # forgiving partial match
            if needle and (needle in loop["id"] or needle in slugify(loop["title"])):
                return loop
        return None


# ---------------------------------------------------------------------------
# Ranking heuristic (transparent, documented — no model involved)
# ---------------------------------------------------------------------------

FIELD_WEIGHTS = [
    ("title", 4.0), ("tags", 3.0), ("use_when", 3.0), ("summary", 2.0),
    ("category", 2.0), ("steps", 1.0), ("verification", 1.0), ("trigger", 0.5),
]


def _score_loop(loop, terms):
    """Weighted keyword-overlap score. Returns (score, why: list of match notes)."""
    score = 0.0
    why = []
    for field, weight in FIELD_WEIGHTS:
        value = loop.get(field, "")
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        field_tokens = set(tokenize(value))
        hits = sorted(set(terms) & field_tokens)
        if hits:
            score += weight * len(hits)
            why.append("%s matches %s" % (field.replace("_", " "), ", ".join(hits)))
    return score, why


def _category_boost(loop, terms):
    hints = CATEGORY_HINTS.get(loop.get("category", ""), [])
    hits = sorted(set(terms) & set(hints))
    if hits:
        return 2.0 * len(hits), "goal terms (%s) map to the %s category" % (
            ", ".join(hits), loop["category"])
    return 0.0, None


def _richness_bonus(loop):
    bonus = 0.0
    for field in ("verification", "stop_condition", "prompt", "use_when"):
        if loop.get(field):
            bonus += 0.25
    return bonus


def _brief(loop):
    return {
        "id": loop["id"],
        "title": loop["title"],
        "category": loop["category"],
        "difficulty": loop["difficulty"],
        "url": loop["url"],
        "summary": loop["summary"],
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def tool_search_loops(catalog, query, category=None, limit=8):
    terms = tokenize(query)
    if not terms:
        return {"results": [], "note": "Query contained no searchable terms."}
    limit = max(1, min(int(limit or 8), 25))
    scored = []
    for loop in catalog.loops:
        if category and loop["category"].lower() != str(category).lower():
            continue
        score, why = _score_loop(loop, terms)
        if score > 0:
            entry = _brief(loop)
            entry["why_matched"] = "; ".join(why[:3])
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return {
        "query": query,
        "category_filter": category,
        "result_count": min(len(scored), limit),
        "results": [entry for _, entry in scored[:limit]],
        "method": "weighted keyword overlap across title/tags/use_when/summary/category/steps",
    }


def tool_get_loop(catalog, id_or_slug):
    loop = catalog.find(id_or_slug)
    if loop is None:
        known = ", ".join(l["id"] for l in catalog.loops[:10])
        raise ValueError("No loop matches %r. Try search_loops first. "
                         "Example ids: %s…" % (id_or_slug, known))
    return loop


def tool_pick_loop_for_goal(catalog, goal, constraints=None, limit=3):
    terms = tokenize(goal) + (tokenize(constraints) if constraints else [])
    if not terms:
        raise ValueError("Goal contained no searchable terms; state the job to be done.")
    limit = max(1, min(int(limit or 3), 5))
    scored = []
    for loop in catalog.loops:
        base, why = _score_loop(loop, terms)
        boost, boost_why = _category_boost(loop, terms)
        if boost_why:
            why.append(boost_why)
        total = base + boost + _richness_bonus(loop)
        if base + boost > 0:
            scored.append((total, loop, why))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    if not scored:
        return {
            "goal": goal,
            "recommendation": None,
            "note": ("No loop matched this goal. Browse %s/library/ or state the "
                     "job to be done in operational terms (what repeats, what proves it)."
                     % BASE_URL),
        }
    top_score, top_loop, top_why = scored[0]
    recommendation = _brief(top_loop)
    recommendation["verification"] = top_loop["verification"]
    recommendation["why_this"] = "; ".join(top_why[:4])
    alternatives = []
    for score, loop, why in scored[1:limit]:
        alt = _brief(loop)
        alt["why_considered"] = "; ".join(why[:2])
        alt["why_not_top"] = ("weaker keyword/category match than %r for this goal "
                              "(score %.1f vs %.1f)" % (top_loop["title"], score, top_score))
        alternatives.append(alt)
    return {
        "goal": goal,
        "constraints": constraints,
        "recommendation": recommendation,
        "alternatives": alternatives,
        "next_step": "Call render_run_protocol(id_or_slug=%r, goal=%r) to get the "
                     "executable protocol." % (top_loop["id"], goal),
        "method": ("transparent heuristic: weighted keyword overlap + goal-term-to-category "
                   "mapping + spec-completeness bonus. Not a model ranking."),
    }


RISK_POSTURES = ("default", "strict")


def tool_render_run_protocol(catalog, id_or_slug, goal=None, risk_posture="default"):
    loop = tool_get_loop(catalog, id_or_slug)
    posture = (risk_posture or "default").lower()
    if posture not in RISK_POSTURES:
        raise ValueError("risk_posture must be one of %s" % (RISK_POSTURES,))
    state_dir = "docs/loops/%s" % loop["id"]
    state_json = json.dumps({
        "loop_id": loop["id"],
        "goal": goal or loop["summary"],
        "round": 0,
        "status": "not_started",
        "verification_command_or_check": loop["verification"],
        "rounds": [],
        "budget": {"max_rounds": 8, "max_minutes": 45, "max_failed_verifications_in_a_row": 3},
        "stopped_because": None,
    }, indent=2)
    steps = "\n".join("- %s" % step for step in loop["steps"]) or "- (see canonical page)"
    goal_line = "\n**Operator goal:** %s" % goal if goal else ""
    strict_note = ("\n> **Strict posture:** treat every yellow action as red — pause and get "
                   "explicit human approval before each round that touches anything shared."
                   if posture == "strict" else "")
    paste_prompt = (
        "Run the \"%s\" loop from AI Loop Library (%s) as a bounded loop.\n"
        "Goal: %s\n"
        "Rules: one change per round; run the same verification every round (%s); "
        "append each round to %s/progress.md and update %s/state.json; "
        "stop on verifier pass, 8 rounds, 3 consecutive failed verifications, no progress, "
        "a blocker, or anything needing human approval (money, production, outbound, deletion). "
        "Finish with a proof report: rounds used, changes made, verification output, "
        "remaining risk, and the next human decision."
        % (loop["title"], loop["url"], goal or loop["summary"], loop["verification"],
           state_dir, state_dir))
    protocol = """# Run protocol — %(title)s

Canonical spec: %(url)s
Category: %(category)s · Trigger: %(trigger)s · Risk posture: %(posture)s
Rendered by the AI Loop Library MCP server (template + catalog data, read-only).

## Objective / done contract

%(summary)s%(goal_line)s

Done means: the verification below passes, evidence is recorded in the state file,
and nothing crossed the approval boundary without a human.

## Allowed actions

%(steps)s

## Loop rules

1. **One change per round.** Make one coherent, bounded move, then verify. No batching.
2. **Same verification every round:** %(verification)s
3. **Record state after every round** before deciding to continue.
4. Do not widen scope mid-loop. New work becomes a new loop, not round 9 of this one.

## State (durable, survives the session)

- Progress log: `%(state_dir)s/progress.md` — per round: round number, the one change,
  verification result, evidence (command output, diff, screenshot path).
- Machine state: `%(state_dir)s/state.json` skeleton:

```json
%(state_json)s
```

## Stop conditions

Stop — and report — when any of these is true:
1. The verifier passes with evidence.
2. Budget exhausted: 8 rounds, 45 minutes, or 3 consecutive failed verifications
   (adjust before starting; %(budget)s).
3. The same failure repeats after two different fixes (no progress).
4. A blocker appears: missing access, credentials, or an ambiguous requirement.
5. The next action crosses the approval boundary below.

## Approval boundary (risk colors)

- **Green — proceed:** local edits, drafts, analysis, reading, tests in a sandbox.
- **Yellow — pause and show a human:** public-facing drafts, PRs to shared branches,
  config or schema changes, anything a teammate will see before you do.
- **Red — never without explicit approval:** money, production, outbound messages,
  deletion, account changes, legal/reputational commitments.

%(approval_boundary)s%(strict_note)s

## Proof format

Final report must include: rounds used, one-line change log per round, the verification
output for the final round, remaining risks or open questions, and the single next
human decision (approve / redirect / stop).

## Paste into Claude Code

```
%(paste_prompt)s
```
""" % {
        "title": loop["title"], "url": loop["url"], "category": loop["category"],
        "trigger": loop["trigger"], "posture": posture, "summary": loop["summary"],
        "goal_line": goal_line, "steps": steps, "verification": loop["verification"],
        "state_dir": state_dir, "state_json": state_json, "budget": loop["budget"],
        "approval_boundary": loop["approval_boundary"], "strict_note": strict_note,
        "paste_prompt": paste_prompt,
    }
    return protocol


def tool_list_categories(catalog):
    counts = {}
    for loop in catalog.loops:
        counts[loop["category"]] = counts.get(loop["category"], 0) + 1
    return {
        "categories": [
            {"category": name, "loop_count": count,
             "library_url": "%s/library/?category=%s" % (BASE_URL, name.replace(" ", "%20"))}
            for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "total_loops": len(catalog.loops),
    }


def tool_catalog_stats(catalog):
    featured = [_brief(loop) for loop in catalog.loops if loop["featured"]]
    return {
        "loop_count": len(catalog.loops),
        "category_count": len({loop["category"] for loop in catalog.loops}),
        "featured_loops": featured,
        "last_updated": catalog.meta.get("last_updated"),
        "catalog_source": catalog.source,
        "site": BASE_URL,
        "library_url": BASE_URL + "/library/",
        "for_agents_url": BASE_URL + "/for-agents/",
    }


TOOL_DEFINITIONS = [
    {
        "name": "search_loops",
        "description": (
            "Search AI Loop Library's bounded, verifiable work loops by job-to-be-done, "
            "keyword, or category. Each loop ships with a trigger, one-change-per-round "
            "discipline, a verification check, a stop condition, a budget, and a human "
            "approval boundary — so an agent can run it without thrashing. Returns ranked "
            "matches with a one-line why-matched."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Job to be done or keywords, e.g. 'flaky tests' or 'source-check a draft'"},
                "category": {"type": ["string", "null"], "description": "Optional exact category filter, e.g. Engineering, Knowledge, Operations"},
                "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 25},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_loop",
        "description": (
            "Fetch one loop's full spec by id or slug: objective, trigger, allowed actions, "
            "verification, stop condition, budget, approval boundary, copyable prompt, and "
            "canonical URL. Use after search_loops or pick_loop_for_goal."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_or_slug": {"type": "string", "description": "Loop id, e.g. 'ci-optimization', or its title"},
            },
            "required": ["id_or_slug"],
        },
    },
    {
        "name": "pick_loop_for_goal",
        "description": (
            "Recommend the best bounded work loop for a stated goal, with why-this and 1-2 "
            "alternatives with why-not. This is the judgment tool: use it when the operator "
            "says what they want done ('speed up CI', 'stop shipping unverified claims') and "
            "you need the right loop, not a keyword list. Ranking is a transparent heuristic "
            "(keyword overlap + goal-to-category mapping + spec completeness), not a model. "
            "Follow with render_run_protocol to get the executable version."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The outcome the operator wants, in plain words"},
                "constraints": {"type": ["string", "null"], "description": "Optional constraints, e.g. 'read-only', 'no production access'"},
                "limit": {"type": "integer", "default": 3, "minimum": 1, "maximum": 5},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "render_run_protocol",
        "description": (
            "Render a loop as an executable markdown run protocol an agent can follow "
            "directly: objective/done contract, allowed actions, one-change-per-round rule, "
            "the same verification every round, a durable state-file skeleton "
            "(progress.md + state.json), stop conditions, budget, risk-colored approval "
            "boundary, proof format, and a paste-ready Claude Code prompt. This is the "
            "packaging tool — the difference between reading about a loop and running one."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_or_slug": {"type": "string", "description": "Loop id or title"},
                "goal": {"type": ["string", "null"], "description": "Optional operator goal to embed in the protocol"},
                "risk_posture": {"type": "string", "enum": ["default", "strict"], "default": "default",
                                 "description": "strict treats every shared-surface action as approval-gated"},
            },
            "required": ["id_or_slug"],
        },
    },
    {
        "name": "list_categories",
        "description": "List loop categories with live counts and library filter URLs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "catalog_stats",
        "description": ("Catalog overview: loop count, categories, featured loops, "
                        "last_updated, and where the catalog was loaded from."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def dispatch_tool(catalog, name, arguments):
    arguments = arguments or {}
    if name == "search_loops":
        return tool_search_loops(catalog, arguments.get("query", ""),
                                 arguments.get("category"), arguments.get("limit", 8))
    if name == "get_loop":
        return tool_get_loop(catalog, arguments.get("id_or_slug", ""))
    if name == "pick_loop_for_goal":
        return tool_pick_loop_for_goal(catalog, arguments.get("goal", ""),
                                       arguments.get("constraints"), arguments.get("limit", 3))
    if name == "render_run_protocol":
        return tool_render_run_protocol(catalog, arguments.get("id_or_slug", ""),
                                        arguments.get("goal"),
                                        arguments.get("risk_posture", "default"))
    if name == "list_categories":
        return tool_list_categories(catalog)
    if name == "catalog_stats":
        return tool_catalog_stats(catalog)
    raise ValueError("Unknown tool: %s" % name)


# ---------------------------------------------------------------------------
# MCP resources
# ---------------------------------------------------------------------------

def resource_list(catalog):
    resources = [{
        "uri": "ailooplibrary://catalog",
        "name": "AI Loop Library catalog",
        "description": "Full catalog of bounded, verifiable AI agent loops (JSON).",
        "mimeType": "application/json",
    }]
    for loop in catalog.loops:
        resources.append({
            "uri": "ailooplibrary://loop/%s" % loop["id"],
            "name": loop["title"],
            "description": loop["summary"][:180],
            "mimeType": "application/json",
        })
    return resources


def resource_read(catalog, uri):
    if uri == "ailooplibrary://catalog":
        payload = dict(catalog.meta)
        payload["loops"] = catalog.loops
        return json.dumps(payload, ensure_ascii=False, indent=2)
    match = re.match(r"^ailooplibrary://loop/(.+)$", uri or "")
    if match:
        loop = catalog.find(match.group(1))
        if loop:
            return json.dumps(loop, ensure_ascii=False, indent=2)
    raise ValueError("Unknown resource URI: %s" % uri)


# ---------------------------------------------------------------------------
# JSON-RPC / MCP plumbing (newline-delimited over stdio)
# ---------------------------------------------------------------------------

def handle_message(catalog, message):
    """Return a JSON-RPC response dict, or None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        requested = params.get("protocolVersion", LATEST_PROTOCOL)
        version = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        return ok({
            "protocolVersion": version,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION,
                           "title": "AI Loop Library"},
            "instructions": (
                "Read-only library of bounded, verifiable agent work loops from "
                "ailooplibrary.com. Typical flow: pick_loop_for_goal(goal) -> "
                "render_run_protocol(id, goal) -> run the protocol with one change per "
                "round, the same verification every round, durable state files, and stop "
                "conditions. Use search_loops/get_loop to browse, list_categories and "
                "catalog_stats for orientation."),
        })
    if method in ("notifications/initialized", "notifications/cancelled",
                  "notifications/roots/list_changed"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOL_DEFINITIONS})
    if method == "tools/call":
        name = params.get("name", "")
        try:
            result = dispatch_tool(catalog, name, params.get("arguments"))
            text = result if isinstance(result, str) else json.dumps(
                result, ensure_ascii=False, indent=2)
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:
            return ok({"content": [{"type": "text", "text": "Error: %s" % exc}],
                       "isError": True})
    if method == "resources/list":
        try:
            return ok({"resources": resource_list(catalog)})
        except Exception as exc:
            return err(-32603, str(exc))
    if method == "resources/templates/list":
        return ok({"resourceTemplates": [{
            "uriTemplate": "ailooplibrary://loop/{id}",
            "name": "Loop spec by id",
            "mimeType": "application/json",
        }]})
    if method == "resources/read":
        try:
            text = resource_read(catalog, params.get("uri"))
            return ok({"contents": [{"uri": params.get("uri"),
                                     "mimeType": "application/json", "text": text}]})
        except Exception as exc:
            return err(-32602, str(exc))
    if method == "prompts/list":
        return ok({"prompts": []})
    if msg_id is None:
        return None  # unknown notification: ignore per spec
    return err(-32601, "Method not found: %s" % method)


def serve():
    catalog = Catalog(allow_network=True)
    log("serving MCP over stdio (newline-delimited JSON-RPC)")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue
        response = handle_message(catalog, message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    log("stdin closed; exiting")


# ---------------------------------------------------------------------------
# Self-test (offline, against the repo's local catalog)
# ---------------------------------------------------------------------------

def self_test():
    failures = []
    total = [0]

    def check(label, condition, detail=""):
        total[0] += 1
        status = "PASS" if condition else "FAIL"
        print("[%s] %s%s" % (status, label, (" — " + detail) if detail and not condition else ""))
        if not condition:
            failures.append(label)

    catalog = Catalog(allow_network=False)
    catalog.load()
    loops = catalog.loops
    check("catalog loads locally (%d loops from %s)" % (len(loops), catalog.source),
          len(loops) > 0)

    result = tool_search_loops(catalog, "ci pipeline slow")
    check("search_loops('ci pipeline slow') returns ranked results",
          result["result_count"] >= 1 and all(
              key in result["results"][0] for key in ("id", "title", "url", "why_matched")),
          json.dumps(result)[:200])

    first_id = loops[0]["id"]
    loop = tool_get_loop(catalog, first_id)
    check("get_loop('%s') returns full spec" % first_id,
          loop["id"] == first_id and loop["verification"] and loop["stop_condition"])
    loop_by_title = tool_get_loop(catalog, loops[0]["title"])
    check("get_loop by title resolves to same loop", loop_by_title["id"] == first_id)

    pick = tool_pick_loop_for_goal(catalog, "speed up CI")
    check("pick_loop_for_goal('speed up CI') recommends with why",
          bool(pick.get("recommendation")) and bool(pick["recommendation"].get("why_this")),
          json.dumps(pick)[:200])
    check("pick_loop_for_goal favors Engineering for a CI goal",
          pick.get("recommendation", {}).get("category") == "Engineering",
          "got %s" % pick.get("recommendation", {}).get("category"))
    check("pick_loop_for_goal declares its heuristic", "heuristic" in pick.get("method", ""))

    protocol = tool_render_run_protocol(catalog, first_id, goal="test goal")
    for needle in ("One change per round", "Stop conditions", "state.json",
                   "Approval boundary", "Paste into Claude Code", "Proof format"):
        check("render_run_protocol includes '%s'" % needle, needle in protocol)
    strict = tool_render_run_protocol(catalog, first_id, risk_posture="strict")
    check("strict posture adds strict note", "Strict posture" in strict)

    categories = tool_list_categories(catalog)
    check("list_categories sums to loop count",
          sum(c["loop_count"] for c in categories["categories"]) == len(loops))

    stats = tool_catalog_stats(catalog)
    check("catalog_stats reports loop_count", stats["loop_count"] == len(loops))

    init = handle_message(catalog, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18"}})
    check("JSON-RPC initialize returns serverInfo",
          init["result"]["serverInfo"]["name"] == SERVER_NAME)
    tools = handle_message(catalog, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    check("JSON-RPC tools/list shows 6 tools", len(tools["result"]["tools"]) == 6)
    call = handle_message(catalog, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "search_loops", "arguments": {"query": "source check"}}})
    check("JSON-RPC tools/call search_loops succeeds",
          call["result"]["isError"] is False and call["result"]["content"])
    read = handle_message(catalog, {
        "jsonrpc": "2.0", "id": 4, "method": "resources/read",
        "params": {"uri": "ailooplibrary://loop/%s" % first_id}})
    check("resources/read loop uri works", "result" in read and read["result"]["contents"])
    bad = handle_message(catalog, {"jsonrpc": "2.0", "id": 5, "method": "nope/nope"})
    check("unknown method returns -32601", bad["error"]["code"] == -32601)

    print("\nself-test: %d checks, %d failed" % (total[0], len(failures)))
    return 1 if failures else 0


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    serve()


if __name__ == "__main__":
    main()
