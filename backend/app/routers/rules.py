from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import rule_service as svc

router = APIRouter(tags=["rules"])


class RuleCreateBody(BaseModel):
    title: str
    check_codes: list[str] | None = None
    params: dict[str, Any] | None = None


class PromoteBody(BaseModel):
    note: str | None = None


@router.get("/rules")
def list_rules():
    return svc.list_versions()


@router.get("/rules/promotions")
def list_promotions():
    return svc.list_promotions()


@router.post("/rules")
def create_rule(body: RuleCreateBody) -> dict[str, Any]:
    return svc.create_shadow_version(body.title, body.check_codes, body.params)


@router.post("/rules/{version_id}/promote")
def promote_rule(version_id: int, body: PromoteBody) -> dict[str, Any]:
    return svc.promote(version_id, body.note)
