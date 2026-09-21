import hashlib
import csv
import io
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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
templates = Jinja2Templates(directory="app/templates")

EXPEDITIONS = ["Lion Parcel", "Rayspeed Asia", "TLX", "JNT", "JNE", "Sicepat", "Lainnya"]
BLOCKS = ["A", "B", "C", "D", "E", "F", "G", "PGMTA", "PMTA", "JMTA"]
FLOORS = ["B3", "B2", "B1", "SLG", "LG", "G", "1", "2", "3", "3A", "4", "5", "6", "7", "8", "9", "10", "11", "12", "12A", "R"]
LOS_OPTIONS = list("ABCDEFGHIJ")
PIC_POSITIONS = ["Owner", "Admin Toko", "Lainnya"]
ROLES = ["data_entry", "admin", "router", "sales"]
DEMO_USERS = [
    {"id": "demo-admin", "name": "Admin Tanah Abang", "email": "admin@lionparcel.local", "role": "admin", "password": "admin123"},
    {"id": "demo-entry", "name": "Data Entry", "email": "entry@lionparcel.local", "role": "data_entry", "password": "entry123"},
    {"id": "demo-router", "name": "Router Visit", "email": "router@lionparcel.local", "role": "router", "password": "router123"},
    {"id": "demo-sales", "name": "Sales Tanah Abang", "email": "sales@lionparcel.local", "role": "sales", "password": "sales123"},
]
DEMO_LEADS: list[dict[str, Any]] = []


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            "email": row.get("email") or row.get("username"),
            "role": row.get("role"),
            "active": row.get("active", row.get("is_active", True)),
        }

    def authenticate(self, email: str, password: str) -> dict | None:
        if self.demo:
            user = next((u for u in DEMO_USERS if u["email"].lower() == email.lower() and u["password"] == password), None)
            return {k: v for k, v in user.items() if k != "password"} if user else None
        users = self._request("GET", "users", params={"select": "*"})
        for row in users:
            login_name = row.get("email") or row.get("username") or ""
            if login_name.lower() != email.lower():
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

    def create_lead(self, data: dict) -> dict:
        data.update({"id": secrets.token_urlsafe(10), "created_at": now_iso(), "updated_at": now_iso(), "visit_count": 0, "routing_status": "Belum diplot"})
        if self.demo:
            DEMO_LEADS.insert(0, data)
            return data
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
        self._request("PATCH", "leads", params={"id": f"eq.{quote(lead_id)}"}, body={**data, "updated_at": now_iso()})

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
        payload = ({"username": data["email"], "full_name": data["name"], "role": data["role"], "is_active": True} if legacy_schema else {"name": data["name"], "email": data["email"], "role": data["role"], "active": True})
        if password:
            if bcrypt:
                payload["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        if user_id:
            self._request("PATCH", "users", params={"id": f"eq.{quote(user_id)}"}, body=payload)
        else:
            self._request("POST", "users", body=payload)

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
    return templates.TemplateResponse(template, {"request": request, "user": current_user(request), "demo": store.demo, "expeditions": EXPEDITIONS, **context})


def can(user: dict | None, *roles: str) -> bool:
    return bool(user and user.get("role") in roles)


def lead_form_context(**extra: Any) -> dict[str, Any]:
    return {"couriers": EXPEDITIONS, "blocks": BLOCKS, "floors": FLOORS, "los_options": LOS_OPTIONS, "pic_positions": PIC_POSITIONS, **extra}


def normalise_choice(value: str, choices: list[str], field: str, required: bool = True) -> str | None:
    value = value.strip().upper() if field in {"block", "floor", "los"} else value.strip()
    if not value and not required:
        return None
    if value not in choices:
        raise ValueError(f"{field} tidak valid")
    return value


def build_lead_data(user: dict, *, store_name: str, block: str, floor: str, los: str, nomor: str, pic_name: str, pic_position: str, phone_number: str, shipment_type: str, current_courier: str, top_country: str, top_city: str, tonnage_potential_kg: float, tonnage_period: str) -> dict:
    return {
        "visit_timestamp": now_iso(),
        "store_name": store_name.strip(),
        "block": normalise_choice(block, BLOCKS, "block"),
        "floor": normalise_choice(floor, FLOORS, "floor"),
        "los": normalise_choice(los, LOS_OPTIONS, "los", required=False),
        "nomor": nomor.strip(),
        "pic_name": pic_name.strip(),
        "pic_position": normalise_choice(pic_position, PIC_POSITIONS, "pic_position"),
        "phone_number": phone_number.strip(),
        "data_entry_pic": user.get("name") or user.get("full_name") or user.get("email", ""),
        "shipment_type": shipment_type,
        "current_courier": current_courier,
        "top_country": top_country.strip(),
        "top_city": top_city.strip(),
        "tonnage_potential_kg": tonnage_potential_kg,
        "tonnage_period": tonnage_period,
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
async def login(request: Request, email: str = Form(""), password: str = Form(...), username: str = Form("")):
    email = (email or username).strip()
    user = store.authenticate(email, password)
    if not user:
        return render(request, "login.html", title="Masuk", error="Email atau password tidak valid.")
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/leads", response_class=HTMLResponse)
async def leads_page(request: Request, search: str = ""):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return render(request, "data_entry/list.html", title="Daftar Leads", leads=store.leads(search), search=search)


@app.get("/leads/new", response_class=HTMLResponse)
async def lead_form(request: Request):
    if not can(current_user(request), "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    return render(request, "data_entry/form.html", title="Tambah Lead", **lead_form_context(success=request.query_params.get("success"), error=request.query_params.get("error")))


@app.post("/leads")
async def create_lead(request: Request, store_name: str = Form(...), block: str = Form(...), floor: str = Form(...), los: str = Form(""), nomor: str = Form(""), pic_name: str = Form(...), pic_position: str = Form(...), phone_number: str = Form(...), shipment_type: str = Form(...), current_courier: str = Form(...), top_country: str = Form(""), top_city: str = Form(""), tonnage_potential_kg: float = Form(0), tonnage_period: str = Form("Bulan")):
    user = current_user(request)
    if not can(user, "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    try:
        store.create_lead(build_lead_data(user, store_name=store_name, block=block, floor=floor, los=los, nomor=nomor, pic_name=pic_name, pic_position=pic_position, phone_number=phone_number, shipment_type=shipment_type, current_courier=current_courier, top_country=top_country, top_city=top_city, tonnage_potential_kg=tonnage_potential_kg, tonnage_period=tonnage_period))
    except ValueError as exc:
        return RedirectResponse(f"/leads/new?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/leads?created=1", status_code=303)


@app.get("/leads/template")
async def download_lead_template(request: Request):
    if not can(current_user(request), "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["store_name", "block", "floor", "los", "nomor", "pic_name", "pic_position", "phone_number", "shipment_type", "current_courier", "top_country", "top_city", "tonnage_potential_kg", "tonnage_period"])
    writer.writerow(["Contoh Toko", "A", "1", "A", "201-203", "Nama PIC", "Owner", "08123456789", "Domestik", "JNE", "Indonesia", "Jakarta", "100", "Bulan"])
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
        for row in rows:
            store.create_lead(build_lead_data(user, store_name=str(row.get("store_name", "")), block=str(row.get("block", "")), floor=str(row.get("floor", "")), los=str(row.get("los", "")), nomor=str(row.get("nomor", "")), pic_name=str(row.get("pic_name", "")), pic_position=str(row.get("pic_position", "")), phone_number=str(row.get("phone_number", "")), shipment_type=str(row.get("shipment_type", "Domestik")), current_courier=str(row.get("current_courier", "")), top_country=str(row.get("top_country", "")), top_city=str(row.get("top_city", "")), tonnage_potential_kg=float(row.get("tonnage_potential_kg") or 0), tonnage_period=str(row.get("tonnage_period", "Bulan"))))
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        return RedirectResponse(f"/leads/new?error={quote(f'Upload gagal: {exc}')}", status_code=303)
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
    recommendations = [row for row in rows if selected and row.get("id") != selected_id and row.get("block") == selected.get("block") and row.get("floor") == selected.get("floor")]
    return render(request, "router/routing.html", title="Routing Visit", groups=grouped, leads=rows, sales_users=[item for item in store.users() if item.get("role") == "sales"], selected_lead=selected, recommendations=recommendations, routes=store.routes())


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
async def save_user(request: Request, name: str = Form(...), email: str = Form(...), role: str = Form(...), password: str = Form(""), user_id: str = Form("")):
    if not can(current_user(request), "admin") or role not in ROLES:
        return RedirectResponse("/", status_code=303)
    store.save_user({"name": name, "email": email, "role": role, "password": password}, user_id or None)
    return RedirectResponse("/users?saved=1", status_code=303)


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