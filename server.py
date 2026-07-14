#!/usr/bin/env python3
"""AI Loop Library MCP server (stdio, read-only, zero dependencies).

Gives coding agents a library of bounded, verifiable work loops — trigger,
one-change-per-round, same verification every round, durable state, stop
condition, budget, and human approval gates — then packages them so the
CALLING agent (which knows the operator's repo, data, and constraints) can
make the final judgment call.

Tools:
  browse_catalog        the whole catalog as a compact digest (~2k tokens) —
                        one call, then YOU pick against operator context
  search_loops          find loops by job-to-be-done / category / keywords
  get_loop              full loop spec + canonical URL
  pick_loop_for_goal    ranked SHORTLIST for a goal, with confidence — never
                        a single blind recommendation
  render_run_protocol   executable markdown protocol + state-file skeleton
                        (session loops and scheduled-tick business loops)
  critique_loop         lint any loop design against the anti-pattern rubric
  design_loop           scaffold a new loop spec from a stated bottleneck
  list_categories       category counts
  catalog_stats         counts, featured loops, last_updated

Ranking is a transparent lexical heuristic — IDF-weighted keyword overlap with
light stemming and a small documented synonym map (see _score_loop) — not a
model. The calling agent is the best ranker available; these tools exist to
hand it clean, compact evidence. The server is read-only: no write tools, no
auth, no shell execution.

Run:            python3 server.py
Self-test:      python3 server.py --self-test    (offline, embedded sample)
Ranking eval:   python3 server.py --eval         (golden queries vs full catalog)
Catalog source: AI_LOOP_LIBRARY_CATALOG_PATH (local file) or
                AI_LOOP_LIBRARY_CATALOG_URL (default https://ailooplibrary.com/catalog.json),
                falling back to a catalog.json next to this file or the site
                repo's catalog.json / data/loops.json.

Requires Python 3.9+. Newline-delimited JSON-RPC 2.0 over stdio per the MCP spec.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.request

SERVER_NAME = "ai-loop-library"
SERVER_VERSION = "2.0.0"
DEFAULT_CATALOG_URL = "https://ailooplibrary.com/catalog.json"
BASE_URL = "https://ailooplibrary.com"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}
LATEST_PROTOCOL = "2025-06-18"
CACHE_TTL_SECONDS = 300

STOPWORDS = {
    "a", "an", "and", "the", "for", "of", "to", "in", "on", "with", "my",
    "our", "your", "is", "it", "that", "this", "loop", "loops", "ai", "agent",
    "i", "we", "me", "up", "at", "by", "or", "be", "do", "make", "get",
    "so", "as", "are", "was", "will", "can", "want", "need", "into", "from",
}

# Brand/vehicle tokens: they name the assistant running the loop, almost never
# the job to be done. Heavily downweighted (not dropped) so "make my repo
# agent-ready for Claude Code" still works but "cited in ChatGPT answers"
# doesn't get hijacked by a loop with "Claude" in its title.
BRAND_TOKENS = {"claude", "chatgpt", "gpt", "gemini", "copilot", "cursor",
                "codex", "openai", "anthropic", "llm", "llms"}
BRAND_WEIGHT = 0.25

# Small, documented synonym map applied AFTER stemming: query-side stems on
# the left are rewritten to the catalog-vocabulary stem on the right.
SYNONYMS_RAW = {
    "cite": "citation", "cited": "citation", "cites": "citation",
    "citing": "citation",
    "sluggish": "slow", "laggy": "slow",
    "speedup": "speed", "quick": "speed", "quicker": "speed",
    "hallucinate": "hallucination", "hallucinated": "hallucination",
    "hallucinations": "hallucination",
    "fabricated": "hallucination", "madeup": "hallucination",
    "rankings": "rank", "ranking": "rank",
    "clients": "customer", "client": "customer",
    "emails": "email", "inboxes": "inbox",
    "vulnerabilities": "cve", "vulnerability": "cve",
}

# Query expansion: when a query stem appears, add related catalog-vocabulary
# stems at reduced weight so paraphrased goals still reach the right loops.
EXPANSIONS_RAW = {
    "citation": ["geo", "visibility", "answer"],
    "slow": ["speed", "load", "performance", "latency"],
    "hallucination": ["grounded", "source", "claim", "fact"],
    "rank": ["seo", "search", "query"],
    "seo": ["rank", "search"],
    "flaky": ["flake", "test"],
    "crash": ["error", "bug"],
    "revenue": ["customer", "conversion"],
    "followers": ["post", "impressions", "content"],
    "docs": ["documentation", "readme"],
}
EXPANSION_WEIGHT = 0.6

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
               "distribution", "citation", "buyers", "customers", "rank",
               "geo", "ads", "visibility"],
    "Operations": ["ops", "incident", "monitor", "monitoring", "production",
                   "handoff", "queue", "process", "toolchain", "sweep"],
    "Evaluation": ["eval", "evaluation", "regression", "quality", "review",
                   "judge", "rubric", "benchmark", "qa", "browser", "grounded"],
    "Security": ["security", "vulnerability", "vulnerabilities", "secrets",
                 "audit", "permissions", "exposure", "leak", "cve"],
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


# Words the light stemmer would mangle (speed -> spe); leave them whole.
NO_STEM = {"speed", "feed", "need", "seed", "breed", "bleed", "embed",
           "proceed", "exceed", "succeed", "shed", "red", "bed", "thing",
           "nothing", "everything", "something", "during", "being"}


def _stem(token):
    """Light suffix stripping — enough to unify cited/cites, rankings/rank."""
    if token in NO_STEM:
        return token
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("es"):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


SYNONYMS = {_stem(k): _stem(v) for k, v in SYNONYMS_RAW.items()}
EXPANSIONS = {_stem(k): [_stem(x) for x in v] for k, v in EXPANSIONS_RAW.items()}
CATEGORY_HINT_STEMS = {
    cat: {SYNONYMS.get(_stem(w), _stem(w)) for w in words}
    for cat, words in CATEGORY_HINTS.items()
}
BRAND_STEMS = {_stem(t) for t in BRAND_TOKENS}


def tokenize(text):
    out = []
    for raw in re.findall(r"[a-z0-9]+", str(text).lower()):
        if raw in STOPWORDS:
            continue
        stem = _stem(raw)
        out.append(SYNONYMS.get(stem, stem))
    return out


def query_weights(text):
    """Query -> {stem: weight}. Base terms 1.0, brand terms damped,
    expansion terms added at reduced weight."""
    weights = {}
    for term in tokenize(text):
        base = BRAND_WEIGHT if term in BRAND_STEMS else 1.0
        weights[term] = max(weights.get(term, 0.0), base)
    for term in list(weights):
        for extra in EXPANSIONS.get(term, []):
            weights.setdefault(extra, EXPANSION_WEIGHT * weights[term])
    return weights


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

# Template step strings shared by dozens of catalog entries. They carry no
# loop-specific signal, so they are excluded from the search index (the IDF
# weighting would bury them anyway; excluding them keeps why-matched honest).
TEMPLATE_STEPS = {
    "Define the exact scope, source of truth, and approval boundary.",
    "Inspect current state and rank the highest-risk gap.",
    "Make one small, reversible improvement.",
    "Run the stated verification and record evidence.",
    "Stop on success, budget, no progress, or approval required.",
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
    loop = {
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
        "tools": raw.get("tools", []),
        "featured": bool(raw.get("featured")),
        "url": url,
        "state_file_suggestion": raw.get("state_file_suggestion")
        or "docs/loops/%s/progress.md" % loop_id,
    }
    loop["verifier_type"] = _verifier_type(loop)
    loop["loop_kind"] = _loop_kind(loop)
    return loop


def _verifier_type(loop):
    """Classify the verification check. The discourse is right that the
    verifier is the bottleneck — surface its strength as a first-class fact."""
    text = str(loop.get("verification", "")).lower()
    if not text:
        return "unspecified"
    if re.search(r"rubric|judge|scored by", text):
        return "rubric-judged"
    if re.search(r"\d|percent|%|pass|fail|zero |count|rank|p50|p95|duration|"
                 r"green|error rate|uptime|coverage|threshold|command", text):
        return "deterministic-leaning"
    return "review-based"


def _loop_kind(loop):
    """session: run-to-done in one sitting. scheduled-tick: business-style loop
    that acts once per cadence tick and re-measures next tick."""
    text = ("%s %s" % (loop.get("trigger", ""), loop.get("summary", ""))).lower()
    if re.search(r"monthly|weekly|quarterly|nightly|daily|every \d|per (week|month|cycle)|"
                 r"cron|scheduled|each (week|month)|next tick|per cycle", text):
        return "scheduled-tick"
    return "session"


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

# Fields scanned by the ranker, with weights. Template/default strings are
# excluded before indexing (see _indexable_fields).
FIELD_WEIGHTS = [
    ("title", 4.0), ("tags", 3.0), ("use_when", 3.0), ("summary", 2.0),
    ("category", 2.0), ("steps", 1.0), ("verification", 1.0), ("trigger", 0.5),
]


def _indexable_fields(loop):
    fields = {}
    for field, _ in FIELD_WEIGHTS:
        value = loop.get(field, "")
        if field == "steps":
            value = [s for s in value if s not in TEMPLATE_STEPS]
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        if field in DEFAULTS and value == DEFAULTS.get(field):
            value = ""
        fields[field] = value
    return fields


class Catalog(object):
    def __init__(self, allow_network=True):
        self.allow_network = allow_network
        self._loops = []
        self._meta = {}
        self._source = ""
        self._loaded_at = 0.0
        self._idf = {}
        self._field_tokens = {}

    def _repo_candidates(self):
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
        return [os.path.join(here, "catalog.json"),
                os.path.join(root, "catalog.json"),
                os.path.join(root, "data", "loops.json")]

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
        self._build_index()
        log("loaded %d loops from %s" % (len(self._loops), source))

    def _build_index(self):
        """Per-loop tokenized fields + corpus IDF. Tokens that appear in most
        loops (template phrasing like 'stop', 'before') score near zero."""
        self._field_tokens = {}
        doc_freq = {}
        for loop in self._loops:
            fields = {f: set(tokenize(v)) for f, v in _indexable_fields(loop).items()}
            self._field_tokens[loop["id"]] = fields
            seen = set()
            for tokens in fields.values():
                seen |= tokens
            for token in seen:
                doc_freq[token] = doc_freq.get(token, 0) + 1
        n = max(1, len(self._loops))
        self._idf = {t: math.log((n + 1.0) / (df + 0.5)) for t, df in doc_freq.items()}

    def idf(self, token):
        return self._idf.get(token, 0.0)

    def field_tokens(self, loop):
        return self._field_tokens.get(loop["id"], {})

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

RANKING_METHOD = ("transparent lexical heuristic: IDF-weighted keyword overlap "
                  "(light stemming + documented synonym map) across "
                  "title/tags/use_when/summary/category/steps, plus goal-term-to-"
                  "category mapping. Not a model ranking — the calling agent "
                  "makes the final judgment with operator context.")


def _score_loop(catalog, loop, qweights):
    """IDF-weighted keyword-overlap score.
    Returns (score, why: list of match notes, matched: set of query stems)."""
    score = 0.0
    why = []
    matched = set()
    fields = catalog.field_tokens(loop)
    for field, weight in FIELD_WEIGHTS:
        field_tokens = fields.get(field, set())
        hits = sorted(t for t in qweights if t in field_tokens)
        if hits:
            gain = weight * sum(qweights[t] * max(catalog.idf(t), 0.05) for t in hits)
            if gain <= 0.0:
                continue
            score += gain
            matched |= set(hits)
            informative = [t for t in hits if catalog.idf(t) >= 0.7]
            if informative:
                why.append("%s matches %s" % (field.replace("_", " "),
                                              ", ".join(informative[:4])))
    return score, why, matched


def _category_boost(loop, qweights):
    hints = CATEGORY_HINT_STEMS.get(loop.get("category", ""), set())
    hits = sorted(t for t in qweights if t in hints and qweights[t] >= 1.0)
    if hits:
        return 1.5 * len(hits), "goal terms (%s) map to the %s category" % (
            ", ".join(hits), loop["category"])
    return 0.0, None


def _richness_bonus(loop):
    bonus = 0.0
    for field in ("verification", "stop_condition", "prompt", "use_when"):
        if loop.get(field):
            bonus += 0.1
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


def _rank(catalog, text):
    """Shared ranking core. Returns [(score, loop, why, matched)] sorted."""
    qweights = query_weights(text)
    scored = []
    for loop in catalog.loops:
        base, why, matched = _score_loop(catalog, loop, qweights)
        boost, boost_why = _category_boost(loop, qweights)
        if boost_why:
            why.append(boost_why)
        if base + boost > 0:
            scored.append((base + boost + _richness_bonus(loop), loop, why, matched))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return scored, qweights


def _confidence(scored, qweights):
    """Honest signal about how much to trust the lexical ranking."""
    if not scored:
        return "none", "no lexical match at all — browse_catalog and judge yourself"
    base_terms = {t for t, w in qweights.items() if w >= 1.0}
    top_score, _, _, top_matched = scored[0]
    coverage = (len(top_matched & base_terms) / len(base_terms)) if base_terms else 0.0
    margin = top_score / scored[1][0] if len(scored) > 1 and scored[1][0] > 0 else 2.0
    if coverage >= 0.5 and margin >= 1.35:
        return "high", "top match covers most goal terms and leads clearly"
    if coverage < 0.25 or margin < 1.1:
        return "low", ("weak term coverage or near-tie between candidates — "
                       "treat this as a browsing aid, not an answer")
    return "medium", "reasonable match, but compare the shortlist before committing"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def tool_search_loops(catalog, query, category=None, limit=8):
    terms = query_weights(query)
    if not terms:
        return {"results": [], "note": "Query contained no searchable terms."}
    limit = max(1, min(int(limit or 8), 25))
    scored, _ = _rank(catalog, query)
    results = []
    for score, loop, why, _matched in scored:
        if category and loop["category"].lower() != str(category).lower():
            continue
        entry = _brief(loop)
        entry["why_matched"] = "; ".join(why[:3])
        results.append(entry)
        if len(results) >= limit:
            break
    return {
        "query": query,
        "category_filter": category,
        "result_count": len(results),
        "results": results,
        "method": RANKING_METHOD,
    }


def tool_get_loop(catalog, id_or_slug):
    loop = catalog.find(id_or_slug)
    if loop is None:
        known = ", ".join(l["id"] for l in catalog.loops[:10])
        raise ValueError("No loop matches %r. Try search_loops first. "
                         "Example ids: %s…" % (id_or_slug, known))
    return loop


def tool_browse_catalog(catalog, category=None):
    """The whole catalog as a compact digest the calling agent can judge from
    directly — the highest-signal single call this server offers."""
    lines = []
    count = 0
    for loop in catalog.loops:
        if category and loop["category"].lower() != str(category).lower():
            continue
        count += 1
        use_when = (loop["use_when"] or loop["summary"]).strip()
        if len(use_when) > 150:
            use_when = use_when[:147].rstrip() + "…"
        lines.append("- %s · %s/%s · %s · verifier: %s%s" % (
            loop["id"], loop["category"], loop["difficulty"] or "?",
            use_when, loop["verifier_type"],
            " · tick-based" if loop["loop_kind"] == "scheduled-tick" else ""))
    header = (
        "AI Loop Library catalog digest — %d loop%s%s. You know the operator's "
        "repo, data, constraints, and recurring pain; this list doesn't. Scan it "
        "against that context and pick yourself (skip loops whose job the operator "
        "already has covered). Then: get_loop(id) for the full spec, "
        "render_run_protocol(id, goal) for the executable protocol. Canonical "
        "pages: %s/loops/<id>/\n"
        % (count, "" if count == 1 else "s",
           " in category %s" % category if category else "", BASE_URL))
    return header + "\n".join(lines)


def tool_pick_loop_for_goal(catalog, goal, constraints=None, limit=5):
    text = goal + (" " + constraints if constraints else "")
    scored, qweights = _rank(catalog, text)
    if not qweights:
        raise ValueError("Goal contained no searchable terms; state the job to be done.")
    limit = max(1, min(int(limit or 5), 8))
    confidence, confidence_note = _confidence(scored, qweights)
    if not scored:
        return {
            "goal": goal,
            "shortlist": [],
            "confidence": confidence,
            "confidence_note": confidence_note,
            "note": ("No loop matched lexically. Call browse_catalog and judge "
                     "against operator context, or design_loop(goal=...) to "
                     "scaffold a new loop instead."),
        }
    shortlist = []
    for score, loop, why, _matched in scored[:limit]:
        entry = _brief(loop)
        entry["use_when"] = loop["use_when"]
        entry["verification"] = loop["verification"]
        entry["verifier_type"] = loop["verifier_type"]
        entry["loop_kind"] = loop["loop_kind"]
        entry["lexical_score"] = round(score, 2)
        entry["why_matched"] = "; ".join(why[:4])
        shortlist.append(entry)
    return {
        "goal": goal,
        "constraints": constraints,
        "confidence": confidence,
        "confidence_note": confidence_note,
        "shortlist": shortlist,
        "your_call": ("This is a lexically ranked shortlist, not a verdict. You "
                      "have operator context this server lacks — pick the loop "
                      "whose use_when matches the operator's actual situation, "
                      "or none if the job is already covered. If nothing fits, "
                      "design_loop(goal=...) scaffolds a new one."),
        "next_step": "render_run_protocol(id_or_slug=<your pick>, goal=%r)" % goal,
        "method": RANKING_METHOD,
    }


RISK_POSTURES = ("default", "strict")
PROTOCOL_KINDS = ("auto", "session", "scheduled-tick")


def tool_render_run_protocol(catalog, id_or_slug, goal=None, risk_posture="default",
                             kind="auto", max_rounds=8, max_minutes=45):
    loop = tool_get_loop(catalog, id_or_slug)
    posture = (risk_posture or "default").lower()
    if posture not in RISK_POSTURES:
        raise ValueError("risk_posture must be one of %s" % (RISK_POSTURES,))
    kind = (kind or "auto").lower()
    if kind not in PROTOCOL_KINDS:
        raise ValueError("kind must be one of %s" % (PROTOCOL_KINDS,))
    if kind == "auto":
        kind = loop["loop_kind"]
    max_rounds = max(1, min(int(max_rounds or 8), 50))
    max_minutes = max(5, min(int(max_minutes or 45), 480))
    tick = kind == "scheduled-tick"
    unit = "tick" if tick else "round"
    state_dir = "docs/loops/%s" % loop["id"]
    state_json = json.dumps({
        "loop_id": loop["id"],
        "goal": goal or loop["summary"],
        "kind": kind,
        unit: 0,
        "status": "not_started",
        "verification_command_or_check": loop["verification"],
        ("ticks" if tick else "rounds"): [],
        "budget": ({"max_ticks_without_progress": 2, "spend_cap": "set before starting"}
                   if tick else
                   {"max_rounds": max_rounds, "max_minutes": max_minutes,
                    "max_failed_verifications_in_a_row": 3}),
        "stopped_because": None,
    }, indent=2)
    steps = "\n".join("- %s" % step for step in loop["steps"]) or "- (see canonical page)"
    goal_line = "\n**Operator goal:** %s" % goal if goal else ""
    strict_note = ("\n> **Strict posture:** treat every yellow action as red — pause and get "
                   "explicit human approval before each round that touches anything shared."
                   if posture == "strict" else "")
    tools_line = ""
    if loop.get("tools"):
        tools_line = "\n## Tools & access required\n\n%s\n" % "\n".join(
            "- %s" % t for t in loop["tools"])

    if tick:
        rules = """## Loop rules (scheduled-tick loop)

