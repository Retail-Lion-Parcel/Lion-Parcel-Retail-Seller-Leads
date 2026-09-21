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
    total_tonnage_kg = sum([float(l.get("tonnage_potential_kg") or 0) for l in leads])

    # Express courier breakdown
    courier_stats = {}
    for l in leads:
        c = l.get("current_courier", "Lainnya")
        courier_stats[c] = courier_stats.get(c, 0) + 1

    # Route status summary
    routes_res = supabase.table("visit_routes").select("visit_status").execute()
    routes = routes_res.data or []
    status_stats = {"Scheduled": 0, "Visited": 0, "Canceled": 0, "Rescheduled": 0}
    for r in routes:
        st = r.get("visit_status", "Scheduled")
        if st in status_stats:
            status_stats[st] += 1

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "total_leads": total_leads,
        "total_tonnage_ton": round(total_tonnage_kg / 1000, 2),
        "courier_stats": courier_stats,
        "status_stats": status_stats
    })