"""Typed contracts. Everything the AI returns is validated here before it is used."""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

MoveType = Literal["studio", "apartment", "house", "office"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PartialJob(BaseModel):
    """Fields a customer can state in chat. Everything optional; each field is validated on its own."""
    move_type: Optional[MoveType] = None
    rooms: Optional[int] = Field(None, ge=1, le=14)
    boxes: Optional[int] = Field(None, ge=0, le=1000)
    large_furniture: Optional[int] = Field(None, ge=0, le=100)
    appliances: Optional[int] = Field(None, ge=0, le=30)
    heavy_items: Optional[int] = Field(None, ge=0, le=3)
    fragile_items: Optional[int] = Field(None, ge=0, le=100)
    disassembly_items: Optional[int] = Field(None, ge=0, le=50)
    floors_origin: Optional[int] = Field(None, ge=0, le=30)
    floors_dest: Optional[int] = Field(None, ge=0, le=30)
    elevator_origin: Optional[int] = Field(None, ge=0, le=1)
    elevator_dest: Optional[int] = Field(None, ge=0, le=1)
    distance_km: Optional[float] = Field(None, gt=0, le=3000)
    packing_service: Optional[int] = Field(None, ge=0, le=1)


JOB_FIELDS = list(PartialJob.model_fields)


class ChatIn(BaseModel):
    session_id: Optional[str] = Field(None, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class RejectIn(BaseModel):
    reason: str = Field("", max_length=300)


class CompleteIn(BaseModel):
    actual_hours: float = Field(gt=0, le=72)
    waive_overtime: bool = False


class AdvanceIn(BaseModel):
    days: float = Field(gt=0, le=60)


class CopilotTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class CopilotIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[CopilotTurn] = Field(default_factory=list, max_length=20)


class RemindIn(BaseModel):
    tone: Literal["friendly", "firm", "final"] = "friendly"
