# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Grimoire MCP server: expose the offline knowledge base over the Model
Context Protocol so an AI model (Claude, Codex, Gemini, ... any MCP client) can
attach and get grounded suggestions and build technical checklists from the
indexed docs.

Grimoire is the MCP *server* (the data/tools); the AI model is the *client*.
Point your client's MCP config at:  grimoire.py mcp

Transport: newline-delimited JSON-RPC 2.0 over stdio (the MCP stdio transport).
Stdlib only - no SDK dependency. The model never gets shell or filesystem
access through here: it can only search the index, read indexed docs (path
traversal guarded), list categories, and request a checklist prompt.
"""
import json
import sys

from . import context, model, runner

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "grimoire", "version": "1.1.1"}

# Path to the operator's engagement context YAML (set by cmd_mcp at launch).
CONTEXT_PATH = None
# Launch mode: "read" (no execution), "assist" (exec + per-call client approval),
# "auto" (autonomous exec). Set by cmd_mcp.
MODE = "read"
# Authorized target scope (context targets + --scope); enforced for grimoire_run.
SCOPE = []

# --------------------------------------------------------------------------- #
# Tools (what the attached model may call)
# --------------------------------------------------------------------------- #
TOOLS = [
    {
        "name": "grimoire_search",
        "description": ("Full-text search across every indexed offline security "
                        "knowledge base (HackTricks, PayloadsAllTheThings, OWASP "
                        "WSTG/MASVS/ASVS, the LOTL DBs, RE/OSINT/DFIR/Bluetooth/"
                        "WiFi/SDR sources, ...). Returns ranked matches with "
                        "source, category, path and a snippet. Use this to ground "
                        "answers and checklists in real documentation."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search terms, e.g. 'kerberoast' or 'ssrf bypass'"},
                "category": {"type": "string", "description": "optional category filter (see grimoire_categories)"},
                "limit": {"type": "integer", "description": "max results (default 20)", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "grimoire_fetch_doc",
        "description": ("Return the full text of one indexed document (markdown, "
                        "notebook converted to markdown, or extracted PDF text). "
                        "Use the source+path from a grimoire_search result to read "
                        "the whole page before answering."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "source name from a search result"},
                "path": {"type": "string", "description": "relpath from a search result"},
            },
            "required": ["source", "path"],
        },
    },
    {
        "name": "grimoire_categories",
        "description": "List the available source categories and their sources.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grimoire_context",
        "description": ("Return the engagement context for THIS assessment: "
                        "operator-declared targets/IPs, interfaces, hardware, SIM/"
                        "telecom and RF parameters, plus host-detected interfaces, "
                        "USB devices and SDRs. Read this first and tailor every "
                        "step and command to the actual targets and hardware in "
                        "scope."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grimoire_checklist_material",
        "description": ("Gather grounded source material for a technical checklist "
                        "on a topic: runs several targeted searches and returns the "
                        "matching documentation snippets with citations. Feed this "
                        "to build a concrete, source-backed checklist."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "e.g. 'web API pentest', 'active directory privesc', 'BLE assessment'"},
                "category": {"type": "string", "description": "optional category filter"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "grimoire_topic_material",
        "description": ("Assemble a broad reading set for a topic: runs a wide sweep "
                        "of related searches across the whole corpus and returns the "
                        "deduped matching docs with citations. Use this to write a "
                        "complete, source-backed tutorial. Pair with grimoire_fetch_doc "
                        "to read the most relevant pages in full."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "e.g. 'kerberoasting', 'BLE sniffing with Sniffle', 'GPS spoofing'"},
                "category": {"type": "string", "description": "optional category filter"},
            },
            "required": ["topic"],
        },
    },
]

# Read-only recon tools - always available (they detect/plan, never execute).
ENV_TOOLS = [
    {
        "name": "grimoire_env",
        "description": ("Detect the runtime environment: whether this is an "
                        "RF-Swift container, the OS and package manager, whether "
                        "RF-Swift install scripts are reachable, and root/sudo "
                        "availability. Use this to decide how to install tools."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grimoire_which",
        "description": "Check whether a tool/binary is installed and where.",
        "inputSchema": {"type": "object",
                        "properties": {"tool": {"type": "string"}},
                        "required": ["tool"]},
    },
    {
        "name": "grimoire_plan_install",
        "description": ("Resolve HOW a missing tool would be installed (RF-Swift "
                        "script recipe first, then the host package manager) and "
                        "return the ordered commands - WITHOUT running anything."),
        "inputSchema": {"type": "object",
                        "properties": {"tool": {"type": "string"}},
                        "required": ["tool"]},
    },
]

# Execution tools - only exposed in assist/auto mode (absent from tools/list in
# read mode, so an attached model cannot even attempt them).
EXEC_TOOLS = [
    {
        "name": "grimoire_install",
        "description": ("Install a missing tool (RF-Swift recipe if available, "
                        "else host package manager). Requires --mode assist|auto."),
        "inputSchema": {"type": "object",
                        "properties": {"tool": {"type": "string"}},
                        "required": ["tool"]},
    },
    {
        "name": "grimoire_run",
        "description": ("Run a shell command for the engagement. Refuses "
                        "destructive commands and (when a scope is set) commands "
                        "aimed at out-of-scope hosts. Requires --mode assist|auto. "
                        "Authorized engagements only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "the command to run"},
                "timeout": {"type": "integer", "description": "seconds (default 300, max 1800)"},
                "cwd": {"type": "string", "description": "optional working directory"},
            },
            "required": ["command"],
        },
    },
]

def active_tools():
    """The tool set visible for the current launch mode."""
    tools = list(TOOLS) + list(ENV_TOOLS)
    if MODE in ("assist", "auto"):
        tools += EXEC_TOOLS
    return tools

# --------------------------------------------------------------------------- #
# Prompts (reusable instructions the client can surface to its model)
# --------------------------------------------------------------------------- #
PROMPTS = [
    {
        "name": "build_checklist",
        "description": ("Build a technical, source-backed checklist for a topic, "
                        "grounded in Grimoire's indexed documentation."),
        "arguments": [
            {"name": "topic", "description": "the engagement/topic to build a checklist for", "required": True},
            {"name": "category", "description": "optional category to scope sources", "required": False},
        ],
    },
    {
        "name": "build_tutorial",
        "description": ("Assemble all relevant reads on a topic and synthesize a "
                        "complete, source-backed pentest tutorial."),
        "arguments": [
            {"name": "topic", "description": "the topic to write a tutorial for", "required": True},
            {"name": "category", "description": "optional category to scope sources", "required": False},
        ],
    },
    {
        "name": "review_techniques",
        "description": ("Assess whether Grimoire's indexed docs for a topic are "
                        "good, current, and complete - and suggest better or newer "
                        "techniques and missing coverage."),
        "arguments": [
            {"name": "topic", "description": "the topic/technique to review", "required": True},
            {"name": "category", "description": "optional category to scope sources", "required": False},
        ],
    },
]

_CHECKLIST_INSTRUCTIONS = (
    "You are assisting an authorized security assessment. Build a technical "
    "checklist for: {topic}.\n\n"
    "Method:\n"
    "1. Call grimoire_checklist_material with topic={topic}{cat} (or several "
    "grimoire_search calls) to gather grounded material from the offline docs.\n"
    "2. Read promising hits in full with grimoire_fetch_doc.\n"
    "3. Produce a checklist as markdown '- [ ] ' items, grouped by phase "
    "(recon, enumeration, exploitation, post-exploitation, reporting as relevant "
    "to the topic).\n"
    "4. Each item must be concrete and actionable; include copy-ready commands in "
    "fenced code blocks where applicable.\n"
    "5. Cite the Grimoire source for each item as '(source: <name>/<path>)' so the "
    "origin is traceable.\n"
    "6. Only include techniques appropriate to an authorized engagement; do not "
    "invent tools or facts - if the docs do not cover something, say so.\n"
)

_TUTORIAL_INSTRUCTIONS = (
    "You are writing for an authorized security assessment. Write a complete, "
    "source-backed pentest tutorial on: {topic}.\n\n"
    "Method:\n"
    "1. Call grimoire_topic_material with topic={topic}{cat} to assemble the "
    "relevant reads, then grimoire_fetch_doc on the most relevant pages to read "
    "them in full before writing.\n"
    "2. Structure the tutorial: Overview / when it applies; Prerequisites and "
    "setup (tools, environment); Step-by-step walkthrough with copy-ready "
    "commands in fenced code blocks; How to read the results; Common pitfalls and "
    "troubleshooting; Detection and defensive notes; Further reading.\n"
    "3. Ground every claim and command in the gathered docs and cite the origin "
    "as '(source: <name>/<path>)'. Do not invent tools, flags, or facts - if the "
    "docs do not cover a step, say so explicitly.\n"
    "4. Keep it to techniques appropriate for an authorized engagement.\n"
)

_REVIEW_INSTRUCTIONS = (
    "Critically review Grimoire's indexed documentation on: {topic}.\n\n"
    "Method:\n"
    "1. Call grimoire_topic_material with topic={topic}{cat} and read the key "
    "pages in full with grimoire_fetch_doc to see what the corpus actually says.\n"
    "2. Assess the docs on: correctness, currency (are the techniques/tools still "
    "current, or deprecated/patched?), completeness (what is missing), and "
    "clarity.\n"
    "3. Where better, safer, or newer techniques or tools exist, name them and "
    "explain how they improve on what the docs describe - clearly separating what "
    "is grounded in the indexed docs (cite '(source: <name>/<path>)') from your "
    "own knowledge (label as 'beyond the indexed docs').\n"
    "4. Finish with concrete suggestions: which sources to add or update, and "
    "specific gaps to fill. Do not fabricate tools or claims.\n"
)

# Prepended to every prompt so the model adapts its output to the engagement.
_CONTEXT_PREFIX = (
    "First call grimoire_context to load the engagement context (targets/IPs, "
    "network interfaces, hardware/SDRs, SIM/telecom and RF parameters, scope "
    "notes) and tailor every step and command to it: use the actual in-scope "
    "target hosts, the available interfaces and hardware, the SIM/RF parameters "
    "where relevant, and respect the scope and any notes. If the context is "
    "empty, ask the operator for the targets and hardware before giving specific "
    "commands.\n\n")

# what each prompt feeds the model: (instructions template)
_PROMPT_TEXT = {
    "build_checklist": _CHECKLIST_INSTRUCTIONS,
    "build_tutorial": _TUTORIAL_INSTRUCTIONS,
    "review_techniques": _REVIEW_INSTRUCTIONS,
}

# topics -> the searches that tend to surface checklist-shaped material
_CHECKLIST_QUERIES = ("{t}", "{t} enumeration", "{t} exploitation",
                      "{t} privilege escalation", "{t} checklist", "{t} testing")
# topics -> a wider sweep to assemble complete tutorial reading material
_TUTORIAL_QUERIES = ("{t}", "{t} tutorial", "{t} guide", "{t} how to", "{t} basics",
                     "{t} introduction", "{t} example", "{t} setup", "{t} tools",
                     "{t} command", "{t} attack", "{t} detection")


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def _search(query, category=None, limit=20):
    rows = model.search(query, category or None, int(limit or 20))
    return [{"source": r[0], "title": r[1], "category": r[2], "path": r[3],
             # strip the FTS5 highlight sentinels for plain-text consumption
             "snippet": (r[4] or "").replace("\x02", "").replace("\x03", "")}
            for r in rows]

def _gather(topic, queries, category=None, per_query=6):
    """Run a set of query templates for a topic and return deduped, cited hits."""
    seen, out = set(), []
    for tmpl in queries:
        for r in model.search(tmpl.format(t=topic), category or None, per_query):
            key = (r[0], r[3])
            if key in seen:
                continue
            seen.add(key)
            out.append({"source": r[0], "title": r[1], "category": r[2], "path": r[3],
                        "snippet": (r[4] or "").replace("\x02", "").replace("\x03", "")})
    return out

def _checklist_material(topic, category=None):
    return _gather(topic, _CHECKLIST_QUERIES, category, per_query=6)

def _topic_material(topic, category=None):
    return _gather(topic, _TUTORIAL_QUERIES, category, per_query=8)

def _call_tool(name, args):
    args = args or {}
    if name == "grimoire_search":
        return _search(args.get("query", ""), args.get("category"), args.get("limit", 20))
    if name == "grimoire_fetch_doc":
        text = model.doc_text(args.get("source", ""), args.get("path", ""))
        if text is None:
            return {"error": "document not found"}
        return {"source": args.get("source"), "path": args.get("path"), "text": text}
    if name == "grimoire_categories":
        return model.categories()
    if name == "grimoire_context":
        return context.full(CONTEXT_PATH)
    if name == "grimoire_checklist_material":
        return {"topic": args.get("topic", ""),
                "material": _checklist_material(args.get("topic", ""), args.get("category"))}
    if name == "grimoire_topic_material":
        return {"topic": args.get("topic", ""),
                "material": _topic_material(args.get("topic", ""), args.get("category"))}
    # read-only recon (available in every mode)
    if name == "grimoire_env":
        return runner.detect_env()
    if name == "grimoire_which":
        t = args.get("tool", "")
        p = runner.which(t)
        return {"tool": t, "present": bool(p), "path": p}
    if name == "grimoire_plan_install":
        return runner.plan_install(args.get("tool", ""))
    # execution (assist/auto only)
    if name in ("grimoire_install", "grimoire_run"):
        if MODE not in ("assist", "auto"):
            return {"error": "execution disabled - relaunch: "
                             "grimoire.py mcp --mode assist|auto"}
        if name == "grimoire_install":
            return runner.install(args.get("tool", ""))
        return runner.run(args.get("command", ""), args.get("timeout"),
                          args.get("cwd"), SCOPE)
    raise KeyError(name)


# --------------------------------------------------------------------------- #
# JSON-RPC dispatch
# --------------------------------------------------------------------------- #
def handle(req):
    """Handle one JSON-RPC request dict. Returns a response dict, or None for
    notifications (no id) which must not be answered."""
    # A JSON-RPC message must be an object, and `params` (when present) a
    # structured value; anything else is a malformed request, not a crash.
    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid request"}}
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params")
    if not isinstance(params, dict):
        params = {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    # notifications (no id) are fire-and-forget
    if rid is None and method and method.startswith("notifications/"):
        return None

    if method == "initialize":
        client_ver = params.get("protocolVersion") or PROTOCOL_VERSION
        return ok({"protocolVersion": client_ver,
                   "capabilities": {"tools": {}, "prompts": {}},
                   "serverInfo": SERVER_INFO})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": active_tools()})
    if method == "tools/call":
        name = params.get("name")
        try:
            result = _call_tool(name, params.get("arguments"))
        except KeyError:
            return err(-32602, f"unknown tool: {name}")
        except Exception as e:                       # never crash the server on a tool error
            return ok({"content": [{"type": "text", "text": f"tool error: {e}"}],
                       "isError": True})
        return ok({"content": [{"type": "text",
                                "text": json.dumps(result, indent=2)}]})
    if method == "prompts/list":
        return ok({"prompts": PROMPTS})
    if method == "prompts/get":
        name = params.get("name")
        if name not in _PROMPT_TEXT:
            return err(-32602, f"unknown prompt: {name}")
        a = params.get("arguments") or {}
        topic = a.get("topic", "the target")
        cat = a.get("category")
        text = _CONTEXT_PREFIX + _PROMPT_TEXT[name].format(
            topic=topic, cat=(f", category={cat}" if cat else ""))
        return ok({"description": f"{name} for {topic}",
                   "messages": [{"role": "user",
                                 "content": {"type": "text", "text": text}}]})
    if rid is None:
        return None
    return err(-32601, f"method not found: {method}")


def serve(stdin=None, stdout=None):
    """Run the stdio JSON-RPC loop. One JSON message per line, both directions."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "parse error"}}
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
            continue
        try:
            resp = handle(req)
        except Exception as e:                 # one bad message must not kill the loop
            rid = req.get("id") if isinstance(req, dict) else None
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": f"internal error: {e}"}}
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


def cmd_mcp(args):
    global CONTEXT_PATH, MODE, SCOPE
    CONTEXT_PATH = getattr(args, "context", None)
    MODE = getattr(args, "mode", "read") or "read"
    declared = context.load_declared(CONTEXT_PATH)
    targets = declared.get("targets") if isinstance(declared, dict) else None
    SCOPE = list(targets or []) + list(getattr(args, "scope", None) or [])
    # stderr banner so it doesn't corrupt the stdio JSON-RPC stream on stdout
    note = f" | context: {CONTEXT_PATH}" if CONTEXT_PATH else " | no context"
    print(f"[grimoire] MCP server on stdio - mode={MODE}{note} (Ctrl-C to stop)",
          file=sys.stderr, flush=True)
    if MODE in ("assist", "auto"):
        scope = ", ".join(SCOPE) if SCOPE else "NONE (out-of-scope hosts NOT enforced)"
        print(f"[grimoire] EXECUTION ENABLED (mode={MODE}): the model can install "
              f"software and run commands. Scope: {scope}. Authorized use only.",
              file=sys.stderr, flush=True)
    try:
        serve()
    except KeyboardInterrupt:
        pass
