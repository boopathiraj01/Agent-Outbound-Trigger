# import io
# import uuid
# import asyncio
# import logging
# import os
# from contextlib import asynccontextmanager
# from datetime import datetime, timedelta, timezone
# from typing import List, Optional, Dict

# import json

# import pandas as pd
# from apscheduler.schedulers.asyncio import AsyncIOScheduler
# from apscheduler.executors.asyncio import AsyncIOExecutor
# from fastapi import FastAPI, File, UploadFile, HTTPException
# from pydantic import BaseModel, field_validator
# from dotenv import load_dotenv

# load_dotenv()

# from livekit import api

# # ── Logging ────────────────────────────────────────────────────────────────────
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
# )
# logger = logging.getLogger("SchedulerApp")

# # ── Global State ───────────────────────────────────────────────────────────────
# lk_client: Optional[api.LiveKitAPI] = None
# _shutting_down = False  # ✅ FIX: starts False; only set True on shutdown

# # ── Trunk Manager ──────────────────────────────────────────────────────────────
# MAX_CALLS_PER_TRUNK = 5
# TRUNK_IDS: List[str] = [
#     t.strip()
#     for t in os.getenv(
#         "SIP_TRUNK_IDS",
#         "ST_xoNGrnCz7vCT,ST_5a66URvUYUao,ST_JXVXARTyWMWr,ST_7eYcRkRgUrrB,ST_rVGkkmqha3gM",
#     ).split(",")
#     if t.strip()
# ]


# class TrunkManager:
#     def __init__(self, trunk_ids: List[str]):
#         self._trunks = list(trunk_ids)
#         self._counts: Dict[str, int] = {tid: 0 for tid in trunk_ids}
#         self._max = MAX_CALLS_PER_TRUNK
#         self._lock = asyncio.Lock()
#         self._rr_index = 0

#     async def acquire(self) -> Optional[str]:
#         async with self._lock:
#             n = len(self._trunks)
#             for i in range(n):
#                 tid = self._trunks[(self._rr_index + i) % n]
#                 if self._counts[tid] < self._max:
#                     self._counts[tid] += 1
#                     self._rr_index = (self._rr_index + i + 1) % n
#                     logger.info(f"🔒 Locked {tid} (Active: {self._counts[tid]})")
#                     return tid
#         return None

#     async def release(self, trunk_id: str):
#         async with self._lock:
#             if self._counts.get(trunk_id, 0) > 0:
#                 self._counts[trunk_id] -= 1
#             logger.info(f"🔓 Released {trunk_id} (Active: {self._counts[trunk_id]})")

#     def status(self) -> Dict[str, int]:
#         """Return a snapshot of active calls per trunk."""
#         return dict(self._counts)

#     @property
#     def total_capacity(self) -> int:
#         return len(self._trunks) * self._max

#     @property
#     def total_active(self) -> int:
#         return sum(self._counts.values())


# trunk_manager = TrunkManager(TRUNK_IDS)

# # ── Scheduler Setup ────────────────────────────────────────────────────────────
# scheduler = AsyncIOScheduler(
#     executors={"default": AsyncIOExecutor()},
#     job_defaults={"max_instances": 100, "coalesce": True},
# )


# # ── Room Monitor ──────────────────────────────────────────────────────────────
# async def _monitor_room(room_name: str, trunk_id: str, phone: str, job_data: dict):
#     global lk_client
#     call_answered = False
#     room_ever_existed = False

#     await asyncio.sleep(15)  # Give SIP gateway time to spin up

#     start_time = asyncio.get_event_loop().time()
#     try:
#         while (asyncio.get_event_loop().time() - start_time) < 3600:
#             try:
#                 res = await lk_client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))

#                 if res.rooms:
#                     room_ever_existed = True

#                 if not res.rooms:
#                     if room_ever_existed:
#                         logger.info(f"🏁 Call session for {phone} concluded.")
#                         break
#                     else:
#                         await asyncio.sleep(5)
#                         continue

#                 p_res = await lk_client.room.list_participants(
#                     api.ListParticipantsRequest(room=room_name)
#                 )
#                 if len(p_res.participants) >= 2:
#                     call_answered = True
#                     logger.info(f"📞 {phone} answered the call!")

#             except Exception as e:
#                 logger.warning(f"⚠️  Monitor transient error (retrying): {e}")
#                 await asyncio.sleep(5)
#                 continue

#             await asyncio.sleep(10)

#     finally:
#         await trunk_manager.release(trunk_id)

#         if not _shutting_down and not call_answered:
#             retries = job_data.get("max_retries", 0)
#             if retries > 0:
#                 job_data["max_retries"] = retries - 1
#                 delay = job_data.get("retry_delay", 300)
#                 run_at = datetime.now() + timedelta(seconds=delay)
#                 scheduler.add_job(
#                     execute_scheduled_call,
#                     trigger="date",
#                     run_date=run_at,
#                     args=[job_data],
#                     id=f"retry_{uuid.uuid4().hex[:4]}_{phone}",
#                 )
#                 logger.warning(f"🔄 {phone} missed/busy – retry in {delay}s")
#         elif call_answered:
#             logger.info(f"✅ Call to {phone} answered and completed.")


# # ── Phone Formatter ────────────────────────────────────────────────────────────
# def format_phone(phone: str) -> str:
#     clean = "".join(filter(str.isdigit, phone)).lstrip("0")
#     if len(clean) == 12 and clean.startswith("91"):
#         return clean[2:]  # strip country code → 10-digit local
#     return clean


# def generate_room_name(company_name: str, phone_number: str) -> str:
#     timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
#     clean_number = phone_number.lstrip("+")
#     clean_company = company_name.lower().replace(" ", "-")
#     return f"{clean_company}-sip-outbound-{clean_number}-{timestamp}"


# # ── Outbound Call ──────────────────────────────────────────────────────────────
# async def make_outbound_call(phone_number: str, trunk_id: str, job_data: dict):
#     global lk_client
#     room_name = generate_room_name("diginest ai", phone_number)
#     formatted_num = format_phone(phone_number)

#     # ✅ FIX: Added missing comma after "company_id" value
#     room_metadata = json.dumps({
#         "phone": phone_number,
#         "job_id": job_data.get("job_id"),
#         "type": "outbound",
#         "company_id": job_data.get("company_id"),          # ✅ comma was missing here
#         "assistant_role": job_data.get("assistant_role"),
#         "company_name": job_data.get("company_name", "diginest ai"),
#         "bot_type": "Telephony",
#         "name": job_data.get("name"),
#     })

#     try:
#         await lk_client.room.create_room(
#             api.CreateRoomRequest(name=room_name, empty_timeout=300, metadata=room_metadata)
#         )
#         logger.info(f"🏠 Room created: {room_name}")

#         await asyncio.sleep(3)

#         await lk_client.sip.create_sip_participant(
#             api.CreateSIPParticipantRequest(
#                 sip_trunk_id=trunk_id,
#                 sip_call_to=formatted_num,
#                 room_name=room_name,
#                 participant_identity=f"sip-{phone_number}-{datetime.utcnow().timestamp()}",
#             )
#         )
#         logger.info(f"✅ SIP dispatched: {formatted_num}")

