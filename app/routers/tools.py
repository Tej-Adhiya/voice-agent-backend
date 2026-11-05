import os
import time
import json
import uuid
import datetime
import httpx
from zoneinfo import ZoneInfo
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from app.models.requests import (
    CheckAvailabilityRequest,
    CreateAppointmentRequest,
    ModifyAppointmentRequest,
    CancelAppointmentRequest,
    GetGuestProfileRequest,
    RouteTransferRequest,
    GetMenuRecommendationsRequest,
)
from app.db.database import (
    get_db,
    make_idempotency_key,
    check_idempotency,
    save_idempotency,
    get_venue_id_by_phone,
    get_venue_details,
)
from app.services.sevenrooms import SevenRoomsClient
from app.services.menu import MenuService

router = APIRouter()
sevenrooms_client = SevenRoomsClient()
menu_service = MenuService()

DEFAULT_VENUE_ID = os.environ.get("DEFAULT_VENUE_ID", "")


def is_valid_uuid(val: str) -> bool:
    if not val:
        return False
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False


def parse_vapi_payload(payload: dict):
    """
    Extracts the tool call arguments and the toolCallId from a Vapi payload.
    Supports both direct raw payloads and wrapped Vapi tool-calls payloads.
    """
    tool_call_id = None
    args = {}

    # Check if it's a wrapped Vapi request
    if "message" in payload and isinstance(payload["message"], dict):
        message = payload["message"]
        
        # 1. Try toolCalls list
        tool_calls = message.get("toolCalls") or message.get("toolCallList")
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            tc = tool_calls[0]
            tool_call_id = tc.get("id")
            func = tc.get("function", {})
            raw_args = func.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            elif isinstance(raw_args, dict):
                args = raw_args
        
        # 2. Try single toolCall object
        elif "toolCall" in message and isinstance(message["toolCall"], dict):
            tc = message["toolCall"]
            tool_call_id = tc.get("id")
            raw_args = tc.get("parameters") or tc.get("arguments") or {}
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            elif isinstance(raw_args, dict):
                args = raw_args
    else:
        # Direct raw payload (e.g. from curl or direct webhook testing)
        args = payload
        # It might contain vapi_call_id at the root
        tool_call_id = payload.get("vapi_call_id") or payload.get("tool_call_id")
        
    return tool_call_id, args


def get_venue_by_inbound_number(payload: dict) -> str:
    """Tries to resolve venue ID by looking up the Twilio/Vapi number in the DB. Falls back to the default venue."""
    call_obj = payload.get("message", {}).get("call", {})
    inbound_number = (
        call_obj.get("phoneNumber") or
        call_obj.get("phone_number", {}).get("number")
    )
    if not inbound_number:
        return DEFAULT_VENUE_ID
        
    venue_id = get_venue_id_by_phone(inbound_number)
    return venue_id or DEFAULT_VENUE_ID


def _is_available(phone: str, staff_schedule: dict, timezone_str: str) -> bool:
    """
    Returns True if the staff member is currently within their scheduled hours.
    Falls back to True (always available) if no schedule entry exists or on any error.
    days use Python isoweekday: 1=Monday, 7=Sunday.
    """
    schedule = staff_schedule.get(phone)
    if not schedule:
        return True  # No schedule defined — treat as always available
    try:
        tz = ZoneInfo(timezone_str)
        now = datetime.datetime.now(tz)
        current_day = now.isoweekday()          # 1=Mon ... 7=Sun
        current_hhmm = now.strftime("%H:%M")
        if current_day not in schedule.get("days", list(range(1, 8))):
            return False
        return schedule.get("open", "00:00") <= current_hhmm <= schedule.get("close", "23:59")
    except Exception:
        return True  # Never block a transfer on a schedule-check error


