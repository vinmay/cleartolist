"""Download invite requests (email, time, alert categories) from the hosted service as CSV.

    CTL_SERVER_URL=https://<service>.onrender.com CTL_ADMIN_SECRET=... \
        uv run python -m scripts.export_signups > signups.csv

The database only accepts connections from inside Render, so the export goes through a signed admin endpoint.
Keep the CSV private: it holds people's email addresses.
"""

from __future__ import annotations

import os
import sys

import httpx

from demo.security import signed_headers


def main() -> None:
    url, secret = os.environ.get("CTL_SERVER_URL", "").rstrip("/"), os.environ.get("CTL_ADMIN_SECRET", "")
    if not url.startswith("https://") or len(secret) < 32:
        sys.exit("set CTL_SERVER_URL (https) and CTL_ADMIN_SECRET (32+ characters)")
    r = httpx.get(url + "/admin/invite-requests", headers=signed_headers(secret, "GET", "/admin/invite-requests"),
                  timeout=90, follow_redirects=False)
    r.raise_for_status()
    sys.stdout.write(r.text)


if __name__ == "__main__":
    main()