1. **One experiment per tick.** Baseline the metric, make one coherent, reversible
   change with a written hypothesis, then WAIT for the next tick. No batching.
2. **Same metric every tick:** %(verification)s
3. **On each tick:** re-read the same metric, mark the last experiment
   win / flat / loss in the experiment log, **undo losers**, then choose the next
   single experiment.
4. **Notify the human after every tick** with a one-screen summary: metric
   movement, what changed, what's next. Do not run silent for months.
5. Do not widen scope mid-loop. New surfaces become a new loop.""" % {
            "verification": loop["verification"]}
        state_block = """- Experiment log: `%(state_dir)s/experiment-log.md` — per tick: date, baseline
  numbers, the one change + hypothesis, result at next tick (win/flat/loss),
  undo decision.
- Progress log: `%(state_dir)s/progress.md` — narrative notes.
- Machine state: `%(state_dir)s/state.json` skeleton:""" % {"state_dir": state_dir}
        stops = """1. The target metric is met with evidence (e.g. the listed queries reach the goal).
2. **Two consecutive ticks with no progress** — report and pause for a human decision.
3. Spend/budget cap reached (set a per-tick and total cap before starting;
   loop guidance: %(budget)s).
4. A blocker appears: missing access, credentials, or an ambiguous requirement.
5. The next action crosses the approval boundary below.""" % {"budget": loop["budget"]}
        paste_prompt = (
            "Run the \"%s\" loop from AI Loop Library (%s) as a scheduled-tick loop.\n"
            "Goal: %s\n"
            "Rules: one reversible experiment per tick with a written hypothesis; "
            "re-measure the same metric next tick (%s); log win/flat/loss in "
            "%s/experiment-log.md and undo losers; update %s/state.json; notify the "
            "human after every tick; stop on target met, 2 no-progress ticks, budget, "
            "a blocker, or anything needing approval (money, production, outbound, "
            "deletion). Each tick ends with: metric movement, the one change made, "
            "and the next human decision."
            % (loop["title"], loop["url"], goal or loop["summary"], loop["verification"],
               state_dir, state_dir))
    else:
        rules = """## Loop rules

