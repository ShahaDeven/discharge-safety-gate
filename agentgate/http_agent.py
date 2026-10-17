"""An agent that lives behind someone else's URL, spoken to through the same three methods as any agent.

The gate is the client here, so the outside agent is untrusted in both directions:
- Where we send requests (SSRF): https only (plain http only to this machine, for an agent registered as
  local); the hostname is resolved and every address must be public (or loopback for a local agent);
  the address is pinned for the whole call so a DNS change can't swap it; no redirects; no proxies.
- What comes back: a fixed reply schema (unknown fields refused), only the call's own tools, limits on
  steps, characters, bytes and time. A broken reply ends the call with a clear error; it is never cached.
- What we send: the EHR-style call context and the tools, under an opaque call id. Never the scenario id,
  the simulated person's hidden facts, or the answer key.
- Every request is signed (HMAC-SHA256 over timestamp, nonce and body), so the agent can tell the gate
  from anyone else and refuse replays. verify() is the agent-side check (adapters/example_agent.py).

Protocol (one URL, JSON POSTs, see adapters/HTTP_CONTRACT.md):
    {"type": "ping"}                                                      -> {"ok": true}
    {"type": "start", "call_id", "context", "tools"}                      -> {"ok": true}
    {"type": "hear", "call_id", "utterance"}                              -> reply
    {"type": "tool_results", "call_id", "results": [...]}                 -> reply
    reply = {"steps": [{"type": "speech", "text"} | {"type": "tool_call", "id", "name", "input"}], "error": null}
"""

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adapter import AgentTurn, CallContext, Speech, ToolCall, ToolResult, ToolSpec

CONTRACT = "dsg-agent/1"
TIMEOUT_S = 15.0            # per request: each network step, and the whole reply body
MAX_REPLY_BYTES = 64_000
MAX_STEPS = 8
MAX_SPEECH = 2_000
MAX_CONCURRENT = 4          # calls in flight to one endpoint
MAX_SKEW_S = 300            # signatures older than this are refused by verify()
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class EndpointRefused(ValueError):
    """The URL may not be called (refused before any request is sent)."""


class AgentProtocolError(RuntimeError):
    """The agent broke the contract: timeout, too large, bad status, bad JSON, bad reply."""


# ---------------------------------------------------------------- where requests may go