# ============================================================
# 1. CHECK AVAILABILITY
# ============================================================
@router.post("/check_availability")
async def check_availability(request: Request):
    payload = await request.json()
    tool_call_id, args = parse_vapi_payload(payload)

    preferred_date = args.get("preferred_date")
    preferred_time = args.get("preferred_time")
    party_size = int(args.get("party_size") or 2)
    venue_id = args.get("venue_id") or get_venue_by_inbound_number(payload)
    appointment_id = args.get("appointment_id")

    if not preferred_date or not preferred_time:
        raise HTTPException(status_code=422, detail="Missing preferred_date or preferred_time")

    # Resolve reservation GUID to discard from availability calculations if rescheduling
    sr_reservation_id = None
    if appointment_id:
        db = get_db()
        # Combine lookups into a single query
        filters = [
            f"sevenrooms_reservation_id.eq.{appointment_id}",
            f"reference_code.eq.{appointment_id}"
        ]
        if is_valid_uuid(appointment_id):
            filters.append(f"id.eq.{appointment_id}")
        
        try:
            res = db.table("appointments").select("*").or_(",".join(filters)).execute()
            if res.data:
                sr_reservation_id = res.data[0].get("sevenrooms_reservation_id")
            else:
                # Fallback to fetch from SevenRooms directly if not in DB
                sr_appt = await sevenrooms_client.get_reservation(appointment_id)
                if sr_appt:
                    sr_reservation_id = sr_appt.get("id")
        except Exception:
            pass

    # Calculate search window (+/- 1 hr)
    try:
        hr, mn = map(int, preferred_time.split(":"))
        start_hr = max(0, hr - 1)
        end_hr = min(23, hr + 1)
        start_time = f"{start_hr:02d}:{mn:02d}"
        end_time = f"{end_hr:02d}:{mn:02d}"
    except Exception:
        start_time = preferred_time
        end_time = preferred_time

    # Call SevenRooms API
    try:
        slots = await sevenrooms_client.check_availability(
            venue_id=venue_id,
            date=preferred_date,
            party_size=party_size,
            start_time=start_time,
            end_time=end_time,
            reservation_id=sr_reservation_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SevenRooms availability check failed: {str(e)}")

    is_available = any(s.get("time") == preferred_time for s in slots)
    alternative_slots = [s.get("time") for s in slots if s.get("time") != preferred_time]

    if is_available:
        msg = f"The slot on {preferred_date} at {preferred_time} is available."
    elif slots:
        msg = f"Sorry, {preferred_time} is not available, but I have other slots available on that day at: {', '.join(alternative_slots[:3])}."
    else:
        msg = f"Sorry, there are no slots available on {preferred_date} around that time."

    result_data = {
        "available": is_available,
        "date": preferred_date,
        "time": preferred_time,
        "alternative_slots": alternative_slots,
        "message": msg
    }

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(result_data)
                }
            ]
        }
    return result_data