#         asyncio.create_task(
#             _monitor_room(room_name, trunk_id, formatted_num, job_data)
#         )

#     except Exception as e:
#         await trunk_manager.release(trunk_id)
#         logger.error(f"❌ Call failed for {formatted_num}: {e}")


# # ── Execution Engine ───────────────────────────────────────────────────────────
# async def execute_scheduled_call(job_data: dict):
#     trunk_id = await trunk_manager.acquire()
#     if not trunk_id:
#         logger.error(f"🚫 No trunk capacity – skipping {job_data['phone_number']}")
#         # ── Optional: auto-requeue in 60s when capacity is full ──────────────
#         run_at = datetime.now() + timedelta(seconds=60)
#         scheduler.add_job(
#             execute_scheduled_call,
#             trigger="date",
#             run_date=run_at,
#             args=[job_data],
#             id=f"cap_retry_{uuid.uuid4().hex[:4]}_{job_data['phone_number']}",
#         )
#         logger.warning(f"⏳ Requeued {job_data['phone_number']} for 60s later (capacity full)")
#         return

#     asyncio.create_task(make_outbound_call(job_data["phone_number"], trunk_id, job_data))


# # ── Models ─────────────────────────────────────────────────────────────────────
# class CallJob(BaseModel):
#     phone_number: str
#     job_id: Optional[str] = None
#     trunk_id: Optional[str] = None
#     name: Optional[str] = None
#     scheduled_time: Optional[str] = None
#     max_retries: int = 2
#     retry_delay: int = 300
#     company_name: Optional[str] = None
#     company_id: Optional[str] = None
#     assistant_role: Optional[str] = None

#     @field_validator("scheduled_time", mode="before")
#     @classmethod
#     def validate_time(cls, v):
#         if not v:
#             return None
#         try:
#             datetime.fromisoformat(str(v))
#             return v
#         except ValueError:
#             raise ValueError("Format must be YYYY-MM-DDTHH:MM:SS")


# # ── Job Registration ───────────────────────────────────────────────────────────
# def register_job(job: CallJob) -> dict:
#     job_id = job.job_id or f"call_{uuid.uuid4().hex[:8]}"
#     now = datetime.now()

#     if job.scheduled_time:
#         run_at = datetime.fromisoformat(job.scheduled_time).replace(tzinfo=None)
#         if run_at < now:
#             logger.warning(f"{job_id}: scheduled time in past – running immediately.")
#             run_at = now
#     else:
#         run_at = now

#     data = job.model_dump()
#     data.setdefault("max_retries", 2)
#     data.setdefault("retry_delay", 300)

#     scheduler.add_job(
#         execute_scheduled_call,
#         trigger="date",
#         run_date=run_at,
#         id=job_id,
#         args=[data],
#         name=f"Call_{job.phone_number}",
#         replace_existing=True,
#     )
#     logger.info(f"📋 Registered: {job_id} → {job.phone_number} at {run_at}")
#     return {"job_id": job_id, "run_at": str(run_at), "status": "queued"}


# # ── Lifespan ───────────────────────────────────────────────────────────────────
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     global lk_client, _shutting_down   # ✅ FIX: declare global so assignments take effect

#     _shutting_down = False              # ✅ FIX: correct — False while app is live

#     lk_client = api.LiveKitAPI(
#         os.getenv("LIVEKIT_URL"),
#         os.getenv("LIVEKIT_API_KEY"),
#         os.getenv("LIVEKIT_API_SECRET"),
#     )

#     scheduler.start()
#     logger.info("✅ Scheduler started.")

#     yield                               # ── app is running here ──

#     # ✅ FIX: set True only NOW so monitors stop retrying during shutdown
#     _shutting_down = True
#     scheduler.shutdown(wait=False)
#     await lk_client.aclose()
#     logger.info("🛑 Shutdown complete.")


# app = FastAPI(title="Outbound Scheduler", lifespan=lifespan)


# # ── Routes ─────────────────────────────────────────────────────────────────────
# @app.get("/")
# async def root():
#     return {
#         "service": "Outbound Scheduler",
#         "endpoints": {
#             "POST /schedule/single":  "Schedule one call",
#             "POST /schedule/bulk":    "Schedule multiple calls",
#             "POST /schedule/file":    "Schedule from CSV/XLSX",
#             "GET  /jobs":             "List scheduled jobs (with capacity info)",
#             "DELETE /jobs/{job_id}":  "Cancel a job",
#             "GET  /capacity":         "Live trunk capacity snapshot",
#         },
#     }


# @app.post("/schedule/single")
# async def schedule_single(job: CallJob):
#     return {"status": "success", "data": register_job(job)}


# @app.post("/schedule/bulk")
# async def schedule_bulk(jobs: List[CallJob]):
#     if not jobs:
#         raise HTTPException(400, "Job list is empty.")
#     results = [register_job(j) for j in jobs]
#     return {"status": "success", "jobs_scheduled": len(results), "jobs": results}


# @app.post("/schedule/file")
# async def schedule_file(file: UploadFile = File(...)):
#     filename = file.filename or ""
#     content = await file.read()

#     if filename.endswith(".csv"):
#         df = pd.read_csv(io.BytesIO(content))
#     elif filename.endswith(".xlsx"):
#         df = pd.read_excel(io.BytesIO(content))
#     else:
#         raise HTTPException(400, "Upload a .csv or .xlsx file.")

#     df.columns = df.columns.str.strip().str.lower()
#     if "phone_number" not in df.columns:
#         raise HTTPException(422, "Missing required column: phone_number")

#     df = df.where(pd.notna(df), None)

#     def col(row, key):
#         """Safe column reader — returns None if column absent or value is NaN."""
#         return str(row[key]) if key in df.columns and row.get(key) is not None else None

#     jobs = []
#     for _, row in df.iterrows():
#         jobs.append(
#             CallJob(
#                 phone_number=str(row["phone_number"]),
#                 job_id=col(row, "job_id"),
#                 trunk_id=col(row, "trunk_id"),
#                 name=col(row, "name"),                         # ✅ FIX: now read from file
#                 scheduled_time=col(row, "scheduled_time"),
#                 max_retries=int(row["max_retries"]) if "max_retries" in df.columns and row.get("max_retries") else 2,
#                 retry_delay=int(row["retry_delay"]) if "retry_delay" in df.columns and row.get("retry_delay") else 300,
#                 company_name=col(row, "company_name"),         # ✅ FIX: now read from file
#                 company_id=col(row, "company_id"),             # ✅ FIX: now read from file
#                 assistant_role=col(row, "assistant_role"),     # ✅ FIX: now read from file
#             )
#         )

#     if not jobs:
#         raise HTTPException(422, "No valid rows found.")

#     results = [register_job(j) for j in jobs]
#     return {"status": "success", "file": filename, "jobs_scheduled": len(results), "jobs": results}


