from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user
from app.database import get_supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login")

    supabase = get_supabase()

    # Dynamic Dashboard Analytics
    leads_res = supabase.table("leads").select("*").execute()
    leads = leads_res.data or []

    total_leads = len(leads)

    # Normalize tonnage to a monthly figure based on tonnage_period
    # (Hari -> x30, Bulan -> x1, Tahun -> /12)
    period_multiplier = {"Hari": 30, "Bulan": 1, "Tahun": 1 / 12}
    total_tonnage_kg = 0.0
    total_tonnage_month_kg = 0.0
    for l in leads:
        kg = float(l.get("tonnage_potential_kg") or 0)
        total_tonnage_kg += kg
        period = l.get("tonnage_period") or "Bulan"
        total_tonnage_month_kg += kg * period_multiplier.get(period, 1)

    # Express courier breakdown
    courier_stats = {}
    for l in leads:
        c = l.get("current_courier", "Lainnya")
        courier_stats[c] = courier_stats.get(c, 0) + 1

    # Route status summary + routed leads (distinct lead_id in visit_routes)
    routes_res = supabase.table("visit_routes").select("lead_id, visit_status").execute()
    routes = routes_res.data or []
    status_stats = {"Scheduled": 0, "Visited": 0, "Canceled": 0, "Rescheduled": 0}
    routed_lead_ids = set()
    for r in routes:
        st = r.get("visit_status", "Scheduled")
        if st in status_stats:
            status_stats[st] += 1
        if r.get("lead_id") is not None:
            routed_lead_ids.add(r["lead_id"])

    leads_routed = len(routed_lead_ids)
    leads_visited = status_stats["Visited"]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "total_leads": total_leads,
        "total_tonnage_ton": round(total_tonnage_kg / 1000, 2),
        "total_tonnage_month_ton": round(total_tonnage_month_kg / 1000, 2),
        "leads_routed": leads_routed,
        "leads_visited": leads_visited,
        "courier_stats": courier_stats,
        "status_stats": status_stats
    })