1. **One change per round.** Make one coherent, bounded move, then verify. No batching.
2. **Same verification every round:** %(verification)s
3. **Record state after every round** before deciding to continue.
4. Do not widen scope mid-loop. New work becomes a new loop, not round %(next)s of this one.""" % {
            "verification": loop["verification"], "next": max_rounds + 1}
        state_block = """- Progress log: `%(state_dir)s/progress.md` — per round: round number, the one change,
  verification result, evidence (command output, diff, screenshot path).
- Machine state: `%(state_dir)s/state.json` skeleton:""" % {"state_dir": state_dir}
        stops = """1. The verifier passes with evidence.
2. Budget exhausted: %(max_rounds)d rounds, %(max_minutes)d minutes, or 3 consecutive
   failed verifications. (Adjust before starting. Loop guidance: %(budget)s)
3. The same failure repeats after two different fixes (no progress).
4. A blocker appears: missing access, credentials, or an ambiguous requirement.
5. The next action crosses the approval boundary below.""" % {
            "max_rounds": max_rounds, "max_minutes": max_minutes, "budget": loop["budget"]}
        paste_prompt = (
            "Run the \"%s\" loop from AI Loop Library (%s) as a bounded loop.\n"
            "Goal: %s\n"
            "Rules: one change per round; run the same verification every round (%s); "
            "append each round to %s/progress.md and update %s/state.json; "
            "stop on verifier pass, %d rounds, 3 consecutive failed verifications, no progress, "
            "a blocker, or anything needing human approval (money, production, outbound, deletion). "
            "Finish with a proof report: rounds used, changes made, verification output, "
            "remaining risk, and the next human decision."
            % (loop["title"], loop["url"], goal or loop["summary"], loop["verification"],
               state_dir, state_dir, max_rounds))

    protocol = """# Run protocol — %(title)s