# ============================================================
# 2.5 ADD TO CANCELLATION LIST / WAITLIST
# ============================================================
@router.post("/add_to_cancellation_list")
async def add_to_cancellation_list(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    tool_call_id, args = parse_vapi_payload(payload)

    vapi_call_id = payload.get("message", {}).get("call", {}).get("id") or args.get("vapi_call_id") or "unknown"
    guest_name = args.get("guest_name") or ""
    guest_phone = args.get("guest_phone")
    guest_email = args.get("guest_email")
    preferred_date = args.get("preferred_date")
    preferred_time = args.get("preferred_time")
    party_size = int(args.get("party_size") or 2)
    notes = args.get("notes") or ""
    venue_id = args.get("venue_id") or get_venue_by_inbound_number(payload)

    # Split name into first and last name for SevenRooms Waitlist
    parts = guest_name.strip().split(maxsplit=1)
    first_name = parts[0] if parts else "Unknown"
    last_name = parts[1] if len(parts) > 1 else "Guest"

    # Idempotency check
    ikey = make_idempotency_key(vapi_call_id, "add_to_cancellation_list", args)
    cached = check_idempotency(ikey)
    if cached:
        cached_response = cached.get("response", {})
        if tool_call_id:
            return {
                "results": [
                    {
                        "toolCallId": tool_call_id,
                        "result": json.dumps(cached_response)
                    }
                ]
            }
        return cached_response

    # Try saving to SevenRooms Waitlist first
    sr_waitlist_id = None
    success = False
    error_msg = ""
    try:
        sr_entry = await sevenrooms_client.create_waitlist_entry(
            venue_id=venue_id,
            date=preferred_date,
            party_size=party_size,
            first_name=first_name,
            last_name=last_name,
            phone=guest_phone,
            email=guest_email,
            notes=notes
        )
        sr_waitlist_id = sr_entry.get("id")
        success = True
    except Exception as e:
        error_msg = str(e)
        print(f"SevenRooms Waitlist API failed (falling back to Supabase): {error_msg}")

    # Build response
    response = {
        "success": True,
        "on_sevenrooms_waitlist": success,
        "sevenrooms_waitlist_id": sr_waitlist_id,
        "message": f"Successfully added {guest_name} to the cancellation list for {preferred_date} at {preferred_time}."
    }

    # Save idempotency and log to local Supabase table in the background
    def _log_cancellation():
        try:
            save_idempotency(ikey, "add_to_cancellation_list", response)
        except Exception:
            pass
        try:
            get_db().table("appointments").insert({
                "venue_id":                  venue_id,
                "guest_name":                 guest_name,
                "guest_phone":                guest_phone,
                "guest_email":                guest_email,
                "party_size":                 party_size,
                "preferred_date":             preferred_date,
                "preferred_time":             preferred_time,
                "notes":                      f"SevenRooms Waitlist error: {error_msg}" if not success else f"SevenRooms waitlist ID: {sr_waitlist_id}",
                "status":                     "cancellation_list",
                "vapi_call_id":               vapi_call_id,
                "sevenrooms_reservation_id":  sr_waitlist_id,
            }).execute()
        except Exception as db_err:
            print(f"Supabase logging failed: {db_err}")

    background_tasks.add_task(_log_cancellation)

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(response)
                }
            ]
        }
    return response


