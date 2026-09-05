from fastapi import APIRouter
from app.web_admin_auth import router as web_admin_auth_router
from app.web_admin_readonly import router as web_admin_router

router=APIRouter(prefix="/api")
router.include_router(web_admin_auth_router)
router.include_router(web_admin_router)
