import os
import hashlib
import json
import time
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

_client: Client = None

def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"]
        )
    return _client


def make_idempotency_key(vapi_call_id: str, tool_name: str, payload: dict) -> str:
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:12]
    return f"{vapi_call_id}:{tool_name}:{payload_hash}"


def check_idempotency(key: str) -> dict | None:
    """Return cached response if this exact request already succeeded."""
    db = get_db()
    try:
        result = (
            db.table("idempotency_keys")
            .select("*")
            .eq("key", key)
            .eq("status", "success")
            .single()
            .execute()
        )
        return result.data
    except Exception:
        return None


def save_idempotency(key: str, tool_name: str, response: dict):
    db = get_db()
    db.table("idempotency_keys").upsert({
        "key": key,
        "tool_name": tool_name,
        "response": response,
        "status": "success",
    }).execute()


_venue_by_phone_cache = {}  # phone_number -> (venue_id, timestamp)
_venue_by_id_cache = {}     # venue_id -> (venue_data, timestamp)
VENUE_CACHE_TTL = 300       # 5 minutes cache lifetime


def get_venue_id_by_phone(phone_number: str) -> str | None:
    """Retrieves venue_id by phone number from memory cache or database (TTL: 5 min)."""
    now = time.time()
    if phone_number in _venue_by_phone_cache:
        val, ts = _venue_by_phone_cache[phone_number]
        if now - ts < VENUE_CACHE_TTL:
            return val
            
    db = get_db()
    try:
        res = db.table("venues").select("id").eq("phone_number", phone_number).execute()
        if res.data:
            venue_id = res.data[0]["id"]
            _venue_by_phone_cache[phone_number] = (venue_id, now)
            return venue_id
    except Exception:
        pass
    return None


def get_venue_details(venue_id: str) -> dict | None:
    """Retrieves venue configuration details from memory cache or database (TTL: 5 min)."""
    now = time.time()
    if venue_id in _venue_by_id_cache:
        val, ts = _venue_by_id_cache[venue_id]
        if now - ts < VENUE_CACHE_TTL:
            return val
            
    db = get_db()
    try:
        res = db.table("venues").select("phone_number, routing_matrix, staff_schedule, timezone").eq("id", venue_id).execute()
        if res.data:
            venue_data = res.data[0]
            _venue_by_id_cache[venue_id] = (venue_data, now)
            return venue_data
    except Exception:
        pass
    return None