# ============================================================
# 2. CREATE APPOINTMENT (RESERVATION)
# ============================================================
@router.post("/create_appointment")
async def create_appointment(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    tool_call_id, args = parse_vapi_payload(payload)

    vapi_call_id = payload.get("message", {}).get("call", {}).get("id") or args.get("vapi_call_id") or "unknown"
    guest_name = args.get("guest_name")
    preferred_date = args.get("preferred_date")
    preferred_time = args.get("preferred_time")
    party_size = int(args.get("party_size") or 2)
    venue_id = args.get("venue_id") or get_venue_by_inbound_number(payload)

    def vapi_error(msg: str):
        """Return a valid Vapi tool result even on error, so the AI gets the message."""
        err_payload = {"success": False, "message": msg}
        if tool_call_id:
            return {
                "results": [
                    {
                        "toolCallId": tool_call_id,
                        "result": json.dumps(err_payload)
                    }
                ]
            }
        raise HTTPException(status_code=500, detail=msg)

    if not guest_name or not preferred_date or not preferred_time:
        return vapi_error("Missing required appointment details: name, date, or time.")

    ikey = make_idempotency_key(vapi_call_id, "create_appointment", args)

    cached = check_idempotency(ikey)
    if cached:
        response = cached["response"]
        if tool_call_id:
            return {
                "results": [
                    {
                        "toolCallId": tool_call_id,
                        "result": json.dumps(response)
                    }
                ]
            }
        return response

    # Parse guest first/last name
    name_parts = guest_name.strip().split(maxsplit=1)
    first_name = name_parts[0] if name_parts else "Guest"
    last_name = name_parts[1] if len(name_parts) > 1 else "Guest"

    guest_phone = args.get("guest_phone") or payload.get("message", {}).get("call", {}).get("customer", {}).get("number") or ""

    # Sanitize email: remove spaces introduced by character-by-character transcription (e.g. "t e j a @ g m a i l . c o m")
    raw_email = args.get("guest_email") or ""
    guest_email = raw_email.replace(" ", "").lower().strip() if raw_email else None
    # Discard if it doesn't look like a valid email after sanitization
    if guest_email and "@" not in guest_email:
        guest_email = None

    notes = args.get("notes") or ""

    # Call SevenRooms API to book (with resilient retry if phone format is rejected)
    try:
        sr_res = await sevenrooms_client.create_reservation(
            venue_id=venue_id,
            date=preferred_date,
            time=preferred_time,
            party_size=party_size,
            first_name=first_name,
            last_name=last_name,
            phone=guest_phone,
            email=guest_email,
            notes=notes
        )
    except Exception as e:
        err_msg = str(e)
        # If SevenRooms rejected the phone format (HTTP 400 Bad Request or validation failure)
        if "phone" in err_msg.lower() or "400" in err_msg or "bad request" in err_msg.lower():
            # Resolve venue's phone number as fallback
            fallback_phone = os.environ.get("FALLBACK_PHONE", "")
            venue_details = get_venue_details(venue_id)
            if venue_details and venue_details.get("phone_number"):
                fallback_phone = venue_details["phone_number"]
            
            # Augment notes with original caller phone number so restaurant staff has it
            notes = f"{notes}\n[System] Original Caller Phone: {guest_phone}".strip()
            try:
                sr_res = await sevenrooms_client.create_reservation(
                    venue_id=venue_id,
                    date=preferred_date,
                    time=preferred_time,
                    party_size=party_size,
                    first_name=first_name,
                    last_name=last_name,
                    phone=fallback_phone,
                    email=guest_email,
                    notes=notes
                )
            except Exception as retry_e:
                return vapi_error(f"SevenRooms booking failed: {str(retry_e)}")
        else:
            return vapi_error(f"SevenRooms booking failed: {str(e)}")

    sr_reservation_id = sr_res.get("reservation_id")
    ref_code = sr_res.get("reservation_reference_code")

    # Build response immediately — SevenRooms is source of truth
    response = {
        "success": True,
        "appointment_id": sr_reservation_id,  # use SR id until DB write completes
        "sevenrooms_reservation_id": sr_reservation_id,
        "reference_code": ref_code,
        "message": f"Successfully created your reservation under the name {guest_name} for {preferred_date} at {preferred_time} for a party of {party_size}. Your reference code is {ref_code}."
    }

    # Save idempotency and log to Supabase in the background — does NOT block the response
    def _log_create():
        try:
            save_idempotency(ikey, "create_appointment", response)
        except Exception:
            pass
        try:
            get_db().table("appointments").insert({
                "venue_id":                  venue_id,
                "guest_name":                 guest_name,
                "guest_phone":                guest_phone,
                "guest_email":                guest_email,
                "service":                    args.get("service"),
                "party_size":                 party_size,
                "preferred_date":             preferred_date,
                "preferred_time":             preferred_time,
                "notes":                      notes,
                "status":                     "confirmed",
                "vapi_call_id":               vapi_call_id,
                "sevenrooms_reservation_id":  sr_reservation_id,
                "reference_code":             ref_code,
            }).execute()
        except Exception:
            pass
    background_tasks.add_task(_log_create)

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(response)
                }
            ]
        }
    return response


