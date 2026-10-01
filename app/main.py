import calendar
import hashlib
import csv
import io
import json
import os
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
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
app = FastAPI(title="Lion Parcel Retail Leads & Routing System")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY", "dev-only-change-me"))
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

EXPEDITIONS = ["Lion Parcel", "Rayspeed Asia", "TLX", "J&T Express", "J&T Cargo", "JNE", "Sicepat", "POS Indonesia", "TIKI", "Ninja Xpress", "AnterAja", "ID Express", "Lainnya", "Tidak Ada"]
BLOCKS = ["A", "B", "C", "D", "E", "F", "G", "PGMTA", "PMTA", "JMTA"]
FLOORS = ["B3", "B2", "B1", "SLG", "LG", "G", "1", "2", "3", "3A", "4", "5", "6", "7", "8", "9", "10", "11", "12", "12A", "R"]
LOS_OPTIONS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "FNO"]
PIC_POSITIONS = ["Owner", "Karyawan Toko", "Lainnya"]
SHIPMENT_TYPES = ["Domestik", "International", "Keduanya"]
TONNAGE_PERIODS = ["Hari", "Bulan", "Tahun"]
TOP_COUNTRIES = ["Malaysia", "Singapore", "Thailand", "Vietnam", "Philippines", "Brunei", "China", "Hong Kong", "Taiwan", "South Korea", "Japan", "Australia", "United States", "United Kingdom", "Lainnya"]
TOP_CITIES = ["Jakarta", "Bandung", "Surabaya", "Medan", "Semarang", "Yogyakarta", "Makassar", "Denpasar", "Palembang", "Banjarmasin", "Pontianak", "Balikpapan", "Padang", "Pekanbaru", "Bandar Lampung", "Malang", "Solo", "Bogor", "Depok", "Tangerang", "Bekasi", "Serang", "Cirebon", "Tasikmalaya", "Purwakarta", "Sukabumi", "Mataram", "Kupang", "Manado", "Palu", "Kendari", "Ambon", "Jayapura", "Samarinda", "Banda Aceh", "Lainnya"]
SHIPMENT_PRIORITY = {
    "International": 4,
    "Keduanya": 3,
    "Domestik": 2,
    "Tidak Sama Sekali": 1,
}
RECOMMENDATION_DISPLAY_LIMIT = 100   # baris yang ditampilkan di tabel rekomendasi
TOP_N_MAX = 50000  
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
DEMO_SALES_QUOTAS: list[dict[str, Any]] = []
DEMO_ROUTING_PERIODS: dict[str, dict[str, Any]] = {}
DEMO_ROUTING_BASKET: list[dict[str, Any]] = []
DEMO_SALES_TERRITORIES: list[dict[str, Any]] = []

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


def resolve_active_flag(row: dict) -> bool:
    """Baca status aktif user dari kolom 'active' atau 'is_active'.
    Kolom yang bernilai NULL di database (bukan true/false eksplisit) dianggap
    AKTIF secara default, bukan nonaktif — supaya user lama yang belum pernah
    disentuh kolom statusnya tidak salah tampil sebagai 'Nonaktif'."""
    raw_value = row.get("active", row.get("is_active"))
    return True if raw_value is None else bool(raw_value)