# @app.get("/jobs")
# async def list_jobs():
#     """
#     Returns all pending/upcoming scheduled jobs plus live trunk capacity.
#     Note: jobs currently being executed (in-flight calls) are NOT in this list —
#     they have already been popped off the APScheduler queue. Use /capacity to
#     see how many concurrent calls are active right now.
#     """
#     jobs = [
#         {
#             "id": j.id,
#             "name": j.name,
#             "next_run_utc": j.next_run_time.isoformat() if j.next_run_time else None,
#             "trigger": str(j.trigger),
#             "args": j.args,            # includes phone_number, retries, etc.
#             "misfire_grace_time": str(j.misfire_grace_time),
#         }
#         for j in scheduler.get_jobs()
#     ]
#     capacity = trunk_manager.status()
#     return {
#         "queued_jobs": len(jobs),
#         "jobs": jobs,
#         "trunk_capacity": {
#             "active_calls": trunk_manager.total_active,
#             "total_slots": trunk_manager.total_capacity,
#             "available_slots": trunk_manager.total_capacity - trunk_manager.total_active,
#             "per_trunk": capacity,
#         },
#     }


# @app.get("/capacity")
# async def get_capacity():
#     """Live snapshot of trunk utilisation."""
#     return {
#         "active_calls": trunk_manager.total_active,
#         "total_slots": trunk_manager.total_capacity,
#         "available_slots": trunk_manager.total_capacity - trunk_manager.total_active,
#         "per_trunk": trunk_manager.status(),
#     }


# @app.delete("/jobs/{job_id}")
# async def cancel_job(job_id: str):
#     if not scheduler.get_job(job_id):
#         raise HTTPException(404, f"Job '{job_id}' not found.")
#     scheduler.remove_job(job_id)
#     return {"status": "cancelled", "job_id": job_id}


# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="127.0.0.1", port=8000)




# [
#   {
#   "phone_number": "6383718908",
#   "name":"raj",
#   "job_id": "call_001",
#   "scheduled_time": "2026-03-03T13:12:00",
# "company_name":"JuristBot AI",
# "company_id":"68ecf3923eeb3e0237c19b1e",
# "assistant_role":"Sales Excutive"
# },
# {
#   "phone_number": "9345115061",
#   "name":"ajith",
#   "job_id": "call_002",
#   "scheduled_time": "2026-03-03T13:11:00",
# "company_name":"JuristBot AI",
# "company_id":"68ecf3923eeb3e0237c19b1e",
# "assistant_role":"Sales Excutive"
# },
# {
#   "phone_number": "6383512037",
#   "name":"nandha",
#   "job_id": "call_003",
# "scheduled_time": "2026-03-03T13:13:00",
# "company_name":"Diginest AI",
# "company_id":"693000a09dbc5e57daffe4dc",
# "assistant_role":"Brand ambassdor"
# }]


# import io
# import uuid
# import asyncio
# import logging
# import os
# from contextlib import asynccontextmanager
# from datetime import datetime, timedelta, timezone
# from typing import List, Optional, Dict

# import json
# import httpx
# import pandas as pd
# from apscheduler.schedulers.asyncio import AsyncIOScheduler
# from apscheduler.executors.asyncio import AsyncIOExecutor
# from fastapi import FastAPI, File, UploadFile, HTTPException
# from pydantic import BaseModel, field_validator
# from dotenv import load_dotenv
# from livekit import api

# from dbms import DBManagement

# load_dotenv()

# # ── Logging ────────────────────────────────────────────────────────────────────
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
# )
# logger = logging.getLogger("OutboundApp")

# # ── Global State ───────────────────────────────────────────────────────────────
# lk_client: Optional[api.LiveKitAPI] = None
# _shutting_down = False

# # ── DB Setup ───────────────────────────────────────────────────────────────────
# dbms = DBManagement(
# )



# # ── Trunk Manager ──────────────────────────────────────────────────────────────
# MAX_CALLS_PER_TRUNK = 5
# TRUNK_IDS: List[str] = [
#     t.strip()
#     for t in os.getenv(
#         "SIP_TRUNK_IDS",
#         "ST_xoNGrnCz7vCT,ST_5a66URvUYUao,ST_JXVXARTyWMWr,ST_7eYcRkRgUrrB,ST_rVGkkmqha3gM",
#     ).split(",")
#     if t.strip()
# ]

# SCHEDULER_URL = "http://127.0.0.1:8000/schedule/bulk"
# BATCH_LIMIT   = 20


# class TrunkManager:
#     def __init__(self, trunk_ids: List[str]):
#         self._trunks = list(trunk_ids)
#         self._counts: Dict[str, int] = {tid: 0 for tid in trunk_ids}
#         self._max = MAX_CALLS_PER_TRUNK
#         self._lock = asyncio.Lock()
#         self._rr_index = 0

#     async def acquire(self) -> Optional[str]:
#         async with self._lock:
#             n = len(self._trunks)
#             for i in range(n):
#                 tid = self._trunks[(self._rr_index + i) % n]
#                 if self._counts[tid] < self._max:
#                     self._counts[tid] += 1
#                     self._rr_index = (self._rr_index + i + 1) % n
#                     logger.info(f"🔒 Locked {tid} (Active: {self._counts[tid]})")
#                     return tid
#         return None

#     async def release(self, trunk_id: str):
#         async with self._lock:
#             if self._counts.get(trunk_id, 0) > 0:
#                 self._counts[trunk_id] -= 1
#             logger.info(f"🔓 Released {trunk_id} (Active: {self._counts[trunk_id]})")

#     def status(self) -> Dict[str, int]:
#         return dict(self._counts)

#     @property
#     def total_capacity(self) -> int:
#         return len(self._trunks) * self._max

#     @property
#     def total_active(self) -> int:
#         return sum(self._counts.values())


# trunk_manager = TrunkManager(TRUNK_IDS)

# # ── Single Shared Scheduler ────────────────────────────────────────────────────
# scheduler = AsyncIOScheduler(
#     executors={"default": AsyncIOExecutor()},
#     job_defaults={"max_instances": 100, "coalesce": True},
# )


# # ═══════════════════════════════════════════════════════════════════════════════
# # SECTION 1 — OUTBOUND CALL ENGINE
# # ═══════════════════════════════════════════════════════════════════════════════

# async def _monitor_room(room_name: str, trunk_id: str, phone: str, job_data: dict):
#     global lk_client
#     call_answered = False
#     room_ever_existed = False

#     await asyncio.sleep(15)

#     start_time = asyncio.get_event_loop().time()
#     try:
#         while (asyncio.get_event_loop().time() - start_time) < 3600:
#             try:
#                 res = await lk_client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))

#                 if res.rooms:
#                     room_ever_existed = True

#                 if not res.rooms:
#                     if room_ever_existed:
#                         logger.info(f"🏁 Call session for {phone} concluded.")
#                         break
#                     else:
#                         await asyncio.sleep(5)
#                         continue

#                 p_res = await lk_client.room.list_participants(
#                     api.ListParticipantsRequest(room=room_name)
#                 )
#                 if len(p_res.participants) >= 2:
#                     call_answered = True
#                     logger.info(f"📞 {phone} answered!")

#                     # ── Update DB status to "answered" ─────────────────────
#                     dbms.update_data(
#                         filter={"job_id": job_data.get("job_id")},
#                         update={"$set": {"status": "answered"}}
#                     )

#             except Exception as e:
#                 logger.warning(f"⚠️  Monitor transient error (retrying): {e}")
#                 await asyncio.sleep(5)
#                 continue

#             await asyncio.sleep(10)

#     finally:
#         await trunk_manager.release(trunk_id)

