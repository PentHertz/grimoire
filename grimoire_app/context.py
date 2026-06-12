# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Engagement context: the operator-declared + auto-detected facts about THIS
assessment, so an attached AI model can tailor its suggestions to the real
targets, hardware, interfaces, SIM/telecom setup and RF parameters in use.

Declared context comes from a YAML file the operator passes at launch
(`grimoire.py mcp --context context.yaml`); detected context is enumerated
read-only from the host (network interfaces, USB devices, SDRs). The model reads
it through the `grimoire_context` MCP tool and the checklist/tutorial/review
prompts instruct it to adapt to this context.

Example context.yaml:

    engagement: "Acme Q3 external"
    authorization: "SOW-1234"            # reminder only
    targets:                             # also the exec-mode scope
      - 10.0.0.0/24
      - app.acme.test
    interfaces: [wlan0mon, eth0]
    hardware: ["HackRF One", "Proxmark3 RDV4"]
    sim:
      operator: "Test PLMN 001-01"
      imsi: "001010000000001"
      type: sysmoUSIM
    rf:
      bands: ["GSM900", "LTE B3"]
    notes: "client asked to avoid any DoS"

Do not put secrets you would not want the attached model to see.
"""
import shutil
import subprocess
from pathlib import Path


def load_declared(path):
    """Read the operator-declared context YAML. Returns {} if no path; an
    {'error': ...} dict (not an exception) on a bad/missing file."""
    if not path:
        return {}
    try:
        import yaml
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {"error": f"context file not found: {path}"}
    except Exception as e:                       # bad YAML, etc. - never crash the server
        return {"error": f"could not parse context: {e}"}


def _run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def detect():
    """Read-only enumeration of host facts useful for adapting commands:
    network interfaces, USB devices (SDRs/Proxmark/adapters show up here), and
    SDRs via SoapySDR when present. Best-effort: missing tools are skipped."""
    det = {}
    try:
        det["interfaces"] = sorted(p.name for p in Path("/sys/class/net").iterdir())
    except OSError:
        det["interfaces"] = []
    if shutil.which("lsusb"):
        det["usb"] = [l for l in _run(["lsusb"]).splitlines() if l]
    if shutil.which("SoapySDRUtil"):
        det["sdr"] = [l.strip() for l in _run(["SoapySDRUtil", "--find"]).splitlines()
                      if ("driver=" in l or "label=" in l)]
    return det


def full(path):
    """The complete context handed to the model: what you declared + what we
    detected on the host."""
    return {"declared": load_declared(path), "detected": detect()}
