from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user
from app.database import get_supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

COURIER_OPTIONS = ["Lion Parcel", "Rayspeed Asia", "TLX", "JNT", "JNE", "Sicepat", "POS Indonesia", "Lain-lain"]

@router.get("/data-entry/new", response_class=HTMLResponse)
async def form_new_lead(request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["data_entry", "admin"]:
        return RedirectResponse(url="/dashboard")

    return templates.TemplateResponse("data_entry/form.html", {
        "request": request,
        "user": user,
        "couriers": COURIER_OPTIONS,
        "success": None
    })

@router.post("/data-entry/new")
async def save_lead(
    request: Request,
    visit_timestamp: str = Form(...),
    store_name: str = Form(...),
    block: str = Form(...),
    floor: str = Form(...),
    los: str = Form(""),
    pic_name: str = Form(...),
    pic_position: str = Form(...),
    phone_number: str = Form(...),
    data_entry_pic: str = Form(...),
    shipment_type: str = Form(...),
    current_courier: str = Form(...),
    top_country: str = Form(""),
    top_city: str = Form(""),
    tonnage_potential_kg: float = Form(0.0),
    tonnage_period: str = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] not in ["data_entry", "admin"]:
        return RedirectResponse(url="/dashboard")

    supabase = get_supabase()
    payload = {
        "visit_timestamp": visit_timestamp,
        "store_name": store_name,
        "block": block.upper(),
        "floor": floor.upper(),
        "los": los.upper() if los else None,
        "pic_name": pic_name,
        "pic_position": pic_position,
        "phone_number": phone_number,
        "data_entry_pic": data_entry_pic,
        "shipment_type": shipment_type,
        "current_courier": current_courier,
        "top_country": top_country,
        "top_city": top_city,
        "tonnage_potential_kg": tonnage_potential_kg,
        "tonnage_period": tonnage_period,
        "created_by": user["id"]
    }

    supabase.table("leads").insert(payload).execute()

    return templates.TemplateResponse("data_entry/form.html", {
        "request": request,
        "user": user,
        "couriers": COURIER_OPTIONS,
        "success": f"Pendataan Toko {store_name} berhasil disimpan!"
    })

@router.get("/data-entry/list", response_class=HTMLResponse)
async def list_leads(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login")

    supabase = get_supabase()
    res = supabase.table("leads").select("*").order("created_at", desc=True).execute()

    return templates.TemplateResponse("data_entry/list.html", {
        "request": request,
        "user": user,
        "leads": res.data or []
    })