#         if not _shutting_down and not call_answered:
#             retries = job_data.get("max_retries", 0)
#             if retries > 0:
#                 job_data["max_retries"] = retries - 1
#                 delay = job_data.get("retry_delay", 300)
#                 run_at = datetime.now() + timedelta(seconds=delay)
#                 scheduler.add_job(
#                     execute_scheduled_call,
#                     trigger="date",
#                     run_date=run_at,
#                     args=[job_data],
#                     id=f"retry_{uuid.uuid4().hex[:4]}_{phone}",
#                 )
#                 logger.warning(f"🔄 {phone} unanswered — retry in {delay}s")

#                 # ── Update DB status to "retry" ────────────────────────────
#                 dbms.update_data(
#                     filter={"job_id": job_data.get("job_id")},
#                     update={"$set": {"status": "retry"}}
#                 )
#             else:
#                 # No retries left — mark failed
#                 dbms.update_data(
#                     filter={"job_id": job_data.get("job_id")},
#                     update={"$set": {"status": "failed"}}
#                 )
#                 logger.info(f"❌ {phone} — no retries left, marked failed.")

#         elif call_answered:
#             logger.info(f"✅ Call to {phone} answered and completed.")


# def format_phone(phone: str) -> str:
#     clean = "".join(filter(str.isdigit, phone)).lstrip("0")
#     if len(clean) == 12 and clean.startswith("91"):
#         return clean[2:]
#     return clean


# def generate_room_name(company_name: str, phone_number: str) -> str:
#     timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
#     clean_number = phone_number.lstrip("+")
#     clean_company = company_name.lower().replace(" ", "-")
#     return f"{clean_company}-sip-outbound-{clean_number}-{timestamp}"


# async def make_outbound_call(phone_number: str, trunk_id: str, job_data: dict):
#     global lk_client
#     room_name = generate_room_name(job_data.get("company_name"), phone_number)
#     formatted_num = format_phone(phone_number)

#     room_metadata = json.dumps({
#         "phone": phone_number,
#         "job_id": job_data.get("job_id"),
#         "type": "outbound",
#         "company_id": job_data.get("company_id"),
#         "assistant_role": job_data.get("assistant_role"),
#         "company_name": job_data.get("company_name", "diginest ai"),
#         "bot_type": "Telephony",
#         "name": job_data.get("name"),
#     })

#     try:
#         await lk_client.room.create_room(
#             api.CreateRoomRequest(name=room_name, empty_timeout=300, metadata=room_metadata)
#         )
#         logger.info(f"🏠 Room created: {room_name}")

#         await asyncio.sleep(3)

#         await lk_client.sip.create_sip_participant(
#             api.CreateSIPParticipantRequest(
#                 sip_trunk_id=trunk_id,
#                 sip_call_to=formatted_num,
#                 room_name=room_name,
#                 participant_identity=f"sip-{phone_number}-{datetime.utcnow().timestamp()}",
#             )
#         )
#         logger.info(f"✅ SIP dispatched: {formatted_num}")

#         # Update DB status to "in-progress"
#         dbms.update_data(
#             filter={"job_id": job_data.get("job_id")},
#             update={"$set": {"status": "in-progress"}}
#         )

#         asyncio.create_task(_monitor_room(room_name, trunk_id, formatted_num, job_data))

#     except Exception as e:
#         await trunk_manager.release(trunk_id)
#         logger.error(f"❌ Call failed for {formatted_num}: {e}")

#         dbms.update_data(
#             filter={"job_id": job_data.get("job_id")},
#             update={"$set": {"status": "failed"}}
#         )


# async def execute_scheduled_call(job_data: dict):
#     trunk_id = await trunk_manager.acquire()
#     if not trunk_id:
#         run_at = datetime.now() + timedelta(seconds=60)
#         scheduler.add_job(
#             execute_scheduled_call,
#             trigger="date",
#             run_date=run_at,
#             args=[job_data],
#             id=f"cap_retry_{uuid.uuid4().hex[:4]}_{job_data['phone_number']}",
#         )
#         logger.warning(f"⏳ Capacity full — requeued {job_data['phone_number']} in 60s")
#         return

#     asyncio.create_task(make_outbound_call(job_data["phone_number"], trunk_id, job_data))


# # ═══════════════════════════════════════════════════════════════════════════════
# # SECTION 2 — CRON JOB ENGINE (runs in background via shared scheduler)
# # ═══════════════════════════════════════════════════════════════════════════════

# def now_iso() -> str:
#     return datetime.now().replace(microsecond=0).isoformat()


# def mark_queued(docs: list):
#     for doc in docs:
#         dbms.update_data(
#             filter={"_id": doc["_id"]},
#             update={"$set": {"status": "queued"}}
#         )


# async def dispatch_to_scheduler(data: list) -> bool:
#     """POST jobs to our own /schedule/bulk endpoint."""
#     if not data:
#         logger.info("📭 No jobs to dispatch.")
#         return False
#     try:
#         async with httpx.AsyncClient(timeout=30) as client:
#             response = await client.post(SCHEDULER_URL, json=data)
#             response.raise_for_status()
#             logger.info(f"📤 Dispatched {len(data)} job(s) → {response.status_code}")
#             return True
#     except httpx.HTTPError as e:
#         logger.error(f"❌ Dispatch failed: {e}")
#         return False


# async def every_15min_task():
#     """
#     Every 15 mins (9 AM – 6 PM):
#     - Small queue (< BATCH_LIMIT) → dispatch all immediately
#     - Large queue (≥ BATCH_LIMIT) → dispatch future-scheduled only
#     """
#     logger.info(f"[{datetime.now():%H:%M}] ⚡ 15-min check — fetching pending jobs")

#     all_pending = dbms.get_data({"status": "not started"})
#     count = len(all_pending)

#     if count == 0:
#         logger.info("📭 Nothing to dispatch.")
#         return

#     if count < BATCH_LIMIT:
#         data = all_pending
#         logger.info(f"📦 Small batch ({count}) — dispatching all")
#     else:
#         data = dbms.get_data({
#             "scheduled_time": {"$gt": now_iso()},
#             "status": "not started"
#         })
#         logger.info(f"📦 Large queue — dispatching {len(data)} future-scheduled job(s)")

#     success = await dispatch_to_scheduler(data)
#     if success:
#         mark_queued(data)


# async def after_hours_task():
#     """Every hour (6 PM – 11 PM) — dispatch leftover not-started jobs."""
#     logger.info(f"[{datetime.now():%H:%M}] 🌙 After-hours check...")

#     data = dbms.get_data({"status": "not started"})
#     if not data:
#         logger.info("📭 No leftover jobs.")
#         return

#     success = await dispatch_to_scheduler(data)
#     if success:
#         mark_queued(data)
#         logger.info(f"🌙 {len(data)} leftover job(s) dispatched.")


# async def start_day():
#     """9:00 AM — Dispatch today's upcoming jobs then register the 15-min job."""
#     logger.info(f"[{datetime.now():%H:%M}] 🌅 Starting the day...")

#     data = dbms.get_data({
#         "scheduled_time": {"$gt": now_iso()},
#         "status": "not started"
#     })
#     success = await dispatch_to_scheduler(data)
#     if success:
#         mark_queued(data)
#         logger.info(f"✅ Morning: {len(data)} job(s) queued.")