# ============================================================
# 3. MODIFY APPOINTMENT (RESERVATION)
# ============================================================
@router.post("/modify_appointment")
async def modify_appointment(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    tool_call_id, args = parse_vapi_payload(payload)

    vapi_call_id = payload.get("message", {}).get("call", {}).get("id") or args.get("vapi_call_id") or "unknown"
    appointment_id = args.get("appointment_id")
    changes = args.get("changes")

    if not appointment_id or not changes:
        raise HTTPException(status_code=422, detail="Missing appointment_id or changes")

    ikey = make_idempotency_key(vapi_call_id, "modify_appointment", args)

    cached = check_idempotency(ikey)
    if cached:
        response = cached["response"]
        if tool_call_id:
            return {
                "results": [
                    {
                        "toolCallId": tool_call_id,
                        "result": json.dumps(response)
                    }
                ]
            }
        return response

    db = get_db()
    sr_reservation_id = None
    venue_id = None
    appt = None

    # Combine lookups into a single query
    filters = [
        f"sevenrooms_reservation_id.eq.{appointment_id}",
        f"reference_code.eq.{appointment_id}"
    ]
    if is_valid_uuid(appointment_id):
        filters.append(f"id.eq.{appointment_id}")

    res = None
    try:
        res = db.table("appointments").select("*").or_(",".join(filters)).execute()
    except Exception:
        pass

    if res and res.data:
        appt = res.data[0]
        sr_reservation_id = appt.get("sevenrooms_reservation_id")
        venue_id = appt.get("venue_id")
    else:
        # If not found in DB at all, query SevenRooms directly
        try:
            sr_appt = await sevenrooms_client.get_reservation(appointment_id)
            if sr_appt:
                sr_reservation_id = sr_appt.get("id")
                venue_id = sr_appt.get("venue_id")
                appt = {
                    "id": None, # Will insert new log
                    "venue_id": venue_id,
                    "guest_name": f"{sr_appt.get('first_name', '')} {sr_appt.get('last_name', '')}".strip() or "Guest",
                    "guest_phone": sr_appt.get("phone_number"),
                    "guest_email": sr_appt.get("email"),
                    "party_size": sr_appt.get("party_size"),
                    "preferred_date": sr_appt.get("date"),
                    "preferred_time": sr_appt.get("arrival_time"),
                    "sevenrooms_reservation_id": sr_reservation_id,
                    "reference_code": sr_appt.get("reference_code"),
                    "status": sr_appt.get("status")
                }
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Reservation '{appointment_id}' not found in database or SevenRooms: {str(e)}")

    if not sr_reservation_id:
        raise HTTPException(status_code=400, detail="This reservation is not linked to SevenRooms.")

    if not venue_id:
        venue_id = args.get("venue_id") or get_venue_by_inbound_number(payload)

    # Get updated values, falling back to existing reservation values
    updated_date = changes.get("preferred_date") or appt["preferred_date"]
    updated_time = changes.get("preferred_time") or appt["preferred_time"]
    updated_party_size = int(changes.get("party_size") or appt.get("party_size") or 2)

    # Call SevenRooms API to modify (prioritized)
    try:
        await sevenrooms_client.modify_reservation(
            venue_id=venue_id,
            reservation_id=sr_reservation_id,
            date=updated_date,
            time=updated_time,
            party_size=updated_party_size
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SevenRooms modification failed: {str(e)}")

    # Build response immediately
    local_id = appt.get("id")
    response = {
        "success": True,
        "appointment_id": local_id or sr_reservation_id,
        "changes_applied": {
            "preferred_date": updated_date,
            "preferred_time": updated_time,
            "party_size": updated_party_size
        },
        "message": "I've successfully updated your reservation in SevenRooms. Someone will send a confirmation shortly."
    }

    # Save idempotency and update or insert local database record in background
    def _log_modify():
        try:
            save_idempotency(ikey, "modify_appointment", response)
        except Exception:
            pass
        try:
            _db = get_db()
            if local_id:
                _db.table("appointments").update({
                    "preferred_date": updated_date,
                    "preferred_time": updated_time,
                    "party_size": updated_party_size,
                    "status": "rescheduled",
                    "updated_at": "now()",
                }).eq("id", local_id).execute()
            else:
                _db.table("appointments").insert({
                    "venue_id":                  venue_id,
                    "guest_name":                 appt["guest_name"],
                    "guest_phone":                appt["guest_phone"],
                    "guest_email":                appt["guest_email"],
                    "party_size":                 updated_party_size,
                    "preferred_date":             updated_date,
                    "preferred_time":             updated_time,
                    "status":                     "rescheduled",
                    "vapi_call_id":               vapi_call_id,
                    "sevenrooms_reservation_id":  sr_reservation_id,
                    "reference_code":             appt["reference_code"],
                }).execute()
        except Exception:
            pass
    background_tasks.add_task(_log_modify)

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(response)
                }
            ]
        }
    return response


