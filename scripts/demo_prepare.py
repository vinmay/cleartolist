"""Pre-run the real pipeline on the demo photos so a screen recording isn't 30 s of spinner per scan.

Results go into the app's scan cache (keyed by the photo's SHA-256). The app marks them as cached.

    uv run python scripts/demo_prepare.py demo/photos/*.jpg
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.main import cached_scan

for p in sys.argv[1:]:
    r = cached_scan(Path(p).read_bytes())
    top = r["matches"][0]["title"] if r["matches"] else ""
    print(f"{p}: {r['verdict']}  {top[:80]}  ({'cached' if r['cached'] else str(r['timings']['total_s']) + ' s'})")
