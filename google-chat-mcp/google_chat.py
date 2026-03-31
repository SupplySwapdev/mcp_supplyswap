import os
import json
import base64
import asyncio
import datetime
from contextvars import ContextVar
from typing import List, Dict, Optional, Tuple
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from pathlib import Path

# Per-SSE-connection credential context (set by server.py before each MCP session)
_per_user_creds: ContextVar[Optional[Credentials]] = ContextVar("per_user_creds", default=None)
_per_user_api_key: ContextVar[Optional[str]] = ContextVar("per_user_api_key", default=None)

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/chat.spaces.readonly',
    'https://www.googleapis.com/auth/chat.messages',
    'https://www.googleapis.com/auth/chat.memberships.readonly',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/contacts.other.readonly',
]

# Cache for user display names: {user_id: display_name}
_user_display_name_cache: Dict[str, str] = {}
DEFAULT_CALLBACK_URL = "http://localhost:8000/auth/callback"
DEFAULT_TOKEN_PATH = 'token.json'

# Store credentials info
token_info = {
    'credentials': None,
    'last_refresh': None,
    'token_path': DEFAULT_TOKEN_PATH
}

def set_token_path(path: str) -> None:
    """Set the global token path for OAuth storage.
    
    Args:
        path: Path where the token should be stored
    """
    token_info['token_path'] = path

# Global flag for message filtering
SAVE_TOKEN_MODE = True

def set_save_token_mode(enabled: bool) -> None:
    """Set whether to filter message fields to save tokens.
    
    Args:
        enabled: True to enable filtering, False to disable
    """
    global SAVE_TOKEN_MODE
    SAVE_TOKEN_MODE = enabled

def save_credentials(creds: Credentials, token_path: Optional[str] = None) -> None:
    """Save credentials to file/Firestore and update in-memory cache."""
    api_key = _per_user_api_key.get()
    if api_key:
        # Multi-user path: persist refreshed token back to Firestore
        try:
            from user_store import update_token
            update_token(api_key, creds.to_json())
            _per_user_creds.set(creds)
        except Exception:
            pass
        return

    # Single-user / local path: save to file
    if token_path is None:
        token_path = token_info['token_path']
    try:
        token_path_obj = Path(token_path)
        token_path_obj.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path_obj, 'w') as f:
            f.write(creds.to_json())
    except Exception:
        pass  # On Cloud Run /tmp may be read-only; in-memory is enough
    token_info['credentials'] = creds
    token_info['last_refresh'] = datetime.datetime.utcnow()

def get_credentials(token_path: Optional[str] = None) -> Optional[Credentials]:
    """Gets valid user credentials from storage or memory.
    
    Checks in order:
    1. Per-request contextvar (multi-user SSE connections)
    2. In-memory cache
    3. GOOGLE_TOKEN_JSON environment variable (base64-encoded token.json content)
    4. Token file on disk
    
    Args:
        token_path: Optional path to token file. If None, uses the configured path.
    
    Returns:
        Credentials object or None if no valid credentials exist
    """
    # Multi-user path: credentials injected per SSE connection
    per_user = _per_user_creds.get()
    if per_user is not None:
        if per_user.expired and per_user.refresh_token:
            try:
                per_user.refresh(Request())
                save_credentials(per_user)  # writes back to Firestore
            except Exception:
                return None
        return per_user if per_user.valid else None

    # Single-user / local path
    if token_path is None:
        token_path = token_info['token_path']
    
    creds = token_info['credentials']
    
    # Check GOOGLE_TOKEN_JSON env var (for cloud deployment without a mounted volume)
    if not creds:
        token_json_env = os.environ.get('GOOGLE_TOKEN_JSON')
        if token_json_env:
            try:
                token_data = base64.b64decode(token_json_env).decode('utf-8')
                creds = Credentials.from_authorized_user_info(json.loads(token_data), SCOPES)
                token_info['credentials'] = creds
            except Exception:
                pass
    
    # If no credentials in memory, try to load from file
    if not creds:
        token_path_obj = Path(token_path)
        if token_path_obj.exists():
            creds = Credentials.from_authorized_user_file(str(token_path_obj), SCOPES)
            token_info['credentials'] = creds
    
    # If we have credentials that need refresh
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds, token_path)
        except Exception:
            return None
    
    return creds if (creds and creds.valid) else None

