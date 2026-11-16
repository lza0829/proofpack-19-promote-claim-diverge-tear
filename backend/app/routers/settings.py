from fastapi import APIRouter

from app.services import release_service as svc

router = APIRouter(tags=["settings"])


@router.get("/settings")
def list_settings():
    return svc.list_settings()
