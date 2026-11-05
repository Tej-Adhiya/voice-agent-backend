-- Migration: Multi-Tenancy support
-- 1. Create Venues table
CREATE TABLE IF NOT EXISTS venues (
    id                  TEXT PRIMARY KEY, -- SevenRooms venue_id
    name                TEXT NOT NULL,
    phone_number        TEXT UNIQUE,      -- Inbound phone number
    timezone            TEXT DEFAULT 'UTC',
    routing_matrix      JSONB DEFAULT '{}'::jsonb,
    opening_hours       JSONB DEFAULT '{}'::jsonb,
    group_threshold     INTEGER DEFAULT 9,
    languages_enabled   TEXT[] DEFAULT ARRAY['en', 'fr', 'de', 'pt', 'es'],
    takeaway_rules      TEXT,
    event_rules         TEXT,
    complaint_handling_rules TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- 2. Alter appointments table to add multi-tenancy columns
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS venue_id TEXT REFERENCES venues(id);
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS party_size INTEGER DEFAULT 2;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS sevenrooms_reservation_id TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reference_code TEXT;

-- 3. Alter call_logs table to add venue_id
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS venue_id TEXT REFERENCES venues(id);

-- 4. Seed Demo Restaurant Pilot Venue
INSERT INTO venues (
    id,
    name,
    phone_number,
    timezone,
    routing_matrix,
    opening_hours,
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
        "complaint": {"primary": "+15550100007", "backup": "+15550100008"},
        "private_event": {"primary": "+15550100009", "backup": "+15550100010"},
        "group_booking": {"primary": "+15550100011", "backup": "+15550100012"},
        "lost_and_found": {"primary": "+15550100013"},
        "supplier": {"primary": "+15550100014"},
        "default": {"primary": "+15550100015"}
    }'::jsonb,
    '{
        "monday": [{"open": "12:00", "close": "14:30"}, {"open": "18:30", "close": "22:30"}],
        "tuesday": [{"open": "12:00", "close": "14:30"}, {"open": "18:30", "close": "22:30"}],
        "wednesday": [{"open": "12:00", "close": "14:30"}, {"open": "18:30", "close": "22:30"}],
        "thursday": [{"open": "12:00", "close": "14:30"}, {"open": "18:30", "close": "22:30"}],
        "friday": [{"open": "12:00", "close": "14:30"}, {"open": "18:30", "close": "23:00"}],
        "saturday": [{"open": "18:30", "close": "23:00"}],
        "sunday": []
    }'::jsonb,
    9,
    ARRAY['en', 'fr', 'de', 'pt', 'es'],
    'Takeaway orders must be placed online at least 30 minutes in advance.',
    'Private buyouts require a minimum of 40 guests and must be booked through the events coordinator.',
    'Complaints are handled by our hospitality manager. We apologize but cannot issue refunds over the phone.'
) ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    phone_number = EXCLUDED.phone_number,
    timezone = EXCLUDED.timezone,
    routing_matrix = EXCLUDED.routing_matrix,
    opening_hours = EXCLUDED.opening_hours,
    group_threshold = EXCLUDED.group_threshold,
    languages_enabled = EXCLUDED.languages_enabled,
    takeaway_rules = EXCLUDED.takeaway_rules,
    event_rules = EXCLUDED.event_rules,
    complaint_handling_rules = EXCLUDED.complaint_handling_rules,
    updated_at = now();
