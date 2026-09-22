import hashlib
import csv
import io
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from starlette.middleware.sessions import SessionMiddleware

try:
    import bcrypt
except ModuleNotFoundError:
    bcrypt = None

load_dotenv()
app = FastAPI(title="Lion Parcel Tanah Abang Leads")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY", "dev-only-change-me"))
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

EXPEDITIONS = ["Lion Parcel", "Rayspeed Asia", "TLX", "J&T Express", "J&T Cargo", "JNE", "Sicepat", "POS Indonesia", "TIKI", "Ninja Xpress", "AnterAja", "ID Express", "Lainnya", "Tidak Ada"]
BLOCKS = ["A", "B", "C", "D", "E", "F", "G", "PGMTA", "PMTA", "JMTA"]
FLOORS = ["B3", "B2", "B1", "SLG", "LG", "G", "1", "2", "3", "3A", "4", "5", "6", "7", "8", "9", "10", "11", "12", "12A", "R"]
LOS_OPTIONS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "FNO"]
PIC_POSITIONS = ["Owner", "Karyawan Toko", "Lainnya"]
TOP_COUNTRIES = ["Indonesia", "Malaysia Timur", "Malaysia Barat", "Singapore", "Thailand", "Vietnam", "Philippines", "Brunei", "China", "Hong Kong", "Taiwan", "South Korea", "Japan", "Australia", "United States", "United Kingdom", "Lainnya"]
TOP_CITIES = ["Jakarta", "Bandung", "Surabaya", "Medan", "Semarang", "Yogyakarta", "Makassar", "Denpasar", "Palembang", "Banjarmasin", "Pontianak", "Balikpapan", "Padang", "Pekanbaru", "Bandar Lampung", "Malang", "Solo", "Bogor", "Depok", "Tangerang", "Bekasi", "Serang", "Cirebon", "Tasikmalaya", "Purwakarta", "Sukabumi", "Mataram", "Kupang", "Manado", "Palu", "Kendari", "Ambon", "Jayapura", "Samarinda", "Banda Aceh", "Lainnya"]
ROLE_LABELS = {"data_entry": "Data Entry", "admin": "Admin", "router": "Router", "sales": "Sales"}
ROLES = list(ROLE_LABELS)
DEMO_USERS = [
    {"id": "demo-admin", "name": "Admin Tanah Abang", "username": "admin", "role": "admin", "password": "admin123"},
    {"id": "demo-entry", "name": "Data Entry", "username": "dataentry", "role": "data_entry", "password": "entry123"},
    {"id": "demo-router", "name": "Router Visit", "username": "router", "role": "router", "password": "router123"},
    {"id": "demo-sales", "name": "Sales Tanah Abang", "username": "sales", "role": "sales", "password": "sales123"},
]
DEMO_LEADS: list[dict[str, Any]] = []
DEMO_ROUTING_TARGETS: dict[str, int] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_datetime(value: Any) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local_time = parsed.astimezone(ZoneInfo("Asia/Jakarta"))
        return local_time.strftime("%d %B %Y, %H:%M WIB")
    except (TypeError, ValueError):
        return str(value)


def password_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