class Store:
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "") or os.getenv("SUPABASE_KEY", "")
        self.demo = not (self.url and self.key)

    def _request(self, method: str, table: str, *, params: dict | None = None, body: Any = None) -> list[dict]:
        response = requests.request(method, f"{self.url}/rest/v1/{table}", headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "Prefer": "return=representation"}, params=params, json=body, timeout=15)
        response.raise_for_status()
        return response.json() if response.content else []

    def _get_all(self, table: str, params: dict, page_size: int = 1000) -> list[dict]:
        """GET semua baris dengan paginasi. Supabase/PostgREST membatasi satu
        request maksimal 1000 baris (default), jadi tanpa ini data di atas
        1000 baris terpotong diam-diam."""
        rows: list[dict] = []
        offset = 0
        while True:
            chunk = self._request("GET", table, params={**params, "limit": str(page_size), "offset": str(offset)})
            rows.extend(chunk)
            if len(chunk) < page_size:
                return rows
            offset += page_size
 
    def add_many_to_basket(self, period_id: str, lead_ids: list[str], user_id: str) -> int:
        """Tambah banyak lead ke basket sekaligus (bulk insert per 500 baris,
        bukan 2 request per lead). Lead yang sudah ada di basket dilewati.
        Mengembalikan jumlah lead yang benar-benar baru ditambahkan."""
        if self.demo:
            added = 0
            for lead_id in lead_ids:
                if not any(i["period_id"] == period_id and i["lead_id"] == lead_id for i in DEMO_ROUTING_BASKET):
                    DEMO_ROUTING_BASKET.append({"id": secrets.token_urlsafe(8), "period_id": period_id, "lead_id": lead_id, "added_by": user_id, "created_at": now_iso()})
                    added += 1
            return added
        existing_rows = self._get_all("routing_basket_items", {"select": "lead_id", "period_id": f"eq.{quote(period_id)}", "order": "id"})
        existing = {row["lead_id"] for row in existing_rows}
        new_ids = [lid for lid in dict.fromkeys(lead_ids) if lid not in existing]
        for start in range(0, len(new_ids), 500):
            chunk = new_ids[start:start + 500]
            self._request("POST", "routing_basket_items", body=[{"period_id": period_id, "lead_id": lid, "added_by": user_id} for lid in chunk])
        return len(new_ids)
 

    def users(self) -> list[dict]:
        if self.demo:
            return [{**{k: v for k, v in user.items() if k != "password"}, "active": resolve_active_flag(user)} for user in DEMO_USERS]
        rows = self._request("GET", "users", params={"select": "*", "order": "created_at"})
        return [self._normalise_user(row) for row in rows]

    @staticmethod
    def _normalise_user(row: dict) -> dict:
        return {
            "id": row.get("id"),
            "name": row.get("name") or row.get("full_name") or row.get("username"),
            "username": row.get("username") or row.get("email"),
            "role": row.get("role"),
            "active": resolve_active_flag(row),
        }

    def authenticate(self, username: str, password: str) -> tuple[dict | None, str | None]:
        """Mengembalikan (user, error_reason).
        error_reason bernilai None jika login berhasil, atau salah satu dari:
        - "invalid"  : username/password salah
        - "inactive" : kredensial benar tapi akun sudah dinonaktifkan
        """
        if self.demo:
            user = next((u for u in DEMO_USERS if u["username"].lower() == username.lower() and u["password"] == password), None)
            if not user:
                return None, "invalid"
            if not user.get("active", True):
                return None, "inactive"
            return {k: v for k, v in user.items() if k != "password"}, None
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
            if not valid:
                return None, "invalid"
            if not resolve_active_flag(row):
                return None, "inactive"
            return self._normalise_user(row), None
        return None, "invalid"

    def leads(self, search: str = "") -> list[dict]:
        rows = DEMO_LEADS if self.demo else self._get_all("leads", {"select": "*", "order": "created_at.desc,id.asc"})
        if search:
            needle = search.lower()
            rows = [row for row in rows if needle in " ".join(str(row.get(k, "")) for k in ("store_name", "block", "pic_name", "sales_id")).lower()]
        return rows

    def get_lead(self, lead_id: str) -> dict | None:
        if self.demo:
            return next((item for item in DEMO_LEADS if item["id"] == lead_id), None)
        rows = self._request("GET", "leads", params={"select": "*", "id": f"eq.{quote(lead_id)}", "limit": "1"})
        return rows[0] if rows else None

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

    def sales_quotas(self) -> list[dict]:
        """Kuota kunjungan harian per sales (sales_visit_quotas).

        Baris tabel mungkin belum ada jika migrasi skema belum dijalankan,
        sehingga error dibalikkan menjadi list kosong agar halaman tetap tampil.
        """
        if self.demo:
            return [dict(quota) for quota in DEMO_SALES_QUOTAS]
        try:
            return self._request("GET", "sales_visit_quotas", params={"select": "*", "order": "quota_date"})
        except requests.HTTPError as exc:
            print(f"Load sales_visit_quotas error (migrasi skema belum dijalankan?): {exc}")
            return []

    def save_sales_quota(self, sales_id: str, quota_date: str, max_visits: int) -> None:
        """Simpan/ubah kuota visit harian satu sales pada satu tanggal."""
        if self.demo:
            for quota in DEMO_SALES_QUOTAS:
                if quota["sales_id"] == sales_id and quota["quota_date"] == quota_date:
                    quota["max_visits"] = max_visits
                    return
            DEMO_SALES_QUOTAS.append({"id": secrets.token_urlsafe(10), "sales_id": sales_id, "quota_date": quota_date, "max_visits": max_visits, "created_at": now_iso()})
            return
        existing = self._request("GET", "sales_visit_quotas", params={"select": "id", "sales_id": f"eq.{quote(sales_id)}", "quota_date": f"eq.{quota_date}", "limit": "1"})
        if existing:
            self._request("PATCH", "sales_visit_quotas", params={"id": f"eq.{quote(str(existing[0]['id']))}"}, body={"max_visits": max_visits})
        else:
            self._request("POST", "sales_visit_quotas", body={"sales_id": sales_id, "quota_date": quota_date, "max_visits": max_visits})

    def delete_sales_quota(self, quota_id: str) -> None:
        if self.demo:
            DEMO_SALES_QUOTAS[:] = [quota for quota in DEMO_SALES_QUOTAS if quota["id"] != quota_id]
            return
        self._request("DELETE", "sales_visit_quotas", params={"id": f"eq.{quote(quota_id)}"})

    def routing_period(self, month: str) -> dict | None:
        """Ambil konfigurasi periode routing untuk satu bulan (atau None jika belum diatur)."""
        if self.demo:
            return DEMO_ROUTING_PERIODS.get(month)
        rows = self._request("GET", "routing_periods", params={"select": "*", "month": f"eq.{quote(month)}", "limit": "1"})
        return rows[0] if rows else None
 
    def save_routing_period(self, month: str, data: dict, user_id: str) -> dict:
        """Buat atau update konfigurasi periode routing (upsert berdasarkan month)."""
        if self.demo:
            existing = DEMO_ROUTING_PERIODS.get(month, {})
            existing.update(data)
            existing.setdefault("id", f"demo-period-{month}")
            existing.setdefault("month", month)
            existing.setdefault("status", "Draft")
            existing["created_by"] = existing.get("created_by") or user_id
            DEMO_ROUTING_PERIODS[month] = existing
            return existing
        existing = self._request("GET", "routing_periods", params={"select": "id", "month": f"eq.{quote(month)}", "limit": "1"})
        if existing:
            return self._request("PATCH", "routing_periods", params={"id": f"eq.{quote(str(existing[0]['id']))}"}, body=data)[0]
        payload = {"month": month, "created_by": user_id, **data}
        return self._request("POST", "routing_periods", body=payload)[0]
 
    def active_sales_count(self) -> int:
        """Jumlah sales berstatus aktif — dasar perhitungan kapasitas kunjungan."""
        return sum(1 for u in self.users() if u.get("role") == "sales" and u.get("active", True))
 
    def routing_basket(self, period_id: str) -> list[dict]:
        """Daftar leads yang sudah dimasukkan ke basket routing pada satu periode."""
        if self.demo:
            items = [dict(item) for item in DEMO_ROUTING_BASKET if item["period_id"] == period_id]
            for item in items:
                item["leads"] = self.get_lead(item["lead_id"]) or {}
            return items
        return self._request("GET", "routing_basket_items", params={"select": "*,leads(*)", "period_id": f"eq.{quote(period_id)}", "order": "created_at"})
 
    def add_to_basket(self, period_id: str, lead_id: str, user_id: str) -> None:
        if self.demo:
            if not any(i["period_id"] == period_id and i["lead_id"] == lead_id for i in DEMO_ROUTING_BASKET):
                DEMO_ROUTING_BASKET.append({"id": secrets.token_urlsafe(8), "period_id": period_id, "lead_id": lead_id, "added_by": user_id, "created_at": now_iso()})
            return
        existing = self._request("GET", "routing_basket_items", params={"select": "id", "period_id": f"eq.{quote(period_id)}", "lead_id": f"eq.{quote(lead_id)}", "limit": "1"})
        if not existing:
            self._request("POST", "routing_basket_items", body={"period_id": period_id, "lead_id": lead_id, "added_by": user_id})
 
    def remove_from_basket(self, item_id: str) -> None:
        if self.demo:
            DEMO_ROUTING_BASKET[:] = [i for i in DEMO_ROUTING_BASKET if i["id"] != item_id]
            return
        self._request("DELETE", "routing_basket_items", params={"id": f"eq.{quote(item_id)}"})

    def sales_territories(self, period_id: str) -> list[dict]:
        if self.demo:
            return [t for t in DEMO_SALES_TERRITORIES if t["period_id"] == period_id]
        return self._request("GET", "sales_territories", params={"select": "*", "period_id": f"eq.{quote(period_id)}", "order": "block"})
 
    def save_sales_territory(self, period_id: str, sales_id: str, block: str, floor: str | None, user_id: str) -> None:
        if self.demo:
            DEMO_SALES_TERRITORIES.append({"id": secrets.token_urlsafe(8), "period_id": period_id, "sales_id": sales_id, "block": block, "floor": floor, "created_at": now_iso()})
            return
        self._request("POST", "sales_territories", body={"period_id": period_id, "sales_id": sales_id, "block": block, "floor": floor, "created_by": user_id})
 
    def delete_sales_territory(self, territory_id: str) -> None:
        if self.demo:
            DEMO_SALES_TERRITORIES[:] = [t for t in DEMO_SALES_TERRITORIES if t["id"] != territory_id]
            return
        self._request("DELETE", "sales_territories", params={"id": f"eq.{quote(territory_id)}"})
 
    def sales_assignment_history(self, sales_id: str) -> list[dict]:
        """Riwayat jumlah leads yang pernah diberikan ke satu sales, per bulan
        (dari routing_basket_items yang sudah pernah di-assign ke sales ini)."""
        if self.demo:
            rows = [i for i in DEMO_ROUTING_BASKET if i.get("sales_id") == sales_id]
            counts: dict[str, int] = {}
            for row in rows:
                month = (DEMO_ROUTING_PERIODS.get(row.get("period_id")) or {}).get("month")
                if month:
                    counts[month] = counts.get(month, 0) + 1
        else:
            rows = self._request("GET", "routing_basket_items", params={"select": "period_id,routing_periods(month)", "sales_id": f"eq.{quote(sales_id)}"})
            counts = {}
            for row in rows:
                month = (row.get("routing_periods") or {}).get("month")
                if month:
                    counts[month] = counts.get(month, 0) + 1
        return sorted([{"month": m, "count": c} for m, c in counts.items()], key=lambda x: x["month"], reverse=True)[:6]
 
    def update_basket_item(self, item_id: str, data: dict) -> None:
        if self.demo:
            item = next((i for i in DEMO_ROUTING_BASKET if i["id"] == item_id), None)
            if item:
                item.update(data)
            return
        self._request("PATCH", "routing_basket_items", params={"id": f"eq.{quote(item_id)}"}, body=data)

    def clear_scheduled_routes(self, lead_id: str, month: str) -> None:
        """Hapus visit_routes berstatus Scheduled untuk satu lead di bulan
        tsb sebelum generate ulang jadwal, supaya tidak menumpuk baris
        duplikat. Baris berstatus Visited/Rescheduled/Canceled TIDAK
        disentuh — histori kunjungan yang sudah terjadi tetap aman."""
        if self.demo:
            return
        existing = self._request("GET", "visit_routes", params={"select": "id,scheduled_date,visit_status", "lead_id": f"eq.{quote(lead_id)}"})
        for row in existing:
            sdate = str(row.get("scheduled_date") or "")
            status = row.get("visit_status") or "Scheduled"
            if sdate[:7] == month and status == "Scheduled":
                self._request("DELETE", "visit_routes", params={"id": f"eq.{quote(str(row['id']))}"})
 
    def publish_period(self, period_id: str, user_id: str) -> None:
        if self.demo:
            for p in DEMO_ROUTING_PERIODS.values():
                if p.get("id") == period_id:
                    p.update({"status": "Published", "published_by": user_id, "published_at": now_iso()})
            return
        self._request("PATCH", "routing_periods", params={"id": f"eq.{quote(period_id)}"}, body={"status": "Published", "published_by": user_id, "published_at": now_iso()})

    def get_route(self, route_id: str) -> dict | None:
        """Ambil satu baris visit_routes berdasarkan id (dibutuhkan untuk
        validasi kepemilikan sebelum update status / reschedule)."""
        if self.demo:
            lead_id = route_id.removeprefix("demo-route-")
            lead = self.get_lead(lead_id)
            if not lead:
                return None
            return {"id": route_id, "lead_id": lead_id, "sales_id": lead.get("sales_id"), "scheduled_date": lead.get("visit_date"), "visit_status": lead.get("visit_status", "Scheduled")}
        rows = self._request("GET", "visit_routes", params={"select": "*", "id": f"eq.{quote(route_id)}", "limit": "1"})
        return rows[0] if rows else None
 
    def update_route_status(self, route_id: str, status: str, notes: str = "") -> None:
        """Update status kunjungan (Visited/Canceled/Scheduled) pada baris
        visit_routes yang benar — BUKAN pada tabel leads."""
        if self.demo:
            lead_id = route_id.removeprefix("demo-route-")
            self.update_lead(lead_id, {"visit_status": status, "visit_notes": notes})
            return
        self._request("PATCH", "visit_routes", params={"id": f"eq.{quote(route_id)}"}, body={"visit_status": status, "notes": notes})
 
    def reschedule_route(self, route_id: str, new_date: str, reason: str, user_id: str) -> None:
        """Ubah tanggal kunjungan (TIDAK mengubah sales_id/ownership) dan catat
        histori perubahan di visit_reschedule_log."""
        route = self.get_route(route_id)
        if not route:
            return
        old_date = route.get("scheduled_date")
        if self.demo:
            lead_id = route_id.removeprefix("demo-route-")
            self.update_lead(lead_id, {"visit_date": new_date, "visit_status": "Scheduled"})
            return
        self._request("PATCH", "visit_routes", params={"id": f"eq.{quote(route_id)}"}, body={"scheduled_date": new_date, "visit_status": "Scheduled"})
        self._request("POST", "visit_reschedule_log", body={
            "route_id": route_id, "lead_id": route.get("lead_id"), "old_date": old_date,
            "new_date": new_date, "reason": reason, "changed_by": user_id,
        })
 
    def reschedule_history(self, route_id: str) -> list[dict]:
        """Histori perubahan jadwal untuk satu route (opsional, untuk audit)."""
        if self.demo:
            return []
        return self._request("GET", "visit_reschedule_log", params={"select": "*", "route_id": f"eq.{quote(route_id)}", "order": "created_at.desc"})

    def reschedule_logs(self) -> list[dict]:
        """Seluruh histori reschedule (untuk dihitung per bulan di dashboard
        monitoring — filter bulan dilakukan di sisi Python)."""
        if self.demo:
            return []
        return self._request("GET", "visit_reschedule_log", params={"select": "*"})

    def set_lead_visit_target(self, lead_id: str, visit_target: int) -> None:
        """Simpan target frekuensi kunjungan lead (jumlah kunjungan per bulan)."""
        self.update_lead(lead_id, {"visit_target_per_month": visit_target})

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

    def delete_lead(self, lead_id: str) -> None:
        if self.demo:
            DEMO_LEADS[:] = [item for item in DEMO_LEADS if item["id"] != lead_id]
            return
        self._request("DELETE", "leads", params={"id": f"eq.{quote(lead_id)}"})

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
            "weight_month": sum(monthly_tonnage(row) for row in rows),
            "status_stats": {"Scheduled": routed, "Visited": sum(int(row.get("visit_count") or 0) > 0 for row in rows), "Rescheduled": 0, "Canceled": 0},
            "courier_stats": courier_stats,
        }


store = Store()

def period_summary(month: str) -> dict:
    """Ringkasan status periode untuk ditampilkan sebagai referensi bulan
    sebelumnya/berikutnya (langkah 2 di flow)."""
    period = store.routing_period(month)
    routes = store.routes()
    routed_count = sum(1 for r in routes if str(r.get("scheduled_date", "")).startswith(month))
    return {
        "month": month,
        "status": (period or {}).get("status", "Belum Diatur"),
        "routed_count": routed_count,
    }

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
    return {
        "couriers": EXPEDITIONS,
        "blocks": BLOCKS,
        "floors": FLOORS,
        "los_options": LOS_OPTIONS,
        "pic_positions": PIC_POSITIONS,
        "shipment_types": SHIPMENT_TYPES,
        "tonnage_periods": TONNAGE_PERIODS,
        "top_countries": TOP_COUNTRIES,
        "top_cities": TOP_CITIES,
        **extra,
    }


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


# ---------- Target Frekuensi Kunjungan & Kuota Harian Sales ----------

