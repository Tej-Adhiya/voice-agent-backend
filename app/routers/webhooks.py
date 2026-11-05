import os
import json
from fastapi import APIRouter, Request
from app.db.database import get_db, get_venue_id_by_phone

router = APIRouter()

DEFAULT_VENUE_ID = os.environ.get("DEFAULT_VENUE_ID", "")
# Comma-separated route_transfer reasons that mean "referred to a partner venue"
REFERRAL_VENUES = [v.strip() for v in os.environ.get("REFERRAL_VENUES", "").split(",") if v.strip()]

def detect_language(transcript: str) -> str:
    if not transcript:
        return "en"
    # Strip punctuation and split into words
    cleaned = "".join(c for c in transcript.lower() if c.isalnum() or c.isspace())
    words = set(cleaned.split())
    
    stop_words = {
        "en": {"the", "and", "you", "for", "table", "reservation", "booking", "hello", "please", "yes", "no"},
        "fr": {"le", "la", "les", "et", "vous", "pour", "table", "reservation", "bonjour", "sil", "plait", "oui", "non"},
        "de": {"der", "die", "das", "und", "sie", "ich", "tisch", "reservierung", "hallo", "bitte", "ja", "nein"},
        "pt": {"o", "os", "a", "as", "e", "você", "mesa", "reserva", "olá", "por", "favor", "sim", "não"},
        "es": {"el", "la", "los", "las", "y", "usted", "para", "mesa", "reserva", "hola", "por", "favor", "si", "no"}
    }
    
    scores = {}
    for lang, sw_set in stop_words.items():
        scores[lang] = len(words.intersection(sw_set))
        
    best_lang = max(scores, key=scores.get)
    if scores[best_lang] == 0:
        return "en"
    return best_lang


