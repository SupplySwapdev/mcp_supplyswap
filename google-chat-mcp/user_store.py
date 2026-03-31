"""Firestore-backed user store for multi-user MCP token management."""
import json
import secrets
import datetime
from typing import Optional, Tuple
from google.cloud import firestore

_db: Optional[firestore.Client] = None
COLLECTION = "mcp_users"


def _get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def create_user(email: str, token_json: str) -> str:
    """Persist a new user's token and return a fresh API key."""
    api_key = secrets.token_urlsafe(32)
    _get_db().collection(COLLECTION).document(api_key).set({
        "email": email,
        "token_json": token_json,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_used": firestore.SERVER_TIMESTAMP,
    })
    return api_key


def get_user(api_key: str) -> Optional[dict]:
    """Return stored user dict or None if the key is unknown."""
    doc = _get_db().collection(COLLECTION).document(api_key).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    _get_db().collection(COLLECTION).document(api_key).update(
        {"last_used": firestore.SERVER_TIMESTAMP}
    )
    return data


def update_token(api_key: str, token_json: str) -> None:
    """Persist a refreshed token back to Firestore."""
    _get_db().collection(COLLECTION).document(api_key).update({
        "token_json": token_json,
        "last_used": firestore.SERVER_TIMESTAMP,
    })


def is_valid_key(api_key: str) -> bool:
    """Quick existence check (used by the auth middleware for POST /messages)."""
    doc = _get_db().collection(COLLECTION).document(api_key).get()
    return doc.exists
