"""Security for the console server (PLAN.md section 12.3).

- Access token: a random token is made at startup and printed in the server's terminal. Anything that
  spends money or changes data must send it in the X-DSG-Token header. A custom header can't be sent
  cross-site without a CORS preflight (which this server never approves), so another website open in
  the same browser can't fire actions at localhost (CSRF).
- Host check: requests whose Host isn't this machine are refused (DNS rebinding).
- Security headers on every response (CSP, nosniff, no framing, no referrer).
- Redaction: the API key, and anything shaped like one, is removed from every error the UI can see.
- Spending caps: per run, per server session, and per day. The day total is saved to disk, so a restart
  doesn't reset it (OWASP LLM10, unbounded consumption).
- Read-only mode (DSG_READ_ONLY=1) for a public demo: nothing can spend money or change data.
- Ownership: an outside agent belongs to the token that registered it; only that token sees its URL and
  transcripts. Set DSG_TOKEN in .env to keep the same token (and your agents) across restarts.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import date
from pathlib import Path

from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from discharge import config

TOKEN = os.getenv("DSG_TOKEN") or secrets.token_urlsafe(24)
TOKEN_ID = hashlib.sha256(TOKEN.encode()).hexdigest()[:8]       # recorded in the audit log, never the token
READ_ONLY = os.getenv("DSG_READ_ONLY", "0") == "1"
ALLOWED_HOSTS = {h.strip() for h in os.getenv(
    "DSG_ALLOWED_HOSTS", "localhost,127.0.0.1,localhost:8001,127.0.0.1:8001,localhost:5175,127.0.0.1:5175").split(",")}

SESSION_MAX_USD = float(os.getenv("DSG_LIVE_MAX_USD", "0.50"))
DAY_MAX_USD = float(os.getenv("DSG_DAY_MAX_USD", "2.00"))
RUN_MAX_USD = float(os.getenv("DSG_RUN_MAX_USD", "1.00"))
BUDGET_PATH = config.RESULTS_DIR / "budget.json"

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'; object-src 'none'")
HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
           "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin",
           "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}


def require_token(x_dsg_token: str | None = Header(default=None)) -> str:
    """FastAPI dependency for every route that spends money or changes data. Returns the token id."""
    if READ_ONLY:
        raise HTTPException(403, "this server is read-only (public demo mode)")
    if not x_dsg_token or not hmac.compare_digest(x_dsg_token, TOKEN):
        raise HTTPException(401, "missing or wrong access token: copy it from the terminal running the server")
    return TOKEN_ID


def viewer(x_dsg_token: str | None = Header(default=None)) -> str | None:
    """FastAPI dependency for reads that show more to an owner: the token id, or None. Never raises."""
    if READ_ONLY or not x_dsg_token or not hmac.compare_digest(x_dsg_token, TOKEN):
        return None
    return TOKEN_ID


_KEY_SHAPES = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9]{20,}|(?i:bearer)\s+[A-Za-z0-9._\-]{16,}")


def redact(text: str) -> str:
    """Remove secrets from text shown to users or written to logs."""
    out = str(text)
    for secret in (os.getenv("ANTHROPIC_API_KEY", ""), TOKEN):
        if secret and len(secret) > 8:
            out = out.replace(secret, "[redacted]")
    return _KEY_SHAPES.sub("[redacted]", out)


class Guard(BaseHTTPMiddleware):
    """Host check before anything runs; security headers on everything that goes out."""

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "host not allowed"}, status_code=400)
        try:
            response = await call_next(request)
        except Exception as exc:                   # never leak a stack trace or a key to the browser
            response = JSONResponse({"detail": redact(f"server error: {type(exc).__name__}: {exc}")}, status_code=500)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        return response


class Budget:
    """Spending caps, checked before every call that costs money and updated after it."""

    def __init__(self, path: Path = BUDGET_PATH, session_max: float = SESSION_MAX_USD, day_max: float = DAY_MAX_USD,
                 run_max: float = RUN_MAX_USD):
        self.path, self.session_max, self.day_max, self.run_max = Path(path), session_max, day_max, run_max
        self.session = 0.0
        self._lock = threading.Lock()

    def _days(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError):
            return {}

    def today(self) -> float:
        return float(self._days().get(date.today().isoformat(), 0.0))

    def check(self, estimate: float) -> None:
        """Refuse (402) if this spend would break any cap."""
        with self._lock:
            if estimate > self.run_max:
                raise HTTPException(402, f"this would cost about ${estimate:.2f}, above the per-run cap of ${self.run_max:.2f}")
            if self.session + estimate > self.session_max:
                raise HTTPException(402, f"session cap reached (${self.session:.2f} of ${self.session_max:.2f}); "
                                         "restart the server to start a new session")
            if self.today() + estimate > self.day_max:
                raise HTTPException(402, f"daily cap reached (${self.today():.2f} of ${self.day_max:.2f}); try tomorrow "
                                         "or raise DSG_DAY_MAX_USD")

    def add(self, spent: float) -> None:
        with self._lock:
            self.session += spent
            days = self._days()
            key = date.today().isoformat()
            days[key] = round(float(days.get(key, 0.0)) + spent, 6)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(days, indent=1), encoding="utf-8")

    def status(self) -> dict:
        return {"session": round(self.session, 4), "session_max": self.session_max, "today": round(self.today(), 4),
                "day_max": self.day_max, "run_max": self.run_max}
