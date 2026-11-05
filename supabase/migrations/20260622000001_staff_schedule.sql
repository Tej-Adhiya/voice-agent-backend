-- Migration: Staff schedule for time-of-day routing
-- Adds staff_schedule JSONB to venues.
-- Format: { "<phone_e164>": { "name": str, "days": [1-7 isoweekday], "open": "HH:MM", "close": "HH:MM" } }
-- 1 = Monday, 7 = Sunday

ALTER TABLE venues
    ADD COLUMN IF NOT EXISTS staff_schedule JSONB DEFAULT '{}'::jsonb;

-- Seed demo staff schedule
UPDATE venues
SET staff_schedule = '{
    "+15550100003": {"name": "Staff A",   "days": [2,3,4,5,6,7], "open": "12:00", "close": "23:00"},
    "+15550100002": {"name": "Staff B",    "days": [1,2,4,5,6,7], "open": "12:00", "close": "23:00"},
    "+15550100004": {"name": "Staff C", "days": [1,2,3,4,5],   "open": "09:00", "close": "17:00"},
    "+15550100005": {"name": "Staff D",    "days": [1,2,3,4,5],   "open": "09:00", "close": "17:00"}
}'::jsonb
WHERE id = 'demo-venue-id';
