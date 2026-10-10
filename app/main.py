import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from pymongo import MongoClient, ReturnDocument
import pymongo.errors

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))


def setup_database_indexes():
    db.employees.create_index([("emp_code", 1)], unique=True)
    db.employees.create_index([("department", 1)])
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.employees.create_index([("joined_on", 1)])

    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("status", 1), ("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", -1)])
    db.attendance_logs.create_index([("date", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1), ("punch_out", 1)])
    db.attendance_logs.create_index([("late_minutes", -1)])


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    setup_database_indexes()
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=app_lifespan)


def do_rounding(val: float, decimals: int = 2) -> float:
    d = Decimal(str(val))
    q = Decimal("10") ** -decimals
    res = float(d.quantize(q, rounding=ROUND_HALF_UP))
    return res


def date_to_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    ms_val = int(ts * 1000)
    return ms_val


def ms_to_date(ms: int) -> datetime:
    sec = ms // 1000
    res_dt = datetime.fromtimestamp(sec, tz=timezone.utc)
    return res_dt


def check_valid_ms(ms: Optional[int]) -> Optional[int]:
    if ms is None:
        return None
    if not isinstance(ms, int) or isinstance(ms, bool):
        raise HTTPException(422, "Epoch ms must be an integer")
    if ms < 100000000000 or ms > 4102444800000:
        raise HTTPException(422, "Epoch ms out of valid range")
    return ms


def calc_attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    punch_in_ist = punch_in_utc.astimezone(IST)
    if shift_end <= shift_start:
        time_str = punch_in_ist.strftime("%H:%M:%S")
        if time_str < f"{shift_end}:00":
            prev_day = punch_in_ist.date() - timedelta(days=1)
            return prev_day.isoformat()
    return punch_in_ist.date().isoformat()


def calc_late_minutes(punch_in_utc: datetime, shift_start: str, attendance_date: str) -> int:
    punch_in_ist = punch_in_utc.astimezone(IST)
    date_parts = attendance_date.split("-")
    year = int(date_parts[0])
    month = int(date_parts[1])
    day = int(date_parts[2])

    start_parts = shift_start.split(":")
    sh = int(start_parts[0])
    sm = int(start_parts[1])

    shift_start_dt = datetime(year, month, day, sh, sm, 0, tzinfo=IST)
    diff_sec = int((punch_in_ist - shift_start_dt).total_seconds())
    if diff_sec > 600:
        late_mins = diff_sec // 60
        return late_mins
    return 0


def calc_overtime_minutes(punch_out_utc: datetime, shift_start: str, shift_end: str, attendance_date: str) -> int:
    punch_out_ist = punch_out_utc.astimezone(IST)
    date_parts = attendance_date.split("-")
    year = int(date_parts[0])
    month = int(date_parts[1])
    day = int(date_parts[2])

    end_parts = shift_end.split(":")
    eh = int(end_parts[0])
    em = int(end_parts[1])

    if shift_end <= shift_start:
        next_day = datetime(year, month, day, tzinfo=IST) + timedelta(days=1)
        shift_end_dt = datetime(next_day.year, next_day.month, next_day.day, eh, em, 0, tzinfo=IST)
    else:
        shift_end_dt = datetime(year, month, day, eh, em, 0, tzinfo=IST)

    diff_sec = int((punch_out_ist - shift_end_dt).total_seconds())
    if diff_sec >= 1800:
        ot_mins = diff_sec // 60
        return ot_mins
    return 0


def format_employee_data(doc: Dict[str, Any]) -> Dict[str, Any]:
    res = {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": date_to_ms(doc["created_at"]),
    }
    return res


def format_history_data(entry: Dict[str, Any]) -> Dict[str, Any]:
    formatted_changes = {}
    changes_dict = entry.get("changes", {})
    for key, val in changes_dict.items():
        if key == "punch_in" or key == "punch_out":
            formatted_changes[key] = {
                "from": date_to_ms(val.get("from")),
                "to": date_to_ms(val.get("to")),
            }
        else:
            formatted_changes[key] = {
                "from": val.get("from"),
                "to": val.get("to"),
            }
    res = {
        "at": date_to_ms(entry["at"]),
        "by": entry["by"],
        "reason": entry["reason"],
        "changes": formatted_changes,
    }
    return res