Canonical spec: %(url)s
Category: %(category)s · Trigger: %(trigger)s · Kind: %(kind)s · Verifier: %(verifier_type)s · Risk posture: %(posture)s
Rendered by the AI Loop Library MCP server (template + catalog data, read-only).

## Objective / done contract

%(summary)s%(goal_line)s

Done means: the verification below passes, evidence is recorded in the state file,
and nothing crossed the approval boundary without a human.

## Allowed actions

%(steps)s
%(tools_line)s
%(rules)s

## State (durable, survives the session)

%(state_block)s

```json
%(state_json)s
```

## Stop conditions

Stop — and report — when any of these is true:
%(stops)s

## Approval boundary (risk colors)

- **Green — proceed:** local edits, drafts, analysis, reading, tests in a sandbox.
- **Yellow — pause and show a human:** public-facing drafts, PRs to shared branches,
  config or schema changes, anything a teammate will see before you do.
- **Red — never without explicit approval:** money, production, outbound messages,
  deletion, account changes, legal/reputational commitments.

%(approval_boundary)s%(strict_note)s

## Proof format

Final report must include: %(units)s used, one-line change log per %(unit)s, the verification
output for the final %(unit)s, remaining risks or open questions, and the single next
human decision (approve / redirect / stop).

## Paste into Claude Code

