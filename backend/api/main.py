"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database.db import init_db, seed_retailers
from api.routes import trends, products, retailers, reports, scrape_jobs, aldi
import structlog

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup_begin")
    await init_db()
    await seed_retailers()
    log.info("startup_complete")
    yield
    log.info("shutdown")


app = FastAPI(
    title="Retail Trend Tracker",
    description="Analyses home décor & storage products across 40+ retailers to identify trends.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://frontend:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trends.router, prefix="/api/trends", tags=["Trends"])
app.include_router(products.router, prefix="/api/products", tags=["Products"])
app.include_router(retailers.router, prefix="/api/retailers", tags=["Retailers"])
app.include_router(reports.router, prefix="/api/reports", tags=["Reports"])
app.include_router(scrape_jobs.router, prefix="/api/scrape-jobs", tags=["Scrape Jobs"])
app.include_router(aldi.router, prefix="/api/aldi", tags=["Aldi"])


@app.get("/health")
async def health():
    return {"status": "ok"}