def format_attendance_data(doc: Dict[str, Any]) -> Dict[str, Any]:
    history_list = []
    raw_history = doc.get("history", [])
    for h in raw_history:
        formatted_h = format_history_data(h)
        history_list.append(formatted_h)

    res = {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": date_to_ms(doc.get("punch_in")),
        "punch_out": date_to_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": history_list,
    }
    return res


def check_working_day(date_str: str) -> bool:
    date_parts = date_str.split("-")
    y = int(date_parts[0])
    m = int(date_parts[1])
    d = int(date_parts[2])
    dt_obj = datetime(y, m, d)
    day_num = dt_obj.weekday()
    if day_num < 5:
        return True
    else:
        return False


def get_month_range_info(month_str: str):
    parts = month_str.split("-")
    y = int(parts[0])
    m = int(parts[1])
    start_date = month_str + "-01"
    if m == 12:
        next_month = datetime(y + 1, 1, 1)
    else:
        next_month = datetime(y, m + 1, 1)
    last_day_dt = next_month - timedelta(days=1)
    end_date = last_day_dt.strftime("%Y-%m-%d")
    total_days = last_day_dt.day
    return start_date, end_date, total_days


class EmployeeCreate(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeRequest(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)


@app.get("/health")
def get_health_status():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")


@app.post("/employees", status_code=201)
def handle_create_employee(body: EmployeeCreate):
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start and shift_end must differ")

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except pymongo.errors.DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    result = format_employee_data(doc)
    return result


@app.get("/employees")
def handle_get_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department is not None and department != "":
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    docs = list(db.employees.find(q).sort("emp_code", 1).skip(skip).limit(page_size))

    items = []
    for d in docs:
        item = format_employee_data(d)
        items.append(item)

    res = {"items": items, "total": total, "page": page, "page_size": page_size}
    return res


@app.post("/attendance/punch-in", status_code=201)
def handle_punch_in(body: PunchInRequest):
    if body.status not in ("PRESENT", "WFH", "ON_DUTY"):
        raise HTTPException(422, "Invalid status for punch-in")

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    if body.punched_at is not None:
        check_valid_ms(body.punched_at)
        sec = body.punched_at // 1000
        punch_in_utc = datetime.fromtimestamp(sec, tz=timezone.utc)
    else:
        sec = int(datetime.now(timezone.utc).timestamp())
        punch_in_utc = datetime.fromtimestamp(sec, tz=timezone.utc)

    date_str = calc_attendance_date(punch_in_utc, emp["shift_start"], emp["shift_end"])
    late_mins = calc_late_minutes(punch_in_utc, emp["shift_start"], date_str)

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": punch_in_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except pymongo.errors.DuplicateKeyError:
        raise HTTPException(409, "Already punched in for this date")

    res = format_attendance_data(doc)
    return res


@app.post("/attendance/punch-out")
def handle_punch_out(body: PunchOutRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    if body.punched_at is not None:
        check_valid_ms(body.punched_at)
        sec = body.punched_at // 1000
        punch_out_utc = datetime.fromtimestamp(sec, tz=timezone.utc)
    else:
        sec = int(datetime.now(timezone.utc).timestamp())
        punch_out_utc = datetime.fromtimestamp(sec, tz=timezone.utc)

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punch_out_utc}, "punch_out": None},
        sort=[("punch_in", -1)],
    )

    if record is None:
        already_out = db.attendance_logs.find_one(
            {"emp_code": body.emp_code, "punch_in": {"$lte": punch_out_utc}, "punch_out": {"$ne": None}},
            sort=[("punch_in", -1)],
        )
        if already_out is not None:
            raise HTTPException(409, "Already punched out")
        raise HTTPException(404, "No matching open punch-in record found")

    punch_in_utc = record["punch_in"]
    if punch_out_utc <= punch_in_utc:
        raise HTTPException(422, "punch_out must be after punch_in")

    duration_sec = (punch_out_utc - punch_in_utc).total_seconds()
    if duration_sec > 86400:
        raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")

    work_hours = do_rounding(duration_sec / 3600.0, 2)
    if record["status"] in ("PRESENT", "WFH", "ON_DUTY"):
        half_day = bool(work_hours < 4.50)
    else:
        half_day = False

    overtime_mins = calc_overtime_minutes(punch_out_utc, emp["shift_start"], emp["shift_end"], record["date"])

    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": punch_out_utc,
                "work_hours": work_hours,
                "half_day": half_day,
                "overtime_minutes": overtime_mins,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(409, "Already punched out")

    res = format_attendance_data(updated)
    return res