@dataclass(frozen=True)
class Target:
    scheme: str
    host: str               # as registered (Host header and TLS name)
    port: int
    path: str
    ip: str                 # pinned address

    def url(self) -> str:
        ip = f"[{self.ip}]" if ":" in self.ip else self.ip
        return f"{self.scheme}://{ip}:{self.port}{self.path}"

    def host_header(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        default = {"https": 443, "http": 80}[self.scheme]
        return host if self.port == default else f"{host}:{self.port}"


def _allowed(ip: ipaddress._BaseAddress, local: bool) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return local
    return ip.is_global and not ip.is_multicast


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EndpointRefused(f"cannot resolve {host}") from exc
    return list(dict.fromkeys(info[4][0] for info in infos))


def check_endpoint(url: str, local: bool = False) -> Target:
    """Refuse anything that could reach this machine's or a private network's services, unless the agent
    is registered as local (then loopback only, nothing else private)."""
    if len(url) > 300:
        raise EndpointRefused("URL too long")
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError as exc:
        raise EndpointRefused(f"not a valid URL: {exc}") from exc
    host = (u.hostname or "").lower()
    if u.scheme not in ("https", "http"):
        raise EndpointRefused("only https URLs are allowed")
    if u.username or u.password:
        raise EndpointRefused("credentials in the URL are not allowed; requests are signed instead")
    if not host:
        raise EndpointRefused("URL has no host")
    if u.scheme == "http" and not (local and host in _LOCAL_HOSTS):
        raise EndpointRefused("plain http is only allowed for an agent on this machine (register it as local)")
    if local and host not in _LOCAL_HOSTS:
        raise EndpointRefused("a local agent must use localhost or 127.0.0.1")
    port = port or (443 if u.scheme == "https" else 80)
    addrs = resolve(host, port)
    bad = [a for a in addrs if not _allowed(ipaddress.ip_address(a.split("%")[0]), local)]
    if bad or not addrs:
        raise EndpointRefused(f"{host} resolves to an address the gate may not call ({', '.join(bad) or 'none'}): "
                              "private, loopback, link-local and cloud-metadata addresses are refused")
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return Target(u.scheme, host, port, path, addrs[0])


# ---------------------------------------------------------------- signing

def sign(secret: bytes, body: bytes, timestamp: str, nonce: str) -> str:
    return hmac.new(secret, f"{timestamp}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()


def verify(secret: bytes, body: bytes, headers, seen: dict[str, float], now: float | None = None) -> bool:
    """Agent side: true only for a fresh, correctly signed request whose nonce hasn't been used."""
    now = time.time() if now is None else now
    ts, nonce, sig = (headers.get("x-dsg-timestamp", ""), headers.get("x-dsg-nonce", ""),
                      headers.get("x-dsg-signature", ""))
    try:
        fresh = abs(now - int(ts)) <= MAX_SKEW_S
    except ValueError:
        return False
    if not (fresh and nonce and hmac.compare_digest(sign(secret, body, ts, nonce), sig)):
        return False
    for n, t in list(seen.items()):
        if now - t > MAX_SKEW_S:
            del seen[n]
    if nonce in seen:
        return False
    seen[nonce] = now
    return True


# ---------------------------------------------------------------- what may come back

class _Speech(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["speech"]
    text: str = Field(max_length=MAX_SPEECH)


class _ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tool_call"]
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(max_length=64)
    input: dict


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[Annotated[_Speech | _ToolCall, Field(discriminator="type")]] = Field(default_factory=list,
                                                                                    max_length=MAX_STEPS)
    error: str | None = Field(default=None, max_length=200)


def public_context(ctx: CallContext) -> dict:
    """The call context an outside agent gets: no harness call id (it names the scenario), no extras."""
    out = asdict(ctx)
    out.pop("call_id", None)
    out.pop("extra", None)
    return out


_slots: dict[str, threading.BoundedSemaphore] = {}
_slots_lock = threading.Lock()


def _slot(host: str) -> threading.BoundedSemaphore:
    with _slots_lock:
        return _slots.setdefault(host, threading.BoundedSemaphore(MAX_CONCURRENT))


class HttpAgent:
    def __init__(self, url: str, secret: bytes, tools: tuple[ToolSpec, ...], *, local: bool = False,
                 timeout: float = TIMEOUT_S, max_bytes: int = MAX_REPLY_BYTES, transport: httpx.BaseTransport | None = None):
        self.url, self.secret, self.local = url, secret, local
        self.tools = tools
        self.tool_names = {t.name for t in tools}
        self.timeout, self.max_bytes = timeout, max_bytes
        self.transport = transport            # tests only
        self.target: Target | None = None
        self.call_id = ""

    # -- the three methods
    def start(self, ctx: CallContext) -> None:
        self.target = check_endpoint(self.url, self.local)          # resolved and pinned once per call
        self.call_id = uuid.uuid4().hex
        tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in self.tools]
        out = self._post({"type": "start", "call_id": self.call_id, "context": public_context(ctx), "tools": tools})
        if out.get("ok") is not True:
            raise AgentProtocolError('start must answer {"ok": true}')

    def hear(self, utterance: str) -> AgentTurn:
        return self._turn({"type": "hear", "call_id": self.call_id, "utterance": utterance})

    def tool_results(self, results: list[ToolResult]) -> AgentTurn:
        return self._turn({"type": "tool_results", "call_id": self.call_id, "results": [
            {"tool_call_id": r.tool_call_id, "name": r.name, "content": r.content} for r in results]})

    def ping(self) -> float:
        """Signed health check; returns seconds taken."""
        self.target = check_endpoint(self.url, self.local)
        t0 = time.monotonic()
        if self._post({"type": "ping"}).get("ok") is not True:
            raise AgentProtocolError('ping must answer {"ok": true}')
        return time.monotonic() - t0

    # -- plumbing
    def _turn(self, payload: dict) -> AgentTurn:
        raw = self._post(payload)
        try:
            reply = Reply.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise AgentProtocolError(f"reply does not match the contract: {'.'.join(map(str, first['loc']))}: "
                                     f"{first['msg']}") from None
        steps = []
        for s in reply.steps:
            if isinstance(s, _Speech):
                steps.append(Speech(s.text))
            elif s.name not in self.tool_names:
                raise AgentProtocolError(f"unknown tool {s.name[:40]!r}")
            else:
                steps.append(ToolCall(s.id, s.name, s.input))
        return AgentTurn(steps=steps, error=f"agent reported: {reply.error}" if reply.error else None)

    def _post(self, payload: dict) -> dict:
        assert self.target is not None, "start() first"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ts, nonce = str(int(time.time())), secrets.token_hex(12)
        headers = {"Content-Type": "application/json", "Host": self.target.host_header(), "X-DSG-Contract": CONTRACT,
                   "X-DSG-Timestamp": ts, "X-DSG-Nonce": nonce, "X-DSG-Signature": sign(self.secret, body, ts, nonce)}
        ext = {"sni_hostname": self.target.host} if self.target.scheme == "https" else {}
        deadline = time.monotonic() + self.timeout
        with _slot(self.target.host), httpx.Client(follow_redirects=False, trust_env=False, timeout=self.timeout,
                                                   transport=self.transport) as client:
            try:
                req = client.build_request("POST", self.target.url(), content=body, headers=headers, extensions=ext)
                resp = client.send(req, stream=True)
                try:
                    if resp.status_code != 200:
                        raise AgentProtocolError(f"agent answered HTTP {resp.status_code}"
                                                 + (" (redirects are not followed)" if resp.is_redirect else ""))
                    data = bytearray()
                    for chunk in resp.iter_bytes():
                        data += chunk
                        if len(data) > self.max_bytes:
                            raise AgentProtocolError(f"reply larger than {self.max_bytes // 1000} KB")
                        if time.monotonic() > deadline:
                            raise AgentProtocolError(f"reply took longer than {self.timeout:g} s")
                finally:
                    resp.close()
            except httpx.TimeoutException:
                raise AgentProtocolError(f"no reply within {self.timeout:g} s") from None
            except httpx.HTTPError as exc:
                raise AgentProtocolError(f"could not reach the agent: {type(exc).__name__}") from None
        try:
            out = json.loads(bytes(data))
        except ValueError:
            raise AgentProtocolError("reply is not valid JSON") from None
        if not isinstance(out, dict):
            raise AgentProtocolError("reply must be a JSON object")
        return out