```
%(paste_prompt)s
```
""" % {
        "title": loop["title"], "url": loop["url"], "category": loop["category"],
        "trigger": loop["trigger"], "kind": kind, "verifier_type": loop["verifier_type"],
        "posture": posture, "summary": loop["summary"],
        "goal_line": goal_line, "steps": steps, "tools_line": tools_line,
        "rules": rules, "state_block": state_block, "state_json": state_json,
        "stops": stops, "approval_boundary": loop["approval_boundary"],
        "strict_note": strict_note, "paste_prompt": paste_prompt,
        "unit": unit, "units": unit + "s",
    }
    return protocol


# ---------------------------------------------------------------------------
# critique_loop — deterministic lint against the anti-pattern rubric
# ---------------------------------------------------------------------------

VIBES_ONLY = r"looks good|feels (good|right|better)|high quality|until (it'?s|it is) (good|great|better|done)"
RISKY_SURFACE = (r"money|spend|billing|payment|ads?\b|production|prod\b|deploy|"
                 r"delete|outbound|send(ing)? (email|dm|message)|publish|post(ing)? to|"
                 r"customer[- ]facing|account (change|settings)")
GATE_WORDS = r"approval|approve|human|gate|review before|confirm|sign[- ]?off|draft(s)? (for|only)|pause"
VANITY = r"100k|100,000|million|\b1m\b|go(es|ing)? viral|blow (up|it up)|famous|10x (followers|traffic)\b"

CRITIQUE_CHECKS = [
    ("verifier", "Has a verification check",
     r"verif|check|test|metric|measure|eval|rank|score|pass|ci\b|search console|analytics",
     "Add a verifier: a command, metric, or check that says pass/fail. Without one, "
     "the loop is a token bonfire — the model grades itself forever."),
    ("deterministic_verifier", "Verifier is objective, not vibes",
     None,  # special-cased below
     "Replace subjective judgment ('looks good') with an observable check: tests pass, "
     "rank moved, p95 under threshold, zero critical errors — or an LLM judge with a "
     "written rubric, scale, and max iterations."),
    ("stop_condition", "Has an explicit stop condition",
     r"stop|until|done when|finish(es|ed)? when|exit|complete when|target (is )?(met|hit)|page one",
     "Write the stop condition before the first run: success evidence, N no-progress "
     "rounds, budget, blocker, or human gate."),
    ("budget", "Has a budget/cap",
     r"budget|cap\b|max(imum)? \d|\d+ (rounds?|minutes?|hours?|ticks?|tokens?|dollars?|iterations?|retries)|spend limit|\$\d",
     "Cap iterations, runtime, tokens, or spend. 'Clone Excel' with no budget runs for days."),
    ("one_change", "One change per round",
     r"one (change|experiment|fix|improvement|thing|bottleneck|page|cluster)( per| each| at a time)?|single (change|experiment|reversible)|smallest (reversible|possible)",
     "Make one attributable change per round — otherwise you can't tell which change "
     "moved the metric, and you can't undo losers."),
    ("state", "Keeps durable state",
     r"state file|progress\.md|experiment log|ledger|journal|log (file|results|each)|record (state|results|evidence)|markdown file|state\.json",
     "Add a state file (progress.md / experiment-log.md) the loop reads and writes every "
     "round, so work survives the session."),
    ("same_check", "Same check every round",
     r"same (check|verification|metric|test|conditions)|every (round|tick|cycle)|each (round|tick|cycle)|re-?measure|re-?run",
     "Run the identical verification every round under the same conditions — otherwise "
     "results aren't comparable across rounds."),
    ("mvl", "Objective is a micro-metric, not vanity scale",
     None,  # special-cased below
     "Start with a Minimal Viable Loop: '10 likes per post' or 'one query onto page one', "
     "not '100k followers'. Expand only after the small loop works."),
    ("risk_gate", "Risky surfaces are human-gated",
     None,  # special-cased below
     "This loop touches money, production, or outbound surfaces — add an explicit human "
     "approval gate (red actions never run unattended)."),
    ("trigger", "Has a defined trigger/cadence",
     r"daily|weekly|monthly|nightly|quarterly|every |cron|schedule|on (pr|push|deploy|error|merge)|when .{0,40}(happens|opened|fails|spikes)|manual(ly)? (start|trigger|run)|tick",
     "Name the trigger: manual, scheduled (nightly/monthly), or event-driven (PR opened, "
     "error spike). 'When I feel like it' is not a trigger."),
]


def tool_critique_loop(catalog, loop_description):
    text = str(loop_description or "").strip()
    if len(text) < 20:
        raise ValueError("Describe the loop in a sentence or three (objective, trigger, "
                         "action, verification, stop) so there is something to lint.")
    low = text.lower()
    has_numbers = bool(re.search(r"\d|percent|%", low))
    findings = []
    passes = 0
    for check_id, label, pattern, fix in CRITIQUE_CHECKS:
        if check_id == "deterministic_verifier":
            vibes = bool(re.search(VIBES_ONLY, low))
            passed = has_numbers and not vibes or bool(
                re.search(r"tests? pass|ci (is )?green|rubric", low))
        elif check_id == "mvl":
            passed = not re.search(VANITY, low)
        elif check_id == "risk_gate":
            risky = bool(re.search(RISKY_SURFACE, low))
            passed = (not risky) or bool(re.search(GATE_WORDS, low))
        else:
            passed = bool(re.search(pattern, low))
        if passed:
            passes += 1
        findings.append({
            "check": check_id,
            "label": label,
            "status": "pass" if passed else "missing",
            "fix": None if passed else fix,
        })
    score = "%d/10" % passes
    if passes >= 8:
        grade = "solid — bounded and verifiable"
    elif passes >= 5:
        grade = "workable, but it will thrash without the missing pieces"
    else:
        grade = "likely to thrash — this is loopmaxxing, not loop engineering"
    related = tool_search_loops(catalog, text, limit=3)["results"]
    return {
        "score": score,
        "grade": grade,
        "verdict": "Loop lint: %s — %s. Rubric: %s/for-agents/" % (score, grade, BASE_URL),
        "findings": findings,
        "related_catalog_loops": related,
        "method": ("deterministic lint against the AI Loop Library anti-pattern rubric "
                   "(trigger, objective verifier, one-change-per-round, state, stop, "
                   "budget, MVL, risk gates). Text matching, not a model — treat "
                   "'missing' as 'not stated', and re-run after stating it."),
    }


# ---------------------------------------------------------------------------
# design_loop — scaffold a new loop spec from a stated bottleneck
# ---------------------------------------------------------------------------

VERIFIER_SUGGESTIONS = [
    (r"seo|rank|geo|citation|visibility|search",
     "Search Console (or SEO API) position/impressions/clicks for 3–10 listed target "
     "queries, re-read on the same cadence; win/flat/loss vs prior baseline"),
    (r"ads?|cpa|roas|facebook|campaign",
     "CPA/ROAS per variant at a fixed spend threshold; kill losers, scale winners; "
     "hard spend cap per cycle"),
    (r"ci\b|pipeline|build",
     "CI p50/p95 duration for the same workflow, before vs after each change, "
     "without weakening tests"),
    (r"test|flake|flaky|coverage",
     "Pass rate / flake count over N consecutive runs of the same suite"),
    (r"error|crash|incident|uptime|sentry",
     "Count of critical production errors (or uptime %) over the last 24h window"),
    (r"content|publish|blog|post|newsletter|claims|fact",
     "Every checkable claim maps to a source or an explicit flag; zero unsourced "
     "claims at the publish gate"),
    (r"email|inbox|support|refund|ticket",
     "Queue size / median response time / % handled with an explicit decision logged"),
    (r"slow|speed|load|performance|latency",
     "p95 load time (or first-load bytes) for the same pages under fixed conditions"),
]


def tool_design_loop(catalog, goal, constraints=None, cadence=None, context=None):
    goal = str(goal or "").strip()
    if len(goal) < 8:
        raise ValueError("State the bottleneck or outcome in plain words, e.g. "
                         "'support inbox backlog keeps growing'.")
    slug = slugify(goal)[:48].strip("-") or "new-loop"
    low = " ".join(filter(None, [goal, constraints or "", context or ""])).lower()
    verifier = "TODO: one command or metric, checked the same way every round"
    for pattern, suggestion in VERIFIER_SUGGESTIONS:
        if re.search(pattern, low):
            verifier = suggestion
            break
    trigger = (cadence.strip() if cadence else
               "Manual first. Schedule (e.g. nightly/monthly) only after one clean manual tick.")
    vanity = bool(re.search(VANITY, low))
    mvl_note = ("Your goal reads as vanity-scale. Shrink it: pick the micro-metric a "
                "single tick can move (one query onto page one, 10 likes per post, one "
                "flaky test stabilized) and expand only after the small loop works."
                if vanity else
                "Keep the first version small (Minimal Viable Loop): one surface, one "
                "metric a single tick can move. Expand after it works.")
    related = tool_search_loops(catalog, goal, limit=3)["results"]
    related_block = "\n".join(
        "- %s — %s (%s)" % (r["title"], r["summary"], r["url"]) for r in related
    ) or "- (no close catalog matches — this may genuinely be a new loop)"
    constraints_line = "\n**Operator constraints:** %s" % constraints if constraints else ""
    context_line = "\n**Context:** %s" % context if context else ""

    draft = """# Draft loop spec — %(goal)s

