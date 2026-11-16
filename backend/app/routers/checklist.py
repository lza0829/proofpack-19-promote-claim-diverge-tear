from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import checklist_service as svc

router = APIRouter(tags=["checklist"])


class ChecklistRunBody(BaseModel):
    pack_id: int


@router.get("/checklist")
def list_checklist(pack_id: int | None = Query(default=None)):
    return svc.list_results(pack_id=pack_id)


@router.post("/checklist/run")
def run_checklist(body: ChecklistRunBody) -> dict[str, Any]:
    return svc.run_checklist(body.pack_id)
