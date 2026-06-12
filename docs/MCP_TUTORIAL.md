# Grimoire MCP - attach an AI model

Grimoire can expose its offline knowledge base over the **Model Context Protocol
(MCP)**. You attach an AI model (Claude, Codex, Gemini, or any MCP-capable
client) to Grimoire, and the model can then search the indexed documentation,
read full pages, and build technical checklists that are grounded in real
sources instead of guesswork.

Grimoire is the MCP **server** (it owns the data and the tools). Your AI model
is the **client**. Nothing in this path gives the model a shell or arbitrary
filesystem access: it can only search the index, read indexed docs (path
traversal guarded), list categories, and request the checklist prompt.

## 1. Build the index first

The MCP server serves what you have already fetched and indexed:

```bash
cd grimoire
pip install -r requirements.txt
./grimoire.py all          # fetch every source + build the search index
```

If there is no index yet, `grimoire.py mcp` exits and tells you to run this.

## 2. Start the server (stdio)

```bash
./grimoire.py mcp
```

It talks newline-delimited JSON-RPC 2.0 on stdin/stdout (the MCP stdio
transport). You normally do not run this by hand - your MCP client launches it
for you using the config below. Use an **absolute path** to `grimoire.py` in
client configs.

## 3. Point your AI client at it

### Claude Desktop
Edit `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "grimoire": {
      "command": "python3",
      "args": ["/ABS/PATH/grimoire/grimoire.py", "mcp"]
    }
  }
}
```

Restart Claude Desktop. Grimoire's tools appear in the tools menu.

### Claude Code (CLI)
```bash
claude mcp add grimoire -- python3 /ABS/PATH/grimoire/grimoire.py mcp
```

### Codex (CLI)
Add to `~/.codex/config.toml`:

```toml
[mcp_servers.grimoire]
command = "python3"
args = ["/ABS/PATH/grimoire/grimoire.py", "mcp"]
```

