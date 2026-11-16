from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import shadow_service as svc

router = APIRouter(tags=["shadow"])


class ShadowRunBody(BaseModel):
    pack_id: int
    rule_version_id: int
    note: str | None = None


@router.get("/shadow")
def list_shadow(
    pack_id: int | None = Query(default=None),
    rule_version_id: int | None = Query(default=None),
):
    return svc.list_runs(pack_id=pack_id, rule_version_id=rule_version_id)


@router.post("/shadow/run")
def run_shadow(body: ShadowRunBody) -> dict[str, Any]:
    return svc.run_shadow(body.pack_id, body.rule_version_id, body.note)
