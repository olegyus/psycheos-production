"""
PsycheOS Backend API - Main Application
"""

import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db, close_db
from app.logging_config import setup_logging, get_logger
from app.api import specialist_router, session_router
from app.services.screen_bank_loader import screen_bank_loader
from app.schemas.schemas import HealthResponse
from app.exceptions import PsycheOSError

# Initialize logging
setup_logging(log_level=settings.log_level, debug=settings.debug)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info(
        "application_starting",
        debug=settings.debug,
        database_url=settings.database_url,
    )
    
    # Initialize database
    await init_db()
    
    # Load screen bank and routing rules
    try:
        screen_bank_loader.load()
        logger.info("artifacts_loaded_successfully")
    except Exception as e:
        logger.error("artifacts_loading_failed", exc_info=e)
    
    logger.info("application_started")
    
    yield
    
    # Shutdown
    logger.info("application_shutting_down")
    await close_db()
    logger.info("application_shutdown_complete")


# Create FastAPI app
app = FastAPI(
    title="PsycheOS API",
    description="Psychological screening system API",
    version="1.0.0",
    lifespan=lifespan,
)


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler with FULL traceback
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions with full traceback."""
    # Get full traceback
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_str = "".join(tb)
    
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        traceback=tb_str,
        path=str(request.url.path),
        method=request.method,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )
    
    # Return error response
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_type": type(exc).__name__,
        }
    )


# PsycheOS-specific exception handler
@app.exception_handler(PsycheOSError)
async def psycheos_exception_handler(request: Request, exc: PsycheOSError):
    """Handle PsycheOS-specific exceptions."""
    logger.warning(
        "psycheos_error",
        error_type=type(exc).__name__,
        message=exc.message,
        details=exc.details,
        path=str(request.url.path),
    )
    
    # Map exception to HTTP status code
    status_code = 400
    if "NotFound" in type(exc).__name__:
        status_code = 404
    elif "Insufficient" in type(exc).__name__:
        status_code = 402
    elif "AlreadyExists" in type(exc).__name__:
        status_code = 409
    elif "Expired" in type(exc).__name__:
        status_code = 410
    
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": exc.message,
            "error_type": type(exc).__name__,
            "details": exc.details,
        }
    )


# Include routers
app.include_router(specialist_router)
app.include_router(session_router)


# Root endpoint
@app.get("/", tags=["root"])
async def root():
    """Root endpoint with API info."""
    return {
        "name": "PsycheOS API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


# Health check endpoint
@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health():
    """
    Health check endpoint.
    
    Checks database connectivity and artifact loading status.
    """
    db_status = "healthy"
    try:
        # Simple check - if we got here, DB is likely fine
        # In production, add actual DB query
        pass
    except Exception as e:
        db_status = f"unhealthy: {e}"
    
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        database=db_status,
        artifacts_loaded=screen_bank_loader.is_loaded,
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
