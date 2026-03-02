📞 Outbound Calling Scheduler
An async, production-ready outbound SIP call scheduler built with FastAPI, LiveKit, and APScheduler. Supports concurrent multi-trunk calling, smart retry logic, CSV/XLSX bulk uploads, and live capacity monitoring.

⚙️ Installation
1. Install Requirements
```bash
pip install -r requirements.txt
```

2. Configure .env

```env
LIVEKIT_URL=wss://<your-livekit-host>
LIVEKIT_API_KEY=AP***
LIVEKIT_API_SECRET=snm***
```

# Optional: comma-separated trunk IDs (defaults provided)
SIP_TRUNK_IDS=ST_xxx,ST_yyy,ST_zzz
3. Run the Scheduler
```bash
python trigger.py

```


4. Open Swagger UI
``` http://127.0.0.1:8000/docs ```

🚀 API Endpoints

```
json{
  "service": "Outbound Scheduler",
  "endpoints": {
    "POST /schedule/single":  "Schedule one call",
    "POST /schedule/bulk":    "Schedule multiple calls",
    "POST /schedule/file":    "Schedule from CSV/XLSX",
    "GET  /jobs":             "List scheduled jobs (with capacity info)",
    "DELETE /jobs/{job_id}":  "Cancel a job",
    "GET  /capacity":         "Live trunk capacity snapshot"
  }
}
```

📥 Input Parameters
```
FieldTypeRequiredDescriptionphone_numberstring✅Destination phone number (10-digit or E.164)job_idstring❌Custom job ID; auto-generated if omittedtrunk_idstring❌Specific SIP trunk to use; round-robin if omittednamestring❌Contact name passed to the AI agentscheduled_timestring❌ISO format YYYY-MM-DDTHH:MM:SS; runs immediately if omittedmax_retriesint❌Number of retry attempts if call is unanswered (default: 2)retry_delayint❌Seconds between retries (default: 300)company_namestring❌Company name passed to the AI agentcompany_idstring❌Internal company identifierassistant_rolestring❌Role/persona of the AI agent (e.g. Sales Executive)

```
Example payload:
json{
  "phone_number": "9876543210",
  "name": "Ravi Kumar",
  "scheduled_time": "2025-06-01T10:30:00",
  "max_retries": 3,
  "retry_delay": 300,
  "company_name": "JuristBot AI",
  "company_id": "68ecf3923eeb3e0237c19b1e",
  "assistant_role": "Sales Executive"
}
```

```

✨ Features

🗓️ Flexible Call Scheduling

Immediate dispatch — omit scheduled_time to call right now.
Future scheduling — provide any ISO datetime to queue a call for later.
Past-time safety — if a scheduled time has already passed, the call fires immediately instead of being dropped.

⚡ Concurrent Multi-Trunk Calling

Supports multiple SIP trunks simultaneously, configurable via SIP_TRUNK_IDS in .env.
Round-robin trunk selection distributes load evenly across all trunks.
Up to 5 concurrent calls per trunk (configurable via MAX_CALLS_PER_TRUNK).
With 5 trunks × 5 slots = 25 simultaneous calls supported out of the box.
New jobs are scheduled and dispatched independently of in-progress calls — no blocking.

🔄 Intelligent Retry Logic

If a call goes unanswered or is missed, it is automatically retried after retry_delay seconds.
Retries are decremented per attempt up to max_retries — no infinite loops.
Retries are suppressed during shutdown to prevent ghost jobs on restart.
If all trunk slots are full at dispatch time, the job auto-requeues itself in 60 seconds instead of being silently dropped.

📊 Live Capacity Monitoring

GET /capacity returns a real-time snapshot:

Total active calls
Available slots
Per-trunk utilisation breakdown


GET /jobs embeds the capacity block alongside the job list for a single-call operational view.

📋 Rich Job List View
Each entry in GET /jobs includes:

id & name — job identity
next_run_utc — exact ISO timestamp of next execution
trigger — APScheduler trigger description
args — full job payload (phone, company, retries, etc.)
trunk_capacity — live active/available/per-trunk counts

