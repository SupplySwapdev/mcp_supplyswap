# server.py
import os
import sys
import argparse
import time
import uuid
import inspect
import functools
from typing import List, Dict

from fastmcp import FastMCP
import datetime
from google_chat import (
    list_chat_spaces, list_space_messages, send_chat_message,
    get_space_members, search_messages_by_keyword,
    get_dm_messages, send_dm, add_reaction, get_space_info, get_thread_messages,
    DEFAULT_CALLBACK_URL, set_token_path, set_save_token_mode,
)
from server_auth import run_auth_server
from auth_cli import run_cli_auth

# Allow Railway / other cloud hosts to pass PORT as env var
_port = int(os.environ.get("PORT", 8000))

# Create an MCP server
mcp = FastMCP("Google Chat", host="0.0.0.0", port=_port)


def _tool_timed(tool_name: str):
    def decorator(func):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                req_id = uuid.uuid4().hex[:12]
                start = time.perf_counter()
                print(f"[tool] id={req_id} name={tool_name} event=start")
                try:
                    result = await func(*args, **kwargs)
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    print(f"[tool] id={req_id} name={tool_name} event=ok duration_ms={duration_ms}")
                    return result
                except Exception as e:
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    print(f"[tool] id={req_id} name={tool_name} event=error duration_ms={duration_ms} error={type(e).__name__}")
                    raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            req_id = uuid.uuid4().hex[:12]
            start = time.perf_counter()
            print(f"[tool] id={req_id} name={tool_name} event=start")
            try:
                result = func(*args, **kwargs)
                duration_ms = int((time.perf_counter() - start) * 1000)
                print(f"[tool] id={req_id} name={tool_name} event=ok duration_ms={duration_ms}")
                return result
            except Exception as e:
                duration_ms = int((time.perf_counter() - start) * 1000)
                print(f"[tool] id={req_id} name={tool_name} event=error duration_ms={duration_ms} error={type(e).__name__}")
                raise

        return sync_wrapper

    return decorator

@mcp.tool()
@_tool_timed("get_current_datetime")
def get_current_datetime() -> Dict:
    """Returns the current date and time in UTC. Always call this first before
    querying messages so you know the correct dates to use in other tools.
    """
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        'utc': now.isoformat(),
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M:%S'),
        'day_of_week': now.strftime('%A'),
        'yesterday': (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
        'last_7_days_start': (now - datetime.timedelta(days=7)).strftime('%Y-%m-%d'),
    }


def _parse_date(date_str: str, end_of_day: bool = False):
    """Parse a YYYY-MM-DD string into a UTC datetime."""
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"Date must be in YYYY-MM-DD format, got: {date_str}")


@mcp.tool()
@_tool_timed("get_chat_spaces")
async def get_chat_spaces(space_type: str = None) -> List[Dict]:
    """List all Google Chat spaces the user is a member of.

    Args:
        space_type: Optional filter — 'SPACE' (named rooms), 'GROUP_CHAT', or 'DIRECT_MESSAGE'.
                    Leave empty to return all types.
    """
    return await list_chat_spaces(space_type=space_type)


@mcp.tool()
@_tool_timed("get_space_messages")
async def get_space_messages(
    space_name: str,
    start_date: str = None,
    end_date: str = None,
    hours: int = None,
    sender_id: str = None,
    keyword: str = None,
    max_results: int = 50,
) -> List[Dict]:
    """List messages from a Google Chat space.

    You can filter by date range OR by recent hours — pick whichever is easier:

    By date:
        start_date: YYYY-MM-DD (e.g. '2026-03-30')
        end_date:   YYYY-MM-DD optional, defaults to end of start_date day

    By recency (simpler):
        hours: how many hours back to look (e.g. 24 = last day, 168 = last week)

    Other filters:
        sender_id:   filter by sender resource name, e.g. 'users/123456'
        keyword:     only return messages containing this text (case-insensitive)
        max_results: cap on messages returned (default 50)
    """
    if hours is not None:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        return await list_space_messages(
            space_name, start_date=since, sender_id=sender_id,
            keyword=keyword, max_results=max_results,
        )
    if not start_date:
        raise ValueError("Provide either 'hours' or 'start_date'.")
    start_dt = _parse_date(start_date)
    end_dt = _parse_date(end_date, end_of_day=True) if end_date else None
    if end_dt and start_dt > end_dt:
        raise ValueError("start_date must be before end_date")
    return await list_space_messages(
        space_name, start_date=start_dt, end_date=end_dt,
        sender_id=sender_id, keyword=keyword, max_results=max_results,
    )