#     if not scheduler.get_job("job_15min"):
#         scheduler.add_job(
#             every_15min_task,
#             trigger="cron",
#             day_of_week="mon-sat",
#             hour="9-17",
#             minute="*/15",
#             id="job_15min",
#             name="Every 15 Min (9AM–6PM)"
#         )
#         logger.info("✅ 15-min job registered.")


# async def stop_day():
#     """6:00 PM — Stop 15-min job, start hourly after-hours job."""
#     if scheduler.get_job("job_15min"):
#         scheduler.remove_job("job_15min")
#         logger.info(f"[{datetime.now():%H:%M}] 🔴 15-min job stopped.")

#     if not scheduler.get_job("job_hourly"):
#         scheduler.add_job(
#             after_hours_task,
#             trigger="cron",
#             day_of_week="mon-sat",
#             hour="18-23",
#             minute=0,
#             id="job_hourly",
#             name="Every Hour (6PM–11PM)"
#         )
#         logger.info(f"[{datetime.now():%H:%M}] 🌙 Hourly after-hours job started.")


# def setup_cron_jobs():
#     """Register all fixed cron triggers onto the shared scheduler."""

#     # 9:00 AM Mon–Sat → dispatch morning jobs + start 15-min job
#     scheduler.add_job(
#         start_day,
#         trigger="cron",
#         day_of_week="mon-sat",
#         hour=16, minute=0,
#         id="trigger_start",
#         name="Day Start (9 AM)"
#     )

#     # 6:00 PM Mon–Sat → stop 15-min + start hourly
#     scheduler.add_job(
#         stop_day,
#         trigger="cron",
#         day_of_week="mon-sat",
#         hour=18, minute=0,
#         id="trigger_stop",
#         name="Day Stop (6 PM)"
#     )

#     logger.info("📋 Cron jobs registered.")


# # ═══════════════════════════════════════════════════════════════════════════════
# # SECTION 3 — MODELS
# # ═══════════════════════════════════════════════════════════════════════════════

# class CallJob(BaseModel):
#     phone_number: str
#     job_id: Optional[str] = None
#     trunk_id: Optional[str] = None
#     name: Optional[str] = None
#     scheduled_time: Optional[str] = None
#     max_retries: int = 2
#     retry_delay: int = 300
#     company_name: Optional[str] = None
#     company_id: Optional[str] = None
#     assistant_role: Optional[str] = None

#     @field_validator("scheduled_time", mode="before")
#     @classmethod
#     def validate_time(cls, v):
#         if not v:
#             return None
#         try:
#             datetime.fromisoformat(str(v))
#             return v
#         except ValueError:
#             raise ValueError("Format must be YYYY-MM-DDTHH:MM:SS")


# def register_job(job: CallJob) -> dict:
#     job_id = job.job_id or f"call_{uuid.uuid4().hex[:8]}"
#     now = datetime.now()

#     if job.scheduled_time:
#         run_at = datetime.fromisoformat(job.scheduled_time).replace(tzinfo=None)
#         if run_at < now:
#             logger.warning(f"{job_id}: scheduled time in past — running immediately.")
#             run_at = now
#     else:
#         run_at = now

#     data = job.model_dump()
#     data["job_id"] = job_id
#     data.setdefault("max_retries", 2)
#     data.setdefault("retry_delay", 300)

#     scheduler.add_job(
#         execute_scheduled_call,
#         trigger="date",
#         run_date=run_at,
#         id=job_id,
#         args=[data],
#         name=f"Call_{job.phone_number}",
#         replace_existing=True,
#     )
#     logger.info(f"📋 Registered: {job_id} → {job.phone_number} at {run_at}")
#     return {"job_id": job_id, "run_at": str(run_at), "status": "queued"}


# # ═══════════════════════════════════════════════════════════════════════════════
# # SECTION 4 — FASTAPI APP + LIFESPAN
# # ═══════════════════════════════════════════════════════════════════════════════

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     global lk_client, _shutting_down

#     _shutting_down = False

#     # Start LiveKit client
#     lk_client = api.LiveKitAPI(
#         os.getenv("LIVEKIT_URL"),
#         os.getenv("LIVEKIT_API_KEY"),
#         os.getenv("LIVEKIT_API_SECRET"),
#     )

#     # Register cron jobs and start the single shared scheduler
#     setup_cron_jobs()
#     scheduler.start()
#     logger.info("✅ Scheduler started (outbound engine + cron jobs running in background).")

#     yield                       # ── App is live here ──

#     _shutting_down = True
#     scheduler.shutdown(wait=False)
#     await lk_client.aclose()
#     dbms.close()
#     logger.info("🛑 Shutdown complete.")


# app = FastAPI(title="Outbound Scheduler", lifespan=lifespan)


# # ═══════════════════════════════════════════════════════════════════════════════
# # SECTION 5 — API ROUTES
# # ═══════════════════════════════════════════════════════════════════════════════

# @app.get("/")
# async def root():
#     return {
#         "service": "Outbound Scheduler",
#         "endpoints": {
#             "POST /schedule/single":  "Schedule one call",
#             "POST /schedule/bulk":    "Schedule multiple calls",
#             "POST /schedule/file":    "Schedule from CSV/XLSX",
#             "GET  /jobs":             "List all jobs + capacity info",
#             "DELETE /jobs/{job_id}":  "Cancel a job",
#             "GET  /capacity":         "Live trunk capacity snapshot",
#         },
#     }


# @app.post("/schedule/single")
# async def schedule_single(job: CallJob):
#     return {"status": "success", "data": register_job(job)}


# @app.post("/schedule/bulk")
# async def schedule_bulk(jobs: List[CallJob]):
#     if not jobs:
#         raise HTTPException(400, "Job list is empty.")
#     results = [register_job(j) for j in jobs]
#     return {"status": "success", "jobs_scheduled": len(results), "jobs": results}


# @app.post("/schedule/file")
# async def schedule_file(file: UploadFile = File(...)):
#     filename = file.filename or ""
#     content = await file.read()

#     if filename.endswith(".csv"):
#         df = pd.read_csv(io.BytesIO(content))
#     elif filename.endswith(".xlsx"):
#         df = pd.read_excel(io.BytesIO(content))
#     else:
#         raise HTTPException(400, "Upload a .csv or .xlsx file.")

#     df.columns = df.columns.str.strip().str.lower()
#     if "phone_number" not in df.columns:
#         raise HTTPException(422, "Missing required column: phone_number")

#     df = df.where(pd.notna(df), None)

#     def col(row, key):
#         return str(row[key]) if key in df.columns and row.get(key) is not None else None

#     jobs = [
#         CallJob(
#             phone_number=str(row["phone_number"]),
#             job_id=col(row, "job_id"),
#             trunk_id=col(row, "trunk_id"),
#             name=col(row, "name"),
#             scheduled_time=col(row, "scheduled_time"),
#             max_retries=int(row["max_retries"]) if "max_retries" in df.columns and row.get("max_retries") else 2,
#             retry_delay=int(row["retry_delay"]) if "retry_delay" in df.columns and row.get("retry_delay") else 300,
#             company_name=col(row, "company_name"),
#             company_id=col(row, "company_id"),
#             assistant_role=col(row, "assistant_role"),
#         )
#         for _, row in df.iterrows()
#     ]