# ============================================================
# 4. CANCEL APPOINTMENT (RESERVATION)
# ============================================================
@router.post("/cancel_appointment")
async def cancel_appointment(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    tool_call_id, args = parse_vapi_payload(payload)

    vapi_call_id = payload.get("message", {}).get("call", {}).get("id") or args.get("vapi_call_id") or "unknown"
    appointment_id = args.get("appointment_id")

    if not appointment_id:
        raise HTTPException(status_code=422, detail="Missing appointment_id")

    ikey = make_idempotency_key(vapi_call_id, "cancel_appointment", args)

    cached = check_idempotency(ikey)
    if cached:
        response = cached["response"]
        if tool_call_id:
            return {
                "results": [
                    {
                        "toolCallId": tool_call_id,
                        "result": json.dumps(response)
                    }
                ]
            }
        return response

    db = get_db()
    sr_reservation_id = None
    appt = None

    # Combine lookups into a single query
    filters = [
        f"sevenrooms_reservation_id.eq.{appointment_id}",
        f"reference_code.eq.{appointment_id}"
    ]
    if is_valid_uuid(appointment_id):
        filters.append(f"id.eq.{appointment_id}")

    res = None
    try:
        res = db.table("appointments").select("*").or_(",".join(filters)).execute()
    except Exception:
        pass

    if res and res.data:
        appt = res.data[0]
        sr_reservation_id = appt.get("sevenrooms_reservation_id")
    else:
        # Fallback: check other columns or fetch from SevenRooms directly
        try:
            sr_appt = await sevenrooms_client.get_reservation(appointment_id)
            if sr_appt:
                sr_reservation_id = sr_appt.get("id")
                appt = {
                    "id": None,
                    "venue_id": sr_appt.get("venue_id"),
                    "guest_name": f"{sr_appt.get('first_name', '')} {sr_appt.get('last_name', '')}".strip() or "Guest",
                    "guest_phone": sr_appt.get("phone_number"),
                    "guest_email": sr_appt.get("email"),
                    "party_size": sr_appt.get("party_size"),
                    "preferred_date": sr_appt.get("date"),
                    "preferred_time": sr_appt.get("arrival_time"),
                    "sevenrooms_reservation_id": sr_reservation_id,
                    "reference_code": sr_appt.get("reference_code"),
                    "status": sr_appt.get("status")
                }
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Reservation '{appointment_id}' not found in database or SevenRooms: {str(e)}")

    if not sr_reservation_id:
        raise HTTPException(status_code=400, detail="This reservation is not linked to SevenRooms.")

    # Call SevenRooms API to cancel (prioritized)
    try:
        await sevenrooms_client.cancel_reservation(sr_reservation_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SevenRooms cancellation failed: {str(e)}")

    # Build response immediately
    local_id = appt.get("id")
    response = {
        "success": True,
        "appointment_id": local_id or sr_reservation_id,
        "message": "The reservation has been cancelled in SevenRooms. You'll receive a confirmation shortly."
    }

    # Save idempotency and update or insert database record in background
    def _log_cancel():
        try:
            save_idempotency(ikey, "cancel_appointment", response)
        except Exception:
            pass
        try:
            _db = get_db()
            if local_id:
                _db.table("appointments").update({
                    "status": "cancelled",
                    "updated_at": "now()",
                }).eq("id", local_id).execute()
            else:
                _db.table("appointments").insert({
                    "venue_id":                  appt["venue_id"],
                    "guest_name":                 appt["guest_name"],
                    "guest_phone":                appt["guest_phone"],
                    "guest_email":                appt["guest_email"],
                    "party_size":                 appt["party_size"],
                    "preferred_date":             appt["preferred_date"],
                    "preferred_time":             appt["preferred_time"],
                    "status":                     "cancelled",
                    "vapi_call_id":               vapi_call_id,
                    "sevenrooms_reservation_id":  sr_reservation_id,
                    "reference_code":             appt["reference_code"],
                }).execute()
        except Exception:
            pass
    background_tasks.add_task(_log_cancel)

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(response)
                }
            ]
        }
    return response