@mcp.tool()
@_tool_timed("send_message")
async def send_message(space_name: str, text: str, thread_name: str = None) -> Dict:
    """Send a message to a Google Chat space or reply in a thread.

    Args:
        space_name: Space resource name, e.g. 'spaces/AAAAxxxxxx'
        text: Message text to send. Supports *bold*, _italic_, ~strikethrough~, `code`.
        thread_name: Optional thread resource name to reply in an existing thread,
                     e.g. 'spaces/xxx/threads/yyy'. Leave empty to start a new thread.
    """
    return await send_chat_message(space_name, text, thread_name=thread_name)


@mcp.tool()
@_tool_timed("search_messages")
async def search_messages(
    keyword: str,
    space_name: str = None,
    start_date: str = None,
    end_date: str = None,
) -> List[Dict]:
    """Search for messages containing a keyword across one or all spaces.

    Args:
        keyword: Text to search for (case-insensitive)
        space_name: Optional — limit search to one space. If omitted, searches all spaces (slower).
        start_date: Optional start date in YYYY-MM-DD format to narrow the search window
        end_date: Optional end date in YYYY-MM-DD format
    """
    start_dt = _parse_date(start_date) if start_date else None
    end_dt = _parse_date(end_date, end_of_day=True) if end_date else None
    return await search_messages_by_keyword(keyword, space_name=space_name, start_date=start_dt, end_date=end_dt)


@mcp.tool()
@_tool_timed("get_space_member_list")
async def get_space_member_list(space_name: str) -> List[Dict]:
    """List all members of a Google Chat space with their display names and roles.

    Args:
        space_name: Space resource name, e.g. 'spaces/AAAAxxxxxx'
    """
    return await get_space_members(space_name)


@mcp.tool()
@_tool_timed("get_direct_messages")
async def get_direct_messages(with_person: str, hours: int = 48, keyword: str = None) -> List[Dict]:
    """Read the DM conversation with a specific person.

    Args:
        with_person: Name or partial name of the person, e.g. 'Iustin', 'Ruanna'
        hours: How many hours back to look (default 48). Use 168 for last week.
        keyword: Optional keyword to filter messages
    """
    return await get_dm_messages(with_person, hours=hours, keyword=keyword)


@mcp.tool()
@_tool_timed("send_direct_message")
async def send_direct_message(to_person: str, text: str) -> Dict:
    """Send a direct message to a specific person.

    Args:
        to_person: Name or partial name of the person, e.g. 'Iustin', 'Ruanna'
        text: Message text to send
    """
    return await send_dm(to_person, text)


@mcp.tool()
@_tool_timed("react_to_message")
async def react_to_message(message_name: str, emoji: str) -> Dict:
    """Add an emoji reaction to a message.

    Args:
        message_name: Full message resource name from a previous query, e.g. 'spaces/xxx/messages/yyy'
        emoji: Emoji to react with, e.g. '👍', '✅', '🎉', '❤️', '😂'
    """
    return await add_reaction(message_name, emoji)


@mcp.tool()
@_tool_timed("get_space_details")
async def get_space_details(space_name: str) -> Dict:
    """Get detailed info about a space: description, creation date, last activity, member count.

    Args:
        space_name: Space resource name, e.g. 'spaces/AAAAxxxxxx'
    """
    return await get_space_info(space_name)