#     if not jobs:
#         raise HTTPException(422, "No valid rows found.")

#     results = [register_job(j) for j in jobs]
#     return {"status": "success", "file": filename, "jobs_scheduled": len(results), "jobs": results}


# @app.get("/jobs")
# async def list_jobs():
#     jobs = [
#         {
#             "id": j.id,
#             "name": j.name,
#             "next_run_utc": j.next_run_time.isoformat() if j.next_run_time else None,
#             "trigger": str(j.trigger),
#             "args": j.args,
#         }
#         for j in scheduler.get_jobs()
#     ]
#     return {
#         "queued_jobs": len(jobs),
#         "jobs": jobs,
#         "trunk_capacity": {
#             "active_calls": trunk_manager.total_active,
#             "total_slots": trunk_manager.total_capacity,
#             "available_slots": trunk_manager.total_capacity - trunk_manager.total_active,
#             "per_trunk": trunk_manager.status(),
#         },
#     }


# @app.get("/capacity")
# async def get_capacity():
#     return {
#         "active_calls": trunk_manager.total_active,
#         "total_slots": trunk_manager.total_capacity,
#         "available_slots": trunk_manager.total_capacity - trunk_manager.total_active,
#         "per_trunk": trunk_manager.status(),
#     }


# @app.delete("/jobs/{job_id}")
# async def cancel_job(job_id: str):
#     if not scheduler.get_job(job_id):
#         raise HTTPException(404, f"Job '{job_id}' not found.")
#     scheduler.remove_job(job_id)
#     return {"status": "cancelled", "job_id": job_id}


# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="127.0.0.1", port=8000)


import io
import uuid
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict

import json
import httpx
import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from fastapi import FastAPI, File, UploadFile, HTTPException
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from livekit import api
from pymongo.errors import DuplicateKeyError

from dbms import DBManagement

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("OutboundApp")

# ── Global State ───────────────────────────────────────────────────────────────
lk_client: Optional[api.LiveKitAPI] = None
_shutting_down = False

# ── DB Setup ───────────────────────────────────────────────────────────────────
dbms = DBManagement(
)

# ── Trunk Manager Config ───────────────────────────────────────────────────────
MAX_CALLS_PER_TRUNK = 5
TRUNK_IDS: List[str] = [
    t.strip()
    for t in os.getenv(
        "SIP_TRUNK_IDS",
        "ST_xoNGrnCz7vCT,ST_5a66URvUYUao,ST_JXVXARTyWMWr,ST_7eYcRkRgUrrB,ST_rVGkkmqha3gM",
    ).split(",")
    if t.strip()
]

SCHEDULER_URL = "http://127.0.0.1:8000/schedule/bulk"
BATCH_LIMIT   = 20


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 0 — DUPLICATE PREVENTION
# ═══════════════════════════════════════════════════════════════════════════════

def setup_indexes():
    """
    Create database-level unique indexes on startup.
    These enforce integrity even under high concurrency.

    Rules enforced:
      1. Same phone_number cannot be scheduled at the exact same time.
      2. Same job_id cannot exist more than once.
    """
    # Rule 1: Unique (phone_number + scheduled_time) — no same call at same minute
    dbms.collection.create_index(
        [("phone_number", 1), ("scheduled_time", 1)],
        unique=True,
        name="unique_phone_scheduled_time"
    )

    # Rule 2: Unique job_id — no duplicate jobs
    dbms.collection.create_index(
        [("job_id", 1)],
        unique=True,
        sparse=True,            # allows multiple docs with job_id=None
        name="unique_job_id"
    )

    logger.info("✅ DB indexes created: unique_phone_scheduled_time, unique_job_id")


def resolve_unique_time(phone_number: str, scheduled_time: str) -> str:
    """
    If the requested scheduled_time already exists for this phone_number,
    auto-shift forward by +1 minute until a free slot is found.

    Returns the resolved ISO timestamp string.
    """
    base_time = datetime.fromisoformat(scheduled_time)

    while True:
        candidate = base_time.replace(second=0, microsecond=0).isoformat()
        existing = dbms.get_one({
            "phone_number": phone_number,
            "scheduled_time": candidate
        })
        if not existing:
            if candidate != scheduled_time:
                logger.warning(
                    f"⏱️  Time conflict for {phone_number} at {scheduled_time} "
                    f"→ shifted to {candidate}"
                )
            return candidate
        base_time += timedelta(minutes=1)


def safe_insert_job(data: dict) -> Optional[dict]:
    """
    Insert a job document into the DB.
    Handles DuplicateKeyError gracefully — returns None on duplicate.
    """
    try:
        result = dbms.add_data(data)
        return result
    except DuplicateKeyError as e:
        logger.warning(f"⚠️  Duplicate job skipped: {e.details.get('keyValue')}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — TRUNK MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class TrunkManager:
    def __init__(self, trunk_ids: List[str]):
        self._trunks = list(trunk_ids)
        self._counts: Dict[str, int] = {tid: 0 for tid in trunk_ids}
        self._max = MAX_CALLS_PER_TRUNK
        self._lock = asyncio.Lock()
        self._rr_index = 0

    async def acquire(self) -> Optional[str]:
        async with self._lock:
            n = len(self._trunks)
            for i in range(n):
                tid = self._trunks[(self._rr_index + i) % n]
                if self._counts[tid] < self._max:
                    self._counts[tid] += 1
                    self._rr_index = (self._rr_index + i + 1) % n
                    logger.info(f"🔒 Locked {tid} (Active: {self._counts[tid]})")
                    return tid
        return None

    async def release(self, trunk_id: str):
        async with self._lock:
            if self._counts.get(trunk_id, 0) > 0:
                self._counts[trunk_id] -= 1
            logger.info(f"🔓 Released {trunk_id} (Active: {self._counts[trunk_id]})")

    def status(self) -> Dict[str, int]:
        return dict(self._counts)

    @property
    def total_capacity(self) -> int:
        return len(self._trunks) * self._max

    @property
    def total_active(self) -> int:
        return sum(self._counts.values())


trunk_manager = TrunkManager(TRUNK_IDS)

# ── Single Shared Scheduler ────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(
    executors={"default": AsyncIOExecutor()},
    job_defaults={"max_instances": 100, "coalesce": True},
)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — OUTBOUND CALL ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