@router.post("/vapi")
async def vapi_webhook(request: Request):
    """
    Vapi sends an end-of-call report here when the call ends.
    We use it to log every call in call_logs.
    In Vapi dashboard: Settings > Webhooks > set this URL.
    """
    payload = await request.json()
    message = payload.get("message", {})

    if message.get("type") == "end-of-call-report":
        db = get_db()
        call = message.get("call", {})
        call_id = call.get("id", "unknown")
        ended_reason = message.get("endedReason", "")

        # 1. Resolve venue_id by checking inbound number
        inbound_number = (
            call.get("phoneNumber") or
            call.get("phone_number", {}).get("number")
        )
        venue_id = DEFAULT_VENUE_ID
        if inbound_number:
            cached_id = get_venue_id_by_phone(inbound_number)
            if cached_id:
                venue_id = cached_id

        # 2. Link appointment_id if an appointment was created/modified for this call
        appointment_id = None
        try:
            appt_res = db.table("appointments").select("id").eq("vapi_call_id", call_id).order("created_at", desc=True).limit(1).execute()
            if appt_res.data:
                appointment_id = appt_res.data[0]["id"]
        except Exception:
            pass

        # 3. Build transcript and detect language
        messages = (
            call.get("messages") or
            message.get("messages") or
            payload.get("messages") or
            []
        )
        transcript = message.get("transcript") or call.get("transcript") or ""
        if not transcript and messages:
            transcript = " ".join([
                msg.get("message", "") or msg.get("content", "")
                for msg in messages
                if msg.get("role") in ("user", "assistant")
            ])
        language_detected = detect_language(transcript)

        # 4. Map endedReason and tool calls to a semantic outcome
        payload_str = json.dumps(payload)
        tool_calls = set()
        for msg in messages:
            role = msg.get("role")
            if role in ("tool-calls", "tool_call"):
                t_calls = msg.get("toolCalls") or msg.get("tool_calls") or []
                for tc in t_calls:
                    func = tc.get("function", {})
                    if func.get("name"):
                        tool_calls.add(func.get("name"))
            elif msg.get("tool_call_id") or msg.get("toolCallId"):
                name = msg.get("name") or msg.get("tool_name")
                if name:
                    tool_calls.add(name)

        # Fallback to string search if messages list was empty/different
        if not tool_calls:
            for tc_name in ["route_transfer", "create_appointment", "modify_appointment", "cancel_appointment", "add_to_cancellation_list"]:
                if tc_name in payload_str:
                    tool_calls.add(tc_name)

        # Check if they were referred to a sister restaurant
        referred_venue = None
        for msg in messages:
            role = msg.get("role")
            if role in ("tool-calls", "tool_call"):
                t_calls = msg.get("toolCalls") or msg.get("tool_calls") or []
                for tc in t_calls:
                    func = tc.get("function", {})
                    if func.get("name") == "route_transfer":
                        args = func.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                pass
                        r_reason = args.get("reason")
                        if r_reason in REFERRAL_VENUES:
                            referred_venue = r_reason
            elif msg.get("tool_call_id") or msg.get("toolCallId"):
                name = msg.get("name") or msg.get("tool_name")
                if name == "route_transfer":
                    # Try to extract the reason
                    for tc_name in REFERRAL_VENUES:
                        if tc_name in payload_str:
                            referred_venue = tc_name
                            break

        if not referred_venue:
            for v_name in REFERRAL_VENUES:
                if f'"reason": "{v_name}"' in payload_str or f'"reason":"{v_name}"' in payload_str:
                    referred_venue = v_name
                    break

        if "add_to_cancellation_list" in tool_calls:
            outcome = "added_to_cancellation_list"
        elif "route_transfer" in tool_calls:
            if referred_venue:
                outcome = f"referred_{referred_venue}"
            else:
                outcome = "escalated"
        elif "cancel_appointment" in tool_calls:
            outcome = "appointment_cancelled"
        elif "modify_appointment" in tool_calls:
            outcome = "appointment_modified"
        elif "create_appointment" in tool_calls:
            outcome = "appointment_created"
        elif ended_reason == "voicemail":
            outcome = "message_taken"
        else:
            # Check if the guest was offered a referral but declined it
            lower_trans = transcript.lower()
            if any(term in lower_trans for term in ["fully booked", "cancellation list", "sister restaurant"]):
                outcome = "declined"
            else:
                outcome = "abandoned"

        started_at = call.get("startedAt")
        ended_at = call.get("endedAt")
        duration_seconds = int(call.get("duration", 0)) if call.get("duration") is not None else None

        # Calculate intent & escalation flags
        reservation_intent = any(tc in tool_calls for tc in ["check_availability", "create_appointment", "modify_appointment", "cancel_appointment", "add_to_cancellation_list"])
        
        event_lead_intent = (
            ("route_transfer" in tool_calls and '"reason": "private_event"' in payload_str.replace(" ", "")) or 
            any(word in transcript.lower() for word in ["wedding", "birthday", "private dining", "buyout", "corporate event", "party of 15", "party of 20", "events team"])
        )
        
        complaint_intent = (
            ("route_transfer" in tool_calls and '"reason": "complaint"' in payload_str.replace(" ", "")) or
            any(word in transcript.lower() for word in ["complaint", "rude", "terrible", "bad service", "hair in", "manager", "manager receives"])
        )
        
        escalated = "route_transfer" in tool_calls and referred_venue is None

        db.table("call_logs").upsert({
            "vapi_call_id":       call_id,
            "venue_id":           venue_id,
            "caller_number":      call.get("customer", {}).get("number"),
            "language_detected":  language_detected,
            "outcome":            outcome,
            "appointment_id":     appointment_id,
            "summary":            message.get("summary", ""),
            "started_at":         started_at or "now()",
            "ended_at":           ended_at or "now()",
            "duration_seconds":   duration_seconds,
            "ended_reason":       ended_reason or None,
            "reservation_intent": reservation_intent,
            "event_lead_intent":  event_lead_intent,
            "complaint_intent":   complaint_intent,
            "escalated":          escalated,
        }).execute()

    return {"ok": True}

