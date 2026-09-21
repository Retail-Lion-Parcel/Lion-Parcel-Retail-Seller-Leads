import os
import gspread
from google.oauth2.service_account import Credentials
from app.config import settings
from app.database import get_supabase

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def get_gsheet_client():
    # Membaca file credentials.json
    if os.path.exists(settings.GOOGLE_SHEETS_CREDENTIALS_JSON):
        creds = Credentials.from_service_account_file(
            settings.GOOGLE_SHEETS_CREDENTIALS_JSON, 
            scopes=SCOPES
        )
        return gspread.authorize(creds)
    else:
        raise FileNotFoundError(f"File {settings.GOOGLE_SHEETS_CREDENTIALS_JSON} tidak ditemukan.")

def sync_leads_and_routes_to_gsheet():
    """Mengkloning seluruh data Leads dan Routing dari Supabase ke Google Sheets"""
    gc = get_gsheet_client()
    
    if not settings.GOOGLE_SHEET_ID or settings.GOOGLE_SHEET_ID == "MASUKKAN_ID_SPREADSHEET_ANDA_DI_SINI":
        raise ValueError("GOOGLE_SHEET_ID belum dikonfigurasi di file .env")

    sheet = gc.open_by_key(settings.GOOGLE_SHEET_ID)
    supabase = get_supabase()

    # 1. Sync Leads Sheet
    leads_res = supabase.table("leads").select("*").order("created_at", desc=True).execute()
    leads_data = leads_res.data or []

    try:
        ws_leads = sheet.worksheet("Leads Tanah Abang")
        ws_leads.clear()
    except gspread.WorksheetNotFound:
        ws_leads = sheet.add_worksheet(title="Leads Tanah Abang", rows="1000", cols="20")

    leads_headers = [
        "ID", "Timestamp Visit", "Nama Toko", "Blok", "Lantai", "Los",
        "PIC Toko", "Jabatan PIC", "Nomor HP/WA", "PIC Data Entry",
        "Jenis Kiriman", "Ekspedisi Saat Ini", "Negara Terbanyak",
        "Kota Terbanyak", "Potensi Tonase (KG)", "Periode", "Jumlah Visit", "Tgl Input"
    ]
    
    leads_rows = [leads_headers]
    for row in leads_data:
        leads_rows.append([
            str(row.get("id")),
            str(row.get("visit_timestamp")),
            row.get("store_name"),
            row.get("block"),
            row.get("floor"),
            row.get("los") or "-",
            row.get("pic_name"),
            row.get("pic_position"),
            row.get("phone_number"),
            row.get("data_entry_pic"),
            row.get("shipment_type"),
            row.get("current_courier"),
            row.get("top_country") or "-",
            row.get("top_city") or "-",
            float(row.get("tonnage_potential_kg") or 0),
            row.get("tonnage_period"),
            row.get("visit_count", 0),
            str(row.get("created_at"))
        ])
    ws_leads.update(leads_rows)

    # 2. Sync Routing Sheet
    routes_res = supabase.table("visit_routes").select(
        "*, leads(store_name, block, floor, los, phone_number), users!visit_routes_sales_id_fkey(full_name)"
    ).execute()
    routes_data = routes_res.data or []

    try:
        ws_routes = sheet.worksheet("Routing Sales")
        ws_routes.clear()
    except gspread.WorksheetNotFound:
        ws_routes = sheet.add_worksheet(title="Routing Sales", rows="1000", cols="15")

    routes_headers = [
        "Route ID", "Tanggal Visit Scheduled", "Nama Toko", "Lokasi (Blok/Lantai/Los)",
        "Sales Assigned", "No HP/WA", "Status Visit", "Catatan"
    ]
    routes_rows = [routes_headers]
    for r in routes_data:
        lead = r.get("leads") or {}
        sales = r.get("users") or {}
        location = f"Blok {lead.get('block', '')} Lt. {lead.get('floor', '')} Los {lead.get('los', '-')}"
        routes_rows.append([
            str(r.get("id")),
            str(r.get("scheduled_date")),
            lead.get("store_name", "-"),
            location,
            sales.get("full_name", "-"),
            lead.get("phone_number", "-"),
            r.get("visit_status"),
            r.get("notes") or ""
        ])
    ws_routes.update(routes_rows)

    return True