@app.get("/attendance")
def handle_get_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    docs = list(
        db.attendance_logs.find(q)
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    items = []
    for d in docs:
        item = format_attendance_data(d)
        items.append(item)

    res = {"items": items, "total": total, "page": page, "page_size": page_size}
    return res


@app.patch("/attendance/{emp_code}/{date}")
def handle_update_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "Attendance record not found for specified date")

    if body.status is not None:
        new_status = body.status
    else:
        new_status = record["status"]

    if body.status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "punch_in and punch_out cannot be supplied when setting ABSENT or LEAVE")
        new_punch_in_utc = None
        new_punch_out_utc = None
    else:
        if body.punch_in is not None:
            check_valid_ms(body.punch_in)
            new_punch_in_utc = ms_to_date(body.punch_in)
        else:
            new_punch_in_utc = record.get("punch_in")

        if body.punch_out is not None:
            check_valid_ms(body.punch_out)
            new_punch_out_utc = ms_to_date(body.punch_out)
        else:
            new_punch_out_utc = record.get("punch_out")

    if new_status in ("PRESENT", "WFH", "ON_DUTY"):
        if new_punch_in_utc is None:
            raise HTTPException(422, "punch_in is required for presence status")

        calc_date = calc_attendance_date(new_punch_in_utc, emp["shift_start"], emp["shift_end"])
        if calc_date != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")

        if new_punch_out_utc is not None:
            if new_punch_out_utc <= new_punch_in_utc:
                raise HTTPException(422, "punch_out must be after punch_in")
            if (new_punch_out_utc - new_punch_in_utc).total_seconds() > 86400:
                raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")

    if new_status in ("ABSENT", "LEAVE"):
        new_work_hours = None
        new_late_mins = 0
        new_overtime_mins = 0
        new_half_day = False
    else:
        new_late_mins = calc_late_minutes(new_punch_in_utc, emp["shift_start"], date)
        if new_punch_out_utc is not None:
            duration_sec = (new_punch_out_utc - new_punch_in_utc).total_seconds()
            new_work_hours = do_rounding(duration_sec / 3600.0, 2)
            new_half_day = bool(new_work_hours < 4.50)
            new_overtime_mins = calc_overtime_minutes(new_punch_out_utc, emp["shift_start"], emp["shift_end"], date)
        else:
            new_work_hours = None
            new_overtime_mins = 0
            new_half_day = False

    changes = {}
    if new_status != record["status"]:
        changes["status"] = {"from": record["status"], "to": new_status}
    if new_punch_in_utc != record.get("punch_in"):
        changes["punch_in"] = {"from": record.get("punch_in"), "to": new_punch_in_utc}
    if new_punch_out_utc != record.get("punch_out"):
        changes["punch_out"] = {"from": record.get("punch_out"), "to": new_punch_out_utc}
    if new_work_hours != record.get("work_hours"):
        changes["work_hours"] = {"from": record.get("work_hours"), "to": new_work_hours}
    if new_late_mins != record.get("late_minutes", 0):
        changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": new_late_mins}
    if new_overtime_mins != record.get("overtime_minutes", 0):
        changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": new_overtime_mins}
    if new_half_day != record.get("half_day", False):
        changes["half_day"] = {"from": record.get("half_day", False), "to": new_half_day}

    if not changes:
        raise HTTPException(422, "Request changes no fields")

    now_utc = datetime.now(timezone.utc)
    history_entry = {
        "at": now_utc,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    set_doc = {
        "status": new_status,
        "punch_in": new_punch_in_utc,
        "punch_out": new_punch_out_utc,
        "work_hours": new_work_hours,
        "late_minutes": new_late_mins,
        "overtime_minutes": new_overtime_mins,
        "half_day": new_half_day,
    }

    current_history = record.get("history", [])
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "history": current_history},
        {
            "$set": set_doc,
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(409, "Concurrent regularization conflict")

    res = format_attendance_data(updated)
    return res


@app.get("/analytics/employees/{emp_code}/monthly")
def handle_get_employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")

    start_date, end_date, num_days = get_month_range_info(month)

    joined_on = emp["joined_on"]
    working_days = 0
    if joined_on <= end_date:
        eff_start = max(joined_on, start_date)
        parts_s = eff_start.split("-")
        y_s = int(parts_s[0])
        m_s = int(parts_s[1])
        d_s = int(parts_s[2])

        parts_e = end_date.split("-")
        y_e = int(parts_e[0])
        m_e = int(parts_e[1])
        d_e = int(parts_e[2])

        cur = datetime(y_s, m_s, d_s)
        end_dt = datetime(y_e, m_e, d_e)
        while cur <= end_dt:
            if cur.weekday() < 5:
                working_days = working_days + 1
            cur = cur + timedelta(days=1)

    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start_date, "$lte": end_date}}},
    ]
    logs = list(db.attendance_logs.aggregate(pipeline))

    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for log in logs:
        log_date = log["date"]
        st = log.get("status")
        is_wf = check_working_day(log_date)

        if st in ("PRESENT", "WFH", "ON_DUTY") and is_wf == True:
            if log.get("half_day"):
                present_days = present_days + 0.5
            else:
                present_days = present_days + 1.0

        if st == "LEAVE":
            leave_days = leave_days + 1

        lm = log.get("late_minutes", 0)
        if lm > 0:
            late_count = late_count + 1
            total_late_minutes = total_late_minutes + lm

        om = log.get("overtime_minutes", 0)
        total_overtime_minutes = total_overtime_minutes + om

    attendance_pct = None
    if working_days > 0:
        attendance_pct = do_rounding((present_days / working_days) * 100.0, 2)

    res = {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }
    return res


