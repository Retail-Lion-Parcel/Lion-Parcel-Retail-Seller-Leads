from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user
from app.database import get_supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/routing", response_class=HTMLResponse)
async def routing_page(request: Request, selected_lead_id: str = None):
    user = get_current_user(request)
    if not user or user["role"] not in ["router", "admin"]:
        return RedirectResponse(url="/dashboard")

    supabase = get_supabase()

    # Get all unrouted / active leads
    leads_res = supabase.table("leads").select("*").order("block").order("floor").order("los").execute()
    all_leads = leads_res.data or []

    # Get Sales list
    sales_res = supabase.table("users").select("id, full_name").eq("role", "sales").eq("is_active", True).execute()
    sales_users = sales_res.data or []

    # Recommendation Clustering Engine based on same Block, Floor, & Los
    recommendations = {}
    selected_lead = None

    if selected_lead_id:
        sel_res = supabase.table("leads").select("*").eq("id", selected_lead_id).execute()
        if sel_res.data:
            selected_lead = sel_res.data[0]
            # Recommendation query: match block & floor
            rec_query = supabase.table("leads").select("*") \
                .eq("block", selected_lead["block"]) \
                .eq("floor", selected_lead["floor"]) \
                .neq("id", selected_lead_id)
            
            if selected_lead.get("los"):
                rec_query = rec_query.eq("los", selected_lead["los"])

            rec_res = rec_query.execute()
            recommendations = rec_res.data or []

    # Get recent schedules
    routes_res = supabase.table("visit_routes").select("*, leads(store_name, block, floor, los), users!visit_routes_sales_id_fkey(full_name)").order("scheduled_date", desc=True).limit(20).execute()

    return templates.TemplateResponse("router/routing.html", {
        "request": request,
        "user": user,
        "leads": all_leads,
        "sales_users": sales_users,
        "selected_lead": selected_lead,
        "recommendations": recommendations,
        "routes": routes_res.data or []
    })

@router.post("/routing/assign")
async def assign_route(
    request: Request,
    lead_id: str = Form(...),
    sales_id: str = Form(...),
    scheduled_date: str = Form(...),
    notes: str = Form("")
):
    user = get_current_user(request)
    if not user or user["role"] not in ["router", "admin"]:
        return RedirectResponse(url="/dashboard")

    supabase = get_supabase()

    # 1. Insert route
    route_payload = {
        "lead_id": lead_id,
        "sales_id": sales_id,
        "router_id": user["id"],
        "scheduled_date": scheduled_date,
        "notes": notes,
        "visit_status": "Scheduled"
    }
    supabase.table("visit_routes").insert(route_payload).execute()

    # 2. Increment lead visit_count
    lead_res = supabase.table("leads").select("visit_count").eq("id", lead_id).execute()
    if lead_res.data:
        curr_count = lead_res.data[0].get("visit_count") or 0
        supabase.table("leads").update({"visit_count": curr_count + 1}).eq("id", lead_id).execute()

    return RedirectResponse(url="/routing?success=1", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/routing/update-visit-count")
async def update_visit_count(request: Request, lead_id: str = Form(...), new_count: int = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] not in ["router", "admin"]:
        return RedirectResponse(url="/dashboard")

    supabase = get_supabase()
    supabase.table("leads").update({"visit_count": new_count}).eq("id", lead_id).execute()
    return RedirectResponse(url="/routing", status_code=status.HTTP_303_SEE_OTHER)