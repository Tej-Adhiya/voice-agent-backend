-- ============================================================
-- Voice Agent - Supabase Schema (Multi-Tenant)
-- Run in: Supabase Dashboard > SQL Editor > New Query
-- ============================================================

-- 1. VENUES
-- ============================================================
CREATE TABLE IF NOT EXISTS venues (
    id                  TEXT PRIMARY KEY, -- SevenRooms venue_id
    name                TEXT NOT NULL,
    phone_number        TEXT UNIQUE,      -- Inbound phone number
    timezone            TEXT DEFAULT 'UTC',
    routing_matrix      JSONB DEFAULT '{}'::jsonb,
    opening_hours       JSONB DEFAULT '{}'::jsonb,
    staff_schedule      JSONB DEFAULT '{}'::jsonb,
    group_threshold     INTEGER DEFAULT 9,
    languages_enabled   TEXT[] DEFAULT ARRAY['en', 'fr', 'de', 'pt', 'es'],
    takeaway_rules      TEXT,
    event_rules         TEXT,
    complaint_handling_rules TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- 2. APPOINTMENTS
-- Every booking, reschedule, or cancellation goes here.
-- ============================================================
CREATE TABLE IF NOT EXISTS appointments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id            TEXT REFERENCES venues(id),
    guest_name          TEXT NOT NULL,
    guest_phone         TEXT,
    guest_email         TEXT,
    service             TEXT,                   -- type of appointment
    party_size          INTEGER DEFAULT 2,
    preferred_date      TEXT,                   -- "2026-06-10"
    preferred_time      TEXT,                   -- "14:00"
    notes               TEXT,
    status              TEXT DEFAULT 'pending', -- pending | confirmed | cancelled | rescheduled
    vapi_call_id        TEXT,                   -- which call created this
    sevenrooms_reservation_id TEXT,             -- SevenRooms API ID
    reference_code      TEXT,                   -- SevenRooms Reference Code
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- 3. CALL LOGS
-- One row per inbound call.
-- ============================================================
CREATE TABLE IF NOT EXISTS call_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vapi_call_id        TEXT UNIQUE NOT NULL,
    venue_id            TEXT REFERENCES venues(id),
    caller_number       TEXT,
    language_detected   TEXT,
    outcome             TEXT,   -- appointment_created | appointment_modified |
                                -- appointment_cancelled | message_taken | abandoned
    appointment_id      UUID REFERENCES appointments(id),
    summary             TEXT,
    started_at          TIMESTAMPTZ DEFAULT now(),
    ended_at            TIMESTAMPTZ,
    duration_seconds    INTEGER,
    ended_reason        TEXT,
    reservation_intent  BOOLEAN DEFAULT false,
    event_lead_intent   BOOLEAN DEFAULT false,
    complaint_intent    BOOLEAN DEFAULT false,
    escalated           BOOLEAN DEFAULT false
);

-- 4. IDEMPOTENCY KEYS
-- Prevents duplicate appointments if Vapi retries a tool call.
-- ============================================================
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key         TEXT UNIQUE NOT NULL,
    tool_name   TEXT NOT NULL,
    response    JSONB,
    status      TEXT DEFAULT 'pending',  -- pending | success | failed
    created_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ DEFAULT now() + INTERVAL '24 hours'
);

-- ============================================================
-- SEED DATA - DEMO VENUE (replace with your own)
-- ============================================================
INSERT INTO venues (
    id,
    name,
    phone_number,
    timezone,
    routing_matrix,
    opening_hours,
    staff_schedule,
    group_threshold,
    languages_enabled,
    takeaway_rules,
    event_rules,
    complaint_handling_rules
) VALUES (
    'demo-venue-id',
    'Demo Restaurant',
    '+15550100001',
    'UTC',
    '{
        "reservation_issue":  {"primary": "+15550100002",  "backup": "+15550100003"},
        "group_booking":       {"primary": "+15550100004",  "backup": "+15550100005"},
        "private_event":       {"primary": "+15550100004",  "backup": "+15550100005"},
        "press":               {"primary": "+15550100004",  "backup": "+15550100005"},
        "supplier":            {"primary": "+15550100003",  "backup": "+15550100005"},
        "job_application":     {"primary": "+15550100005",  "backup": "+15550100003"},
        "complaint":           {"primary": "+15550100003",  "backup": "+15550100005"},
        "lost_and_found":      {"primary": "+15550100002",  "backup": "+15550100003"},
        "allergy":             {"primary": "+15550100002",  "backup": "+15550100003"},
        "gift_voucher":        {"primary": "+15550100005",  "backup": "+15550100003"},
        "corporate_invoice":   {"primary": "+15550100004",  "backup": "+15550100005"},
        "default":             {"primary": "+15550100006"}
    }'::jsonb,
    '{
        "monday":    [{"open": "12:00", "close": "00:00"}],
        "tuesday":   [{"open": "12:00", "close": "00:00"}],
        "wednesday": [{"open": "12:00", "close": "00:00"}],
        "thursday":  [{"open": "12:00", "close": "01:00"}],
        "friday":    [{"open": "12:00", "close": "01:00"}],
        "saturday":  [{"open": "12:00", "close": "01:00"}],
        "sunday":    [{"open": "12:00", "close": "23:00"}]
    }'::jsonb,
    '{
        "+15550100003": {"name": "Staff A",   "days": [2,3,4,5,6,7], "open": "12:00", "close": "23:00"},
        "+15550100002": {"name": "Staff B",    "days": [1,2,4,5,6,7], "open": "12:00", "close": "23:00"},
        "+15550100004": {"name": "Staff C", "days": [1,2,3,4,5],   "open": "09:00", "close": "17:00"},
        "+15550100005": {"name": "Staff D",    "days": [1,2,3,4,5],   "open": "09:00", "close": "17:00"}
    }'::jsonb,
    10,
    ARRAY['en', 'fr', 'de', 'pt'],
    'Takeaway orders must be placed online at least 30 minutes in advance.',
    'Private buyouts require a minimum of 40 guests and must be booked through the events coordinator.',
    'Complaints are handled by our hospitality manager. We apologize but cannot issue refunds over the phone.'
) ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    phone_number = EXCLUDED.phone_number,
    timezone = EXCLUDED.timezone,
    routing_matrix = EXCLUDED.routing_matrix,
    opening_hours = EXCLUDED.opening_hours,
    staff_schedule = EXCLUDED.staff_schedule,
    group_threshold = EXCLUDED.group_threshold,
    languages_enabled = EXCLUDED.languages_enabled,
    takeaway_rules = EXCLUDED.takeaway_rules,
    event_rules = EXCLUDED.event_rules,
    complaint_handling_rules = EXCLUDED.complaint_handling_rules,
    updated_at = now();