async def _monitor_room(room_name: str, trunk_id: str, phone: str, job_data: dict):
    global lk_client
    call_answered = False
    room_ever_existed = False

    await asyncio.sleep(15)

    start_time = asyncio.get_event_loop().time()
    try:
        while (asyncio.get_event_loop().time() - start_time) < 3600:
            try:
                res = await lk_client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))

                if res.rooms:
                    room_ever_existed = True

                if not res.rooms:
                    if room_ever_existed:
                        logger.info(f"🏁 Call session for {phone} concluded.")
                        break
                    else:
                        await asyncio.sleep(5)
                        continue

                p_res = await lk_client.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
                if len(p_res.participants) >= 2:
                    call_answered = True
                    logger.info(f"📞 {phone} answered!")
                    dbms.update_data(
                        filter={"job_id": job_data.get("job_id")},
                        update={"$set": {"status": "answered"}}
                    )

            except Exception as e:
                logger.warning(f"⚠️  Monitor transient error (retrying): {e}")
                await asyncio.sleep(5)
                continue

            await asyncio.sleep(10)

    finally:
        await trunk_manager.release(trunk_id)

        if not _shutting_down and not call_answered:
            retries = job_data.get("max_retries", 0)
            if retries > 0:
                job_data["max_retries"] = retries - 1
                delay = job_data.get("retry_delay", 300)
                run_at = datetime.now() + timedelta(seconds=delay)
                scheduler.add_job(
                    execute_scheduled_call,
                    trigger="date",
                    run_date=run_at,
                    args=[job_data],
                    id=f"retry_{uuid.uuid4().hex[:4]}_{phone}",
                )
                logger.warning(f"🔄 {phone} unanswered — retry in {delay}s")
                dbms.update_data(
                    filter={"job_id": job_data.get("job_id")},
                    update={"$set": {"status": "retry"}}
                )
            else:
                dbms.update_data(
                    filter={"job_id": job_data.get("job_id")},
                    update={"$set": {"status": "failed"}}
                )
                logger.info(f"❌ {phone} — no retries left, marked failed.")

        elif call_answered:
            logger.info(f"✅ Call to {phone} answered and completed.")


def format_phone(phone: str) -> str:
    clean = "".join(filter(str.isdigit, phone)).lstrip("0")
    if len(clean) == 12 and clean.startswith("91"):
        return clean[2:]
    return clean


def generate_room_name(company_name: str, phone_number: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    clean_number = phone_number.lstrip("+")
    clean_company = (company_name or "diginest-ai").lower().replace(" ", "-")
    return f"{clean_company}-sip-outbound-{clean_number}-{timestamp}"


async def make_outbound_call(phone_number: str, trunk_id: str, job_data: dict):
    global lk_client
    room_name = generate_room_name(job_data.get("company_name", "diginest ai"), phone_number)
    formatted_num = format_phone(phone_number)

    room_metadata = json.dumps({
        "phone": phone_number,
        "job_id": job_data.get("job_id"),
        "type": "outbound",
        "company_id": job_data.get("company_id"),
        "assistant_role": job_data.get("assistant_role"),
        "company_name": job_data.get("company_name", "diginest ai"),
        "bot_type": "Telephony",
        "name": job_data.get("name"),
    })

    try:
        await lk_client.room.create_room(
            api.CreateRoomRequest(name=room_name, empty_timeout=300, metadata=room_metadata)
        )
        logger.info(f"🏠 Room created: {room_name}")
        await asyncio.sleep(3)

        await lk_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=formatted_num,
                room_name=room_name,
                participant_identity=f"sip-{phone_number}-{datetime.utcnow().timestamp()}",
            )
        )
        logger.info(f"✅ SIP dispatched: {formatted_num}")

        dbms.update_data(
            filter={"job_id": job_data.get("job_id")},
            update={"$set": {"status": "in-progress"}}
        )

        asyncio.create_task(_monitor_room(room_name, trunk_id, formatted_num, job_data))

    except Exception as e:
        await trunk_manager.release(trunk_id)
        logger.error(f"❌ Call failed for {formatted_num}: {e}")
        dbms.update_data(
            filter={"job_id": job_data.get("job_id")},
            update={"$set": {"status": "failed"}}
        )


async def execute_scheduled_call(job_data: dict):
    trunk_id = await trunk_manager.acquire()
    if not trunk_id:
        run_at = datetime.now() + timedelta(seconds=60)
        scheduler.add_job(
            execute_scheduled_call,
            trigger="date",
            run_date=run_at,
            args=[job_data],
            id=f"cap_retry_{uuid.uuid4().hex[:4]}_{job_data['phone_number']}",
        )
        logger.warning(f"⏳ Capacity full — requeued {job_data['phone_number']} in 60s")
        return

    asyncio.create_task(make_outbound_call(job_data["phone_number"], trunk_id, job_data))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — CRON JOB ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def mark_queued(docs: list):
    for doc in docs:
        dbms.update_data(
            filter={"_id": doc["_id"]},
            update={"$set": {"status": "queued"}}
        )


async def dispatch_to_scheduler(data: list) -> bool:
    if not data:
        logger.info("📭 No jobs to dispatch.")
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(SCHEDULER_URL, json=data)
            response.raise_for_status()
            logger.info(f"📤 Dispatched {len(data)} job(s) → {response.status_code}")
            return True
    except httpx.HTTPError as e:
        logger.error(f"❌ Dispatch failed: {e}")
        return False


async def every_15min_task():
    logger.info(f"[{datetime.now():%H:%M}] ⚡ 15-min check — fetching pending jobs")
    all_pending = dbms.get_data({"status": "not started"})
    count = len(all_pending)

    if count == 0:
        logger.info("📭 Nothing to dispatch.")
        return

    if count < BATCH_LIMIT:
        data = all_pending
        logger.info(f"📦 Small batch ({count}) — dispatching all")
    else:
        data = dbms.get_data({
            "scheduled_time": {"$gt": now_iso()},
            "status": "not started"
        })
        logger.info(f"📦 Large queue — dispatching {len(data)} future-scheduled job(s)")

    success = await dispatch_to_scheduler(data)
    if success:
        mark_queued(data)


async def after_hours_task():
    logger.info(f"[{datetime.now():%H:%M}] 🌙 After-hours check...")
    data = dbms.get_data({"status": "not started"})
    if not data:
        logger.info("📭 No leftover jobs.")
        return
    success = await dispatch_to_scheduler(data)
    if success:
        mark_queued(data)
        logger.info(f"🌙 {len(data)} leftover job(s) dispatched.")


async def start_day():
    logger.info(f"[{datetime.now():%H:%M}] 🌅 Starting the day...")
    data = dbms.get_data({
        "scheduled_time": {"$gt": now_iso()},
        "status": "not started"
    })
    success = await dispatch_to_scheduler(data)
    if success:
        mark_queued(data)
        logger.info(f"✅ Morning: {len(data)} job(s) queued.")

    if not scheduler.get_job("job_15min"):
        scheduler.add_job(
            every_15min_task,
            trigger="cron",
            day_of_week="mon-sat",
            hour="9-17",
            minute="*/15",
            id="job_15min",
            name="Every 15 Min (9AM–6PM)"
        )
        logger.info("✅ 15-min job registered.")


async def stop_day():
    if scheduler.get_job("job_15min"):
        scheduler.remove_job("job_15min")
        logger.info(f"[{datetime.now():%H:%M}] 🔴 15-min job stopped.")

    if not scheduler.get_job("job_hourly"):
        scheduler.add_job(
            after_hours_task,
            trigger="cron",
            day_of_week="mon-sat",
            hour="18-23",
            minute=0,
            id="job_hourly",
            name="Every Hour (6PM–11PM)"
        )
        logger.info(f"[{datetime.now():%H:%M}] 🌙 Hourly after-hours job started.")


def setup_cron_jobs():
    scheduler.add_job(
        start_day,
        trigger="cron",
        day_of_week="mon-sat",
        hour=9, minute=0,
        id="trigger_start",
        name="Day Start (9 AM)"
    )
    scheduler.add_job(
        stop_day,
        trigger="cron",
        day_of_week="mon-sat",
        hour=18, minute=0,
        id="trigger_stop",
        name="Day Stop (6 PM)"
    )
    logger.info("📋 Cron jobs registered.")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — MODELS & JOB REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════

