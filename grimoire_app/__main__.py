# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Allow `python -m grimoire_app ...` (used by the background update worker and
as a fallback entrypoint when pip/pipx-installed)."""
from .controller import main

if __name__ == "__main__":
    main()
