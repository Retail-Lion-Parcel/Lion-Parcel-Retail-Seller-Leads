from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user
from app.database import get_supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/sales/schedule", response_class=HTMLResponse)
async def sales_schedule(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login")

    supabase = get_supabase()

    # Fetch assigned routes for this sales
    query = supabase.table("visit_routes").select("*, leads(*)").order("scheduled_date", desc=True)
    if user["role"] == "sales":
        query = query.eq("sales_id", user["id"])

    res = query.execute()

    return templates.TemplateResponse("sales/schedule.html", {
        "request": request,
        "user": user,
        "routes": res.data or []
    })

@router.post("/sales/update-status")
async def update_visit_status(
    request: Request,
    route_id: str = Form(...),
    visit_status: str = Form(...),
    notes: str = Form("")
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login")

    supabase = get_supabase()
    supabase.table("visit_routes").update({
        "visit_status": visit_status,
        "notes": notes
    }).eq("id", route_id).execute()

    return RedirectResponse(url="/sales/schedule", status_code=status.HTTP_303_SEE_OTHER)