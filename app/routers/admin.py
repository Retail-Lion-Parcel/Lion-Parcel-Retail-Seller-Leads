from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_password_hash
from app.database import get_supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/admin/users", response_class=HTMLResponse)
async def manage_users(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/dashboard")

    supabase = get_supabase()
    res = supabase.table("users").select("*").order("created_at", desc=True).execute()

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "user": user,
        "users_list": res.data or []
    })

@router.post("/admin/users/create")
async def create_user(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/dashboard")

    supabase = get_supabase()
    hashed_pwd = get_password_hash(password)

    payload = {
        "username": username,
        "full_name": full_name,
        "password_hash": hashed_pwd,
        "role": role,
        "is_active": True
    }
    supabase.table("users").insert(payload).execute()

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/admin/users/toggle-status")
async def toggle_user_status(request: Request, user_id: str = Form(...), current_status: bool = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/dashboard")

    supabase = get_supabase()
    supabase.table("users").update({"is_active": not current_status}).eq("id", user_id).execute()

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)