async def refresh_token(token_path: Optional[str] = None) -> Tuple[bool, str]:
    """Attempt to refresh the current token.
    
    Args:
        token_path: Path to the token file. If None, uses the configured path.
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    if token_path is None:
        token_path = token_info['token_path']
        
    try:
        creds = token_info['credentials']
        if not creds:
            token_path = Path(token_path)
            if not token_path.exists():
                return False, "No token file found"
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        
        if not creds.refresh_token:
            return False, "No refresh token available"
        
        creds.refresh(Request())
        save_credentials(creds, token_path)
        return True, "Token refreshed successfully"
    except Exception as e:
        return False, f"Failed to refresh token: {str(e)}"

def get_user_display_name(sender: Dict, creds=None) -> str:
    """Returns a readable sender label from the Chat API sender object.

    The Chat API does not include displayName for most users in message/membership
    responses, and People API lookups require additional scopes that often fail.
    We use the displayName if the API happens to provide it, otherwise return a
    short readable form of the user/bot ID. No extra HTTP calls are made.
    """
    user_id = sender.get('name', '')
    sender_type = sender.get('type', 'HUMAN')

    if user_id in _user_display_name_cache:
        return _user_display_name_cache[user_id]

    # Use displayName if the Chat API returned it
    if sender.get('displayName'):
        name = sender['displayName']
        _user_display_name_cache[user_id] = name
        return name

    # Shorten the numeric ID to something readable
    numeric = user_id.replace('users/', '').replace('bots/', '')
    if sender_type == 'BOT':
        name = f"Bot-{numeric[:6]}"
    else:
        name = f"User-{numeric[:6]}" if numeric else 'Unknown'

    _user_display_name_cache[user_id] = name
    return name


# MCP functions
async def list_chat_spaces(space_type: Optional[str] = None) -> List[Dict]:
    """Lists all Google Chat spaces the user has access to.
    
    Args:
        space_type: Optional filter — 'SPACE' (named rooms), 'GROUP_CHAT', or 'DIRECT_MESSAGE'.
                    If omitted, returns all types.
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        service = build('chat', 'v1', credentials=creds)

        filter_str = None
        if space_type:
            filter_str = f"spaceType = \"{space_type.upper()}\""

        all_spaces = []
        page_token = None
        while True:
            args: Dict = {'pageSize': 100}
            if filter_str:
                args['filter'] = filter_str
            if page_token:
                args['pageToken'] = page_token
            response = service.spaces().list(**args).execute()
            all_spaces.extend(response.get('spaces', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break

        return [
            {
                'name': s.get('name'),
                'displayName': s.get('displayName') or '(DM)',
                'spaceType': s.get('spaceType'),
                'memberCount': s.get('memberCount'),
            }
            for s in all_spaces
        ]
    except Exception as e:
        raise Exception(f"Failed to list chat spaces: {str(e)}")


def _fmt_dt(dt: datetime.datetime) -> str:
    """Format a datetime as RFC3339 UTC string expected by the Chat API filter."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_message_filter(
    start_date: Optional[datetime.datetime],
    end_date: Optional[datetime.datetime],
    sender_id: Optional[str] = None,
) -> Optional[str]:
    """Builds a Chat API filter string from optional date/sender constraints."""
    parts = []
    if start_date and end_date:
        parts.append(f"createTime > \"{_fmt_dt(start_date)}\" AND createTime < \"{_fmt_dt(end_date)}\"")
    elif start_date:
        # "since X" — no upper bound, just return everything after the timestamp
        parts.append(f"createTime > \"{_fmt_dt(start_date)}\"")
    if sender_id:
        parts.append(f"sender = \"{sender_id}\"")
    return " AND ".join(parts) if parts else None


def _format_message(msg: Dict, creds=None) -> Dict:
    """Returns a compact, readable message dict. No extra API calls."""
    sender = msg.get('sender', {})
    text = msg.get('text', '')
    return {
        'name': msg.get('name'),
        'sender': get_user_display_name(sender),
        'createTime': msg.get('createTime'),
        'text': text[:2000] if text else '',  # cap very long messages
        'thread': msg.get('thread', {}).get('name'),
        'attachments': len(msg.get('attachment', [])),
    }


async def list_space_messages(
    space_name: str,
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
    sender_id: Optional[str] = None,
    keyword: Optional[str] = None,
    max_results: int = 50,
) -> List[Dict]:
    """Lists messages from a Google Chat space with rich filtering.

    Args:
        space_name: Space identifier, e.g. 'spaces/AAAAxxxxxx'
        start_date: Filter messages on or after this datetime (UTC)
        end_date: Filter messages on or before this datetime (UTC)
        sender_id: Optional user resource name to filter by sender, e.g. 'users/123456'
        keyword: Optional keyword — messages not containing this string are excluded
        max_results: Max total messages to return (default 200)
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        service = build('chat', 'v1', credentials=creds)
        filter_str = _build_message_filter(start_date, end_date, sender_id)

        messages: List[Dict] = []
        page_token = None

        while len(messages) < max_results:
            args: Dict = {'parent': space_name, 'pageSize': min(100, max_results - len(messages))}
            if filter_str:
                args['filter'] = filter_str
            if page_token:
                args['pageToken'] = page_token

            response = service.spaces().messages().list(**args).execute()
            page_msgs = response.get('messages', [])
            messages.extend(page_msgs)

            page_token = response.get('nextPageToken')
            if not page_token:
                break

        if not SAVE_TOKEN_MODE:
            return messages

        formatted = [_format_message(m, creds) for m in messages]

        if keyword:
            kw = keyword.lower()
            formatted = [m for m in formatted if kw in (m.get('text') or '').lower()]

        return formatted

    except Exception as e:
        raise Exception(f"Failed to list messages in space: {str(e)}")


async def send_chat_message(
    space_name: str,
    text: str,
    thread_name: Optional[str] = None,
) -> Dict:
    """Sends a message to a Google Chat space.

    Args:
        space_name: Space identifier, e.g. 'spaces/AAAAxxxxxx'
        text: The message text to send (supports basic markdown)
        thread_name: Optional thread resource name to reply in a thread, e.g. 'spaces/xxx/threads/yyy'

    Returns:
        The created message object
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        service = build('chat', 'v1', credentials=creds)

        body: Dict = {'text': text}
        if thread_name:
            body['thread'] = {'name': thread_name}

        message = service.spaces().messages().create(
            parent=space_name,
            body=body,
            messageReplyOption='REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD' if thread_name else 'MESSAGE_REPLY_OPTION_UNSPECIFIED',
        ).execute()

        return {
            'name': message.get('name'),
            'text': message.get('text'),
            'createTime': message.get('createTime'),
            'thread': message.get('thread', {}).get('name'),
            'sender': message.get('sender', {}).get('name'),
        }
    except Exception as e:
        raise Exception(f"Failed to send message: {str(e)}")


async def get_space_members(space_name: str) -> List[Dict]:
    """Lists all members of a Google Chat space with their display names.

    Args:
        space_name: Space identifier, e.g. 'spaces/AAAAxxxxxx'
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        service = build('chat', 'v1', credentials=creds)

        members: List[Dict] = []
        page_token = None
        while True:
            args: Dict = {'parent': space_name, 'pageSize': 100}
            if page_token:
                args['pageToken'] = page_token
            response = service.spaces().members().list(**args).execute()
            members.extend(response.get('memberships', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break

        result = []
        for m in members:
            member = m.get('member', {})
            display_name = get_user_display_name(member)
            result.append({
                'name': member.get('name'),
                'displayName': display_name,
                'type': member.get('type'),
                'role': m.get('role'),
                'state': m.get('state'),
            })
        return result
    except Exception as e:
        raise Exception(f"Failed to get space members: {str(e)}")


async def get_recent_messages(space_name: str, hours: int = 24, keyword: Optional[str] = None) -> List[Dict]:
    """Gets messages from the last N hours in a space. Shortcut for quick lookups.

    Args:
        space_name: Space identifier, e.g. 'spaces/AAAAxxxxxx'
        hours: How many hours back to look (default: 24). Use 1 for the last hour,
               168 for the last week, etc.
        keyword: Optional keyword to filter messages
    """
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    return await list_space_messages(space_name, start_date=since, keyword=keyword)


def _fetch_space_messages_sync(
    sname: str,
    creds,
    filter_str: Optional[str],
    keyword: Optional[str],
    max_results: int,
) -> List[Dict]:
    """Synchronous inner fetch — safe to run in a thread pool."""
    try:
        service = build('chat', 'v1', credentials=creds)
        messages: List[Dict] = []
        page_token = None
        while len(messages) < max_results:
            args: Dict = {'parent': sname, 'pageSize': min(100, max_results - len(messages))}
            if filter_str:
                args['filter'] = filter_str
            if page_token:
                args['pageToken'] = page_token
            response = service.spaces().messages().list(**args).execute()
            messages.extend(response.get('messages', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        formatted = [_format_message(m) for m in messages]
        if keyword:
            kw = keyword.lower()
            formatted = [m for m in formatted if kw in (m.get('text') or '').lower()]
        for m in formatted:
            m['space'] = sname
        return formatted
    except Exception:
        return []


async def search_messages_by_keyword(
    keyword: str,
    space_name: Optional[str] = None,
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
) -> List[Dict]:
    """Searches for messages containing a keyword — runs all space fetches in parallel threads.

    Without a date filter this searches the most recent 50 messages per space.
    Providing start_date/end_date narrows the window and speeds things up significantly.
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        if space_name:
            spaces_to_search = [space_name]
        else:
            spaces_response = await list_chat_spaces()
            spaces_to_search = [s['name'] for s in spaces_response]

        filter_str = _build_message_filter(start_date, end_date)

        # Use thread pool so synchronous googleapiclient calls run truly in parallel
        tasks = [
            asyncio.to_thread(_fetch_space_messages_sync, sname, creds, filter_str, keyword, 50)
            for sname in spaces_to_search
        ]
        batches = await asyncio.gather(*tasks)
        return [msg for batch in batches for msg in batch]
    except Exception as e:
        raise Exception(f"Search failed: {str(e)}")


def _get_current_user_id(creds) -> Optional[str]:
    """Returns the authenticated user's Chat user resource name."""
    try:
        import googleapiclient.discovery as disc
        oauth2 = disc.build('oauth2', 'v2', credentials=creds)
        info = oauth2.userinfo().get().execute()
        user_id = info.get('id')
        return f"users/{user_id}" if user_id else None
    except Exception:
        return None


def _search_people(query: str, creds) -> List[Dict]:
    """Searches contacts + other contacts for a person by name/email.
    Returns list of {resourceName, displayName} dicts.
    Requires contacts.other.readonly scope.
    """
    try:
        people_service = build('people', 'v1', credentials=creds)
        results = people_service.people().searchContacts(
            query=query,
            readMask='names,emailAddresses',
            sources=['READ_SOURCE_TYPE_CONTACT', 'READ_SOURCE_TYPE_OTHER_CONTACT'],
            pageSize=10,
        ).execute()
        people = []
        for r in results.get('results', []):
            person = r.get('person', {})
            resource = person.get('resourceName', '')
            names = person.get('names', [])
            display = names[0].get('displayName', resource) if names else resource
            people.append({'resourceName': resource, 'displayName': display})
        return people
    except Exception:
        return []


async def find_dm_space(person_query: str) -> Optional[Dict]:
    """Finds the DM space with a specific person.

    Accepts a name (e.g. 'Iustin'), full name ('Iustin Zaharioiu'), or email
    ('iustin@supplyswap.com'). Uses the Chat API findDirectMessage endpoint which
    accepts email addresses directly, and falls back to a People API name search
    to resolve the email when only a name is provided.

    Returns:
        Dict with 'space_name' and 'display_name', or raises if not found.
    """
    creds = get_credentials()
    if not creds:
        raise Exception("No valid credentials found. Please authenticate first.")

    service = build('chat', 'v1', credentials=creds)

    # If the query looks like an email, use it directly
    email = person_query if '@' in person_query else None

    if not email:
        # Resolve name → email via otherContacts search
        people_service = build('people', 'v1', credentials=creds)
        try:
            resp = people_service.otherContacts().search(
                query=person_query,
                readMask='names,emailAddresses',
                pageSize=5,
            ).execute()
            results = resp.get('results', [])
            if not results:
                raise Exception(
                    f"Could not find a contact named '{person_query}'. "
                    "Try their full name or email address (e.g. 'iustin@supplyswap.com')."
                )
            # Use the first result that has an email
            for r in results:
                person = r.get('person', {})
                emails = person.get('emailAddresses', [])
                if emails:
                    email = emails[0]['value']
                    names = person.get('names', [])
                    display_name = names[0]['displayName'] if names else email
                    break
            if not email:
                raise Exception(f"Found '{person_query}' in contacts but no email address is available.")
        except Exception as e:
            if 'Could not find' in str(e) or 'no email' in str(e):
                raise
            raise Exception(f"Contact lookup failed: {str(e)}")

    # Resolve display name if we only have email (was passed directly)
    if 'display_name' not in locals():
        display_name = email

    # Use Chat API findDirectMessage with the email
    try:
        space = service.spaces().findDirectMessage(name=f'users/{email}').execute()
        return {
            'space_name': space.get('name'),
            'display_name': display_name,
        }
    except Exception as e:
        if '404' in str(e):
            raise Exception(
                f"No DM conversation found with '{person_query}' ({email}). "
                "Start a DM with them in Google Chat first."
            )
        raise Exception(f"Failed to find DM: {str(e)}")


async def get_dm_messages(
    person_query: str,
    hours: int = 48,
    keyword: Optional[str] = None,
) -> List[Dict]:
    """Gets messages from the DM conversation with a specific person.

    Args:
        person_query: Name or partial name of the person (e.g. 'Iustin', 'Ruanna')
        hours: How many hours back to look (default 48)
        keyword: Optional keyword to filter messages
    """
    dm = await find_dm_space(person_query)
    if not dm:
        raise Exception(f"No DM conversation found with '{person_query}'. Check the name and try again.")

    messages = await get_recent_messages(dm['space_name'], hours=hours, keyword=keyword)
    for m in messages:
        m['dm_with'] = dm['display_name']
        m['space_name'] = dm['space_name']
    return messages


async def send_dm(person_query: str, text: str) -> Dict:
    """Sends a direct message to a specific person.

    Args:
        person_query: Name or partial name of the person (e.g. 'Iustin', 'Ruanna')
        text: Message text to send
    """
    dm = await find_dm_space(person_query)
    if not dm:
        raise Exception(f"No DM conversation found with '{person_query}'. Check the name and try again.")

    result = await send_chat_message(dm['space_name'], text)
    result['dm_with'] = dm['display_name']
    return result


async def add_reaction(message_name: str, emoji: str) -> Dict:
    """Adds an emoji reaction to a message.

    Args:
        message_name: Full message resource name, e.g. 'spaces/xxx/messages/yyy'
        emoji: Emoji unicode character or shortcode, e.g. '👍', '✅', '🎉'
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        service = build('chat', 'v1', credentials=creds)

        # Strip variation selectors and determine emoji type
        emoji_char = emoji.strip()
        body = {'emoji': {'unicode': emoji_char}}

        result = service.spaces().messages().reactions().create(
            parent=message_name,
            body=body,
        ).execute()

        return {'name': result.get('name'), 'emoji': emoji_char, 'message': message_name}
    except Exception as e:
        raise Exception(f"Failed to add reaction: {str(e)}")


async def get_space_info(space_name: str) -> Dict:
    """Gets detailed information about a Google Chat space.

    Args:
        space_name: Space resource name, e.g. 'spaces/AAAAxxxxxx'
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        service = build('chat', 'v1', credentials=creds)
        space = service.spaces().get(name=space_name).execute()

        return {
            'name': space.get('name'),
            'displayName': space.get('displayName') or '(DM)',
            'spaceType': space.get('spaceType'),
            'description': space.get('spaceDetails', {}).get('description', ''),
            'guidelines': space.get('spaceDetails', {}).get('guidelines', ''),
            'memberCount': space.get('memberCount'),
            'adminInstalled': space.get('adminInstalled'),
            'createTime': space.get('createTime'),
            'lastActiveTime': space.get('lastActiveTime'),
            'externalUserAllowed': space.get('externalUserAllowed'),
        }
    except Exception as e:
        raise Exception(f"Failed to get space info: {str(e)}")


async def get_thread_messages(space_name: str, thread_name: str) -> List[Dict]:
    """Gets all messages in a specific thread.

    Args:
        space_name: Space resource name, e.g. 'spaces/AAAAxxxxxx'
        thread_name: Thread resource name, e.g. 'spaces/xxx/threads/yyy'
    """
    try:
        creds = get_credentials()
        if not creds:
            raise Exception("No valid credentials found. Please authenticate first.")

        service = build('chat', 'v1', credentials=creds)

        messages = []
        page_token = None
        while True:
            args: Dict = {
                'parent': space_name,
                'pageSize': 100,
                'filter': f'thread.name = "{thread_name}"',
            }
            if page_token:
                args['pageToken'] = page_token
            response = service.spaces().messages().list(**args).execute()
            messages.extend(response.get('messages', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break

        return [_format_message(m, creds) for m in messages]
    except Exception as e:
        raise Exception(f"Failed to get thread messages: {str(e)}")
    