@app.get("/analytics/departments/summary")
def handle_get_department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    start_date, end_date, _ = get_month_range_info(month)

    emp_match = {"joined_on": {"$lte": end_date}}
    if department:
        emp_match["department"] = department

    pipeline = [
        {"$match": emp_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"e_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$e_code"]},
                                    {"$gte": ["$date", start_date]},
                                    {"$lte": ["$date", end_date]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code",
                },
                "logs": {"$push": "$logs"},
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "all_logs": {"$push": "$logs"},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    dept_docs = list(db.employees.aggregate(pipeline))

    items = []
    for ddoc in dept_docs:
        dept_name = ddoc["_id"]
        headcount = ddoc["headcount"]
        present_days = 0.0
        work_hours_list = []
        late_count = 0
        total_late_minutes = 0
        leave_count = 0
        on_duty_count = 0

        for emp_logs in ddoc["all_logs"]:
            for log in emp_logs:
                if not log or "date" not in log:
                    continue
                st = log.get("status")
                ldate = log["date"]

                if st in ("PRESENT", "WFH", "ON_DUTY") and check_working_day(ldate) == True:
                    if log.get("half_day"):
                        present_days = present_days + 0.5
                    else:
                        present_days = present_days + 1.0

                if st in ("PRESENT", "WFH", "ON_DUTY") and log.get("work_hours") is not None:
                    work_hours_list.append(log["work_hours"])

                lm = log.get("late_minutes", 0)
                if lm > 0:
                    late_count = late_count + 1
                    total_late_minutes = total_late_minutes + lm

                if st == "LEAVE":
                    leave_count = leave_count + 1
                elif st == "ON_DUTY":
                    on_duty_count = on_duty_count + 1

        avg_work_hours = None
        if len(work_hours_list) > 0:
            avg_work_hours = do_rounding(sum(work_hours_list) / len(work_hours_list), 2)

        items.append(
            {
                "department": dept_name,
                "headcount": headcount,
                "present_days": present_days,
                "avg_work_hours": avg_work_hours,
                "late_count": late_count,
                "total_late_minutes": total_late_minutes,
                "leave_count": leave_count,
                "on_duty_count": on_duty_count,
            }
        )

    res = {"month": month, "items": items}
    return res