class CallJob(BaseModel):
    phone_number: str
    job_id: Optional[str] = None
    trunk_id: Optional[str] = None
    name: Optional[str] = None
    scheduled_time: Optional[str] = None
    max_retries: int = 2
    retry_delay: int = 300
    company_name: Optional[str] = None
    company_id: Optional[str] = None
    assistant_role: Optional[str] = None

    @field_validator("scheduled_time", mode="before")
    @classmethod
    def validate_time(cls, v):
        if not v:
            return None
        try:
            datetime.fromisoformat(str(v))
            return v
        except ValueError:
            raise ValueError("Format must be YYYY-MM-DDTHH:MM:SS")


def register_job(job: CallJob) -> dict:
    job_id = job.job_id or f"call_{uuid.uuid4().hex[:8]}"
    now = datetime.now()

    # ── Resolve scheduled time (auto-shift if collision) ───────────────────────
    if job.scheduled_time:
        resolved_time = resolve_unique_time(job.phone_number, job.scheduled_time)
        run_at = datetime.fromisoformat(resolved_time).replace(tzinfo=None)
        if run_at < now:
            logger.warning(f"{job_id}: scheduled time in past — running immediately.")
            run_at = now
            resolved_time = run_at.isoformat()
    else:
        run_at = now
        resolved_time = run_at.replace(microsecond=0).isoformat()

    data = job.model_dump()
    data["job_id"] = job_id
    data["scheduled_time"] = resolved_time   # store the resolved (possibly shifted) time
    data["status"] = "not started"
    data.setdefault("max_retries", 2)
    data.setdefault("retry_delay", 300)

    # ── Safe insert — skip silently on duplicate ───────────────────────────────
    insert_result = safe_insert_job(data)
    if insert_result is None:
        logger.warning(f"⚠️  Job skipped (duplicate): {job.phone_number} at {resolved_time}")
        return {
            "job_id": job_id,
            "run_at": resolved_time,
            "status": "skipped",
            "reason": "duplicate phone_number + scheduled_time"
        }

    # ── Register in APScheduler ────────────────────────────────────────────────
    scheduler.add_job(
        execute_scheduled_call,
        trigger="date",
        run_date=run_at,
        id=job_id,
        args=[data],
        name=f"Call_{job.phone_number}",
        replace_existing=True,
    )
    logger.info(f"📋 Registered: {job_id} → {job.phone_number} at {resolved_time}")
    return {"job_id": job_id, "run_at": resolved_time, "status": "queued"}


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — FASTAPI APP + LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    global lk_client, _shutting_down
    _shutting_down = False

    lk_client = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET"),
    )

    setup_indexes()       # ✅ DB-level unique constraints applied on startup
    setup_cron_jobs()
    scheduler.start()
    logger.info("✅ App started — indexes, cron jobs, and scheduler all running.")

    yield

    _shutting_down = True
    scheduler.shutdown(wait=False)
    await lk_client.aclose()
    dbms.close()
    logger.info("🛑 Shutdown complete.")


app = FastAPI(title="Outbound Scheduler", lifespan=lifespan)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — API ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "service": "Outbound Scheduler",
        "endpoints": {
            "POST /schedule/single":  "Schedule one call",
            "POST /schedule/bulk":    "Schedule multiple calls",
            "POST /schedule/file":    "Schedule from CSV/XLSX",
            "GET  /jobs":             "List all jobs + capacity info",
            "DELETE /jobs/{job_id}":  "Cancel a job",
            "GET  /capacity":         "Live trunk capacity snapshot",
        },
    }


@app.post("/schedule/single")
async def schedule_single(job: CallJob):
    return {"status": "success", "data": register_job(job)}


@app.post("/schedule/bulk")
async def schedule_bulk(jobs: List[CallJob]):
    if not jobs:
        raise HTTPException(400, "Job list is empty.")
    results = [register_job(j) for j in jobs]
    queued   = [r for r in results if r["status"] == "queued"]
    skipped  = [r for r in results if r["status"] == "skipped"]
    return {
        "status": "success",
        "jobs_scheduled": len(queued),
        "jobs_skipped": len(skipped),
        "jobs": results
    }


@app.post("/schedule/file")
async def schedule_file(file: UploadFile = File(...)):
    filename = file.filename or ""
    content  = await file.read()

    if filename.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content))
    elif filename.endswith(".xlsx"):
        df = pd.read_excel(io.BytesIO(content))
    else:
        raise HTTPException(400, "Upload a .csv or .xlsx file.")

    df.columns = df.columns.str.strip().str.lower()
    if "phone_number" not in df.columns:
        raise HTTPException(422, "Missing required column: phone_number")

    df = df.where(pd.notna(df), None)

    def col(row, key):
        return str(row[key]) if key in df.columns and row.get(key) is not None else None

    jobs = [
        CallJob(
            phone_number=str(row["phone_number"]),
            job_id=col(row, "job_id"),
            trunk_id=col(row, "trunk_id"),
            name=col(row, "name"),
            scheduled_time=col(row, "scheduled_time"),
            max_retries=int(row["max_retries"]) if "max_retries" in df.columns and row.get("max_retries") else 2,
            retry_delay=int(row["retry_delay"]) if "retry_delay" in df.columns and row.get("retry_delay") else 300,
            company_name=col(row, "company_name"),
            company_id=col(row, "company_id"),
            assistant_role=col(row, "assistant_role"),
        )
        for _, row in df.iterrows()
    ]

    if not jobs:
        raise HTTPException(422, "No valid rows found.")

    results = [register_job(j) for j in jobs]
    queued  = [r for r in results if r["status"] == "queued"]
    skipped = [r for r in results if r["status"] == "skipped"]
    return {
        "status": "success",
        "file": filename,
        "jobs_scheduled": len(queued),
        "jobs_skipped": len(skipped),
        "jobs": results
    }


@app.get("/jobs")
async def list_jobs():
    jobs = [
        {
            "id": j.id,
            "name": j.name,
            "next_run_utc": j.next_run_time.isoformat() if j.next_run_time else None,
            "trigger": str(j.trigger),
            "args": j.args,
        }
        for j in scheduler.get_jobs()
    ]
    return {
        "queued_jobs": len(jobs),
        "jobs": jobs,
        "trunk_capacity": {
            "active_calls": trunk_manager.total_active,
            "total_slots": trunk_manager.total_capacity,
            "available_slots": trunk_manager.total_capacity - trunk_manager.total_active,
            "per_trunk": trunk_manager.status(),
        },
    }


@app.get("/capacity")
async def get_capacity():
    return {
        "active_calls": trunk_manager.total_active,
        "total_slots": trunk_manager.total_capacity,
        "available_slots": trunk_manager.total_capacity - trunk_manager.total_active,
        "per_trunk": trunk_manager.status(),
    }


@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    if not scheduler.get_job(job_id):
        raise HTTPException(404, f"Job '{job_id}' not found.")
    scheduler.remove_job(job_id)
    return {"status": "cancelled", "job_id": job_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)