# ============================================================
# 5. GET GUEST PROFILE
# ============================================================
@router.post("/get_guest_profile")
async def get_guest_profile(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    tool_call_id, args = parse_vapi_payload(payload)

    phone = args.get("phone")
    name = args.get("name")
    venue_id = args.get("venue_id") or get_venue_by_inbound_number(payload)

    if not phone:
        # Fallback: try to get customer phone from Vapi call object if webhook
        phone = payload.get("message", {}).get("call", {}).get("customer", {}).get("number")

    if not phone and not name:
        raise HTTPException(status_code=422, detail="Missing phone number or name for profile lookup")

    # Prioritize searching SevenRooms by phone first (prevents name collision issues)
    res_list = []
    try:
        if phone:
            res_list = await sevenrooms_client.search_reservations(phone=phone)
        
        # Fallback to name search only if no phone was provided or if phone lookup returned zero results
        if not res_list and name:
            res_list = await sevenrooms_client.search_reservations(name=name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SevenRooms reservations query failed: {str(e)}")

    # Filter and sort results to find the most relevant active booking
    active_bookings = []
    upcoming_active = []  # kept at outer scope for multi-booking detection below
    if res_list:
        import datetime
        today = datetime.date.today()

        def parse_res_datetime(r):
            r_date = r.get("date") or "1970-01-01"
            r_time = r.get("arrival_time") or "00:00"
            if len(r_time) > 5:
                r_time = r_time[:5]
            try:
                return datetime.datetime.strptime(f"{r_date} {r_time}", "%Y-%m-%d %H:%M")
            except Exception:
                return datetime.datetime.min

        # Filter out canceled / no-show bookings
        active_candidates = [
            r for r in res_list 
            if r.get("status") not in ["CANCELED", "NO_SHOW", "CANCELLED"]
        ]

        # Split into upcoming and past
        past_active = []

        for r in active_candidates:
            r_date_str = r.get("date")
            try:
                r_date = datetime.datetime.strptime(r_date_str, "%Y-%m-%d").date()
            except Exception:
                r_date = today
            
            if r_date >= today:
                upcoming_active.append(r)
            else:
                past_active.append(r)

        # Sort upcoming active ascending (soonest first)
        upcoming_active.sort(key=parse_res_datetime)
        # Sort past active descending (most recent first)
        past_active.sort(key=parse_res_datetime, reverse=True)

        active_bookings = upcoming_active + past_active

    if not active_bookings:
        response = {
            "found": False,
            "multiple_bookings": False,
            "message": "No active reservations found in SevenRooms for this guest."
        }
    elif len(upcoming_active) > 1:
        # Multiple upcoming bookings — surface all so the AI can disambiguate before acting
        response = {
            "found": True,
            "multiple_bookings": True,
            "bookings": [
                {
                    "appointment_id": r.get("id"),
                    "sevenrooms_reservation_id": r.get("id"),
                    "reference_code": r.get("reference_code"),
                    "guest_name": f"{r.get('first_name', '')} {r.get('last_name', '')}".strip(),
                    "preferred_date": r.get("date"),
                    "preferred_time": r.get("arrival_time"),
                    "party_size": r.get("party_size"),
                    "status": r.get("status"),
                }
                for r in upcoming_active[:3]  # cap at 3 to avoid token bloat
            ],
            "message": (
                f"Found {len(upcoming_active)} upcoming reservations for this guest. "
                "Ask the caller which one they mean before taking any action."
            )
        }
    else:
        appt = active_bookings[0]

        response = {
            "found": True,
            "multiple_bookings": False,
            "appointment_id": appt.get("id"),
            "sevenrooms_reservation_id": appt.get("id"),
            "reference_code": appt.get("reference_code"),
            "guest_name": f"{appt.get('first_name', '')} {appt.get('last_name', '')}".strip(),
            "preferred_date": appt.get("date"),
            "preferred_time": appt.get("arrival_time"),
            "party_size": appt.get("party_size"),
            "status": appt.get("status"),
            "message": f"Found an active reservation for {appt.get('first_name')} on {appt.get('date')} at {appt.get('arrival_time')}."
        }

        # Sync to Supabase in background (does NOT delay the response)
        def _sync_guest_profile():
            try:
                _db = get_db()
                local_check = _db.table("appointments").select("id").eq("sevenrooms_reservation_id", appt.get("id")).execute()
                if not local_check.data:
                    guest_name = f"{appt.get('first_name', '')} {appt.get('last_name', '')}".strip() or "Unknown Guest"
                    _db.table("appointments").insert({
                        "venue_id": venue_id,
                        "guest_name": guest_name,
                        "guest_phone": appt.get("phone_number"),
                        "guest_email": appt.get("email"),
                        "party_size": appt.get("party_size") or 2,
                        "preferred_date": appt.get("date"),
                        "preferred_time": appt.get("arrival_time"),
                        "status": "confirmed" if appt.get("status") == "BOOKED" else appt.get("status", "pending"),
                        "sevenrooms_reservation_id": appt.get("id"),
                        "reference_code": appt.get("reference_code"),
                    }).execute()
            except Exception:
                pass
        background_tasks.add_task(_sync_guest_profile)

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(response)
                }
            ]
        }
    return response


