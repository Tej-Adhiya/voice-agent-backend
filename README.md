# Voice Agent

Vapi + FastAPI + Supabase appointment scheduler.

## Setup (do this in order)

### 1. Supabase
1. Create a free project at https://supabase.com
2. Go to SQL Editor → New Query
3. Paste contents of `supabase/schema.sql` → Run
4. Go to Project Settings → API → copy:
   - Project URL  
   - service_role key (not anon key)

### 2. Environment
```bash
cp .env.example .env
# paste your Supabase URL and service_role key into .env
```

### 3. Run backend
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 4. Expose with ngrok
```bash
ngrok http 8000
# copy the https://xxxx.ngrok.io URL
```

### 5. Vapi tools
1. Open `vapi_tools.json`
2. Replace all `YOUR-NGROK-URL` with your ngrok URL
3. Vapi Dashboard → your assistant → Tools → Add Tool
4. Paste each of the 4 tool objects one by one

### 6. Vapi webhook (for call logging)
Vapi Dashboard → your assistant → Advanced → Server URL  
Set it to: `https://YOUR-SERVICE-URL/webhooks/vapi`

---

## Endpoints

| Method | Path | What it does |
|--------|------|-------------|
| POST | /tools/check_availability | Check a date/time slot |
| POST | /tools/create_appointment | Save a new appointment |
| POST | /tools/modify_appointment | Reschedule an appointment |
| POST | /tools/cancel_appointment | Cancel an appointment |
| POST | /webhooks/vapi | Receives end-of-call report |
| GET  | /health | Health check |

## Testing locally
```bash
curl -X POST http://localhost:8000/tools/check_availability \
  -H "Content-Type: application/json" \
  -d '{"vapi_call_id":"test-123","preferred_date":"2026-06-10","preferred_time":"14:00"}'
```
