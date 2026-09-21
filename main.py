from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routers import auth, dashboard, data_entry, router as router_module, sales, admin, gsheets

app = FastAPI(
    title="Lion Parcel Tanah Abang Leads & Route API",
    description="Sistem pendataan leads & routing sales untuk area Tanah Abang",
    version="1.0.0"
)

# Include Routers
app.include_router(auth.router, prefix="/auth", tags=["Auth"])
app.include_router(dashboard.router, tags=["Dashboard"])
app.include_router(data_entry.router, tags=["Data Entry"])
app.include_router(router_module.router, tags=["Router Routing"])
app.include_router(sales.router, tags=["Sales"])
app.include_router(admin.router, tags=["Admin"])
app.include_router(gsheets.router, tags=["Google Sheets"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)