class Store:
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "") or os.getenv("SUPABASE_KEY", "")
        self.demo = not (self.url and self.key)

    def _request(self, method: str, table: str, *, params: dict | None = None, body: Any = None) -> list[dict]:
        response = requests.request(method, f"{self.url}/rest/v1/{table}", headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "Prefer": "return=representation"}, params=params, json=body, timeout=15)
        response.raise_for_status()
        return response.json() if response.content else []

    def users(self) -> list[dict]:
        if self.demo:
            return [{k: v for k, v in user.items() if k != "password"} for user in DEMO_USERS]
        rows = self._request("GET", "users", params={"select": "*", "order": "created_at"})
        return [self._normalise_user(row) for row in rows]

    @staticmethod
    def _normalise_user(row: dict) -> dict:
        return {
            "id": row.get("id"),
            "name": row.get("name") or row.get("full_name") or row.get("username"),
            "username": row.get("username") or row.get("email"),
            "role": row.get("role"),
            "active": row.get("active", row.get("is_active", True)),
        }

    def authenticate(self, username: str, password: str) -> dict | None:
        if self.demo:
            user = next((u for u in DEMO_USERS if u["username"].lower() == username.lower() and u["password"] == password and u.get("active", True)), None)
            return {k: v for k, v in user.items() if k != "password"} if user else None
        users = self._request("GET", "users", params={"select": "*"})
        for row in users:
            login_name = row.get("username") or ""
            if login_name.lower() != username.lower():
                continue
            stored_hash = row.get("password_hash", "")
            valid = False
            try:
                valid = bool(bcrypt and bcrypt.checkpw(password.encode(), stored_hash.encode())) if stored_hash.startswith("$2") else stored_hash == password_hash(password)
            except ValueError:
                valid = False
            if valid and row.get("active", row.get("is_active", True)):
                return self._normalise_user(row)
        return None

    def leads(self, search: str = "") -> list[dict]:
        rows = DEMO_LEADS if self.demo else self._request("GET", "leads", params={"select": "*", "order": "created_at.desc"})
        if search:
            needle = search.lower()
            rows = [row for row in rows if needle in " ".join(str(row.get(k, "")) for k in ("store_name", "block", "pic_name", "sales_id")).lower()]
        return rows

    def routes(self) -> list[dict]:
        if self.demo:
            return [
                {"id": f"demo-route-{lead['id']}", "lead_id": lead["id"], "sales_id": lead.get("sales_id"), "scheduled_date": lead.get("visit_date"), "visit_status": "Scheduled", "notes": "", "leads": lead}
                for lead in DEMO_LEADS if lead.get("sales_id")
            ]
        return self._request("GET", "visit_routes", params={"select": "*,leads(*),users!visit_routes_sales_id_fkey(id,username,full_name)", "order": "scheduled_date.desc"})

    def routing_target(self, month: str) -> int:
        if self.demo:
            return DEMO_ROUTING_TARGETS.get(month, 0)
        rows = self._request("GET", "routing_targets", params={"select": "target_count", "month": f"eq.{quote(month)}", "limit": "1"})
        return int(rows[0].get("target_count") or 0) if rows else 0

    def save_routing_target(self, month: str, target_count: int, user_id: str) -> None:
        if self.demo:
            DEMO_ROUTING_TARGETS[month] = target_count
            return
        existing = self._request("GET", "routing_targets", params={"select": "id", "month": f"eq.{quote(month)}", "limit": "1"})
        payload = {"month": month, "target_count": target_count, "updated_by": user_id}
        if existing:
            self._request("PATCH", "routing_targets", params={"id": f"eq.{quote(str(existing[0]['id']))}"}, body=payload)
        else:
            self._request("POST", "routing_targets", body=payload)

    def create_lead(self, data: dict) -> dict:
        if self.demo:
            data.update({"id": secrets.token_urlsafe(10), "created_at": now_iso(), "visit_count": 0, "routing_status": "Belum diplot"})
            DEMO_LEADS.insert(0, data)
            return data
        data.update({"created_at": now_iso(), "visit_count": 0})
        return self._request("POST", "leads", body=data)[0]

    def assign_route(self, lead_id: str, sales_id: str, router_id: str, scheduled_date: str, notes: str = "") -> None:
        if self.demo:
            self.update_lead(lead_id, {"visit_date": scheduled_date, "sales_id": sales_id, "visit_count": 1})
            return
        self._request("POST", "visit_routes", body={"lead_id": lead_id, "sales_id": sales_id, "router_id": router_id, "scheduled_date": scheduled_date, "notes": notes, "visit_status": "Scheduled"})
        lead_rows = self._request("GET", "leads", params={"select": "visit_count", "id": f"eq.{quote(lead_id)}"})
        current = int(lead_rows[0].get("visit_count") or 0) if lead_rows else 0
        self.update_lead(lead_id, {"visit_count": current + 1})

    def update_lead(self, lead_id: str, data: dict) -> None:
        if self.demo:
            lead = next((item for item in DEMO_LEADS if item["id"] == lead_id), None)
            if lead:
                lead.update(data)
            return
        self._request("PATCH", "leads", params={"id": f"eq.{quote(lead_id)}"}, body=data)

    def save_user(self, data: dict, user_id: str | None = None) -> None:
        password = data.pop("password", "")
        if self.demo:
            if user_id:
                user = next((item for item in DEMO_USERS if item["id"] == user_id), None)
                if user:
                    user.update({k: v for k, v in data.items() if v})
                    if password:
                        user["password"] = password
            else:
                DEMO_USERS.append({"id": secrets.token_urlsafe(8), **data, "password": password})
            return
        existing = self._request("GET", "users", params={"select": "*", "limit": "1"})
        legacy_schema = bool(existing and "username" in existing[0])
        payload = {"username": data["username"], "full_name": data["name"], "role": data["role"]} if legacy_schema else {"name": data["name"], "username": data["username"], "role": data["role"]}
        if not user_id:
            payload["is_active" if legacy_schema else "active"] = True
        if password:
            if bcrypt:
                payload["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        if user_id:
            self._request("PATCH", "users", params={"id": f"eq.{quote(user_id)}"}, body=payload)
        else:
            self._request("POST", "users", body=payload)

    def set_user_active(self, user_id: str, active: bool) -> None:
        if self.demo:
            user = next((item for item in DEMO_USERS if item["id"] == user_id), None)
            if user:
                user["active"] = active
            return
        existing = self._request("GET", "users", params={"select": "*", "limit": "1"})
        status_field = "is_active" if existing and "is_active" in existing[0] else "active"
        self._request("PATCH", "users", params={"id": f"eq.{quote(user_id)}"}, body={status_field: active})

    def delete_user(self, user_id: str) -> None:
        if self.demo:
            DEMO_USERS[:] = [user for user in DEMO_USERS if user["id"] != user_id]
            return
        self._request("DELETE", "users", params={"id": f"eq.{quote(user_id)}"})

    def stats(self) -> dict:
        rows = self.leads()
        routed = sum(row.get("routing_status") == "Sudah diplot" for row in rows)
        courier_stats: dict[str, int] = {}
        for row in rows:
            courier = row.get("expedition") or "Belum diisi"
            courier_stats[courier] = courier_stats.get(courier, 0) + 1
        return {
            "total": len(rows),
            "routed": routed,
            "visits": sum(int(row.get("visit_count") or 0) for row in rows),
            "weight": sum(float(row.get("tonnage_potential_kg") or 0) for row in rows),
            "status_stats": {"Scheduled": routed, "Visited": sum(int(row.get("visit_count") or 0) > 0 for row in rows), "Rescheduled": 0, "Canceled": 0},
            "courier_stats": courier_stats,
        }


store = Store()


def current_user(request: Request) -> dict | None:
    return request.session.get("user")


def render(request: Request, template: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(template, {"request": request, "user": current_user(request), "demo": store.demo, "expeditions": EXPEDITIONS, "role_labels": ROLE_LABELS, "format_datetime": format_datetime, **context})


def can(user: dict | None, *roles: str) -> bool:
    return bool(user and user.get("role") in roles)


def normalise_role(value: str) -> str:
    cleaned = value.strip().lower().replace(" ", "_")
    if cleaned not in ROLE_LABELS:
        raise ValueError("Role user tidak valid")
    return cleaned


def lead_form_context(**extra: Any) -> dict[str, Any]:
    return {"couriers": EXPEDITIONS, "blocks": BLOCKS, "floors": FLOORS, "los_options": LOS_OPTIONS, "pic_positions": PIC_POSITIONS, "top_countries": TOP_COUNTRIES, "top_cities": TOP_CITIES, **extra}


def normalise_choice(value: str, choices: list[str], field: str, required: bool = True) -> str | None:
    value = value.strip().upper() if field in {"block", "floor", "los"} else value.strip()
    if not value and not required:
        return None
    if value not in choices:
        raise ValueError(f"{field} tidak valid")
    return value


def normalise_phone_number(value: str) -> str:
    phone = re.sub(r"[\s().-]", "", value.strip())
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "62" + phone[1:]
    if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", phone):
        raise ValueError("Nomor HP/WhatsApp tidak valid. Contoh: 082123456789 otomatis menjadi 6282123456789")
    return phone


def required_text(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} wajib diisi")
    return value


def monthly_tonnage(lead: dict) -> float:
    amount = float(lead.get("tonnage_potential_kg") or 0)
    period = str(lead.get("tonnage_period") or "Bulan").strip().lower()
    if period in {"hari", "per hari"}:
        return amount * 30
    if period in {"tahun", "per tahun"}:
        return amount / 12
    return amount


def has_international_history(lead: dict) -> bool:
    shipment_type = str(lead.get("shipment_type", "")).lower()
    return shipment_type in {"international", "keduanya"} or "international" in shipment_type or "international" in str(lead.get("top_country", "")).lower()


def recommendation_score(candidate: dict, selected: dict | None = None) -> tuple[float, float]:
    score = monthly_tonnage(candidate)
    if has_international_history(candidate):
        score += 100000
    if selected:
        if candidate.get("block") == selected.get("block"):
            score += 50000
        if candidate.get("floor") == selected.get("floor"):
            score += 30000
        if candidate.get("los") == selected.get("los"):
            score += 10000
    return score, monthly_tonnage(candidate)


def selection_text(value: str | list[str], field: str, required: bool = True) -> str:
    values = [value] if isinstance(value, str) else value
    cleaned = [item.strip() for item in values if item and item.strip()]
    if not cleaned and required:
        raise ValueError(f"{field} wajib diisi")
    return ", ".join(dict.fromkeys(cleaned))


def normalise_bulk_value(value: Any, field: str) -> str:
    text = "" if value is None else str(value).strip()
    if field == "pic_position" and text.lower() == "karyawan":
        return "Karyawan Toko"
    if field == "tonnage_period":
        return {"Per Hari": "Hari", "Per Bulan": "Bulan", "Per Tahun": "Tahun"}.get(text, text)
    return text


def build_lead_data(user: dict, *, store_name: str, block: str, floor: str, los: str, nomor: str, pic_name: str, pic_position: str, phone_number: str, shipment_type: str, current_courier: str | list[str], top_country: str | list[str], top_city: str | list[str], tonnage_potential_kg: float, tonnage_period: str) -> dict:
    return {
        "visit_timestamp": now_iso(),
        "store_name": store_name.strip(),
        "block": normalise_choice(block, BLOCKS, "block"),
        "floor": normalise_choice(floor, FLOORS, "floor"),
        "los": normalise_choice(los, LOS_OPTIONS, "los"),
        "nomor": required_text(nomor, "Nomor Toko"),
        "pic_name": pic_name.strip(),
        "pic_position": normalise_choice(pic_position, PIC_POSITIONS, "pic_position"),
        "phone_number": normalise_phone_number(phone_number),
        "data_entry_pic": user.get("name") or user.get("full_name") or user.get("username", ""),
        "shipment_type": shipment_type,
        "current_courier": selection_text(current_courier, "Ekspedisi"),
        "top_country": selection_text(top_country, "Negara Terbanyak", required=False),
        "top_city": selection_text(top_city, "Kota Terbanyak", required=False),
        "tonnage_potential_kg": tonnage_potential_kg,
        "tonnage_period": normalise_bulk_value(tonnage_period, "tonnage_period"),
        "created_by": user["id"],
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    stats = store.stats()
    return render(request, "dashboard.html", title="Dashboard", stats=stats, total_leads=stats["total"], total_tonnage_ton=round(stats["weight"] / 1000, 2), status_stats=stats["status_stats"], courier_stats=stats["courier_stats"], recent=store.leads()[:8])


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render(request, "login.html", title="Masuk")


@app.post("/login")
@app.post("/auth/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = store.authenticate(username.strip(), password)
    if not user:
        return render(request, "login.html", title="Masuk", error="Username atau password tidak valid.")
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/leads", response_class=HTMLResponse)
async def leads_page(request: Request, search: str = "", block: str = "", floor: str = "", los: str = "", expedition: str = "", sort_by: str = "newest", page: int = 1, per_page: int = 20):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    all_leads = store.leads(search)
    filtered = []
    for lead in all_leads:
        if block and str(lead.get("block", "")) != block:
            continue
        if floor and str(lead.get("floor", "")) != floor:
            continue
        if los and str(lead.get("los", "")) != los:
            continue
        if expedition and expedition.lower() not in str(lead.get("current_courier", "")).lower():
            continue
        lead["monthly_tonnage_kg"] = monthly_tonnage(lead)
        filtered.append(lead)
    if sort_by == "monthly_tonnage":
        filtered.sort(key=lambda lead: lead["monthly_tonnage_kg"], reverse=True)
    else:
        sort_by = "newest"
        filtered.sort(key=lambda lead: str(lead.get("created_at") or lead.get("visit_timestamp") or ""), reverse=True)
    per_page = min(max(per_page, 10), 100)
    total = len(filtered)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * per_page
    return render(request, "data_entry/list.html", title="Daftar Leads", leads=filtered[start:start + per_page], search=search, block=block, floor=floor, los=los, expedition=expedition, sort_by=sort_by, page=page, per_page=per_page, total=total, total_pages=total_pages, blocks=BLOCKS, floors=FLOORS, los_options=LOS_OPTIONS, courier_options=EXPEDITIONS)


@app.get("/leads/new", response_class=HTMLResponse)
async def lead_form(request: Request):
    if not can(current_user(request), "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    return render(request, "data_entry/form.html", title="Tambah Lead", **lead_form_context(success=request.query_params.get("success"), error=request.query_params.get("error")))


@app.post("/leads")
async def create_lead(request: Request, store_name: str = Form(...), block: str = Form(...), floor: str = Form(...), los: str = Form(...), nomor: str = Form(...), pic_name: str = Form(...), pic_position: str = Form(...), phone_number: str = Form(...), shipment_type: str = Form(...), current_courier: list[str] = Form(...), top_country: list[str] = Form(...), top_city: list[str] = Form(...), tonnage_potential_kg: float = Form(0), tonnage_period: str = Form("Bulan")):
    user = current_user(request)
    if not can(user, "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    try:
        store.create_lead(build_lead_data(user, store_name=store_name, block=block, floor=floor, los=los, nomor=nomor, pic_name=pic_name, pic_position=pic_position, phone_number=phone_number, shipment_type=shipment_type, current_courier=current_courier, top_country=top_country, top_city=top_city, tonnage_potential_kg=tonnage_potential_kg, tonnage_period=tonnage_period))
    except ValueError as exc:
        return RedirectResponse(f"/leads/new?error={quote(str(exc))}", status_code=303)
    except requests.HTTPError as exc:
        detail = "Supabase menolak data. Pastikan kolom tabel leads sudah sesuai, termasuk nomor."
        if exc.response is not None and exc.response.text:
            print(f"Create lead Supabase error: {exc.response.text}")
        return RedirectResponse(f"/leads/new?error={quote(detail)}", status_code=303)
    except requests.RequestException as exc:
        print(f"Create lead connection error: {exc}")
        return RedirectResponse(f"/leads/new?error={quote('Tidak dapat terhubung ke Supabase. Coba lagi.')}", status_code=303)
    return RedirectResponse("/leads?created=1", status_code=303)


@app.get("/leads/template")
async def download_lead_template(request: Request):
    if not can(current_user(request), "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["store_name", "block", "floor", "los", "nomor", "pic_name", "pic_position", "phone_number", "shipment_type", "current_courier", "top_country", "top_city", "tonnage_potential_kg", "tonnage_period"])
    writer.writerow(["Contoh Toko", "A", "1", "A", "201-203", "Nama PIC", "Owner", "628123456789", "Domestik", "JNE", "Indonesia", "Jakarta", "100", "Bulan"])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=template_leads.csv"})


@app.post("/leads/upload")
async def upload_leads(request: Request, file: UploadFile = File(...)):
    user = current_user(request)
    if not can(user, "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    filename = (file.filename or "").lower()
    raw = await file.read()
    try:
        if filename.endswith(".csv"):
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        elif filename.endswith(".xlsx"):
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = workbook.active
            values = list(sheet.values)
            rows = [dict(zip(values[0], row)) for row in values[1:] if any(row)]
        else:
            raise ValueError("File harus berformat CSV atau XLSX")
        if not rows:
            raise ValueError("File tidak memiliki data")
        prepared_rows = []
        for row_number, row in enumerate(rows, start=2):
            try:
                prepared_rows.append(build_lead_data(
                    user,
                    store_name=normalise_bulk_value(row.get("store_name"), "store_name"),
                    block=normalise_bulk_value(row.get("block"), "block"),
                    floor=normalise_bulk_value(row.get("floor"), "floor"),
                    los=normalise_bulk_value(row.get("los"), "los"),
                    nomor=normalise_bulk_value(row.get("nomor"), "nomor"),
                    pic_name=normalise_bulk_value(row.get("pic_name"), "pic_name"),
                    pic_position=normalise_bulk_value(row.get("pic_position"), "pic_position"),
                    phone_number=normalise_bulk_value(row.get("phone_number"), "phone_number"),
                    shipment_type=normalise_bulk_value(row.get("shipment_type", "Domestik"), "shipment_type"),
                    current_courier=normalise_bulk_value(row.get("current_courier"), "current_courier"),
                    top_country=normalise_bulk_value(row.get("top_country"), "top_country"),
                    top_city=normalise_bulk_value(row.get("top_city"), "top_city"),
                    tonnage_potential_kg=float(row.get("tonnage_potential_kg") or 0),
                    tonnage_period=normalise_bulk_value(row.get("tonnage_period", "Bulan"), "tonnage_period"),
                ))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"baris {row_number}: {exc}") from exc
        for row_number, lead_data in enumerate(prepared_rows, start=2):
            try:
                store.create_lead(lead_data)
            except requests.HTTPError as exc:
                response_text = exc.response.text if exc.response is not None else ""
                print(f"Bulk lead Supabase error pada baris {row_number}: {response_text}")
                if "22001" in response_text:
                    raise ValueError(f"baris {row_number}: kolom database terlalu pendek untuk pilihan multi-value. Jalankan supabase_migration_add_nomor.sql di Supabase SQL Editor") from exc
                raise ValueError(f"baris {row_number}: Supabase menolak data ({response_text[:240]})") from exc
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        return RedirectResponse(f"/leads/new?error={quote(f'Upload gagal: {exc}')}", status_code=303)
    except requests.HTTPError as exc:
        detail = "Supabase menolak data upload. Periksa nama kolom dan tipe datanya."
        if exc.response is not None and exc.response.text:
            print(f"Bulk lead Supabase error: {exc.response.text}")
        return RedirectResponse(f"/leads/new?error={quote(detail)}", status_code=303)
    except requests.RequestException as exc:
        print(f"Bulk lead connection error: {exc}")
        return RedirectResponse(f"/leads/new?error={quote('Tidak dapat terhubung ke Supabase saat upload.')}", status_code=303)
    return RedirectResponse(f"/leads/new?success={quote(f'{len(rows)} leads berhasil diupload')}", status_code=303)


@app.get("/routing", response_class=HTMLResponse)
async def routing_page(request: Request):
    if not can(current_user(request), "router", "admin", "sales"):
        return RedirectResponse("/", status_code=303)
    rows = store.leads()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = f"Blok {row.get('block') or '-'} / Lantai {row.get('floor') or '-'}"
        grouped.setdefault(key, []).append(row)
    selected_id = request.query_params.get("selected_lead_id")
    selected = next((row for row in rows if row.get("id") == selected_id), None)
    if selected:
        selected["monthly_tonnage_kg"] = monthly_tonnage(selected)
        selected["has_international"] = has_international_history(selected)
    recommendations = [row for row in rows if row.get("id") != selected_id]
    for row in recommendations:
        row["monthly_tonnage_kg"] = monthly_tonnage(row)
        row["has_international"] = has_international_history(row)
        row["recommendation_score"] = recommendation_score(row, selected)[0]
    recommendations.sort(key=lambda row: row["recommendation_score"], reverse=True)
    sales_users = [item for item in store.users() if item.get("role") in {"sales", "Sales"}]
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    routes = store.routes()
    routed_this_month = sum(1 for route in routes if str(route.get("scheduled_date", "")).startswith(month))
    routing_target = store.routing_target(month)
    routing_progress = min((routed_this_month / routing_target) * 100, 100) if routing_target else 0
    return render(request, "router/routing.html", title="Routing Visit", groups=grouped, leads=rows, sales_users=sales_users, selected_lead=selected, recommendations=recommendations[:30], routes=routes, target_month=month, routing_target=routing_target, routed_this_month=routed_this_month, routing_progress=routing_progress, monthly_tonnage=monthly_tonnage, has_international_history=has_international_history)


@app.post("/routing/target")
async def save_routing_target(request: Request, month: str = Form(...), target_count: int = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    if target_count < 0:
        return RedirectResponse(f"/routing?month={quote(month)}&error=Target%20tidak%20boleh%20negatif", status_code=303)
    store.save_routing_target(month, target_count, user["id"])
    return RedirectResponse(f"/routing?month={quote(month)}&target_saved=1", status_code=303)


@app.get("/sales/schedule", response_class=HTMLResponse)
async def sales_schedule(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    routes = store.routes()
    if user.get("role") == "sales":
        routes = [route for route in routes if route.get("sales_id") == user.get("id")]
    return render(request, "sales/schedule.html", title="Jadwal Sales", routes=routes)


@app.post("/sales/update-status")
async def update_visit_status(request: Request, route_id: str = Form(...), visit_status: str = Form(...), notes: str = Form("")):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    lead_id = route_id.removeprefix("demo-route-")
    store.update_lead(lead_id, {"visit_status": visit_status, "visit_notes": notes})
    return RedirectResponse("/sales/schedule", status_code=303)


@app.post("/routing/assign")
async def assign_route(request: Request, lead_id: str = Form(...), sales_id: str = Form(...), scheduled_date: str = Form(...), notes: str = Form("")):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    store.assign_route(lead_id, sales_id, user["id"], scheduled_date, notes)
    return RedirectResponse("/routing?success=1", status_code=303)


@app.post("/routing/assign-bulk")
async def assign_routes_bulk(request: Request):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    lead_ids = form.getlist("lead_id")
    if not lead_ids:
        return RedirectResponse("/routing?error=Pilih%20minimal%20satu%20lead", status_code=303)
    notes = str(form.get("notes") or "")
    try:
        for lead_id in lead_ids:
            sales_id = str(form.get(f"sales_id_{lead_id}") or "")
            scheduled_date = str(form.get(f"scheduled_date_{lead_id}") or "")
            if not sales_id or not scheduled_date:
                raise ValueError(f"Sales dan tanggal wajib diisi untuk lead {lead_id}")
            store.assign_route(str(lead_id), sales_id, user["id"], scheduled_date, notes)
    except (ValueError, requests.RequestException) as exc:
        return RedirectResponse(f"/routing?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/routing?success={len(lead_ids)}", status_code=303)


@app.post("/routing/{lead_id}")
async def update_routing(request: Request, lead_id: str, visit_date: str = Form(...), visit_count: int = Form(0), sales_id: str = Form("")):
    if not can(current_user(request), "router", "admin"):
        return RedirectResponse("/", status_code=303)
    store.update_lead(lead_id, {"visit_date": visit_date, "visit_count": visit_count, "sales_id": sales_id, "routing_status": "Sudah diplot"})
    return RedirectResponse("/routing?saved=1", status_code=303)


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    if not can(current_user(request), "admin"):
        return RedirectResponse("/", status_code=303)
    return render(request, "admin/users.html", title="Pengguna", users_list=store.users())


@app.post("/users")
async def save_user(request: Request, name: str = Form(...), username: str = Form(...), role: str = Form(...), password: str = Form(""), user_id: str = Form("")):
    try:
        role = normalise_role(role)
    except ValueError:
        return RedirectResponse("/", status_code=303)
    if not can(current_user(request), "admin"):
        return RedirectResponse("/", status_code=303)
    store.save_user({"name": name, "username": username, "role": role, "password": password}, user_id or None)
    return RedirectResponse("/users?saved=1", status_code=303)


@app.post("/users/toggle-status")
async def toggle_user_status(request: Request, user_id: str = Form(...), active: bool = Form(...)):
    current = current_user(request)
    if not can(current, "admin") or user_id == current.get("id"):
        return RedirectResponse("/users?error=Tidak%20dapat%20mengubah%20status%20akun%20sendiri", status_code=303)
    try:
        store.set_user_active(user_id, not active)
    except requests.RequestException as exc:
        print(f"Toggle user error: {exc}")
        return RedirectResponse("/users?error=Status%20user%20gagal%20diubah", status_code=303)
    return RedirectResponse("/users?saved=1", status_code=303)


@app.post("/users/delete")
async def delete_user(request: Request, user_id: str = Form(...)):
    current = current_user(request)
    if not can(current, "admin") or user_id == current.get("id"):
        return RedirectResponse("/users?error=Tidak%20dapat%20menghapus%20akun%20sendiri", status_code=303)
    try:
        store.delete_user(user_id)
    except requests.RequestException as exc:
        print(f"Delete user error: {exc}")
        return RedirectResponse("/users?error=User%20gagal%20dihapus", status_code=303)
    return RedirectResponse("/users?deleted=1", status_code=303)


@app.post("/sync/google-sheets")
@app.get("/gsheets/sync")
@app.get("/sync/google-sheets")
async def sync_google_sheets(request: Request):
    if not can(current_user(request), "admin", "router"):
        return RedirectResponse("/", status_code=303)
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheet_id or not credentials:
        return RedirectResponse("/routing?sync=missing", status_code=303)
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        service = build("sheets", "v4", credentials=Credentials.from_service_account_info(json.loads(credentials), scopes=["https://www.googleapis.com/auth/spreadsheets"]))
        rows = store.leads()
        header = ["ID", "Timestamp Visit", "Nama Toko", "Blok", "Lantai", "Los", "Nomor", "PIC", "Jabatan", "HP", "PIC Data Entry", "Jenis Kiriman", "Ekspedisi", "Negara", "Kota", "Tonase KG", "Periode", "Status Routing", "Jumlah Visit", "Sales"]
        values = [header] + [[row.get(key, "") for key in ("id", "visit_timestamp", "store_name", "block", "floor", "los", "nomor", "pic_name", "pic_position", "phone_number", "data_entry_pic", "shipment_type", "current_courier", "top_country", "top_city", "tonnage_potential_kg", "tonnage_period", "routing_status", "visit_count", "sales_id")] for row in rows]
        service.spreadsheets().values().update(spreadsheetId=sheet_id, range="Leads!A1", valueInputOption="RAW", body={"values": values}).execute()
        return RedirectResponse("/routing?sync=success", status_code=303)
    except Exception:
        return RedirectResponse("/routing?sync=error", status_code=303)