### Gemini CLI / other MCP clients
Most clients use the same shape as Claude Desktop. Add an entry under
`mcpServers` (e.g. `~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "grimoire": { "command": "python3", "args": ["/ABS/PATH/grimoire/grimoire.py", "mcp"] }
  }
}
```

Any client that speaks MCP over stdio works: the launch command is always
`python3 /ABS/PATH/grimoire/grimoire.py mcp`.

## 3b. Give it your engagement context (adapt to YOUR assessment)

So the model tailors its answers to the real targets and gear in front of you -
instead of generic advice - launch the server with an engagement context:

```bash
cp context.example.yaml context.yaml   # then edit it
./grimoire.py mcp --context context.yaml
```

The context has two halves:

- **Declared** (what you write in `context.yaml`): `targets` (IPs / CIDRs /
  hostnames - also the scope for exec mode later), `interfaces` (e.g.
  `wlan0mon`), `hardware` (HackRF, Proxmark, Wi-Fi adapter, ...), `sim`
  (operator / IMSI / type), `rf` (bands, frequency), and free-form `notes`
  (rules of engagement). See [`context.example.yaml`](context.example.yaml).
- **Detected** (read-only, from the host): network interfaces, USB devices
  (`lsusb` - SDRs/Proxmark show up here), and SDRs (`SoapySDRUtil --find`).

The model reads all of this through the `grimoire_context` tool, and every
checklist / tutorial / review prompt instructs it to **use the actual in-scope
targets, the available interfaces and hardware, and the SIM/RF parameters**, and
to respect your scope notes. So "build me a Wi-Fi assessment checklist" becomes
commands that use your `wlan0mon` and your declared SSIDs/targets, and a GSM task
uses your SIM/band parameters.

Do not put secrets in `context.yaml` that you would not want the model to see.

## 4. What the model can do

Tools exposed to the model:

| Tool | Purpose |
|---|---|
| `grimoire_search(query, category?, limit?)` | Ranked full-text search across every source. Returns source, category, path, snippet. |
| `grimoire_fetch_doc(source, path)` | Read one full document (markdown, notebook-as-markdown, or extracted PDF text). |
| `grimoire_categories()` | List categories and their sources (use to scope a search). |
| `grimoire_context()` | The engagement context: declared targets/interfaces/hardware/SIM/RF/notes + host-detected interfaces/USB/SDRs. |
| `grimoire_checklist_material(topic, category?)` | Run several targeted searches and return deduped, cited material for a checklist. |
| `grimoire_topic_material(topic, category?)` | Wide sweep of related searches; returns the full deduped reading set for writing a tutorial. |
| `grimoire_env()` | Detect the runtime: RF-Swift container? OS + package manager? scripts reachable? root/sudo? (read-only) |
| `grimoire_which(tool)` | Is a tool installed, and where? (read-only) |
| `grimoire_plan_install(tool)` | Resolve how a missing tool *would* be installed (RF-Swift recipe, then host pkg mgr) - **without running anything**. (read-only) |
| `grimoire_install(tool)` | Install a missing tool. **Only in `--mode assist|auto`.** |
| `grimoire_run(command, timeout?, cwd?)` | Run a command for the engagement (denylist + scope guarded). **Only in `--mode assist|auto`.** |

Prompts exposed to the model:

| Prompt | Arguments | Purpose |
|---|---|---|
| `build_checklist` | `topic`, `category?` | Assemble a source-backed technical checklist. |
| `build_tutorial` | `topic`, `category?` | Assemble all relevant reads and synthesize a complete, cited pentest tutorial. |
| `review_techniques` | `topic`, `category?` | Assess whether the indexed docs are correct/current/complete and suggest better or newer techniques. |

## 5. Example: ask the model for grounded help

Once attached, just ask in natural language - the model calls the tools itself:

> "Use Grimoire to find how to enumerate Kerberos and summarize the steps with
> the source for each."

The model will call `grimoire_search("kerberoast")`, maybe
`grimoire_fetch_doc(...)` on the best hit, and answer with citations like
`(source: hacktricks/.../kerberoast.md)`.

## 6. Example: build a technical checklist

This is the headline use case. Either invoke the `build_checklist` prompt from
your client's prompt menu with `topic = "web API pentest"`, or just say:

> "Build me a web API pentest checklist using Grimoire, with a copy-ready
> command for each item and a citation to the source."

The model calls `grimoire_checklist_material("web API pentest")`, reads the
top hits, and produces something like:

```markdown
## Web API pentest checklist

### Recon
- [ ] Enumerate API endpoints and methods (source: owasp-wstg/.../API...)
- [ ] Identify auth scheme (JWT / OAuth / API key) (source: api-security-top-10/...)

### Authentication / authorization
- [ ] Test for BOLA / IDOR on object IDs (source: api-security-top-10/...)
      curl -s https://target/api/orders/1234 -H "Authorization: Bearer $T"
- [ ] Test JWT alg=none and key confusion (source: cheatsheets/JWT...)
...
```

Because the items are drawn from WSTG / MASVS / ASVS / the API Security Top 10
and the rest of the indexed corpus, the checklist is grounded and traceable.

## 7. Example: write a full tutorial on a topic

Invoke the `build_tutorial` prompt (or just ask) with `topic = "BLE sniffing
with Sniffle"`:

> "Use Grimoire to assemble everything on BLE sniffing with Sniffle and write me
> a complete tutorial with setup, step-by-step capture, and how to read the
> output, citing sources."

The model calls `grimoire_topic_material("BLE sniffing with Sniffle")`, reads the
top pages in full, and produces a structured tutorial (overview, prerequisites,
step-by-step with copy-ready commands, pitfalls, detection notes, further
reading) with a `(source: ...)` citation on each step.

## 8. Example: review the docs / find better techniques

Invoke `review_techniques` with `topic = "kerberoasting"`:

> "Review what Grimoire has on kerberoasting - is it current, what is missing,
> and are there better or newer techniques?"

The model reads the indexed coverage and reports on correctness, currency, gaps,
and clarity, then suggests newer/better techniques - clearly separating what is
grounded in the indexed docs (cited) from its own knowledge (labelled "beyond
the indexed docs"), and recommends which sources to add or update.

## 9. Performing a pentest (execution modes)

Once you have validated a checklist, Grimoire can also *run* it - detect missing
tools, install them, and execute steps. This is gated behind a launch **mode**:

```bash
./grimoire.py mcp                                   # mode=read (default): NO execution
./grimoire.py mcp --mode assist --context context.yaml
./grimoire.py mcp --mode auto   --scope 10.0.0.0/24 app.example.test
```

| Mode | What the model can do |
|---|---|
| `read` (default) | Knowledge + read-only recon only: search, docs, checklist/tutorial/review, `grimoire_env`/`grimoire_which`/`grimoire_plan_install`. The install/run tools are **not even listed**, so the model cannot attempt them. |
| `assist` | Adds `grimoire_install` + `grimoire_run`. Intended with an MCP client that prompts you to **approve each call** (the normal MCP UX = human in the loop). |
| `auto` | Same tools, for autonomous operation (the model chains install + run without a per-call prompt). |

**Installing missing tools.** When a checklist needs a tool that is not present,
the model calls `grimoire_plan_install` / `grimoire_install`. Grimoire resolves it
in this order:

1. **RF-Swift recipe** - if it is inside an RF-Swift container, or the RF-Swift
   `scripts/` are reachable (auto-detected, or set `GRIMOIRE_RFSWIFT_SCRIPTS`),
   it finds the matching install function and runs it via `entrypoint.sh`.
2. **Host package manager** - otherwise it detects the host (Kali / Debian /
   Ubuntu / Arch / Fedora / Alpine / ...) and uses `apt-get` / `pacman` / `dnf`
   / `apk` / etc. `sudo` is used automatically when you are not root.

So the same checklist works whether you run Grimoire inside RF-Swift or on a
plain Kali box - it adapts to where it is.

**Guardrails (always on in assist/auto):**

- **Destructive denylist** - `rm -rf /`, `mkfs`, `dd of=/dev/...`, fork bombs,
  `shutdown`/`reboot`, etc. are refused in every mode.
- **Target scope** - set authorized targets via the context `targets:` and/or
  `--scope`. `grimoire_run` refuses any command aimed at a host outside the
  scope. With no scope set it warns (and relies on your per-call approval) but
  does not block - so set a scope for real engagements.
- **No magic sandbox** - this runs real commands on your host/container. Use it
  only on systems and targets you are authorized to test. `assist` + your MCP
  client's per-call approval is the recommended posture; `auto` removes that
  prompt, so reserve it for contained lab use.

Example:

> "Validate this AD checklist against 10.0.0.0/24, install anything missing via
> RF-Swift, and run the enumeration steps."

The model calls `grimoire_env`, `grimoire_plan_install`/`grimoire_install` for
e.g. `netexec`, then `grimoire_run` for each enumeration command - each refused
if it targets a host outside `10.0.0.0/24`.

## 10. Notes and safety

- The model only ever sees what you indexed. Update with `./grimoire.py update`
  (or the **Update docs** button in the web UI) and the model sees the new docs.
- Read-only by design: no shell, no writes, no network egress through these
  tools. `grimoire_fetch_doc` is path-traversal guarded - it cannot read
  `.git/config`, `.env`, or files outside a source.
- OSINT note: OSINT material can target individuals; scope what you ask the
  model to collect to what an authorized engagement justifies (GDPR).

You can run the web UI (`./grimoire.py serve`) and the MCP server at the same
time - they share the same `data/index.db`.