Scaffolded by the AI Loop Library MCP server from the operator's stated bottleneck.
Fill every TODO before the first run; a loop with fuzzy fields thrashes.%(constraints_line)s%(context_line)s

| Field | Draft |
|---|---|
| **Objective / done** | %(goal)s — TODO: restate with an observable proof ("done means X is true, shown by Y") |
| **Trigger** | %(trigger)s |
| **Scope** | TODO: exact files/pages/surfaces this loop may touch — and what it must never touch |
| **One-round action** | One reversible change per round, with a written hypothesis when the verifier is a business metric |
| **Verification** | %(verifier)s |
| **State** | `docs/loops/%(slug)s/progress.md` (+ `experiment-log.md` if metric ticks are slow) |
| **Stop conditions** | Success evidence · 2 rounds/ticks with no progress · budget hit · blocker · human gate |
| **Budget** | TODO: max rounds/ticks, max minutes, spend cap — set BEFORE the first run |
| **Risk colors** | Green: local/read/draft · Yellow: shared surfaces, pause and show a human · Red: money/production/outbound/deletion — never without approval |
| **Safe output** | Branch/PR/report/notification — not silent mutation |

**MVL check:** %(mvl_note)s

## Nearest existing loops (steal their structure before inventing)

%(related_block)s

## Next steps

1. Fill the TODOs, then run `critique_loop` on the completed spec — target 8/10+.
2. Dry-run one round manually and inspect the state file before scheduling anything.
3. If an existing loop above already fits, prefer `render_run_protocol(id, goal)` over
   a custom design.