@app.get("/analytics/leaderboard/late")
def handle_get_late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    start_date, end_date, _ = get_month_range_info(month)

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend(
        [
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
        ]
    )

    results = list(db.attendance_logs.aggregate(pipeline))

    items = []
    for r in results:
        items.append(
            {
                "rank": r["rank"],
                "emp_code": r["_id"],
                "name": r["emp"]["name"],
                "department": r["emp"]["department"],
                "total_late_minutes": r["total_late_minutes"],
                "late_count": r["late_count"],
            }
        )

    res = {"month": month, "items": items}
    return res


@app.get("/analytics/departments/{department}/trend")
def handle_get_department_trend(
    department: str = Path(...),
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    dept_exists = db.employees.find_one({"department": department})
    if dept_exists is None:
        raise HTTPException(404, "Department not found")

    if to_date < from_date:
        raise HTTPException(422, "to cannot be before from")

    d_from = datetime.strptime(from_date, "%Y-%m-%d")
    d_to = datetime.strptime(to_date, "%Y-%m-%d")
    diff_days = (d_to - d_from).days
    if diff_days > 92:
        raise HTTPException(422, "Date range cannot exceed 92 days")

    date_list = []
    cur = d_from
    while cur <= d_to:
        date_list.append(cur.strftime("%Y-%m-%d"))
        cur = cur + timedelta(days=1)

    items = []
    recent_rates: List[Optional[float]] = []

    for d_str in date_list:
        is_wf = check_working_day(d_str)

        hc = db.employees.count_documents(
            {"department": department, "joined_on": {"$lte": d_str}}
        )

        dept_emps = list(
            db.employees.find({"department": department}, {"emp_code": 1, "_id": 0})
        )
        emp_codes = []
        for e in dept_emps:
            emp_codes.append(e["emp_code"])

        logs = list(
            db.attendance_logs.find(
                {"emp_code": {"$in": emp_codes}, "date": d_str}
            )
        )

        present_count = 0.0
        late_count = 0

        for log in logs:
            st = log.get("status")
            if st in ("PRESENT", "WFH", "ON_DUTY"):
                if log.get("half_day"):
                    present_count = present_count + 0.5
                else:
                    present_count = present_count + 1.0

            if log.get("late_minutes", 0) > 0:
                late_count = late_count + 1

        att_rate = None
        if is_wf == True and hc > 0:
            att_rate = do_rounding(present_count / hc, 4)

        recent_rates.append(att_rate)
        window = recent_rates[-7:]
        valid_rates = []
        for r in window:
            if r is not None:
                valid_rates.append(r)

        moving_avg = None
        if len(valid_rates) > 0:
            moving_avg = do_rounding(sum(valid_rates) / len(valid_rates), 4)

        items.append(
            {
                "date": d_str,
                "is_working_day": is_wf,
                "headcount": hc,
                "present_count": present_count,
                "late_count": late_count,
                "attendance_rate": att_rate,
                "moving_avg_7d": moving_avg,
            }
        )

    res = {"department": department, "items": items}
    return res


@app.get("/admin/explain/{endpoint}")
def handle_explain_query(
    endpoint: str = Path(...),
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: Optional[int] = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
):
    if endpoint == "attendance_list":
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        skip = (page - 1) * page_size
        expl = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size).explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": expl}

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required for employee_monthly")
        start_date, end_date, _ = get_month_range_info(month)
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": start_date, "$lte": end_date}}},
        ]
        expl = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": expl}

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required for department_summary")
        start_date, end_date, _ = get_month_range_info(month)
        emp_match = {"joined_on": {"$lte": end_date}}
        if department:
            emp_match["department"] = department
        pipeline = [
            {"$match": emp_match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"e_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$e_code"]},
                                        {"$gte": ["$date", start_date]},
                                        {"$lte": ["$date", end_date]},
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
        ]
        expl = db.command("explain", {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "employees", "explain": expl}

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required for late_leaderboard")
        start_date, end_date, _ = get_month_range_info(month)
        pipeline = [
            {
                "$match": {
                    "date": {"$gte": start_date, "$lte": end_date},
                    "late_minutes": {"$gt": 0},
                }
            },
        ]
        expl = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": expl}

    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(422, "department, from, and to are required for department_trend")
        expl = db.attendance_logs.find({"date": {"$gte": from_date, "$lte": to_date}}).explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": expl}

    else:
        raise HTTPException(422, "Unknown endpoint name")