@mcp.tool()
@_tool_timed("get_thread")
async def get_thread(space_name: str, thread_name: str) -> List[Dict]:
    """Get all messages in a specific thread.

    Args:
        space_name: Space resource name, e.g. 'spaces/AAAAxxxxxx'
        thread_name: Thread resource name from a message, e.g. 'spaces/xxx/threads/yyy'
    """
    return await get_thread_messages(space_name, thread_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='MCP Server with Google Chat Authentication')
    parser.add_argument('--auth', choices=['web', 'cli'],
                        help='Run OAuth authentication (web: browser-based, cli: headless/terminal)')
    parser.add_argument('--serve', action='store_true',
                        help='Run as HTTP/SSE server (for remote hosting). SSE endpoint: /sse')
    parser.add_argument('--host', default='localhost', help='Host to bind the auth server to (default: localhost)')
    parser.add_argument('--port', type=int, default=8000, help='Port to run the auth server on (default: 8000)')
    parser.add_argument('--token-path', default='token.json', help='Path to store OAuth token (default: token.json)')
    parser.add_argument('--disable-token-saving', action='store_false', help='Disable token saving mode (enabled by default)')

    args = parser.parse_args()

    # Set the token path for OAuth storage
    set_token_path(args.token_path)

    # Set message filtering
    set_save_token_mode(args.disable_token_saving)

    if args.auth == 'web':
        print(f"\nStarting OAuth authentication server at http://{args.host}:{args.port}")
        print("Available endpoints:")
        print("  - /auth   : Start OAuth authentication flow")
        print("  - /status : Check authentication status")
        print("  - /auth/callback : OAuth callback endpoint")
        print(f"\nDefault callback URL: {DEFAULT_CALLBACK_URL}")
        print(f"Token will be stored at: {args.token_path}")
        print("\nPress CTRL+C to stop the server")
        print("-" * 50)
        run_auth_server(port=args.port, host=args.host)
    elif args.auth == 'cli':
        run_cli_auth()
    elif args.serve:
        import asyncio
        import json as _json
        import base64 as _base64
        import uuid as _uuid
        import hashlib as _hashlib
        import uvicorn
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.requests import Request
        from starlette.responses import Response, HTMLResponse, RedirectResponse
        from starlette.routing import Route
        from mcp.server.sse import SseServerTransport
        from google_auth_oauthlib.flow import Flow
        from google.oauth2.credentials import Credentials
        from google_chat import SCOPES, _per_user_creds, _per_user_api_key
        from user_store import create_user, get_user, is_valid_key

        # Legacy single-user API key (still supported for backward compat)
        _legacy_api_key = os.environ.get("API_KEY", "")

        # Public URL used in setup instructions (set this env var on Cloud Run)
        _server_url = os.environ.get(
            "MCP_SERVER_URL",
            "https://google-chat-mcp-253940259390.us-central1.run.app"
        ).rstrip("/")

        # In-memory cache: api_key -> Credentials (avoids a Firestore hit per SSE poll)
        _creds_cache: dict = {}

        # Pending OAuth flows keyed by state
        _oauth_flows: dict = {}
        _session_ttl_seconds = 10 * 60  # 10 minutes
        # session_id -> {"api_key": str, "expires_at": float}
        _message_sessions: dict = {}
        # fallback fingerprint -> {"api_key": str, "expires_at": float}
        _fingerprint_sessions: dict = {}

        def _load_client_config() -> dict:
            """Load OAuth client config from env var or credentials.json file."""
            env_creds = os.environ.get("GOOGLE_CREDENTIALS_JSON")
            if env_creds:
                raw = _base64.b64decode(env_creds).decode("utf-8")
                return _json.loads(raw)
            creds_path = os.path.join(os.path.dirname(__file__), "credentials.json")
            with open(creds_path) as f:
                return _json.load(f)

        def _extract_bearer(request, prefer_query: bool = False) -> tuple[str, str]:
            q_key = (
                request.query_params.get("api_key", "").strip()
                or request.query_params.get("token", "").strip()
            )
            if prefer_query and q_key:
                return q_key, "query"
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:].strip(), "header"
            # Accept common API key header variants used by MCP clients/gateways.
            key = (
                request.headers.get("X-API-Key", "").strip()
                or request.headers.get("X-BLToken", "").strip()
                or request.headers.get("Api-Key", "").strip()
            )
            if key:
                return key, "header"
            # Query fallback for clients that fail to forward headers on SSE connect.
            if q_key:
                return q_key, "query"
            return "", "none"

        def _token_fingerprint(token: str) -> str:
            if not token:
                return "<none>"
            digest = _hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
            return f"{token[:6]}...{digest}"

        def _log_auth(route: str, source: str, token: str, decision: str) -> None:
            print(
                f"[auth] route={route} source={source} token={_token_fingerprint(token)} decision={decision}"
            )

        def _now_ts() -> float:
            import time
            return time.time()

        def _prune_sessions() -> None:
            now = _now_ts()
            for store in (_message_sessions, _fingerprint_sessions):
                expired = [k for k, v in store.items() if v.get("expires_at", 0) <= now]
                for k in expired:
                    store.pop(k, None)

        def _client_fingerprint(request) -> str:
            xff = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            xrip = request.headers.get("X-Real-IP", "").strip()
            ua = request.headers.get("User-Agent", "").strip()
            return _hashlib.sha256(f"{xff}|{xrip}|{ua}".encode("utf-8")).hexdigest()[:16]

        def _create_message_session(api_key: str, request) -> str:
            _prune_sessions()
            sid = _uuid.uuid4().hex
            expires_at = _now_ts() + _session_ttl_seconds
            _message_sessions[sid] = {"api_key": api_key, "expires_at": expires_at}
            _fingerprint_sessions[_client_fingerprint(request)] = {
                "api_key": api_key,
                "expires_at": expires_at,
            }
            return sid

        def _resolve_session_key(request) -> str:
            _prune_sessions()
            sid = (
                request.query_params.get("session_id", "").strip()
                or request.cookies.get("mcp_session_id", "").strip()
            )
            if sid:
                sess = _message_sessions.get(sid)
                if sess and sess.get("expires_at", 0) > _now_ts():
                    return sess.get("api_key", "")
            fp_sess = _fingerprint_sessions.get(_client_fingerprint(request))
            if fp_sess and fp_sess.get("expires_at", 0) > _now_ts():
                return fp_sess.get("api_key", "")
            return ""

        def _resolve_credentials(api_key: str) -> "Credentials | None":
            """Return cached or freshly loaded Credentials for an API key."""
            if api_key in _creds_cache:
                creds = _creds_cache[api_key]
                if creds.valid:
                    return creds
            user = get_user(api_key)
            if not user:
                return None
            creds = Credentials.from_authorized_user_info(
                _json.loads(user["token_json"]), SCOPES
            )
            _creds_cache[api_key] = creds
            return creds

        # ── Middleware ────────────────────────────────────────────────────────
        class APIKeyMiddleware:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope.get("type") != "http":
                    await self.app(scope, receive, send)
                    return

                request = Request(scope, receive=receive)
                path = request.url.path
                # Public routes
                if path == "/healthz" or path.startswith("/setup") or path == "/auth/callback":
                    await self.app(scope, receive, send)
                    return

                key, source = _extract_bearer(request, prefer_query=(path == "/sse"))
                if not key and path == "/messages":
                    key = _resolve_session_key(request)
                    source = "session-fallback" if key else "none"
                if not key:
                    _log_auth(path, source, key, "rejected")
                    response = Response("Unauthorized – missing token", status_code=401)
                    await response(scope, receive, send)
                    return

                if _legacy_api_key and key == _legacy_api_key:
                    _log_auth(path, source, key, "accepted")
                    await self.app(scope, receive, send)
                    return

                if not is_valid_key(key):
                    _log_auth(path, source, key, "rejected")
                    response = Response("Unauthorized – unknown token", status_code=401)
                    await response(scope, receive, send)
                    return

                _log_auth(path, source, key, "accepted")
                await self.app(scope, receive, send)

        # ── SSE / MCP handlers ────────────────────────────────────────────────
        sse = SseServerTransport("/messages")

        class SseEndpoint:
            async def __call__(self, scope, receive, send):
                request = Request(scope, receive=receive)
                key, source = _extract_bearer(request, prefer_query=True)
                creds = None
                session_id = ""

                if key and key != _legacy_api_key:
                    creds = await asyncio.to_thread(_resolve_credentials, key)
                    if creds:
                        session_id = _create_message_session(key, request)
                        _log_auth("/sse", source, key, "accepted")

                creds_token = _per_user_creds.set(creds)
                key_token = _per_user_api_key.set(key if creds else None)
                try:
                    async with sse.connect_sse(scope, receive, send) as streams:
                        await mcp._mcp_server.run(
                            streams[0], streams[1],
                            mcp._mcp_server.create_initialization_options(),
                        )
                finally:
                    _per_user_creds.reset(creds_token)
                    _per_user_api_key.reset(key_token)
                    if session_id:
                        _message_sessions.pop(session_id, None)

        class MessagesEndpoint:
            async def __call__(self, scope, receive, send):
                request = Request(scope, receive=receive)
                key, source = _extract_bearer(request)
                if not key:
                    key = _resolve_session_key(request)
                    source = "session-fallback" if key else "none"
                creds = None

                if key and key != _legacy_api_key:
                    creds = await asyncio.to_thread(_resolve_credentials, key)
                _log_auth("/messages", source, key, "accepted" if key else "rejected")

                creds_token = _per_user_creds.set(creds)
                key_token = _per_user_api_key.set(key if creds else None)
                try:
                    await sse.handle_post_message(scope, receive, send)
                finally:
                    _per_user_creds.reset(creds_token)
                    _per_user_api_key.reset(key_token)

        # ── Setup / onboarding pages ──────────────────────────────────────────
        _SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Google Chat MCP – Connect</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f0f4ff;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .card {
      background: #fff;
      border-radius: 20px;
      padding: 48px 40px;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 8px 40px rgba(0,0,0,0.10);
      text-align: center;
    }
    .logo {
      width: 64px; height: 64px;
      background: linear-gradient(135deg, #4285F4, #34A853);
      border-radius: 18px;
      margin: 0 auto 24px;
      display: flex; align-items: center; justify-content: center;
    }
    .logo svg { width: 36px; height: 36px; fill: #fff; }
    h1 { font-size: 22px; font-weight: 700; color: #111827; margin-bottom: 12px; }
    .subtitle { color: #6b7280; font-size: 15px; line-height: 1.6; margin-bottom: 36px; }
    .btn-google {
      display: inline-flex; align-items: center; gap: 12px;
      background: #fff;
      border: 2px solid #e5e7eb;
      border-radius: 12px;
      padding: 14px 28px;
      font-size: 15px; font-weight: 600; color: #374151;
      text-decoration: none;
      cursor: pointer;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .btn-google:hover {
      border-color: #4285F4;
      box-shadow: 0 4px 16px rgba(66,133,244,0.18);
    }
    .note { margin-top: 28px; font-size: 13px; color: #9ca3af; line-height: 1.6; }
    .note strong { color: #6b7280; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <svg viewBox="0 0 24 24"><path d="M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/></svg>
    </div>
    <h1>Google Chat MCP</h1>
    <p class="subtitle">Connect your Google account to use Google Chat through Claude, Notion AI, or any AI assistant that supports MCP.</p>
    <a href="/setup/auth" class="btn-google">
      <svg width="20" height="20" viewBox="0 0 48 48">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
      </svg>
      Sign in with Google
    </a>
    <p class="note">
      <strong>Only @supplyswap.com accounts are supported.</strong><br>
      Your messages are never stored — only a secure access token.
    </p>
  </div>
</body>
</html>"""

        def _success_html(api_key: str, email: str) -> str:
            sse_url = f"{_server_url}/sse"
            return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Google Chat MCP – Connected!</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f0f4ff;
      min-height: 100vh;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 40px 24px;
    }}
    .card {{
      background: #fff;
      border-radius: 20px;
      padding: 48px 40px;
      max-width: 520px;
      width: 100%;
      box-shadow: 0 8px 40px rgba(0,0,0,0.10);
    }}
    .check {{
      width: 56px; height: 56px;
      background: #dcfce7;
      border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      margin: 0 auto 20px;
      font-size: 26px;
    }}
    h1 {{ font-size: 22px; font-weight: 700; color: #111827; text-align: center; margin-bottom: 6px; }}
    .email {{ text-align: center; color: #6b7280; font-size: 14px; margin-bottom: 32px; }}
    .section {{ margin-bottom: 20px; }}
    .section label {{
      display: block;
      font-size: 12px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.06em;
      color: #9ca3af;
      margin-bottom: 8px;
    }}
    .field-row {{
      display: flex;
      align-items: center;
      background: #f8fafc;
      border: 1.5px solid #e5e7eb;
      border-radius: 10px;
      overflow: hidden;
    }}
    .field-value {{
      flex: 1;
      padding: 12px 14px;
      font-size: 14px;
      font-family: 'SF Mono', 'Fira Code', monospace;
      color: #374151;
      word-break: break-all;
    }}
    .copy-btn {{
      padding: 12px 16px;
      border: none;
      background: transparent;
      cursor: pointer;
      color: #6b7280;
      font-size: 13px;
      font-weight: 600;
      border-left: 1.5px solid #e5e7eb;
      transition: background 0.15s, color 0.15s;
      white-space: nowrap;
    }}
    .copy-btn:hover {{ background: #f0f4ff; color: #4285F4; }}
    .token-highlight .field-row {{
      border-color: #4285F4;
      background: #f0f4ff;
    }}
    .divider {{ border: none; border-top: 1.5px solid #f3f4f6; margin: 28px 0; }}
    .steps h3 {{ font-size: 15px; font-weight: 700; color: #111827; margin-bottom: 14px; }}
    .steps ol {{ padding-left: 20px; color: #374151; font-size: 14px; line-height: 2; }}
    .steps ol li span {{ font-family: monospace; background: #f3f4f6; padding: 1px 6px; border-radius: 4px; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="check">✅</div>
    <h1>You're connected!</h1>
    <p class="email">Signed in as <strong>{email}</strong></p>

    <div class="section">
      <label>MCP Server URL</label>
      <div class="field-row">
        <span class="field-value" id="url-val">{sse_url}</span>
        <button class="copy-btn" onclick="copy('url-val', this)">Copy</button>
      </div>
    </div>

    <div class="section">
      <label>Authentication type</label>
      <div class="field-row">
        <span class="field-value">Bearer token</span>
      </div>
    </div>

    <div class="section token-highlight">
      <label>Your token (keep this private)</label>
      <div class="field-row">
        <span class="field-value" id="key-val">{api_key}</span>
        <button class="copy-btn" onclick="copy('key-val', this)">Copy</button>
      </div>
    </div>

    <hr class="divider" />

    <div class="steps">
      <h3>How to add to your AI tool</h3>
      <ol>
        <li>Open your AI tool and go to <strong>Settings → MCP / Integrations</strong></li>
        <li>Click <strong>Add Custom MCP</strong> or <strong>Connect MCP</strong></li>
        <li>Paste the <strong>MCP Server URL</strong> above</li>
        <li>Select <span>Bearer token</span> as the authentication type</li>
        <li>Paste your <strong>token</strong> into the Token field</li>
        <li>Click <strong>Connect</strong> — you're done!</li>
      </ol>
    </div>
  </div>
  <script>
    function copy(id, btn) {{
      const val = document.getElementById(id).textContent;
      navigator.clipboard.writeText(val).then(() => {{
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        btn.style.color = '#16a34a';
        setTimeout(() => {{ btn.textContent = orig; btn.style.color = ''; }}, 2000);
      }});
    }}
  </script>
</body>
</html>"""

        async def handle_setup(request):
            return HTMLResponse(_SETUP_HTML)

        async def handle_setup_auth(request):
            """Start the OAuth flow for a new user."""
            try:
                client_config = _load_client_config()
            except Exception as e:
                return HTMLResponse(f"<p>Error loading credentials: {e}</p>", status_code=500)

            callback_uri = f"{_server_url}/auth/callback"
            flow = Flow.from_client_config(
                client_config, SCOPES, redirect_uri=callback_uri
            )
            auth_url, state = flow.authorization_url(
                access_type="offline",
                prompt="consent",
                include_granted_scopes="true",
            )
            _oauth_flows[state] = flow
            return RedirectResponse(url=auth_url)

        async def handle_auth_callback(request):
            """Exchange OAuth code for token, persist to Firestore, show success."""
            params = dict(request.query_params)
            error = params.get("error")
            if error:
                return HTMLResponse(f"<p>Auth error: {error}</p>", status_code=400)

            state = params.get("state", "")
            code = params.get("code", "")
            flow = _oauth_flows.pop(state, None)
            if not flow:
                return HTMLResponse("<p>Invalid or expired OAuth state. Please try again.</p>", status_code=400)

            try:
                flow.fetch_token(code=code)
                creds = flow.credentials
                if not creds.refresh_token:
                    return HTMLResponse("<p>No refresh token received. Please try again.</p>", status_code=400)

                # Get the user's email from the id_token
                import google.auth.transport.requests as _tr
                import google.oauth2.id_token as _id_token
                email = "unknown@supplyswap.com"
                try:
                    id_info = _id_token.verify_oauth2_token(
                        creds.id_token, _tr.Request(), clock_skew_in_seconds=10
                    )
                    email = id_info.get("email", email)
                except Exception:
                    pass

                api_key = await asyncio.to_thread(create_user, email, creds.to_json())
                _creds_cache[api_key] = creds
                return HTMLResponse(_success_html(api_key, email))
            except Exception as e:
                return HTMLResponse(f"<p>Error during authentication: {e}</p>", status_code=500)

        async def handle_healthz(request):
            return Response(
                _json.dumps(
                    {
                        "ok": True,
                        "service": "google-chat-mcp",
                        "revision": os.environ.get("K_REVISION", "unknown"),
                    }
                ),
                media_type="application/json",
            )

        starlette_app = Starlette(
            routes=[
                Route("/healthz", endpoint=handle_healthz),
                Route("/setup", endpoint=handle_setup),
                Route("/setup/auth", endpoint=handle_setup_auth),
                Route("/auth/callback", endpoint=handle_auth_callback),
                Route("/sse", endpoint=SseEndpoint()),
                Route("/messages", endpoint=MessagesEndpoint(), methods=["POST"]),
            ],
            middleware=[Middleware(APIKeyMiddleware)],
        )

        print(f"\nStarting Google Chat MCP server in SSE mode")
        print(f"  SSE endpoint  : http://0.0.0.0:{_port}/sse")
        print(f"  Setup page    : http://0.0.0.0:{_port}/setup")
        print(f"  Public URL    : {_server_url}")
        print(f"  Auth          : multi-user (Firestore) {'+ legacy key' if _legacy_api_key else ''}")
        print("\nPress CTRL+C to stop\n")

        async def run():
            config = uvicorn.Config(starlette_app, host="0.0.0.0", port=_port)
            server = uvicorn.Server(config)
            await server.serve()

        asyncio.run(run())
    else:
        mcp.run()