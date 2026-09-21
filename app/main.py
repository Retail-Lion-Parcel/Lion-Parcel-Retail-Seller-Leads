import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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

    def create_lead(self, data: dict) -> dict:
        data.update({"id": secrets.token_urlsafe(10), "created_at": now_iso(), "updated_at": now_iso(), "visit_count": 0, "routing_status": "Belum diplot"})
        if self.demo:
            DEMO_LEADS.insert(0, data)
            return data
        return self._request("POST", "leads", body=data)[0]

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
            "weight": sum(float(row.get("tonnage") or 0) for row in rows),
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
    return render(request, "leads.html", title="Leads", leads=store.leads(search), search=search)


@app.get("/leads/new", response_class=HTMLResponse)
async def lead_form(request: Request):
    if not can(current_user(request), "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    return render(request, "lead_form.html", title="Tambah Lead", form={})


@app.post("/leads")
async def create_lead(request: Request, timestamp_visit: str = Form(...), store_name: str = Form(...), block: str = Form(...), floor: str = Form(...), los: str = Form(""), pic_name: str = Form(...), pic_title: str = Form(""), phone: str = Form(...), domestic: str = Form("Tidak"), international: str = Form("Tidak"), expedition: str = Form(...), top_country: str = Form(""), top_city: str = Form(""), tonnage: float = Form(0), tonnage_period: str = Form("bulan")):
    user = current_user(request)
    if not can(user, "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    store.create_lead({"timestamp_visit": timestamp_visit, "store_name": store_name, "block": block, "floor": floor, "los": los, "pic_name": pic_name, "pic_title": pic_title, "phone": phone, "domestic": domestic, "international": international, "expedition": expedition, "top_country": top_country, "top_city": top_city, "tonnage": tonnage, "tonnage_period": tonnage_period, "entry_user_id": user["id"]})
    return RedirectResponse("/leads?created=1", status_code=303)


@app.get("/routing", response_class=HTMLResponse)
async def routing_page(request: Request):
    if not can(current_user(request), "router", "admin", "sales"):
        return RedirectResponse("/", status_code=303)
    rows = store.leads()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = f"Blok {row.get('block') or '-'} / Lantai {row.get('floor') or '-'}"
        grouped.setdefault(key, []).append(row)
    return render(request, "routing.html", title="Routing Visit", groups=grouped, leads=rows, sales=[item for item in store.users() if item.get("role") == "sales"])


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
    return render(request, "users.html", title="Pengguna", users=store.users())


@app.post("/users")
async def save_user(request: Request, name: str = Form(...), email: str = Form(...), role: str = Form(...), password: str = Form(""), user_id: str = Form("")):
    if not can(current_user(request), "admin") or role not in ROLES:
        return RedirectResponse("/", status_code=303)
    store.save_user({"name": name, "email": email, "role": role, "password": password}, user_id or None)
    return RedirectResponse("/users?saved=1", status_code=303)


@app.post("/sync/google-sheets")
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
        header = ["ID", "Tanggal Visit", "Nama Toko", "Blok", "Lantai", "Los", "PIC", "Jabatan", "HP", "Domestik", "International", "Ekspedisi", "Negara", "Kota", "Tonase", "Periode", "Status Routing", "Jumlah Visit", "Sales"]
        values = [header] + [[row.get(key, "") for key in ("id", "visit_date", "store_name", "block", "floor", "los", "pic_name", "pic_title", "phone", "domestic", "international", "expedition", "top_country", "top_city", "tonnage", "tonnage_period", "routing_status", "visit_count", "sales_id")] for row in rows]
        service.spreadsheets().values().update(spreadsheetId=sheet_id, range="Leads!A1", valueInputOption="RAW", body={"values": values}).execute()
        return RedirectResponse("/routing?sync=success", status_code=303)
    except Exception:
        return RedirectResponse("/routing?sync=error", status_code=303)