def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_date_safe(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def frequency_label(target: Any) -> str:
    """Label frekuensi kunjungan dari target per bulan.

    Contoh: 4 -> "4x/bulan (tiap 7 hari)" alias tiap minggu sekali.
    """
    count = _to_int(target, 0)
    if count <= 0:
        return "Belum ditentukan"
    if count == 1:
        return "1x/bulan"
    interval = max(1, round(28 / count))
    return f"{count}x/bulan (tiap {interval} hari)"

def work_days_in_month(year: int, month: int, work_days_per_week: int) -> int:
    """Hitung jumlah hari kerja dalam sebulan dari aturan hari kerja/minggu.
 
    Asumsi hari libur diambil dari akhir pekan:
    - 6 hari kerja/minggu -> Minggu libur
    - 5 hari kerja/minggu -> Sabtu & Minggu libur
    - 7 hari kerja/minggu -> semua hari kerja
    - Nilai lain (custom) -> dihitung proporsional dari total hari.
    """
    total_days = calendar.monthrange(year, month)[1]
    if work_days_per_week >= 7:
        return total_days
    if work_days_per_week == 6:
        off_days = {6}  # Minggu (Python: Monday=0 ... Sunday=6)
    elif work_days_per_week == 5:
        off_days = {5, 6}  # Sabtu & Minggu
    else:
        return max(1, round(total_days * work_days_per_week / 7))
    return sum(1 for day in range(1, total_days + 1) if date(year, month, day).weekday() not in off_days)
 
 
def compute_period_capacity(active_sales: int, work_days: int, min_per_day: int, max_per_day: int) -> dict[str, int]:
    """Kapasitas kunjungan sales sebulan = jumlah sales aktif x hari kerja x target/hari."""
    return {
        "active_sales": active_sales,
        "work_days": work_days,
        "min_capacity": active_sales * work_days * min_per_day,
        "max_capacity": active_sales * work_days * max_per_day,
    }
 
 
def adjacent_months(month: str) -> tuple[str, str]:
    """Kembalikan (bulan_sebelumnya, bulan_berikutnya) dari string 'YYYY-MM'."""
    year, mon = int(month[:4]), int(month[5:7])
    prev_year, prev_mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
    next_year, next_mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return f"{prev_year:04d}-{prev_mon:02d}", f"{next_year:04d}-{next_mon:02d}"
 
 
def lead_priority_score(lead: dict) -> tuple:
    """Skor prioritas rekomendasi routing (lebih besar = lebih prioritas).
 
    Urutan: Shipment Type (International > Keduanya > Domestik > Tidak Sama
    Sekali) -> estimasi tonase/bulan -> status duplikat (Unik lebih baik
    dari Duplikat) -> riwayat (lead yang sudah pernah punya sales dianggap
    punya riwayat penanganan).
    """
    shipment = lead.get("shipment_type") or "Tidak Sama Sekali"
    shipment_rank = SHIPMENT_PRIORITY.get(shipment, 1)
    tonnage = monthly_tonnage(lead)
    dup_rank = {"Unik": 0, "Perlu Dicek": -1, "Kemungkinan Duplikat": -2, "Duplikat": -3}.get(lead.get("dup_status"), 0)
    has_history = 1 if lead.get("sales_id") else 0
    return (shipment_rank, tonnage, dup_rank, has_history)

def filter_recommendation_leads(leads: list[dict], owner_filter: str, search: str) -> list[dict]:
    """Filter daftar rekomendasi. 'owned' = lead yang sudah punya sales
    (kolom leads.sales_id terisi), 'unowned' = belum punya sales.
    Dipakai bersama oleh halaman rekomendasi dan aksi Top N supaya
    keduanya selalu konsisten."""
    result = leads
    if owner_filter == "owned":
        result = [lead for lead in result if lead.get("sales_id")]
    elif owner_filter == "unowned":
        result = [lead for lead in result if not lead.get("sales_id")]
    if search:
        needle = search.lower()
        result = [
            lead for lead in result
            if needle in " ".join(str(lead.get(k) or "") for k in ("store_name", "pic_name", "block", "floor", "los")).lower()
        ]
    return result

def preferred_owner_map(routes: list[dict]) -> dict[str, str]:
    """Map lead_id -> sales_id berdasarkan kunjungan TERAKHIR (tanggal
    scheduled_date paling baru) lead tersebut. Dipakai sebagai 'Preferred
    Owner' agar lead yang sudah pernah ditangani sales tertentu diprioritaskan
    kembali ke sales yang sama."""
    latest: dict[str, tuple[str, str]] = {}
    for route in routes:
        lead_id, sales_id = route.get("lead_id"), route.get("sales_id")
        sdate = str(route.get("scheduled_date") or "")
        if not lead_id or not sales_id:
            continue
        if lead_id not in latest or sdate > latest[lead_id][0]:
            latest[lead_id] = (sdate, sales_id)
    return {lead_id: sid for lead_id, (_, sid) in latest.items()}
 
 
def auto_assign_leads(
    basket_items: list[dict], sales_users: list[dict], territories: list[dict],
    owner_map: dict[str, str], capacity_per_sales: int, default_frequency: int,
) -> dict[str, Any]:
    """Bagi leads di basket ke sales secara otomatis.
 
    Prioritas per lead:
    1. Preferred Owner (sales yang sebelumnya menangani lead ini), jika masih
       aktif dan kapasitasnya cukup.
    2. Territory Match: sales dengan area (blok+lantai, atau blok saja) yang
       cocok dan sisa kapasitas terbanyak.
    3. Kapasitas Tersedia: sales manapun dengan sisa kapasitas terbanyak
       (least loaded), untuk pemerataan beban.
 
    Kapasitas dihitung dalam satuan KUNJUNGAN (visit_target_per_month lead),
    bukan jumlah lead, supaya lead dengan frekuensi tinggi tidak membebani
    sales secara tidak proporsional. Lead yang tidak bisa dicarikan slot
    dikembalikan di 'unassigned' untuk penyesuaian manual oleh Router/Admin.
    """
    active_ids = {s["id"] for s in sales_users if s.get("active", True)}
    load: dict[str, int] = {sid: 0 for sid in active_ids}
 
    territory_map: dict[str, set] = {}
    for t in territories:
        territory_map.setdefault(t["sales_id"], set()).add((t.get("block"), t.get("floor") or None))
 
    def territory_score(sid: str, block: str, floor: str) -> int:
        covers = territory_map.get(sid, set())
        if (block, floor) in covers:
            return 2
        if (block, None) in covers:
            return 1
        return 0
 
    def sort_key(item):
        lead = item.get("leads") or {}
        has_pref = 1 if owner_map.get(lead.get("id")) in active_ids else 0
        need = _to_int(lead.get("visit_target_per_month"), default_frequency)
        return (-has_pref, -need)
 
    assignments: dict[str, dict] = {}
    unassigned: list[dict] = []
 
    for item in sorted(basket_items, key=sort_key):
        lead = item.get("leads") or {}
        lead_id, block, floor = lead.get("id"), lead.get("block"), lead.get("floor")
        need = max(_to_int(lead.get("visit_target_per_month"), default_frequency), 1)
 
        candidate_sid, reason = None, ""
        preferred = owner_map.get(lead_id)
        if preferred and preferred in active_ids and load.get(preferred, 0) + need <= capacity_per_sales:
            candidate_sid, reason = preferred, "Preferred Owner"
 
        if not candidate_sid:
            for sid in sorted(active_ids, key=lambda s: (-territory_score(s, block, floor), load.get(s, 0))):
                if territory_score(sid, block, floor) > 0 and load.get(sid, 0) + need <= capacity_per_sales:
                    candidate_sid, reason = sid, "Territory Match"
                    break
 
        if not candidate_sid:
            for sid in sorted(active_ids, key=lambda s: load.get(s, 0)):
                if load.get(sid, 0) + need <= capacity_per_sales:
                    candidate_sid, reason = sid, "Kapasitas Tersedia"
                    break
 
        if candidate_sid:
            load[candidate_sid] = load.get(candidate_sid, 0) + need
            assignments[item["id"]] = {"sales_id": candidate_sid, "reason": reason, "is_preferred_owner": reason == "Preferred Owner"}
        else:
            unassigned.append(item)
 
    return {"assignments": assignments, "unassigned": unassigned, "load": load}

def work_dates_in_month(year: int, month: int, work_days_per_week: int) -> list[date]:
    """Daftar tanggal kerja (bukan hanya jumlahnya) dalam sebulan, dipakai
    sebagai slot kandidat penjadwalan otomatis. Aturan libur sama seperti
    `work_days_in_month` (6 hari -> Minggu libur, 5 hari -> Sabtu+Minggu libur)."""
    total_days = calendar.monthrange(year, month)[1]
    if work_days_per_week >= 7:
        off_days: set[int] = set()
    elif work_days_per_week == 6:
        off_days = {6}
    elif work_days_per_week == 5:
        off_days = {5, 6}
    else:
        off_days = set()
    return [date(year, month, day) for day in range(1, total_days + 1) if date(year, month, day).weekday() not in off_days]
 
 
def generate_auto_schedule(
    basket_items: list[dict], month: str, work_dates: list[date], max_per_day: int,
    allow_multi_week: bool, max_per_week: int, default_frequency: int, existing_routes: list[dict],
) -> dict[str, Any]:
    """Buat usulan jadwal kunjungan otomatis untuk seluruh leads di basket yang
    sudah punya sales (hasil Fase 2), disebar merata sepanjang hari kerja bulan
    tsb, dengan menghormati:
    - kapasitas maksimum kunjungan/hari per sales (dari config periode)
    - aturan frekuensi per minggu (allow_multiple_visits_per_week & max_visits_per_week)
    - kunjungan lain yang sudah terjadwal di bulan yang sama (existing_routes)
 
    Mengembalikan {"schedule": {basket_item_id: [tanggal_iso, ...]}, "unscheduled": [...]}.
    Lead dengan slot tidak mencukupi (kapasitas penuh) masuk ke 'unscheduled'
    untuk ditinjau manual oleh Router/Admin.
    """
    load_by_key: dict[tuple, int] = {}
    for route in existing_routes:
        sdate = str(route.get("scheduled_date") or "")[:10]
        if sdate[:7] != month:
            continue
        key = (route.get("sales_id"), sdate)
        load_by_key[key] = load_by_key.get(key, 0) + 1
 
    def week_index(d: date) -> int:
        return d.isocalendar()[1]
 
    schedule: dict[str, list[str]] = {}
    unscheduled: list[dict] = []
 
    ordered = sorted(basket_items, key=lambda i: -max(_to_int((i.get("leads") or {}).get("visit_target_per_month"), default_frequency), 1))
 
    for item in ordered:
        lead = item.get("leads") or {}
        sales_id = item.get("sales_id")
        if not sales_id or not work_dates:
            unscheduled.append(item)
            continue
        target = max(_to_int(lead.get("visit_target_per_month"), default_frequency), 1)
        max_week = max_per_week if allow_multi_week else 1
 
        picked: list[date] = []
        week_count: dict[int, int] = {}
        step = max(1, len(work_dates) // target)
        cursor_idx = 0
        guard = 0
        while len(picked) < target and guard < len(work_dates) * 2:
            idx = min(cursor_idx, len(work_dates) - 1)
            d = work_dates[idx]
            key = (sales_id, d.isoformat())
            wk = week_index(d)
            used_today = load_by_key.get(key, 0)
            if used_today < max_per_day and week_count.get(wk, 0) < max_week and d not in picked:
                picked.append(d)
                load_by_key[key] = used_today + 1
                week_count[wk] = week_count.get(wk, 0) + 1
                cursor_idx += step
            else:
                cursor_idx += 1
            guard += 1
            if cursor_idx >= len(work_dates):
                cursor_idx = cursor_idx % len(work_dates)
 
        if len(picked) < target:
            for d in work_dates:
                if len(picked) >= target:
                    break
                if d in picked:
                    continue
                key = (sales_id, d.isoformat())
                wk = week_index(d)
                used_today = load_by_key.get(key, 0)
                if used_today < max_per_day and week_count.get(wk, 0) < max_week:
                    picked.append(d)
                    load_by_key[key] = used_today + 1
                    week_count[wk] = week_count.get(wk, 0) + 1
 
        schedule[item["id"]] = sorted(d.isoformat() for d in picked)
        if len(picked) < target:
            unscheduled.append(item)
 
    return {"schedule": schedule, "unscheduled": unscheduled}
 
 
def validate_routing_period(month: str, basket_items: list[dict], schedule_map: dict[str, list], territories: list[dict], max_per_day: int) -> dict[str, list]:
    """Validasi sebelum publish: sales kosong, jadwal kurang dari target,
    kapasitas harian sales terlampaui, dan (soft warning) lead di luar
    territory sales yang menanganinya."""
    issues: dict[str, list] = {"leads_without_sales": [], "leads_without_schedule": [], "capacity_exceeded": [], "territory_mismatch": []}
    territory_map: dict[str, set] = {}
    for t in territories:
        territory_map.setdefault(t["sales_id"], set()).add((t.get("block"), t.get("floor") or None))
 
    daily_count: dict[tuple, int] = {}
    for item in basket_items:
        lead = item.get("leads") or {}
        sales_id = item.get("sales_id")
        if not sales_id:
            issues["leads_without_sales"].append(lead.get("store_name") or lead.get("id"))
            continue
        dates = schedule_map.get(item["id"], [])
        target = max(_to_int(lead.get("visit_target_per_month"), 1), 1)
        if len(dates) < target:
            issues["leads_without_schedule"].append(f"{lead.get('store_name')} ({len(dates)}/{target} kunjungan)")
        for d in dates:
            key = (sales_id, str(d)[:10])
            daily_count[key] = daily_count.get(key, 0) + 1
        covers = territory_map.get(sales_id, set())
        if covers and (lead.get("block"), lead.get("floor")) not in covers and (lead.get("block"), None) not in covers:
            issues["territory_mismatch"].append(f"{lead.get('store_name')} (Blok {lead.get('block')})")
 
    for (sales_id, d), count in daily_count.items():
        if count > max_per_day:
            issues["capacity_exceeded"].append(f"Sales {sales_id} pada {d}: {count}/{max_per_day} kunjungan")
    return issues


def build_month_calendar(year: int, month: int, routes_by_date: dict[str, list[dict]]) -> list[list[dict]]:
    """Bangun grid kalender bulanan (Senin-Minggu), termasuk hari dari
    bulan sebelum/sesudahnya untuk mengisi baris pertama/terakhir."""
    weeks: list[list[dict]] = []
    today = datetime.now(ZoneInfo("Asia/Jakarta")).date()
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        days = []
        for d in week:
            days.append({
                "date": d, "in_month": d.month == month,
                "routes": routes_by_date.get(d.isoformat(), []),
                "is_today": d == today,
            })
        weeks.append(days)
    return weeks

def suggest_visit_dates(month: str, target: int, quota_dates: list[str] | None = None, start_date: str | None = None) -> list[str]:
    """Usulan tanggal kunjungan untuk satu lead dalam sebulan.

    Kunjungan disebar merata sepanjang sisa bulan — target 4 berarti
    kira-kira tiap minggu sekali. Jika sales punya daftar tanggal kuota,
    tanggal-tanggal tersebut diprioritaskan. start_date (jika diisi router)
    menjadi tanggal kunjungan pertama, sisanya disebar mingguan.
    """
    if target <= 0:
        return []
    try:
        year, mon = int(str(month)[:4]), int(str(month)[5:7])
    except (TypeError, ValueError):
        return []
    days_in_month = calendar.monthrange(year, mon)[1]
    first = date(year, mon, 1)
    last = date(year, mon, days_in_month)
    today = datetime.now(ZoneInfo("Asia/Jakarta")).date()
    start = _parse_date_safe(start_date) if start_date else None
    start = min(max(start or today, first), last)

    chosen: list[date] = []
    if start_date:
        chosen.append(start)

    remaining = target - len(chosen)
    if remaining <= 0:
        return [d.isoformat() for d in chosen][:target]

    # 1) Prioritaskan tanggal kuota sales dalam bulan tsb (tidak sebelum tanggal mulai)
    usable = sorted({d for d in (_parse_date_safe(q) for q in (quota_dates or [])) if d and start <= d <= last and d not in chosen})
    if usable:
        step = len(usable) / remaining
        picks = {usable[min(int(i * step), len(usable) - 1)] for i in range(remaining)}
        chosen.extend(sorted(picks - set(chosen)))

    # 2) Kekurangan disebar mingguan dari tanggal mulai (dikompresi jika bulan hampir habis)
    if len(chosen) < target:
        span = max((last - start).days, 1)
        need = target - len(chosen)
        step = 7 if span >= need * 7 else max(1, span // need)
        cursor = start
        guard = 0
        while len(chosen) < target and cursor <= last and guard < 128:
            if cursor not in chosen:
                chosen.append(cursor)
            cursor += timedelta(days=step)
            guard += 1

    return sorted(d.isoformat() for d in chosen)[:target]


def build_routing_plan(rows: list[dict], quotas: list[dict], routes: list[dict], month: str) -> dict[str, Any]:
    """Hitung kapasitas sales & kebutuhan kunjungan leads untuk satu bulan.

    - Kapasitas: kuota harian per sales dari tabel sales_visit_quotas
      (misal Sales A: 15 kunjungan/hari pada tanggal-tanggal tertentu).
    - Kebutuhan: target kunjungan/bulan tiap lead dikurangi kunjungan yang
      sudah diplot pada bulan tersebut.
    - Rekomendasi: usulan tanggal kunjungan per lead yang disebar tiap
      minggu sekali dan menghormati sisa kuota harian sales.

    Setiap row di-annotate in-place dengan:
    - visit_target_per_month, frequency_label
    - visits_done_this_month, visits_remaining
    - suggested_dates (usulan tanggal), quota_dates (tanggal kuota sales)
    Mengembalikan dict ringkasan: capacity_total, need_total, planned_total,
    planned_by_key & capacity_by_key (kunci: (sales_id, "YYYY-MM-DD")).
    """
    quota_by_sales: dict[str, list[str]] = {}
    capacity_by_key: dict[tuple, int] = {}
    capacity_total = 0
    for quota in quotas:
        sid = quota.get("sales_id")
        qdate = str(quota.get("quota_date") or "")[:10]
        if not sid or not qdate or qdate[:7] != month:
            continue
        max_visits = _to_int(quota.get("max_visits"), 0)
        quota_by_sales.setdefault(sid, [])
        if qdate not in quota_by_sales[sid]:
            quota_by_sales[sid].append(qdate)
        key = (sid, qdate)
        capacity_by_key[key] = capacity_by_key.get(key, 0) + max_visits
        capacity_total += max_visits

    planned_by_key: dict[tuple, int] = {}
    planned_by_lead: dict[str, int] = {}
    planned_total = 0
    for route in routes:
        sdate = str(route.get("scheduled_date") or "")[:10]
        if sdate[:7] != month:
            continue
        sid = route.get("sales_id")
        lid = route.get("lead_id")
        key = (sid, sdate)
        planned_by_key[key] = planned_by_key.get(key, 0) + 1
        planned_by_lead[lid] = planned_by_lead.get(lid, 0) + 1
        planned_total += 1

    # Kebutuhan kunjungan per lead (target - yang sudah diplot bulan ini)
    need_total = 0
    for row in rows:
        target = _to_int(row.get("visit_target_per_month"), 0)
        done = planned_by_lead.get(row.get("id"), 0)
        remaining = max(target - done, 0)
        row["visit_target_per_month"] = target
        row["frequency_label"] = frequency_label(target)
        row["visits_done_this_month"] = done
        row["visits_remaining"] = remaining
        need_total += remaining

    # Usulan tanggal diproses berurutan agar pemakaian kuota sales realistis
    tentative: dict[tuple, int] = {}
    for row in rows:
        remaining = _to_int(row.get("visits_remaining"), 0)
        sid = row.get("sales_id") or None
        if remaining > 0:
            # Patokan tanggal: kuota sales terkait; jika belum ada, gabungan semua kuota
            candidates = sorted(set(quota_by_sales[sid])) if sid and quota_by_sales.get(sid) else sorted({d for ds in quota_by_sales.values() for d in ds})
            picked: list[str] = []
            for sdate in suggest_visit_dates(month, remaining, candidates):
                cap = capacity_by_key.get((sid, sdate))
                if sid and cap is None:
                    # Sales belum punya kuota: pakai total kapasitas lintas sales pada tanggal itu
                    cap = sum(c for (_s, d), c in capacity_by_key.items() if d == sdate) or None
                if cap is not None:
                    used = planned_by_key.get((sid, sdate), 0) + tentative.get((sid, sdate), 0)
                    if used >= cap:
                        continue  # kuota sales pada tanggal itu sudah penuh
                picked.append(sdate)
                tentative[(sid, sdate)] = tentative.get((sid, sdate), 0) + 1
                if len(picked) >= remaining:
                    break
            row["suggested_dates"] = picked
        else:
            row["suggested_dates"] = []
        row["quota_dates"] = sorted(quota_by_sales.get(sid, [])) if sid else []

    return {
        "capacity_total": capacity_total,
        "need_total": need_total,
        "planned_total": planned_total,
        "planned_by_key": planned_by_key,
        "capacity_by_key": capacity_by_key,
    }


# ---------- Deteksi Duplikat Leads ----------
# Status: "Duplikat" (hampir pasti data ganda), "Kemungkinan Duplikat" (perlu dicek,
# bisa karena typo atau beda tempat), "Perlu Dicek" (kemiripan sedang), "Unik" (aman).

def _duplicate_normalise(text) -> str:
    """Normalisasi teks untuk perbandingan: lowercase, tanpa tanda baca/spasi ganda."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def _duplicate_similarity(a: str, b: str) -> float:
    """Skor kemiripan 0-1, toleran terhadap typo (difflib)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _duplicate_address_key(lead: dict) -> str:
    """Alamat toko = blok + lantai + los + nomor (dinormalisasi)."""
    return _duplicate_normalise(" ".join(str(lead.get(k) or "") for k in ("block", "floor", "los", "nomor")))


def _duplicate_location_text(lead: dict) -> str:
    parts = [str(lead.get(k) or "").strip() for k in ("block", "floor", "los")]
    return " / ".join(p for p in parts if p) or "-"


def _duplicate_evaluate(name_sim: float, addr_sim: float, same_nomor: bool) -> tuple[str, int]:
    """Tentukan status & persentase duplikat dari skor kemiripan nama dan alamat."""
    # Nama & alamat sama -> hampir pasti duplikat
    if name_sim >= 0.92 and addr_sim >= 0.92:
        return "Duplikat", min(100, round(90 + max(name_sim, addr_sim) * 10))
    if addr_sim >= 0.92:
        # Alamat sama persis (blok+lantai+los+nomor sama) -> satu stall yang sama,
        # nama beda berarti kemungkinan typo / input ulang -> tetap duplikat
        if same_nomor:
            if name_sim >= 0.75:
                return "Duplikat", round(82 + name_sim * 15)
            return "Duplikat", round(75 + name_sim * 15)
        # Alamat mirip tapi nomor kosong/tidak persis -> perlu dicek
        if name_sim >= 0.75:
            return "Duplikat", round(80 + name_sim * 15)
        return "Kemungkinan Duplikat", round(65 + addr_sim * 10)
    # Nama sama, alamat beda -> bisa jadi beda tempat
    if name_sim >= 0.92:
        return "Kemungkinan Duplikat", round(55 + (name_sim - 0.92) * 60)
    # Keduanya mirip tapi tidak identik (indikasi typo)
    combined = 0.6 * name_sim + 0.4 * addr_sim
    if combined >= 0.72:
        return "Perlu Dicek", round(combined * 100)
    return "Unik", round(combined * 100)


def annotate_duplicates(leads: list[dict]) -> None:
    """Annotate setiap lead dengan info duplikat terhadap lead lain (in-place).

    Menambahkan key: dup_status, dup_percent, dup_match_name, dup_match_location.
    """
    prepared = []
    for lead in leads:
        prepared.append((
            _duplicate_normalise(lead.get("store_name")),
            _duplicate_address_key(lead),
        ))

    for i, lead in enumerate(leads):
        name_i, addr_i = prepared[i]
        best_status, best_percent, best_idx = "Unik", 0, None
        for j, (name_j, addr_j) in enumerate(prepared):
            if j == i:
                continue
            # Hemat komputasi: hitung kemiripan alamat hanya jika perlu
            name_sim = _duplicate_similarity(name_i, name_j)
            if name_sim < 0.5 and addr_i != addr_j:
                continue
            addr_sim = 1.0 if addr_i == addr_j else _duplicate_similarity(addr_i, addr_j)
            if name_sim < 0.5 and addr_sim < 0.5:
                continue
            same_nomor = bool(str(leads[i].get("nomor") or "").strip()) and \
                bool(str(leads[j].get("nomor") or "").strip()) and addr_i == addr_j
            status, percent = _duplicate_evaluate(name_sim, addr_sim, same_nomor)
            if percent > best_percent or (percent == best_percent and status == "Duplikat" and best_status != "Duplikat"):
                best_status, best_percent, best_idx = status, percent, j
        lead["dup_status"] = best_status
        lead["dup_percent"] = best_percent
        if best_idx is not None:
            match = leads[best_idx]
            lead["dup_match_name"] = match.get("store_name") or "-"
            lead["dup_match_location"] = _duplicate_location_text(match)
        else:
            lead["dup_match_name"] = None
            lead["dup_match_location"] = None



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
    return render(request, "dashboard.html", title="Dashboard", stats=stats, total_leads=stats["total"], total_tonnage_ton=round(stats["weight"] / 1000, 2), total_tonnage_month_ton=round(stats["weight_month"] / 1000, 2), leads_routed=stats["routed"], leads_visited=stats["status_stats"]["Visited"], status_stats=stats["status_stats"], courier_stats=stats["courier_stats"], recent=store.leads()[:8])


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render(request, "login.html", title="Masuk")


@app.post("/login")
@app.post("/auth/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user, error_reason = store.authenticate(username.strip(), password)
    if not user:
        if error_reason == "inactive":
            message = "Akun Anda telah dinonaktifkan. Silakan hubungi Admin untuk informasi lebih lanjut."
        else:
            message = "Username atau password tidak valid."
        return render(request, "login.html", title="Masuk", error=message)
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
    annotate_duplicates(all_leads)  # cek duplikat terhadap seluruh leads, bukan hanya yang tampil
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
    return render(
        request, "data_entry/list.html", title="Daftar Leads",
        leads=filtered[start:start + per_page], search=search, block=block, floor=floor, los=los,
        expedition=expedition, sort_by=sort_by, page=page, per_page=per_page, total=total, total_pages=total_pages,
        blocks=BLOCKS, floors=FLOORS, los_options=LOS_OPTIONS, courier_options=EXPEDITIONS,
        created=request.query_params.get("created"), updated=request.query_params.get("updated"),
        deleted=request.query_params.get("deleted"), error=request.query_params.get("error"),
    )


@app.get("/leads/export")
async def export_leads_csv(request: Request, search: str = "", block: str = "", floor: str = "", los: str = "", expedition: str = "", sort_by: str = "newest"):
    if not can(current_user(request), "admin"):
        return RedirectResponse("/leads", status_code=303)

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
        filtered.sort(key=lambda lead: str(lead.get("created_at") or lead.get("visit_timestamp") or ""), reverse=True)

    output = io.StringIO()
    output.write("\ufeff")  # BOM agar karakter khusus tampil benar saat dibuka di Excel
    writer = csv.writer(output)
    writer.writerow([
        "Nama Toko", "Blok", "Lantai", "Los", "Nomor", "Nama PIC", "Jabatan PIC", "No HP/WA",
        "PIC Data Entry", "Ekspedisi Saat Ini", "Jenis Kiriman", "Negara Terbanyak", "Kota Terbanyak",
        "Tonase Potensi (KG)", "Periode Tonase", "Estimasi Tonase/Bulan (KG)", "Status Routing",
        "Tanggal Kunjungan",
    ])
    for lead in filtered:
        writer.writerow([
            lead.get("store_name") or "",
            lead.get("block") or "",
            lead.get("floor") or "",
            lead.get("los") or "",
            lead.get("nomor") or "",
            lead.get("pic_name") or "",
            lead.get("pic_position") or "",
            lead.get("phone_number") or "",
            lead.get("data_entry_pic") or "",
            lead.get("current_courier") or "",
            lead.get("shipment_type") or "",
            lead.get("top_country") or "",
            lead.get("top_city") or "",
            lead.get("tonnage_potential_kg") or 0,
            lead.get("tonnage_period") or "Bulan",
            f"{lead.get('monthly_tonnage_kg', 0):.2f}",
            lead.get("routing_status") or "Belum diplot",
            format_datetime(lead.get("visit_timestamp")),
        ])
    filename = f"daftar_leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


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
                    raise ValueError(f"baris {row_number}: kolom database terlalu pendek untuk pilihan multi-value. Perbesar panjang kolom terkait (mis. tipe VARCHAR/text) di Supabase SQL Editor") from exc
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


@app.get("/leads/{lead_id}/edit", response_class=HTMLResponse)
async def edit_lead_form(request: Request, lead_id: str):
    if not can(current_user(request), "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    lead = store.get_lead(lead_id)
    if not lead:
        return RedirectResponse("/leads?error=Lead%20tidak%20ditemukan", status_code=303)
    return render(
        request, "data_entry/edit.html", title="Edit Lead", lead=lead,
        **lead_form_context(error=request.query_params.get("error")),
    )


@app.post("/leads/{lead_id}/edit")
async def update_lead_route(
    request: Request, lead_id: str,
    store_name: str = Form(...), block: str = Form(...), floor: str = Form(...), los: str = Form(...),
    nomor: str = Form(...), pic_name: str = Form(...), pic_position: str = Form(...), phone_number: str = Form(...),
    shipment_type: str = Form(...), current_courier: list[str] = Form(...), top_country: list[str] = Form(...),
    top_city: list[str] = Form(...), tonnage_potential_kg: float = Form(0), tonnage_period: str = Form("Bulan"),
):
    user = current_user(request)
    if not can(user, "data_entry", "admin"):
        return RedirectResponse("/leads", status_code=303)
    if not store.get_lead(lead_id):
        return RedirectResponse("/leads?error=Lead%20tidak%20ditemukan", status_code=303)
    try:
        updated_data = build_lead_data(
            user, store_name=store_name, block=block, floor=floor, los=los, nomor=nomor,
            pic_name=pic_name, pic_position=pic_position, phone_number=phone_number,
            shipment_type=shipment_type, current_courier=current_courier, top_country=top_country,
            top_city=top_city, tonnage_potential_kg=tonnage_potential_kg, tonnage_period=tonnage_period,
        )
        # Jangan timpa jejak audit/waktu kunjungan asli & pemilik data saat proses edit
        updated_data.pop("visit_timestamp", None)
        updated_data.pop("created_by", None)
        store.update_lead(lead_id, updated_data)
    except ValueError as exc:
        return RedirectResponse(f"/leads/{lead_id}/edit?error={quote(str(exc))}", status_code=303)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.text:
            print(f"Update lead Supabase error: {exc.response.text}")
        return RedirectResponse(f"/leads/{lead_id}/edit?error={quote('Supabase menolak perubahan data.')}", status_code=303)
    except requests.RequestException as exc:
        print(f"Update lead connection error: {exc}")
        return RedirectResponse(f"/leads/{lead_id}/edit?error={quote('Tidak dapat terhubung ke Supabase.')}", status_code=303)
    return RedirectResponse("/leads?updated=1", status_code=303)


@app.post("/leads/{lead_id}/delete")
async def delete_lead_route(request: Request, lead_id: str):
    if not can(current_user(request), "admin"):
        return RedirectResponse("/leads?error=Hanya%20admin%20yang%20dapat%20menghapus%20data", status_code=303)
    if not store.get_lead(lead_id):
        return RedirectResponse("/leads?error=Lead%20tidak%20ditemukan", status_code=303)
    try:
        store.delete_lead(lead_id)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.text:
            print(f"Delete lead Supabase error: {exc.response.text}")
        return RedirectResponse("/leads?error=Data%20gagal%20dihapus", status_code=303)
    except requests.RequestException as exc:
        print(f"Delete lead connection error: {exc}")
        return RedirectResponse("/leads?error=Tidak%20dapat%20terhubung%20ke%20Supabase", status_code=303)
    return RedirectResponse("/leads?deleted=1", status_code=303)


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
    all_users = store.users()
    sales_users = [item for item in all_users if item.get("role") in {"sales", "Sales"}]
    sales_names = {item.get("id"): (item.get("name") or item.get("username") or "-") for item in all_users}
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = datetime.now().strftime("%Y-%m")
    routes = store.routes()
    routed_this_month = sum(1 for route in routes if str(route.get("scheduled_date", "")).startswith(month))
    routing_target = store.routing_target(month)
    routing_progress = min((routed_this_month / routing_target) * 100, 100) if routing_target else 0
    # Kapasitas sales (kuota harian) vs kebutuhan kunjungan leads (target per lead)
    sales_quotas = store.sales_quotas()
    plan_stats = build_routing_plan(rows, sales_quotas, routes, month)
    for quota in sales_quotas:
        key = (quota.get("sales_id"), str(quota.get("quota_date") or "")[:10])
        quota["planned_count"] = plan_stats["planned_by_key"].get(key, 0)
    sales_capacity_summary = []
    for sales in sales_users:
        month_quotas = [q for q in sales_quotas if q.get("sales_id") == sales.get("id") and str(q.get("quota_date") or "")[:7] == month]
        if not month_quotas:
            continue
        sales_capacity_summary.append({
            "name": sales.get("name") or sales.get("username") or "Sales",
            "days": len(month_quotas),
            "daily_max": max(_to_int(q.get("max_visits"), 0) for q in month_quotas),
            "total": sum(_to_int(q.get("max_visits"), 0) for q in month_quotas),
        })
    return render(request, "router/routing.html", title="Routing Visit", groups=grouped, leads=rows, sales_users=sales_users, sales_names=sales_names, selected_lead=selected, recommendations=recommendations[:30], routes=routes, target_month=month, routing_target=routing_target, routed_this_month=routed_this_month, routing_progress=routing_progress, monthly_tonnage=monthly_tonnage, has_international_history=has_international_history, sales_quotas=sales_quotas, capacity_total=plan_stats["capacity_total"], need_total=plan_stats["need_total"], planned_total=plan_stats["planned_total"], sales_capacity_summary=sales_capacity_summary, frequency_label=frequency_label)


@app.post("/routing/target")
async def save_routing_target(request: Request, month: str = Form(...), target_count: int = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    if target_count < 0:
        return RedirectResponse(f"/routing?month={quote(month)}&error=Target%20tidak%20boleh%20negatif", status_code=303)
    store.save_routing_target(month, target_count, user["id"])
    return RedirectResponse(f"/routing?month={quote(month)}&target_saved=1", status_code=303)


@app.post("/routing/sales-quota")
async def save_sales_quota_route(request: Request, sales_id: str = Form(...), quota_date: str = Form(...), max_visits: int = Form(...)):
    """Tetapkan kuota visit harian satu sales pada satu tanggal tertentu."""
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    if max_visits < 1:
        return RedirectResponse("/routing?error=Kuota%20visit%20minimal%201", status_code=303)
    if not _parse_date_safe(quota_date):
        return RedirectResponse("/routing?error=Tanggal%20kuota%20tidak%20valid", status_code=303)
    try:
        store.save_sales_quota(sales_id, quota_date, max_visits)
    except requests.HTTPError as exc:
        detail = exc.response.text[:200] if exc.response is not None and exc.response.text else str(exc)
        print(f"Save sales quota Supabase error: {detail}")
        return RedirectResponse(f"/routing?error={quote('Kuota gagal disimpan: ' + detail)}", status_code=303)
    except requests.RequestException as exc:
        print(f"Save sales quota connection error: {exc}")
        return RedirectResponse("/routing?error=Tidak%20dapat%20terhubung%20ke%20Supabase", status_code=303)
    return RedirectResponse("/routing?quota_saved=1", status_code=303)


@app.post("/routing/sales-quota/delete")
async def delete_sales_quota_route(request: Request, quota_id: str = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    try:
        store.delete_sales_quota(quota_id)
    except requests.RequestException as exc:
        print(f"Delete sales quota error: {exc}")
        return RedirectResponse("/routing?error=Kuota%20gagal%20dihapus", status_code=303)
    return RedirectResponse("/routing?quota_deleted=1", status_code=303)

@app.get("/routing/periods", response_class=HTMLResponse)
async def routing_periods_page(request: Request):
    """Fase 1: pilih periode, lihat info & kapasitas, atur config, pilih
    leads ke basket berdasarkan rekomendasi prioritas."""
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
 
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = datetime.now().strftime("%Y-%m")
    year, mon = int(month[:4]), int(month[5:7])
 
    period = store.routing_period(month) or {}
    work_days_per_week = _to_int(period.get("work_days_per_week"), 6)
    min_per_day = _to_int(period.get("min_visit_target_per_day"), 8)
    max_per_day = _to_int(period.get("max_visit_capacity_per_day"), 15)
    default_frequency = _to_int(period.get("default_visit_frequency"), 1)
    allow_multi_week = bool(period.get("allow_multiple_visits_per_week", False))
    max_per_week = _to_int(period.get("max_visits_per_week"), 1)
    period_status = period.get("status", "Belum Diatur")
 
    all_leads = store.leads()
    annotate_duplicates(all_leads)
    for lead in all_leads:
        lead["monthly_tonnage_kg"] = monthly_tonnage(lead)
        lead["has_international"] = has_international_history(lead)
 
    active_sales = store.active_sales_count()
    work_days = work_days_in_month(year, mon, work_days_per_week)
    capacity = compute_period_capacity(active_sales, work_days, min_per_day, max_per_day)
 
    prev_month, next_month = adjacent_months(month)
    prev_info = period_summary(prev_month)
    next_info = period_summary(next_month)
 
    basket_items = store.routing_basket(period["id"]) if period.get("id") else []
    basket_lead_ids = {item.get("lead_id") for item in basket_items}
    basket_need = sum(_to_int((item.get("leads") or {}).get("visit_target_per_month"), default_frequency) for item in basket_items)
 
    recommendations = [lead for lead in all_leads if lead["id"] not in basket_lead_ids]
    recommendations.sort(key=lead_priority_score, reverse=True)
 
    return render(
        request, "router/routing_period.html", title="Routing Visit — Periode & Kapasitas",
        target_month=month, prev_month=prev_month, next_month=next_month, prev_info=prev_info, next_info=next_info,
        period=period, work_days_per_week=work_days_per_week, min_per_day=min_per_day, max_per_day=max_per_day,
        default_frequency=default_frequency, allow_multi_week=allow_multi_week, max_per_week=max_per_week,
        period_status=period_status, active_sales=active_sales, work_days=work_days, capacity=capacity,
        total_leads=len(all_leads), recommendations=recommendations[:50], basket_items=basket_items, basket_need=basket_need,
    )
 
 
@app.post("/routing/periods")
async def save_routing_periods_route(
    request: Request, month: str = Form(...), work_days_per_week: int = Form(...),
    min_visit_target_per_day: int = Form(...), max_visit_capacity_per_day: int = Form(...),
    default_visit_frequency: int = Form(...), allow_multiple_visits_per_week: str = Form(""),
    max_visits_per_week: int = Form(1), notes: str = Form(""),
):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    if not (1 <= work_days_per_week <= 7):
        return RedirectResponse(f"/routing/periods?month={quote(month)}&error=Hari%20kerja%2Fminggu%20harus%201-7", status_code=303)
    if min_visit_target_per_day < 0 or max_visit_capacity_per_day < min_visit_target_per_day:
        return RedirectResponse(f"/routing/periods?month={quote(month)}&error=Target%20min%2Fmaks%20kunjungan%20tidak%20valid", status_code=303)
    try:
        store.save_routing_period(month, {
            "work_days_per_week": work_days_per_week,
            "min_visit_target_per_day": min_visit_target_per_day,
            "max_visit_capacity_per_day": max_visit_capacity_per_day,
            "default_visit_frequency": default_visit_frequency,
            "allow_multiple_visits_per_week": bool(allow_multiple_visits_per_week),
            "max_visits_per_week": max_visits_per_week,
            "notes": notes,
        }, user["id"])
    except requests.HTTPError as exc:
        detail = exc.response.text[:200] if exc.response is not None and exc.response.text else str(exc)
        print(f"Save routing period Supabase error: {detail}")
        return RedirectResponse(f"/routing/periods?month={quote(month)}&error={quote('Config gagal disimpan: ' + detail)}", status_code=303)
    return RedirectResponse(f"/routing/periods?month={quote(month)}&config_saved=1", status_code=303)
 
 
def _ensure_period(month: str, user_id: str) -> dict:
    """Ambil periode bulan ini, atau buat dengan nilai default jika belum ada
    (supaya Router bisa langsung menambah ke basket tanpa mengisi config dulu)."""
    period = store.routing_period(month)
    if period:
        return period
    return store.save_routing_period(month, {
        "work_days_per_week": 6, "min_visit_target_per_day": 8, "max_visit_capacity_per_day": 15,
        "default_visit_frequency": 1, "allow_multiple_visits_per_week": False, "max_visits_per_week": 1,
    }, user_id)
 
 
@app.post("/routing/basket/add")
async def add_basket_route(request: Request, month: str = Form(...), lead_id: str = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    period = _ensure_period(month, user["id"])
    store.add_to_basket(period["id"], lead_id, user["id"])
    return RedirectResponse(f"/routing/periods?month={quote(month)}&basket_added=1", status_code=303)
 
 
@app.post("/routing/basket/add-bulk")
async def add_basket_bulk_route(request: Request):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    month = str(form.get("month") or datetime.now().strftime("%Y-%m"))
    lead_ids = [str(item) for item in form.getlist("lead_id")]
    if not lead_ids:
        return RedirectResponse(f"/routing/periods?month={quote(month)}&error=Pilih%20minimal%20satu%20lead", status_code=303)
    period = _ensure_period(month, user["id"])
    for lead_id in lead_ids:
        store.add_to_basket(period["id"], lead_id, user["id"])
    return RedirectResponse(f"/routing/periods?month={quote(month)}&basket_added={len(lead_ids)}", status_code=303)
 
 
@app.post("/routing/basket/remove")
async def remove_basket_route(request: Request, item_id: str = Form(...), month: str = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    store.remove_from_basket(item_id)
    return RedirectResponse(f"/routing/periods?month={quote(month)}&basket_removed=1", status_code=303)

@app.get("/routing/assignment", response_class=HTMLResponse)
async def routing_assignment_page(request: Request):
    """Fase 2: kelola territory sales & pembagian leads (auto-assign +
    penyesuaian manual) untuk satu periode routing."""
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
 
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = datetime.now().strftime("%Y-%m")
    year, mon = int(month[:4]), int(month[5:7])
 
    period = _ensure_period(month, user["id"])
    work_days_per_week = _to_int(period.get("work_days_per_week"), 6)
    max_per_day = _to_int(period.get("max_visit_capacity_per_day"), 15)
    default_frequency = _to_int(period.get("default_visit_frequency"), 1)
    work_days = work_days_in_month(year, mon, work_days_per_week)
    capacity_per_sales = work_days * max_per_day
 
    all_users = store.users()
    sales_users = [u for u in all_users if u.get("role") == "sales"]
    sales_names = {u.get("id"): (u.get("name") or u.get("username") or "-") for u in all_users}
 
    territories = store.sales_territories(period["id"])
    territories_by_sales: dict[str, list[dict]] = {}
    for t in territories:
        territories_by_sales.setdefault(t["sales_id"], []).append(t)
 
    sales_history = {s["id"]: store.sales_assignment_history(s["id"]) for s in sales_users}
 
    basket_items = store.routing_basket(period["id"])
    for item in basket_items:
        lead = item.get("leads") or {}
        item["monthly_tonnage_kg"] = monthly_tonnage(lead)
 
    # Filter pencarian manual (toko, PIC, blok, lantai, los, sales)
    search = request.query_params.get("search", "").strip().lower()
    filter_sales = request.query_params.get("filter_sales", "")
    if search:
        basket_items = [
            i for i in basket_items
            if search in " ".join(str((i.get("leads") or {}).get(k, "")) for k in ("store_name", "pic_name", "block", "floor", "los")).lower()
        ]
    if filter_sales:
        basket_items = [i for i in basket_items if i.get("sales_id") == filter_sales]
 
    routes = store.routes()
    owner_map = preferred_owner_map(routes)
    for item in basket_items:
        lead_id = (item.get("leads") or {}).get("id")
        item["preferred_owner_id"] = owner_map.get(lead_id)
        item["preferred_owner_name"] = sales_names.get(owner_map.get(lead_id)) if owner_map.get(lead_id) else None
 
    # Ringkasan per sales: jumlah leads, total tonase, kapasitas, sisa kapasitas
    summary = []
    unassigned_count = 0
    for s in sales_users:
        assigned = [i for i in store.routing_basket(period["id"]) if i.get("sales_id") == s["id"]]
        used = sum(max(_to_int((i.get("leads") or {}).get("visit_target_per_month"), default_frequency), 1) for i in assigned)
        summary.append({
            "id": s["id"], "name": s.get("name") or s.get("username"),
            "lead_count": len(assigned),
            "tonnage": sum(monthly_tonnage(i.get("leads") or {}) for i in assigned),
            "used_capacity": used, "max_capacity": capacity_per_sales,
        })
    unassigned_count = sum(1 for i in store.routing_basket(period["id"]) if not i.get("sales_id"))
 
    return render(
        request, "router/routing_assignment.html", title="Routing Visit — Sales Assignment",
        target_month=month, period=period, sales_users=sales_users, sales_names=sales_names,
        territories_by_sales=territories_by_sales, sales_history=sales_history,
        basket_items=basket_items, search=search, filter_sales=filter_sales,
        capacity_per_sales=capacity_per_sales, summary=summary, unassigned_count=unassigned_count,
        blocks=BLOCKS, floors=FLOORS,
    )
 
 
@app.post("/routing/territory/add")
async def add_territory_route(request: Request, month: str = Form(...), sales_id: str = Form(...), block: str = Form(...), floor: str = Form("")):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    period = _ensure_period(month, user["id"])
    store.save_sales_territory(period["id"], sales_id, block, floor or None, user["id"])
    return RedirectResponse(f"/routing/assignment?month={quote(month)}&territory_saved=1", status_code=303)
 
 
@app.post("/routing/territory/delete")
async def delete_territory_route(request: Request, territory_id: str = Form(...), month: str = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    store.delete_sales_territory(territory_id)
    return RedirectResponse(f"/routing/assignment?month={quote(month)}&territory_deleted=1", status_code=303)
 
 
@app.post("/routing/assignment/auto")
async def auto_assign_route(request: Request, month: str = Form(...)):
    """Jalankan pembagian leads otomatis (preferred owner -> territory -> kapasitas)."""
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    period = _ensure_period(month, user["id"])
    year, mon = int(month[:4]), int(month[5:7])
    work_days = work_days_in_month(year, mon, _to_int(period.get("work_days_per_week"), 6))
    capacity_per_sales = work_days * _to_int(period.get("max_visit_capacity_per_day"), 15)
    default_frequency = _to_int(period.get("default_visit_frequency"), 1)
 
    sales_users = [u for u in store.users() if u.get("role") == "sales"]
    territories = store.sales_territories(period["id"])
    owner_map = preferred_owner_map(store.routes())
    basket_items = store.routing_basket(period["id"])
 
    result = auto_assign_leads(basket_items, sales_users, territories, owner_map, capacity_per_sales, default_frequency)
    for item_id, info in result["assignments"].items():
        store.update_basket_item(item_id, {
            "sales_id": info["sales_id"], "is_preferred_owner": info["is_preferred_owner"],
            "manual_override": False, "assigned_at": now_iso(),
        })
    unassigned_count = len(result["unassigned"])
    suffix = f"&unassigned={unassigned_count}" if unassigned_count else ""
    return RedirectResponse(f"/routing/assignment?month={quote(month)}&auto_assigned=1{suffix}", status_code=303)
 
 
@app.post("/routing/assignment/save")
async def save_assignment_route(request: Request):
    """Penyesuaian manual: ubah sales_id per basket item (menandai manual_override)."""
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    month = str(form.get("month") or datetime.now().strftime("%Y-%m"))
    updated = 0
    for key, value in form.multi_items():
        if not key.startswith("sales_id_"):
            continue
        item_id = key.removeprefix("sales_id_")
        original = str(form.get(f"original_sales_id_{item_id}") or "")
        new_value = str(value or "")
        if new_value != original:
            store.update_basket_item(item_id, {
                "sales_id": new_value or None,
                "manual_override": True,
                "is_preferred_owner": False,
                "assigned_at": now_iso(),
            })
            updated += 1
    return RedirectResponse(f"/routing/assignment?month={quote(month)}&assignment_saved={updated}", status_code=303)

@app.get("/routing/schedule", response_class=HTMLResponse)
async def routing_schedule_page(request: Request):
    """Fase 3: generate jadwal otomatis, lihat validasi, preview multi-view,
    dan publish routing."""
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
 
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = datetime.now().strftime("%Y-%m")
 
    period = _ensure_period(month, user["id"])
    sales_names = {u.get("id"): (u.get("name") or u.get("username") or "-") for u in store.users()}
 
    basket_items = store.routing_basket(period["id"])
    territories = store.sales_territories(period["id"])
    routes_this_month = [r for r in store.routes() if str(r.get("scheduled_date", ""))[:7] == month]
 
    schedule_map: dict[str, list[str]] = {}
    for item in basket_items:
        lead_id = (item.get("leads") or {}).get("id")
        schedule_map[item["id"]] = sorted(r.get("scheduled_date") for r in routes_this_month if r.get("lead_id") == lead_id)
 
    max_per_day = _to_int(period.get("max_visit_capacity_per_day"), 15)
    issues = validate_routing_period(month, basket_items, schedule_map, territories, max_per_day)
    has_blocking_issues = bool(issues["leads_without_sales"] or issues["leads_without_schedule"] or issues["capacity_exceeded"])
 
    view = request.query_params.get("view", "sales")
    grouped: dict[str, list[dict]] = {}
    for route in routes_this_month:
        lead = route.get("leads") or {}
        if view == "date":
            key = str(route.get("scheduled_date") or "-")
        elif view == "block":
            key = f"Blok {lead.get('block') or '-'}"
        elif view == "lead":
            key = lead.get("store_name") or "-"
        else:
            key = sales_names.get(route.get("sales_id")) or "Belum ada sales"
        grouped.setdefault(key, []).append(route)
    grouped = dict(sorted(grouped.items()))
 
    return render(
        request, "router/routing_schedule.html", title="Routing Visit — Jadwal & Validasi",
        target_month=month, period=period, basket_items=basket_items, schedule_map=schedule_map,
        issues=issues, has_blocking_issues=has_blocking_issues, view=view, grouped=grouped,
        sales_names=sales_names, total_routes=len(routes_this_month),
    )
 
 
@app.post("/routing/schedule/generate")
async def generate_schedule_route(request: Request, month: str = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    period = _ensure_period(month, user["id"])
    year, mon = int(month[:4]), int(month[5:7])
    work_dates = work_dates_in_month(year, mon, _to_int(period.get("work_days_per_week"), 6))
    max_per_day = _to_int(period.get("max_visit_capacity_per_day"), 15)
    default_frequency = _to_int(period.get("default_visit_frequency"), 1)
    allow_multi_week = bool(period.get("allow_multiple_visits_per_week", False))
    max_per_week = _to_int(period.get("max_visits_per_week"), 1)
 
    basket_items = [i for i in store.routing_basket(period["id"]) if i.get("sales_id")]
    if not basket_items:
        return RedirectResponse(f"/routing/schedule?month={quote(month)}&error={quote('Belum ada lead yang punya sales. Selesaikan Sales Assignment dulu.')}", status_code=303)
 
    for item in basket_items:
        lead_id = (item.get("leads") or {}).get("id")
        if lead_id:
            store.clear_scheduled_routes(lead_id, month)
 
    existing_routes = [r for r in store.routes() if str(r.get("scheduled_date", ""))[:7] == month]
    result = generate_auto_schedule(basket_items, month, work_dates, max_per_day, allow_multi_week, max_per_week, default_frequency, existing_routes)
 
    saved = 0
    for item in basket_items:
        lead = item.get("leads") or {}
        dates = result["schedule"].get(item["id"], [])
        for visit_date in dates:
            store.assign_route(lead["id"], item["sales_id"], user["id"], visit_date, "Auto-generated (Fase 3)")
            saved += 1
        if dates:
            store.update_lead(lead["id"], {"sales_id": item["sales_id"], "routing_status": "Sudah diplot"})
 
    unscheduled_count = len(result["unscheduled"])
    suffix = f"&unscheduled={unscheduled_count}" if unscheduled_count else ""
    return RedirectResponse(f"/routing/schedule?month={quote(month)}&generated={saved}{suffix}", status_code=303)
 
 
@app.post("/routing/publish")
async def publish_routing_route(request: Request, month: str = Form(...)):
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
    period = _ensure_period(month, user["id"])
    basket_items = store.routing_basket(period["id"])
    territories = store.sales_territories(period["id"])
    routes_this_month = [r for r in store.routes() if str(r.get("scheduled_date", ""))[:7] == month]
    schedule_map: dict[str, list[str]] = {}
    for item in basket_items:
        lead_id = (item.get("leads") or {}).get("id")
        schedule_map[item["id"]] = sorted(r.get("scheduled_date") for r in routes_this_month if r.get("lead_id") == lead_id)
    max_per_day = _to_int(period.get("max_visit_capacity_per_day"), 15)
    issues = validate_routing_period(month, basket_items, schedule_map, territories, max_per_day)
    if issues["leads_without_sales"] or issues["leads_without_schedule"] or issues["capacity_exceeded"]:
        return RedirectResponse(f"/routing/schedule?month={quote(month)}&error={quote('Validasi gagal. Selesaikan isu di bawah sebelum publish.')}", status_code=303)
    store.publish_period(period["id"], user["id"])
    return RedirectResponse(f"/routing/schedule?month={quote(month)}&published=1", status_code=303)

@app.get("/sales/schedule", response_class=HTMLResponse)
async def sales_schedule(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
 
    view = request.query_params.get("view", "month")
    if view not in {"month", "week", "day", "sales", "block"}:
        view = "month"
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = datetime.now().strftime("%Y-%m")
    year, mon = int(month[:4]), int(month[5:7])
 
    focus_date_str = request.query_params.get("date") or datetime.now().strftime("%Y-%m-%d")
    focus_date = _parse_date_safe(focus_date_str) or datetime.now(ZoneInfo("Asia/Jakarta")).date()
 
    all_users = store.users()
    sales_names = {u.get("id"): (u.get("name") or u.get("username") or "-") for u in all_users}
    sales_users = [u for u in all_users if u.get("role") == "sales"]
 
    sales_filter = request.query_params.get("sales_id", "")
    if user.get("role") == "sales":
        sales_filter = user.get("id")  # sales hanya boleh melihat jadwal miliknya sendiri
 
    routes = store.routes()
    if sales_filter:
        routes = [r for r in routes if r.get("sales_id") == sales_filter]
 
    routes_by_date: dict[str, list[dict]] = {}
    for r in routes:
        sdate = str(r.get("scheduled_date") or "")[:10]
        if sdate:
            routes_by_date.setdefault(sdate, []).append(r)
 
    month_weeks, week_days, day_routes, grouped = [], [], [], {}
 
    if view == "month":
        month_weeks = build_month_calendar(year, mon, routes_by_date)
    elif view == "week":
        week_start = focus_date - timedelta(days=focus_date.weekday())
        today = datetime.now(ZoneInfo("Asia/Jakarta")).date()
        for i in range(7):
            d = week_start + timedelta(days=i)
            week_days.append({"date": d, "routes": routes_by_date.get(d.isoformat(), []), "is_today": d == today})
    elif view == "day":
        day_routes = routes_by_date.get(focus_date.isoformat(), [])
    else:  # sales / block
        month_routes = [r for r in routes if str(r.get("scheduled_date", ""))[:7] == month]
        for r in month_routes:
            lead = r.get("leads") or {}
            key = sales_names.get(r.get("sales_id"), "Belum ada sales") if view == "sales" else f"Blok {lead.get('block') or '-'}"
            grouped.setdefault(key, []).append(r)
        grouped = dict(sorted(grouped.items()))
 
    prev_month, next_month = adjacent_months(month)
 
    return render(
        request, "sales/schedule.html", title="Jadwal Sales",
        view=view, target_month=month, prev_month=prev_month, next_month=next_month,
        focus_date=focus_date.isoformat(), month_weeks=month_weeks, week_days=week_days,
        day_routes=day_routes, grouped=grouped, sales_users=sales_users, sales_filter=sales_filter,
        sales_names=sales_names, can_manage=can(user, "router", "admin", "sales"),
    )

@app.post("/sales/update-status")
async def update_visit_status(request: Request, route_id: str = Form(...), visit_status: str = Form(...), notes: str = Form("")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    route = store.get_route(route_id)
    if not route:
        return RedirectResponse("/sales/schedule?error=Jadwal%20tidak%20ditemukan", status_code=303)
    if user.get("role") == "sales" and route.get("sales_id") != user.get("id"):
        return RedirectResponse("/sales/schedule?error=Anda%20tidak%20berwenang%20mengubah%20jadwal%20ini", status_code=303)
    store.update_route_status(route_id, visit_status, notes)
    return RedirectResponse("/sales/schedule?status_updated=1", status_code=303)
 
 
@app.post("/sales/reschedule")
async def reschedule_visit(request: Request, route_id: str = Form(...), new_date: str = Form(...), reason: str = Form(...)):
    """Reschedule TIDAK mengubah ownership lead (sales_id tetap sama),
    hanya mengubah tanggal kunjungan. Alasan perubahan disimpan sebagai
    histori di visit_reschedule_log."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    route = store.get_route(route_id)
    if not route:
        return RedirectResponse("/sales/schedule?error=Jadwal%20tidak%20ditemukan", status_code=303)
    if user.get("role") == "sales" and route.get("sales_id") != user.get("id"):
        return RedirectResponse("/sales/schedule?error=Anda%20tidak%20berwenang%20reschedule%20jadwal%20ini", status_code=303)
    if not _parse_date_safe(new_date):
        return RedirectResponse("/sales/schedule?error=Tanggal%20baru%20tidak%20valid", status_code=303)
    if not reason.strip():
        return RedirectResponse("/sales/schedule?error=Alasan%20reschedule%20wajib%20diisi", status_code=303)
    store.reschedule_route(route_id, new_date, reason.strip(), user["id"])
    return RedirectResponse("/sales/schedule?rescheduled=1", status_code=303)

@app.get("/routing/monitoring", response_class=HTMLResponse)
async def routing_monitoring_page(request: Request):
    """Fase 5: dashboard progres routing & kunjungan untuk satu periode —
    leads di-route, status kunjungan (scheduled/visited/rescheduled/canceled),
    kapasitas sales, dan pencapaian target."""
    user = current_user(request)
    if not can(user, "router", "admin"):
        return RedirectResponse("/", status_code=303)
 
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = datetime.now().strftime("%Y-%m")
    year, mon = int(month[:4]), int(month[5:7])
 
    period = store.routing_period(month) or {}
    period_id = period.get("id")
 
    all_users = store.users()
    sales_users = [u for u in all_users if u.get("role") == "sales"]
 
    routes_this_month = [r for r in store.routes() if str(r.get("scheduled_date", ""))[:7] == month]
    status_counts = {"Scheduled": 0, "Visited": 0, "Canceled": 0}
    for r in routes_this_month:
        status = r.get("visit_status") or "Scheduled"
        status_counts[status] = status_counts.get(status, 0) + 1
 
    reschedule_count = sum(1 for log in store.reschedule_logs() if str(log.get("new_date", ""))[:7] == month)
 
    leads_routed_ids = {r.get("lead_id") for r in routes_this_month if r.get("lead_id")}
    total_leads = len(store.leads())
 
    routing_target = store.routing_target(month)
    routing_progress = min((len(leads_routed_ids) / routing_target) * 100, 100) if routing_target else 0
 
    basket_items = store.routing_basket(period_id) if period_id else []
 
    work_days = work_days_in_month(year, mon, _to_int(period.get("work_days_per_week"), 6))
    max_per_day = _to_int(period.get("max_visit_capacity_per_day"), 15)
    capacity_per_sales = work_days * max_per_day if period_id else 0
 
    per_sales = []
    for s in sales_users:
        s_routes = [r for r in routes_this_month if r.get("sales_id") == s["id"]]
        visited = sum(1 for r in s_routes if (r.get("visit_status") or "Scheduled") == "Visited")
        canceled = sum(1 for r in s_routes if (r.get("visit_status") or "Scheduled") == "Canceled")
        scheduled = sum(1 for r in s_routes if (r.get("visit_status") or "Scheduled") == "Scheduled")
        per_sales.append({
            "name": s.get("name") or s.get("username"),
            "total": len(s_routes), "visited": visited, "scheduled": scheduled, "canceled": canceled,
            "capacity_max": capacity_per_sales,
            "completion_rate": round((visited / len(s_routes) * 100), 1) if s_routes else 0,
        })
    per_sales.sort(key=lambda x: x["total"], reverse=True)
 
    return render(
        request, "router/routing_monitoring.html", title="Routing Visit — Dashboard Monitoring",
        target_month=month, period=period, status_counts=status_counts, reschedule_count=reschedule_count,
        total_visits=len(routes_this_month), leads_routed=len(leads_routed_ids), total_leads=total_leads,
        routing_target=routing_target, routing_progress=routing_progress, basket_total=len(basket_items),
        capacity_per_sales=capacity_per_sales, per_sales=per_sales,
    )

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
    lead_ids = [str(item) for item in form.getlist("lead_id")]
    if not lead_ids:
        return RedirectResponse("/routing?error=Pilih%20minimal%20satu%20lead", status_code=303)
    notes = str(form.get("notes") or "")
    month = str(form.get("month") or datetime.now().strftime("%Y-%m"))
    try:
        # 1. Simpan target frekuensi kunjungan per lead & tanggal kunjungan pertama (opsional)
        targets: dict[str, int | None] = {}
        first_dates: dict[str, str | None] = {}
        for lead_id in lead_ids:
            raw_target = form.get(f"visit_target_{lead_id}")
            targets[lead_id] = _to_int(raw_target, None) if raw_target not in (None, "") else None
            first_dates[lead_id] = str(form.get(f"scheduled_date_{lead_id}") or "").strip() or None
            if targets[lead_id] is not None:
                store.set_lead_visit_target(lead_id, targets[lead_id])

        # 2. Bangun ulang rencana kunjungan (kapasitas sales vs kebutuhan lead)
        rows = store.leads()
        build_routing_plan(rows, store.sales_quotas(), store.routes(), month)
        plan_by_lead = {row.get("id"): row for row in rows}

        # 3. Plot kunjungan: satu baris visit_routes per tanggal usulan (disebar tiap minggu)
        for lead_id in lead_ids:
            sales_id = str(form.get(f"sales_id_{lead_id}") or "")
            if not sales_id:
                raise ValueError(f"Sales wajib dipilih untuk lead {lead_id}")
            plan = plan_by_lead.get(lead_id, {})
            target = targets.get(lead_id) if targets.get(lead_id) is not None else _to_int(plan.get("visit_target_per_month"), 1)
            first = first_dates.get(lead_id)
            if first and first[:7] != month:
                raise ValueError(f"Tanggal kunjungan lead {plan.get('store_name') or lead_id} harus dalam bulan {month}")
            quota_dates = plan.get("quota_dates") or []
            if first:
                dates = suggest_visit_dates(month, target, quota_dates, first)
            else:
                dates = list(plan.get("suggested_dates") or [])
            if not dates:
                raise ValueError(f"Tidak ada tanggal kunjungan yang bisa diusulkan untuk {plan.get('store_name') or lead_id} (target sudah tercapai atau kuota sales penuh)")
            store.update_lead(lead_id, {"sales_id": sales_id, "routing_status": "Sudah diplot"})
            for visit_date in dates:
                store.assign_route(lead_id, sales_id, user["id"], visit_date, notes)
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
    except requests.HTTPError as exc:
        detail = exc.response.text[:300] if exc.response is not None and exc.response.text else str(exc)
        print(f"Toggle user Supabase error: {detail}")
        return RedirectResponse(f"/users?error={quote('Status user gagal diubah: ' + detail)}", status_code=303)
    except requests.RequestException as exc:
        print(f"Toggle user connection error: {exc}")
        return RedirectResponse(f"/users?error={quote('Tidak dapat terhubung ke Supabase saat mengubah status user.')}", status_code=303)
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


@app.get("/routing/export")
async def export_routing_csv(request: Request, month: str = ""):
    if not can(current_user(request), "admin", "router"):
        return RedirectResponse("/", status_code=303)
    routes = store.routes()
    if month and re.fullmatch(r"\d{4}-\d{2}", month):
        routes = [r for r in routes if str(r.get("scheduled_date", ""))[:7] == month]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Route ID", "Tanggal Visit", "Nama Toko", "Blok", "Lantai", "Los", "Nomor", "PIC", "No HP/WA", "Sales", "Status Visit", "Target Visit/Bulan (Lead)", "Frekuensi Kunjungan", "Catatan"])
    for route in routes:
        lead = route.get("leads") or {}
        sales = route.get("users") or {}
        writer.writerow([
            route.get("id", ""),
            route.get("scheduled_date") or "",
            lead.get("store_name") or "",
            lead.get("block") or "",
            lead.get("floor") or "",
            lead.get("los") or "",
            lead.get("nomor") or "",
            lead.get("pic_name") or "",
            lead.get("phone_number") or "",
            sales.get("full_name") or sales.get("username") or route.get("sales_id") or "",
            route.get("visit_status") or "Scheduled",
            lead.get("visit_target_per_month") or "",
            frequency_label(lead.get("visit_target_per_month")) if lead.get("visit_target_per_month") else "",
            route.get("notes") or "",
        ])
    suffix = month if month else "semua_periode"
    filename = f"data_routing_{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})
    if not can(current_user(request), "admin", "router"):
        return RedirectResponse("/", status_code=303)
    routes = store.routes()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Route ID", "Tanggal Visit", "Nama Toko", "Blok", "Lantai", "Los", "Nomor", "PIC", "No HP/WA", "Sales", "Status Visit", "Target Visit/Bulan (Lead)", "Frekuensi Kunjungan", "Catatan"])
    for route in routes:
        lead = route.get("leads") or {}
        sales = route.get("users") or {}
        writer.writerow([
            route.get("id", ""),
            route.get("scheduled_date") or "",
            lead.get("store_name") or "",
            lead.get("block") or "",
            lead.get("floor") or "",
            lead.get("los") or "",
            lead.get("nomor") or "",
            lead.get("pic_name") or "",
            lead.get("phone_number") or "",
            sales.get("full_name") or sales.get("username") or route.get("sales_id") or "",
            route.get("visit_status") or "Scheduled",
            lead.get("visit_target_per_month") or "",
            frequency_label(lead.get("visit_target_per_month")) if lead.get("visit_target_per_month") else "",
            route.get("notes") or "",
        ])
    filename = f"data_routing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})