📁 Bulk Scheduling via CSV / XLSX

Upload a .csv or .xlsx file to /schedule/file.
Supported columns: phone_number, job_id, trunk_id, name, scheduled_time, max_retries, retry_delay, company_name, company_id, assistant_role.
Missing optional columns are safely skipped — only phone_number is required.
Processes every valid row into independent scheduled jobs in one request.

🏠 Room & Participant Lifecycle Management

A dedicated LiveKit room is created per call with full metadata (company, agent role, contact name, etc.).
An async room monitor tracks participant count — detects when the human picks up (≥2 participants).
Room is polled for up to 1 hour to handle long calls gracefully.
Trunk slot is released automatically when the room closes, freeing capacity for the next call.

📞 Smart Phone Formatting

Strips non-digit characters, leading zeros, and country codes automatically.
Converts 12-digit numbers starting with 91 (India) to 10-digit local format compatible with Pulse SIP trunks.

🛡️ Graceful Shutdown

_shutting_down flag is set only during actual shutdown, ensuring in-flight monitors don't trigger spurious retries.
APScheduler and LiveKit client are cleanly closed on exit — no dangling tasks.

🔁 Job Management

DELETE /jobs/{job_id} — cancel any pending job by ID before it fires.
replace_existing=True — re-registering the same job_id safely updates it in place.


📂 Bulk Upload – CSV Format
csvphone_number,name,company_name,company_id,assistant_role,scheduled_time,max_retries,retry_delay
9876543210,Ravi Kumar,JuristBot AI,68ecf392,Sales Executive,2025-06-01T10:00:00,2,300
9123456780,Priya Sharma,JuristBot AI,68ecf392,Support Agent,,1,180

🏗️ Architecture Overview
FastAPI
  ├── /schedule/* ──► register_job() ──► APScheduler (date trigger)
  │                                            │
  │                                   execute_scheduled_call()
  │                                            │
  │                                   TrunkManager.acquire()
  │                                            │
  │                                   make_outbound_call()
  │                                     ├── Create LiveKit Room
  │                                     ├── Dispatch SIP Participant
  │                                     └── _monitor_room() [async task]
  │                                               │
  │                                     TrunkManager.release()
  │                                     └── Retry if unanswered
  │
  ├── GET /jobs      ──► APScheduler jobs + TrunkManager.status()
  └── GET /capacity  ──► TrunkManager.status()

🔒 Environment Variables
VariableRequiredDescriptionLIVEKIT_URL✅LiveKit server WebSocket URLLIVEKIT_API_KEY✅LiveKit API keyLIVEKIT_API_SECRET✅LiveKit API secretSIP_TRUNK_IDS❌Comma-separated SIP trunk IDs (5 defaults provided)

📦 Tech Stack
LibraryPurposefastapiREST API frameworklivekitRoom & SIP participant managementapschedulerAsync job schedulingpandasCSV / XLSX parsingpydanticRequest validationpython-dotenvEnvironment configuvicornASGI server


cron jobs : 

9:00 AM  →  start_day()
              ├── dispatch future-scheduled jobs from DB
              └── register 15-min job

9:15 AM  →  every_15min_task()  (repeats until 5:45 PM)

6:00 PM  →  stop_day()
              ├── remove 15-min job
              └── register hourly job

6:00 PM – 11:00 PM  →  after_hours_task()  (auto-stops at midnight via cron)


FastAPI starts
    └── lifespan()
          ├── lk_client = LiveKitAPI(...)        # outbound engine
          ├── setup_cron_jobs()                  # register cron triggers
          └── scheduler.start()                  # ONE scheduler runs both



What runs in background automatically


9:00 AM  → start_day()  → dispatch DB jobs → register 15-min job
9:15 AM  → every_15min_task()  (repeats every 15 min)
           ...
6:00 PM  → stop_day()  → remove 15-min → register hourly job
6:00 PM – 11:00 PM  → after_hours_task()  (every hour)


Bonus — DB status tracking added
EventStatus in DBCron dispatchedqueuedSIP call firedin-progressHuman answeredansweredUnanswered, retryingretryAll retries exhaustedfailed