async def _trigger_vapi_transfer(url: str, destination: str):
    vapi_key = os.environ.get("VAPI_PRIVATE_API_KEY")
    if not url or not destination or not vapi_key:
        return
    
    headers = {
        "Authorization": f"Bearer {vapi_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "type": "transfer",
        "destination": {
            "type": "number",
            "number": destination
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(url, json=payload, headers=headers, timeout=10.0)
            print(f"Vapi transfer request status: {res.status_code}, response: {res.text}")
        except Exception as e:
            print(f"Failed to post transfer to Vapi controlUrl: {e}")


# ============================================================
# 6. ROUTE TRANSFER
# ============================================================
@router.post("/route_transfer")
async def route_transfer(request: Request):
    payload = await request.json()
    tool_call_id, args = parse_vapi_payload(payload)

    reason = args.get("reason", "default")
    venue_id = args.get("venue_id") or get_venue_by_inbound_number(payload)

    routing_matrix = {}
    staff_schedule = {}
    timezone_str = os.environ.get("DEFAULT_TIMEZONE", "UTC")
    
    venue_details = get_venue_details(venue_id)
    if venue_details:
        routing_matrix = venue_details.get("routing_matrix") or {}
        staff_schedule = venue_details.get("staff_schedule") or {}
        timezone_str  = venue_details.get("timezone") or os.environ.get("DEFAULT_TIMEZONE", "UTC")

    # Partner-venue phone mapping, configured as JSON: {"venue_key": "+E164 number"}
    sister_restaurants = json.loads(os.environ.get("TRANSFER_NUMBERS", "{}"))

    if reason in sister_restaurants:
        destination_number = sister_restaurants[reason]
        backup = destination_number
    else:
        # Resolve contact for this reason, fall back to default
        contact = routing_matrix.get(reason) or routing_matrix.get("default") or {}
        primary = contact.get("primary")
        backup  = contact.get("backup")

        # Time-of-day check: if primary is off-shift, promote backup
        if primary and not _is_available(primary, staff_schedule, timezone_str):
            destination_number = backup or primary
        else:
            destination_number = primary or backup

    if not destination_number:
        response = {
            "success": False,
            "transfer_reason": reason,
            "destination_number": None,
            "backup_number": backup,
            "message": f"Could not find a valid number to route transfer for {reason}."
        }
    else:
        response = {
            "success": True,
            "transfer_reason": reason,
            "destination_number": destination_number,
            "backup_number": backup,
            "message": f"Routing transfer for {reason} to {destination_number}."
        }

        # Execute the transfer synchronously on Vapi side to prevent race conditions
        control_url = payload.get("message", {}).get("call", {}).get("monitor", {}).get("controlUrl")
        if control_url:
            await _trigger_vapi_transfer(control_url, destination_number)

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(response)
                }
            ]
        }
    return response


@router.post("/get_menu_recommendations")
async def get_menu_recommendations(req: Request):
    payload = await req.json()
    tool_call_id, args = parse_vapi_payload(payload)

    category = args.get("category")
    exclude_allergens = args.get("exclude_allergens")
    search_query = args.get("search_query")

    recommendations = menu_service.get_recommendations(
        category=category,
        exclude_allergens=exclude_allergens,
        search_query=search_query
    )

    if tool_call_id:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": json.dumps(recommendations)
                }
            ]
        }
    return recommendations
