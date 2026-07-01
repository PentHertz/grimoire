# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Execution layer for Grimoire MCP: detect the environment, resolve how to
install a missing tool (RF-Swift script recipe first, then the host package
manager), and run commands - all gated behind an explicit launch mode.

SAFETY - read this. This is NOT a sandbox. It only becomes active when the
operator launches `grimoire.py mcp --mode assist|auto`; the default `read` mode
exposes none of the execution tools at all (they are absent from tools/list, so
an attached model cannot even attempt them). Even when enabled:
  * a destructive-command denylist is refused in every mode;
  * an optional target scope (from the engagement context `targets:` and/or
    --scope) blocks commands aimed at out-of-scope hosts;
  * `assist` is meant to be used with an MCP client that approves each call.
Use only for authorized engagements.
"""
import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import config

MAX_OUTPUT = 20000
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 1800

# Clearly destructive / host-wrecking patterns, refused in ALL modes.
_DENY = [
    r"\brm\s+-[rf]{1,2}\s+/(?:\s|$|\*)",        # rm -rf /  or  rm -rf /*
    r"\bmkfs\b", r"\bdd\b[^|]*\bof=/dev/", r">\s*/dev/sd",
    r":\s*\(\)\s*\{.*:\s*\|\s*:.*&.*\}",          # fork bomb
    r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b",
    r"\bchmod\s+-R\s+0?0?0\s+/(?:\s|$)",
    r">\s*/etc/(passwd|shadow|sudoers)",
]
_DENY_RE = re.compile("|".join(_DENY), re.I)

# Hosts never treated as "targets" for scope enforcement: loopback only. GitHub
# is deliberately NOT exempt here - a scoped engagement should not get a free
# fetch-and-exec/exfil channel to github.com/raw.githubusercontent.com. Operators
# who genuinely need extra hosts exempt can list them (comma-separated) in
# GRIMOIRE_SCOPE_ALLOW; fetch-piped-to-a-shell stays refused regardless.
_SCOPE_IGNORE = {"127.0.0.1", "0.0.0.0", "::1", "::", "localhost", "localhost.localdomain"}
_SCOPE_IGNORE |= {h.strip().lower()
                  for h in os.environ.get("GRIMOIRE_SCOPE_ALLOW", "").split(",")
                  if h.strip()}
_HOST_RE = re.compile(
    r"\b((?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?|(?:[a-z0-9-]+\.)+[a-z]{2,})\b", re.I)
# IPv6 literals (validated with ipaddress before we treat them as hosts, so this
# never false-positives on ports/ints the way bare-integer parsing would).
_IPV6_RE = re.compile(r"(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{0,4}:){2,}:?[0-9A-Fa-f]{0,4}")

# Command substitution / indirection hides the real target from static scope
# checking; when a scope is set we fail closed rather than allow an unverifiable
# host. Backticks, $(...) and ${...} all count.
_SUBST_RE = re.compile(r"\$\(|`|\$\{")
# A network fetch piped into an interpreter is remote code execution / exfil.
_PIPE_SHELL_RE = re.compile(
    r"\b(?:curl|wget|fetch)\b[^|]*\|\s*(?:sudo\s+)?"
    r"(?:ba|z|da|a|k)?sh\b|\b(?:curl|wget|fetch)\b[^|]*\|\s*(?:sudo\s+)?"
    r"(?:python[0-9.]*|perl|ruby|node)\b", re.I | re.S)


def _decode_ip(host):
    """Canonical dotted-quad for the legacy integer/hex/octal IPv4 encodings that
    curl/wget accept (e.g. 3232235521 or 0xC0A80001 -> 192.168.0.1), else None.
    Lets an in-scope encoded IP still be recognised as in-scope."""
    try:
        if re.fullmatch(r"0[xX][0-9A-Fa-f]+", host):
            v = int(host, 16)
        elif re.fullmatch(r"0[0-7]+", host):
            v = int(host, 8)
        elif re.fullmatch(r"\d+", host):
            v = int(host)
        else:
            return None
        if 0 <= v <= 0xFFFFFFFF:
            return str(ipaddress.IPv4Address(v))
    except (ValueError, ipaddress.AddressValueError):
        pass
    return None


def _authority_hosts(command):
    """Host in every ``scheme://[user@]host[:port]`` and ``user@host`` position.
    These are target positions, so we check them fail-closed - a single-label or
    encoded host that the loose _HOST_RE never recognised is still caught."""
    hosts = []
    for m in re.finditer(r"://([^/\s?#]+)", command):
        auth = m.group(1).rsplit("@", 1)[-1]          # drop any user[:pass]@
        if auth.startswith("["):                       # [IPv6]:port
            hosts.append(auth[1:auth.find("]")] if "]" in auth else auth[1:])
        else:
            hosts.append(auth.split(":")[0])
    for m in re.finditer(r"(?:^|\s)[A-Za-z0-9_.\-]+@([A-Za-z0-9_.\-]+)", command):
        hosts.append(m.group(1))                       # ssh/scp user@host
    return [h for h in hosts if h]


def _host_in_scope(host, scope):
    h = host.strip("[]")
    if h.lower() in _SCOPE_IGNORE:
        return True
    return _in_scope(_decode_ip(h) or h, scope)

# A valid package / tool name: a leading alnum then alnum and . _ + - only.
# Anything else (shell metacharacters, spaces) is refused before it can reach a
# shell command - this is what prevents install command injection.
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")

def valid_tool_name(tool):
    return isinstance(tool, str) and _TOOL_RE.match(tool) is not None


# --------------------------------------------------------------------------- #
# environment detection
# --------------------------------------------------------------------------- #
def which(tool):
    return shutil.which(tool) if tool else None

def _os_release():
    info = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v.strip().strip('"')
    except OSError:
        pass
    return info

_PKG_MGRS = [("apt-get", "apt"), ("pacman", "pacman"), ("dnf", "dnf"),
             ("yum", "yum"), ("apk", "apk"), ("zypper", "zypper"), ("brew", "brew")]

def _detect_pkg_mgr():
    for binname, name in _PKG_MGRS:
        if shutil.which(binname):
            return name
    return None

def _is_dir(p):
    """`p.is_dir()` that returns False instead of raising when the path (or a
    parent) is unreadable - e.g. probing /root/scripts as a non-root user."""
    try:
        return p.is_dir()
    except OSError:
        return False

def _has_scripts(p):
    """True if p is a readable directory holding at least one *.sh."""
    try:
        return p.is_dir() and any(p.glob("*.sh"))
    except OSError:
        return False

def rfswift_scripts_dir():
    """Locate the RF-Swift install scripts: an explicit override, else the repo
    layout (Grimoire ships inside RF-Swift-images), else common container paths.
    Candidate probing tolerates inaccessible paths (see _is_dir/_has_scripts)."""
    env = os.environ.get("GRIMOIRE_RFSWIFT_SCRIPTS")
    if env and _is_dir(Path(env)):
        return Path(env)
    cand = config.ROOT.parent / "scripts"   # <repo>/grimoire/.. -> <repo>/scripts
    if _has_scripts(cand):
        return cand
    for p in ("/root/scripts", "/scripts", "/opt/rfswift/scripts"):
        if _has_scripts(Path(p)):
            return Path(p)
    return None

def _entrypoint():
    scripts = rfswift_scripts_dir()
    if scripts:
        for cand in (scripts / "entrypoint.sh", scripts.parent / "entrypoint.sh"):
            if cand.is_file():
                return str(cand)
    return None

def in_rfswift():
    if os.environ.get("RFSWIFT") or os.environ.get("RFSWIFT_VERSION"):
        return True
    for p in ("/var/lib/db/rfswift_components.lst", "/var/lib/rfswift"):
        if Path(p).exists():
            return True
    return False

def detect_env():
    info = _os_release()
    scripts = rfswift_scripts_dir()
    return {
        "in_rfswift": in_rfswift(),
        "rfswift_scripts": str(scripts) if scripts else None,
        "rfswift_entrypoint": _entrypoint(),
        "os_id": info.get("ID"),
        "os_name": info.get("PRETTY_NAME"),
        "pkg_manager": _detect_pkg_mgr(),
        "is_root": (os.geteuid() == 0 if hasattr(os, "geteuid") else False),
        "has_sudo": bool(shutil.which("sudo")),
    }


# --------------------------------------------------------------------------- #
# RF-Swift recipe resolution
# --------------------------------------------------------------------------- #
_FUNC_RE = re.compile(r"(?m)^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")

def _iter_functions(text):
    """Yield (name, body) for each shell function defined in `text`."""
    for m in _FUNC_RE.finditer(text):
        name = m.group(1)
        i = m.end() - 1            # position of the opening brace
        depth = 0
        while i < len(text):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield name, text[m.end() - 1:i + 1]

def find_rfswift_recipe(tool, limit=5):
    """Scan the RF-Swift scripts for install functions related to `tool`.
    Returns ranked candidates [{script, function, score}] (best-effort)."""
    scripts = rfswift_scripts_dir()
    if not scripts:
        return []
    t = tool.lower()
    needles = {t, t.replace("-", "_"), t.replace("_", "-")}
    out = []
    for sh in sorted(scripts.glob("*.sh")):
        try:
            text = sh.read_text(errors="ignore")
        except OSError:
            continue
        for name, body in _iter_functions(text):
            nl, bl = name.lower(), body.lower()
            score = 0
            if any(n in nl for n in needles):
                score += 3
                if nl.endswith(("_install", "_soft_install", "install")):
                    score += 2
            if any(re.search(r"\b" + re.escape(n) + r"\b", bl) for n in needles):
                score += 1
            if score:
                out.append({"script": sh.name, "function": name, "score": score})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]

_PKG_INSTALL = {
    "apt": "apt-get install -y {t}",
    "pacman": "pacman -S --noconfirm {t}",
    "dnf": "dnf install -y {t}",
    "yum": "yum install -y {t}",
    "apk": "apk add {t}",
    "zypper": "zypper install -y {t}",
    "brew": "brew install {t}",
}

def plan_install(tool):
    """Produce an ordered install plan WITHOUT running anything. Prefers an
    RF-Swift recipe, then the host package manager."""
    if not valid_tool_name(tool):
        return {"tool": tool, "present": None, "steps": [
            {"kind": "none", "why": "refused: invalid tool name "
             "(must match [A-Za-z0-9][A-Za-z0-9._+-]*) - blocks command injection"}]}
    env = detect_env()
    present = which(tool)
    plan = {"tool": tool, "present": present, "env": env, "steps": []}
    if present:
        plan["steps"].append({"kind": "noop", "why": f"{tool} already at {present}"})
        return plan
    ep = env.get("rfswift_entrypoint")
    scripts = env.get("rfswift_scripts")
    for r in find_rfswift_recipe(tool):
        if ep:
            cmd = f"{ep} {r['function']}"
        elif scripts:
            cmd = (f"bash -c 'cd {scripts}; source common.sh 2>/dev/null; "
                   f"source {r['script']}; {r['function']}'")
        else:
            continue
        plan["steps"].append({"kind": "rfswift", "command": cmd,
                              "why": f"RF-Swift recipe {r['function']} "
                                     f"({r['script']}, score {r['score']})"})
    tmpl = _PKG_INSTALL.get(env.get("pkg_manager"))
    if tmpl:
        plan["steps"].append({"kind": "host", "command": tmpl.format(t=tool),
                              "why": f"{env['pkg_manager']} package install"})
    if not plan["steps"]:
        plan["steps"].append({"kind": "none",
                              "why": "no RF-Swift recipe and no known package manager"})
    return plan


# --------------------------------------------------------------------------- #
# scope + denylist guard
# --------------------------------------------------------------------------- #
def _in_scope(token, scope):
    bare = token.split("/")[0]
    for s in scope:
        if token == s or bare == s:
            return True
        try:                                   # CIDR / IP membership
            net = ipaddress.ip_network(s, strict=False)
            try:
                if ipaddress.ip_address(bare) in net:
                    return True
            except ValueError:
                pass
        except ValueError:                     # s is a hostname -> suffix match
            if bare == s or bare.endswith("." + s):
                return True
    return False

def out_of_scope_hosts(command, scope):
    bad = set()
    for m in _HOST_RE.finditer(command):
        tok = m.group(1)
        if tok.lower() in _SCOPE_IGNORE:
            continue
        if not _in_scope(tok, scope):
            bad.add(tok)
    # IPv6 literals (only flagged if they parse as a real address)
    for m in _IPV6_RE.finditer(command):
        tok = m.group(0)
        try:
            ipaddress.ip_address(tok)
        except ValueError:
            continue
        if tok.lower() in _SCOPE_IGNORE:
            continue
        if not _in_scope(tok, scope):
            bad.add(tok)
    # Fail closed on target (URL / user@) host positions: anything not provably in
    # scope is flagged, so encoded IPs and single-label hosts can't slip past the
    # loose _HOST_RE above.
    for host in _authority_hosts(command):
        if not _host_in_scope(host, scope):
            bad.add(host)
    return sorted(bad)

def guard(command, scope=None):
    """Return a refusal reason string, or None if the command may run."""
    if not command or not command.strip():
        return "refused: empty command"
    if _DENY_RE.search(command):
        return "refused: matches destructive-command denylist"
    if scope:
        if _PIPE_SHELL_RE.search(command):
            return ("refused: piping a network fetch into a shell/interpreter is not "
                    "allowed under an engagement scope (remote code execution)")
        if _SUBST_RE.search(command):
            return ("refused: shell substitution/indirection ($(...), backticks, ${...}) "
                    "hides the target host from scope checking")
        bad = out_of_scope_hosts(command, scope)
        if bad:
            return (f"refused: out-of-scope host(s) {', '.join(bad)} "
                    f"(authorized scope: {', '.join(scope)})")
    return None


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def _maybe_sudo(cmd):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return cmd
    if shutil.which("sudo"):
        return "sudo " + cmd
    return cmd

def run(command, timeout=DEFAULT_TIMEOUT, cwd=None, scope=None):
    """Run a command through bash, guarded by the denylist and (if set) scope.
    Output is truncated. Never raises - returns a result dict."""
    reason = guard(command, scope)
    if reason:
        return {"command": command, "refused": True, "reason": reason}
    warning = None if scope else "no target scope set: out-of-scope hosts are NOT enforced"
    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    try:
        p = subprocess.run(["bash", "-c", command], capture_output=True,
                           text=True, timeout=timeout, cwd=cwd or None)
        return {"command": command, "rc": p.returncode,
                "stdout": p.stdout[-MAX_OUTPUT:], "stderr": p.stderr[-MAX_OUTPUT:],
                "warning": warning}
    except subprocess.TimeoutExpired:
        return {"command": command, "error": f"timeout after {timeout}s", "warning": warning}
    except OSError as e:
        return {"command": command, "error": str(e), "warning": warning}

def install(tool):
    """Resolve and run the best install step for a missing tool (installs are
    local, so scope does not apply; the denylist still does)."""
    if not valid_tool_name(tool):
        return {"tool": tool, "installed": False,
                "reason": "refused: invalid tool name (blocks command injection)"}
    plan = plan_install(tool)
    if plan.get("present"):
        return {"tool": tool, "already_installed": plan["present"]}
    steps = [s for s in plan["steps"] if s.get("command")]
    if not steps:
        return {"tool": tool, "installed": False, "reason": "no install method found",
                "plan": plan}
    step = steps[0]
    cmd = _maybe_sudo(step["command"])
    res = run(cmd, timeout=900)
    return {"tool": tool, "method": step["kind"], "command": cmd, "result": res,
            "now_present": which(tool)}
