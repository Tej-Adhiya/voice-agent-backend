from pydantic import BaseModel
from typing import Optional


class CreateAppointmentRequest(BaseModel):
    vapi_call_id: str
    guest_name: str
    guest_phone: Optional[str] = None
    guest_email: Optional[str] = None
    service: Optional[str] = None
    preferred_date: str        # "2026-06-10"
    preferred_time: str        # "14:00"
    party_size: Optional[int] = 2
    venue_id: Optional[str] = None
    notes: Optional[str] = None


class ModifyAppointmentRequest(BaseModel):
    vapi_call_id: str
    appointment_id: str
    changes: dict              # e.g. {"preferred_date": "2026-06-11", "preferred_time": "15:00", "party_size": 4}
    venue_id: Optional[str] = None


class CancelAppointmentRequest(BaseModel):
    vapi_call_id: str
    appointment_id: str
    venue_id: Optional[str] = None


class CheckAvailabilityRequest(BaseModel):
    vapi_call_id: Optional[str] = None
    preferred_date: str
    preferred_time: str
    party_size: Optional[int] = 2
    venue_id: Optional[str] = None
    appointment_id: Optional[str] = None


class GetGuestProfileRequest(BaseModel):
    phone: Optional[str] = None
    name: Optional[str] = None
    venue_id: Optional[str] = None


class RouteTransferRequest(BaseModel):
    reason: str                # e.g. "complaint", "private_event"
    venue_id: Optional[str] = None
    time_of_day: Optional[str] = None


class GetMenuRecommendationsRequest(BaseModel):
    vapi_call_id: Optional[str] = None
    category: Optional[str] = None
    exclude_allergens: Optional[list[str]] = None
    search_query: Optional[str] = None
