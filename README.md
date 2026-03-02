# 📞 Outbound Calling Scheduler

An asynchronous, production-ready outbound SIP call scheduler built with FastAPI, LiveKit, and APScheduler.

It supports:

- Concurrent multi-trunk outbound calling
- Smart retry handling
- CSV/XLSX bulk scheduling
- Live trunk capacity monitoring
- Business-hour automation via cron
- Graceful shutdown and lifecycle management

Designed for reliability, operational clarity, and long-term scalability.

---

# 🏗 Architecture Overview

```
FastAPI
├── /schedule/* ──► register_job() ──► APScheduler (date trigger)
│ │
│ execute_scheduled_call()
│ │
│ TrunkManager.acquire()
│ │
│ make_outbound_call()
│ ├── Create LiveKit Room
│ ├── Dispatch SIP Participant
│ └── _monitor_room() [async task]
│ │
│ TrunkManager.release()
│ └── Retry if unanswered
│
├── GET /jobs ──► APScheduler jobs + TrunkManager.status()
└── GET /capacity ──► TrunkManager.status()
```

---

# ⚙️ Installation

## 1️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

## 2️⃣ Configure Environment Variables

Create a `.env` file:

```
LIVEKIT_URL=wss://<your-livekit-host>
LIVEKIT_API_KEY=AP***
LIVEKIT_API_SECRET=snm***

# Optional (comma-separated trunk IDs)
SIP_TRUNK_IDS=ST_xxx,ST_yyy,ST_zzz
```

If not provided, default trunk IDs will be used.

## 3️⃣ Run the Application

```bash
python trigger.py
```

## 4️⃣ Open Swagger UI

`http://127.0.0.1:8000/docs`

# 🚀 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | /schedule/single | Schedule a single call |
| POST | /schedule/bulk | Schedule multiple calls |
| POST | /schedule/file | Upload CSV/XLSX for bulk scheduling |
| GET | /jobs | List scheduled jobs + live capacity |
| GET | /capacity | View trunk capacity snapshot |
| DELETE | /jobs/{job_id} | Cancel a scheduled job |

# 📥 Input Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| phone_number | string | ✅ | Destination number (10-digit or E.164) |
| job_id | string | ❌ | Custom ID (auto-generated if omitted) |
| trunk_id | string | ❌ | Force specific trunk |
| name | string | ❌ | Contact name |
| scheduled_time | string | ❌ | ISO format YYYY-MM-DDTHH:MM:SS |
| max_retries | int | ❌ | Retry attempts (default: 2) |
| retry_delay | int | ❌ | Seconds between retries (default: 300) |
| company_name | string | ❌ | Company name |
| company_id | string | ❌ | Internal company identifier |
| assistant_role | string | ❌ | AI agent persona |

**Example Payload**

```json
{
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

# ✨ Features

## 🗓 Flexible Scheduling

- Immediate execution (omit scheduled_time)
- Future scheduling with ISO datetime
- Past-time safety → executes immediately instead of failing

## ⚡ Concurrent Multi-Trunk Calling

- Configurable via SIP_TRUNK_IDS
- Round-robin load distribution
- Default: 5 concurrent calls per trunk
- 5 trunks × 5 slots = 25 concurrent calls
- Automatic requeue if capacity is full

## 🔄 Intelligent Retry Logic

- Automatic retry if unanswered
- Controlled by max_retries
- Delayed by retry_delay
- No infinite loops
- Suppressed during shutdown

## 📊 Live Capacity Monitoring

**GET /capacity returns:**

- Active calls
- Total slots
- Available slots
- Per-trunk usage

**GET /jobs includes:**

- Job ID
- Next run time (UTC)
- Trigger type
- Full payload
- Capacity snapshot

## 📁 Bulk Scheduling (CSV / XLSX)

Upload via:

`POST /schedule/file`

**Supported columns:**

- phone_number
- job_id
- trunk_id
- name
- scheduled_time
- max_retries
- retry_delay
- company_name
- company_id
- assistant_role

Only `phone_number` is required.

**CSV Example**

```csv
phone_number,name,company_name,company_id,assistant_role,scheduled_time,max_retries,retry_delay
9876543210,Ravi Kumar,JuristBot AI,68ecf392,Sales Executive,2025-06-01T10:00:00,2,300
9123456780,Priya Sharma,JuristBot AI,68ecf392,Support Agent,,1,180
```

## 📞 Call Lifecycle

For each scheduled call:

- LiveKit room is created
- SIP participant is dispatched
- Room is monitored asynchronously
- If answered → marked answered
- If not answered → retry (if configured)
- Trunk capacity is released on completion

Each call runs independently and asynchronously.

## 🛡 Graceful Shutdown

On shutdown:

- Scheduler stops cleanly
- LiveKit client closes
- DB connection closes
- Retry logic is disabled
- No orphaned jobs

Ensures predictable system behaviour.

## ⏰ Cron Automation

| Time | Action |
|---|---|
| 9:00 AM | start_day() |
| 9:15 AM – 5:45 PM | every_15min_task() |
| 6:00 PM | stop_day() |
| 6:00 PM – 11:00 PM | after_hours_task() |

**Startup Flow**

```
App Start
   └── lifespan()
         ├── LiveKit API client init
         ├── setup_cron_jobs()
         └── scheduler.start()
```

One shared scheduler manages both:

- Cron automation
- Individual call jobs

# 📦 Technology Stack

| Library | Purpose |
|---|---|
| fastapi | REST API framework |
| livekit | SIP + room management |
| apscheduler | Async scheduling engine |
| pandas | CSV/XLSX parsing |
| pydantic | Request validation |
| python-dotenv | Environment management |
| uvicorn | ASGI server |

# 🏛 Design Principles

- Database-level uniqueness enforcement
- Capacity-first trunk management
- Idempotent job scheduling
- Async-first architecture
- Operational visibility
- Clear separation of responsibilities

Built for controlled growth rather than quick fixes.

# 🔧 Production Recommendations

For serious deployments:

- Use MongoDB replica set
- Deploy behind a reverse proxy (e.g., Nginx)
- Use process managers (systemd / supervisor)
- Add monitoring (Prometheus / Grafana)
- Consider persistent job store for APScheduler
- Plan horizontal scaling carefully (single scheduler leader pattern)

# 📜 License

Internal or proprietary usage as applicable.

---

If you'd like, I can next provide:

- A Dockerfile
- docker-compose setup
- Production deployment guide
- High-availability architecture plan

You’ve built a strong foundation. With careful deployment discipline, it can operate reliably at scale.

live
