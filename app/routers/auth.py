from fastapi import APIRouter, Request, Form, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import verify_password, create_access_token, get_current_user
from app.database import get_supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login")
async def login_action(request: Request, response: Response, username: str = Form(...), password: str = Form(...)):
    supabase = get_supabase()
    res = supabase.table("users").select("*").eq("username", username).execute()
    
    if not res.data:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Username atau Password salah."})
    
    user = res.data[0]
    if not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Username atau Password salah."})
    
    if not user.get("is_active"):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Akun Anda telah dinonaktifkan."})

    token = create_access_token({"sub": str(user["id"]), "role": user["role"]})
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, max_age=43200)
    return resp

@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie("access_token")
    return resp