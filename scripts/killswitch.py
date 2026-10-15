"""Turn hosted photo uploads off or on instantly, without a redeploy. The lookup stays up either way.

    CTL_SERVER_URL=https://<your-service>.onrender.com CTL_ADMIN_SECRET=... python -m scripts.killswitch off
    ... on | env   (env = drop the runtime override and follow CTL_UPLOADS_ENABLED again)

The override lives in memory, so a restart falls back to CTL_UPLOADS_ENABLED (which defaults to off).
"""

from __future__ import annotations

import json
import os
import sys

import httpx

from demo.security import signed_headers


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off", "env"):
        sys.exit("usage: python -m scripts.killswitch on|off|env")
    url, secret = os.environ.get("CTL_SERVER_URL", "").rstrip("/"), os.environ.get("CTL_ADMIN_SECRET", "")
    if not url.startswith("https://") or len(secret) < 32:
        sys.exit("set CTL_SERVER_URL (https) and CTL_ADMIN_SECRET (32+ characters)")
    body = json.dumps({"enabled": {"on": True, "off": False, "env": None}[sys.argv[1]]}).encode()
    h = signed_headers(secret, "POST", "/admin/uploads", body) | {"Content-Type": "application/json"}
    r = httpx.post(url + "/admin/uploads", content=body, headers=h, timeout=90, follow_redirects=False)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
