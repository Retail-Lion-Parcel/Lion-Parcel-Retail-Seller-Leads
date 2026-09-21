from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse
from app.auth import get_current_user
from app.services.gsheets_service import sync_leads_and_routes_to_gsheet

router = APIRouter()

@router.get("/gsheets/sync")
async def sync_gsheets(request: Request):
    user = get_current_user(request)
    if not user or user["role"] not in ["admin", "router"]:
        return RedirectResponse(url="/dashboard")

    try:
        sync_leads_and_routes_to_gsheet()
        return RedirectResponse(url="/dashboard?sync=success", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        print(f"GSheets Sync Error: {e}")
        return RedirectResponse(url="/dashboard?sync=error", status_code=status.HTTP_303_SEE_OTHER)