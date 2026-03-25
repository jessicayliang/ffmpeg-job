import re
from typing import Optional

from pydantic import BaseModel, field_validator


class Clip(BaseModel):
    start: str  # "HH:MM:SS" or "SS.mmm"
    end: str
    label: Optional[str] = None

    @field_validator("start", "end")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        if re.match(r"^\d+(\.\d+)?$", v):
            return v
        if re.match(r"^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$", v):
            return v
        raise ValueError(f"Invalid timestamp format: {v!r}. Use HH:MM:SS or seconds.")


class ClipRequest(BaseModel):
    drive_url: str    # Public Google Drive share URL
    clips: list[Clip] # One or more time ranges