""" % {
        "goal": goal, "constraints_line": constraints_line, "context_line": context_line,
        "trigger": trigger, "verifier": verifier, "slug": slug,
        "mvl_note": mvl_note, "related_block": related_block,
    }
    return draft


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


READ_ONLY_ANNOTATIONS = {"readOnlyHint": True, "idempotentHint": True}

TOOL_DEFINITIONS = [
    {
        "name": "browse_catalog",
        "description": (
            "The entire AI Loop Library as a compact digest (~2k tokens): every loop's "
            "id, category, one-line use_when, and verifier strength. This is the "
            "highest-signal single call here — you know the operator's repo, data, and "
            "recurring pain, so scan the digest against that context and make the pick "
            "yourself. Follow with get_loop for depth or render_run_protocol to run one."),
        "annotations": READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": ["string", "null"], "description": "Optional exact category filter, e.g. Engineering, Growth"},
            },
        },
    },
    {
        "name": "search_loops",
        "description": (
            "Search AI Loop Library's bounded, verifiable work loops by job-to-be-done, "
            "keyword, or category. Each loop ships with a trigger, one-change-per-round "
            "discipline, a verification check, a stop condition, a budget, and a human "
            "approval boundary — so an agent can run it without thrashing. Returns ranked "
            "matches with a one-line why-matched."),
        "annotations": READ_ONLY_ANNOTATIONS,
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
            "verification (+ verifier strength), stop condition, budget, approval boundary, "
            "copyable prompt, and canonical URL. Use after browse_catalog or search_loops."),
        "annotations": READ_ONLY_ANNOTATIONS,
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
            "Shortlist the best-matching loops for a stated goal (lexical ranking with an "
            "honest confidence signal) — you make the final call. Returns 5 candidates "
            "with use_when, verification, and why-matched so you can judge against "
            "operator context the server can't see. When confidence is low, trust your "
            "own read of browse_catalog over this ranking. Follow with "
            "render_run_protocol for the executable version."),
        "annotations": READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The outcome the operator wants, in plain words"},
                "constraints": {"type": ["string", "null"], "description": "Optional constraints, e.g. 'read-only', 'no production access'"},
                "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 8},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "render_run_protocol",
        "description": (
            "Render a loop as an executable markdown run protocol an agent can follow "
            "directly: objective/done contract, allowed actions, one-change-per-round rule, "
            "the same verification every round, a durable state-file skeleton, stop "
            "conditions, budget, risk-colored approval boundary, proof format, and a "
            "paste-ready Claude Code prompt. Session loops get bounded rounds; "
            "scheduled-tick business loops (SEO, ads, product metrics) get experiment "
            "logs, undo-losers discipline, and notify-the-human ticks."),
        "annotations": READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_or_slug": {"type": "string", "description": "Loop id or title"},
                "goal": {"type": ["string", "null"], "description": "Optional operator goal to embed in the protocol"},
                "risk_posture": {"type": "string", "enum": ["default", "strict"], "default": "default",
                                 "description": "strict treats every shared-surface action as approval-gated"},
                "kind": {"type": "string", "enum": ["auto", "session", "scheduled-tick"], "default": "auto",
                         "description": "auto infers from the loop's trigger; override to force a session or tick protocol"},
                "max_rounds": {"type": "integer", "default": 8, "minimum": 1, "maximum": 50,
                               "description": "Session loops: round budget"},
                "max_minutes": {"type": "integer", "default": 45, "minimum": 5, "maximum": 480,
                                "description": "Session loops: time budget"},
            },
            "required": ["id_or_slug"],
        },
    },
    {
        "name": "critique_loop",
        "description": (
            "Lint any loop design — the operator's own, or one you drafted — against the "
            "AI Loop Library anti-pattern rubric: verifier present and objective, stop "
            "condition, budget, one-change-per-round, durable state, same-check-every-"
            "round, micro-metric objective (MVL), human gates on risky surfaces, defined "
            "trigger. Returns a 0–10 score, per-check fixes, and related catalog loops. "
            "Deterministic text lint, not a model — 'missing' means 'not stated'."),
        "annotations": READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "loop_description": {"type": "string", "description": "The loop design in plain words: objective, trigger, action, verification, stop condition, budget, risk handling"},
            },
            "required": ["loop_description"],
        },
    },
    {
        "name": "design_loop",
        "description": (
            "Scaffold a NEW loop spec from a stated bottleneck when no catalog loop fits: "
            "returns a draft with every required field (trigger, scope, one-round action, "
            "suggested verifier for the domain, state files, stop conditions, budget, risk "
            "colors), an MVL sanity check on the objective, and the nearest existing loops "
            "to steal structure from. Follow with critique_loop on the completed draft."),
        "annotations": READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The bottleneck or outcome, in plain words"},
                "constraints": {"type": ["string", "null"], "description": "Optional constraints, e.g. 'read-only', 'no budget for APIs'"},
                "cadence": {"type": ["string", "null"], "description": "Optional trigger/cadence, e.g. 'nightly', 'monthly tick'"},
                "context": {"type": ["string", "null"], "description": "Optional operator context: stack, data sources, what already exists"},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "list_categories",
        "description": "List loop categories with live counts and library filter URLs.",
        "annotations": READ_ONLY_ANNOTATIONS,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "catalog_stats",
        "description": ("Catalog overview: loop count, categories, featured loops, "
                        "last_updated, and where the catalog was loaded from."),
        "annotations": READ_ONLY_ANNOTATIONS,
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def dispatch_tool(catalog, name, arguments):
    arguments = arguments or {}
    if name == "browse_catalog":
        return tool_browse_catalog(catalog, arguments.get("category"))
    if name == "search_loops":
        return tool_search_loops(catalog, arguments.get("query", ""),
                                 arguments.get("category"), arguments.get("limit", 8))
    if name == "get_loop":
        return tool_get_loop(catalog, arguments.get("id_or_slug", ""))
    if name == "pick_loop_for_goal":
        return tool_pick_loop_for_goal(catalog, arguments.get("goal", ""),
                                       arguments.get("constraints"), arguments.get("limit", 5))
    if name == "render_run_protocol":
        return tool_render_run_protocol(
            catalog, arguments.get("id_or_slug", ""), arguments.get("goal"),
            arguments.get("risk_posture", "default"), arguments.get("kind", "auto"),
            arguments.get("max_rounds", 8), arguments.get("max_minutes", 45))
    if name == "critique_loop":
        return tool_critique_loop(catalog, arguments.get("loop_description", ""))
    if name == "design_loop":
        return tool_design_loop(catalog, arguments.get("goal", ""),
                                arguments.get("constraints"), arguments.get("cadence"),
                                arguments.get("context"))
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
                "ailooplibrary.com. Start with browse_catalog (~2k-token digest of "
                "every loop) and judge against the operator's actual context — you "
                "are the ranker; the server only packages. pick_loop_for_goal gives "
                "a lexical shortlist with a confidence signal when you want one. "
                "render_run_protocol(id, goal) turns a loop into an executable "
                "protocol (one change per round, same verification, durable state, "
                "stop conditions). critique_loop lints any loop design against the "
                "anti-pattern rubric; design_loop scaffolds a new spec when nothing "
                "in the catalog fits."),
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
# Golden-query ranking eval (runs against the full catalog)
# ---------------------------------------------------------------------------

# Each entry: operator-phrased goal -> acceptable loop ids (any in top-3 passes).
# The first three are the confirmed pre-2.0 ranking failures; keep them green.
GOLDEN_QUERIES = [
    ("get my site cited more often in ChatGPT and Claude answers",
     ["seo-geo-visibility", "seo-monthly-rank-experiment"]),
    ("my app feels sluggish and customers complain it takes forever to load",
     ["cold-load-trim"]),
    ("stop hallucinated facts before publishing",
     ["prepublish-source-check", "rag-grounding-audit"]),
    ("speed up CI", ["ci-optimization"]),
    ("our test suite is flaky and randomly fails",
     ["test-flake-stabilizer"]),
    ("triage production errors every night",
     ["production-error-sweep"]),
    ("keep the docs in sync with what the code actually does",
     ["project-docs-freshness"]),
    ("improve our Google rankings month over month",
     ["seo-monthly-rank-experiment", "seo-geo-visibility"]),
    ("run ad copy experiments and kill the losers",
     ["paid-ads-copy-variant-loop"]),
    ("turn user feedback into product changes we can measure",
     ["product-feedback-to-measure-loop"]),
    ("audit whether our RAG answers are grounded in the right sources",
     ["rag-grounding-audit"]),
    ("make this repo ready for coding agents",
     ["claude-code-repo-readiness", "fresh-clone-onboarding"]),
    ("burn down the known vulnerabilities in our dependencies",
     ["dependency-cve-burndown"]),
    ("publish one good post every week",
     ["one-post-a-week"]),
    ("talk to customers to figure out what to build next",
     ["talk-to-five-buyers"]),
    ("stop prompt tweaks from regressing the assistant",
     ["prompt-regression-suite"]),
    ("agents keep losing context between sessions",
     ["agent-handoff-continuity", "memory-bank-continuity",
      "agent-instructions-after-action"]),
    ("shrink integration permissions to least privilege",
     ["permission-scope-minimizer"]),
    ("mine meeting transcripts for action items",
     ["meeting-transcript-action-miner"]),
    ("reconstruct what actually happened during the incident",
     ["incident-timeline-reconstruction"]),
]

EVAL_PASS_THRESHOLD = 0.85


def run_eval():
    catalog = Catalog(allow_network=True)
    catalog.load()
    if len(catalog.loops) < 10:
        print("eval needs the full catalog (got %d loops from %s); set "
              "AI_LOOP_LIBRARY_CATALOG_PATH or _URL" % (len(catalog.loops), catalog.source))
        return 1
    passes = 0
    failures = []
    for goal, expected in GOLDEN_QUERIES:
        result = tool_pick_loop_for_goal(catalog, goal)
        top3 = [e["id"] for e in result.get("shortlist", [])[:3]]
        hit = any(e in top3 for e in expected)
        if hit:
            passes += 1
        else:
            failures.append((goal, expected, top3))
        print("[%s] %r -> top3 %s (want any of %s)" % (
            "PASS" if hit else "FAIL", goal[:58], top3, expected))
    rate = passes / float(len(GOLDEN_QUERIES))
    print("\neval: %d/%d golden queries in top-3 (%.0f%%; threshold %.0f%%; catalog: %s)"
          % (passes, len(GOLDEN_QUERIES), rate * 100, EVAL_PASS_THRESHOLD * 100,
             catalog.source))
    for goal, expected, top3 in failures:
        print("  FAIL: %r wanted %s got %s" % (goal, expected, top3))
    return 0 if rate >= EVAL_PASS_THRESHOLD else 1


# ---------------------------------------------------------------------------
# Self-test (offline, against the embedded sample)
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

    check("stemming unifies cited/citations",
          tokenize("cited")[0] == tokenize("citations")[0] == "citation")
    check("synonym map sends sluggish to slow", tokenize("sluggish") == ["slow"])
    check("brand tokens are damped in queries",
          query_weights("claude tests").get(_stem("claude"), 1.0) < 0.5)
    check("expansion adds related stems at reduced weight",
          0 < query_weights("hallucinated").get("source", 0) < 1.0)

    result = tool_search_loops(catalog, "ci pipeline slow")
    check("search_loops('ci pipeline slow') returns ranked results",
          result["result_count"] >= 1 and all(
              key in result["results"][0] for key in ("id", "title", "url", "why_matched")),
          json.dumps(result)[:200])

    first_id = loops[0]["id"]
    loop = tool_get_loop(catalog, first_id)
    check("get_loop('%s') returns full spec" % first_id,
          loop["id"] == first_id and loop["verification"] and loop["stop_condition"])
    check("get_loop classifies the verifier", loop["verifier_type"] in
          ("deterministic-leaning", "rubric-judged", "review-based", "unspecified"))
    loop_by_title = tool_get_loop(catalog, loops[0]["title"])
    check("get_loop by title resolves to same loop", loop_by_title["id"] == first_id)

    digest = tool_browse_catalog(catalog)
    check("browse_catalog lists every loop with verifier tag",
          all(l["id"] in digest for l in loops) and "verifier:" in digest)

    pick = tool_pick_loop_for_goal(catalog, "speed up CI")
    check("pick_loop_for_goal returns a shortlist, not a verdict",
          isinstance(pick.get("shortlist"), list) and "recommendation" not in pick)
    check("pick_loop_for_goal shortlist entries carry use_when + verification",
          all(("use_when" in e and "verification" in e) for e in pick["shortlist"]))
    check("pick_loop_for_goal declares confidence",
          pick.get("confidence") in ("high", "medium", "low", "none"))
    check("pick_loop_for_goal ranks CI loop first for a CI goal",
          pick["shortlist"][0]["id"] == "ci-optimization",
          json.dumps(pick["shortlist"][:1])[:200])
    check("pick_loop_for_goal declares its heuristic", "heuristic" in pick.get("method", ""))

    protocol = tool_render_run_protocol(catalog, first_id, goal="test goal")
    for needle in ("Stop conditions", "state.json",
                   "Approval boundary", "Paste into Claude Code", "Proof format"):
        check("render_run_protocol includes '%s'" % needle, needle in protocol)
    strict = tool_render_run_protocol(catalog, first_id, risk_posture="strict")
    check("strict posture adds strict note", "Strict posture" in strict)
    bounded = tool_render_run_protocol(catalog, first_id, kind="session",
                                       max_rounds=12, max_minutes=90)
    check("session protocol honors round/time budget params",
          "12 rounds" in bounded and "90 minutes" in bounded)
    tick = tool_render_run_protocol(catalog, "ci-optimization", kind="scheduled-tick")
    check("tick protocol renders experiment log + undo discipline",
          "experiment-log.md" in tick and "undo losers" in tick.lower()
          and "Notify the human" in tick)
    check("ci-optimization auto-detects scheduled-tick from its trigger",
          tool_get_loop(catalog, "ci-optimization")["loop_kind"] == "scheduled-tick")

    good = tool_critique_loop(catalog, (
        "Nightly loop: fix one flaky test per round, re-run the same suite each round, "
        "log every round to progress.md, stop when 10 consecutive runs pass or after "
        "8 rounds / 45 minutes, PRs only, human approval before merging."))
    check("critique_loop scores a solid loop >= 8/10",
          int(good["score"].split("/")[0]) >= 8, good["score"])
    bad = tool_critique_loop(catalog, "keep improving the app until it is good and goes viral")
    check("critique_loop flags a vibes loop <= 3/10",
          int(bad["score"].split("/")[0]) <= 3, bad["score"])
    check("critique_loop findings carry fixes for misses",
          all(f["fix"] for f in bad["findings"] if f["status"] == "missing"))
    risky = tool_critique_loop(catalog, (
        "Loop that adjusts our ad spend and publishes posts automatically every day, "
        "measuring CPA, one change per day, logging to experiment-log.md, stop at $500."))
    gate = [f for f in risky["findings"] if f["check"] == "risk_gate"][0]
    check("critique_loop demands a human gate on money/outbound surfaces",
          gate["status"] == "missing")

    draft = tool_design_loop(catalog, "our support inbox backlog keeps growing",
                             cadence="daily")
    for needle in ("Draft loop spec", "Verification", "Stop conditions", "MVL check",
                   "critique_loop", "docs/loops/"):
        check("design_loop draft includes '%s'" % needle, needle in draft)
    vanity_draft = tool_design_loop(catalog, "get 100k followers on X")
    check("design_loop pushes vanity goals down to a micro-metric",
          "vanity" in vanity_draft)

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
    check("JSON-RPC tools/list shows 9 tools", len(tools["result"]["tools"]) == 9,
          "got %d" % len(tools["result"]["tools"]))
    check("every tool declares readOnlyHint",
          all(t.get("annotations", {}).get("readOnlyHint")
              for t in tools["result"]["tools"]))
    call = handle_message(catalog, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "search_loops", "arguments": {"query": "source check"}}})
    check("JSON-RPC tools/call search_loops succeeds",
          call["result"]["isError"] is False and call["result"]["content"])
    read = handle_message(catalog, {
        "jsonrpc": "2.0", "id": 4, "method": "resources/read",
        "params": {"uri": "ailooplibrary://loop/%s" % first_id}})
    check("resources/read loop uri works", "result" in read and read["result"]["contents"])
    bad_rpc = handle_message(catalog, {"jsonrpc": "2.0", "id": 5, "method": "nope/nope"})
    check("unknown method returns -32601", bad_rpc["error"]["code"] == -32601)

    print("\nself-test: %d checks, %d failed" % (total[0], len(failures)))
    return 1 if failures else 0


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--eval" in sys.argv:
        sys.exit(run_eval())
    serve()


if __name__ == "__main__":
    main()
