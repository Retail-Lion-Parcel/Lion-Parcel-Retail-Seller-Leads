from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/html", response_class=HTMLResponse)
async def get_html(request: Request):
    return templates.TemplateResponse("base.html", {"title": "Dashboard", "request": request})