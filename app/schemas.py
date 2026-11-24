from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReviewRequest(BaseModel):
    title: str = Field(default="未命名稿件", max_length=120)
    text: str = Field(min_length=1, max_length=30000)
    reference: str = Field(default="", max_length=20000)
    instructions: str = Field(default="", max_length=3000)
    use_ai: bool = True

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请输入稿件正文")
        return value


class ConfigRequest(BaseModel):
    key: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_-]*$")
    model: str = Field(default="deepseek-flash", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    vision_model: str = Field(default="deepseek-flash", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    severity: Literal["high", "medium", "low"]
    category: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1, max_length=1500)
    suggestion: str = Field(min_length=1, max_length=1500)
    source_id: str = Field(min_length=1, max_length=80)
    needs_verification: bool = True


class AIReport(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    issues: list[Finding] = Field(max_length=100)


class VideoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_profile: Literal["central", "news", "government"] = "central"
    freeze_check: bool = True
    audio_check: bool = True
    scene_check: bool = True
    caption_check: bool = True
    alignment_check: bool = True
    vision_limit: Literal[24, 48, 96] = 48
    freeze_seconds: float = Field(default=3, ge=1, le=30, allow_inf_nan=False)
    safe_margin: float = Field(default=0.05, ge=0.01, le=0.15, allow_inf_nan=False)
    min_text_height: float = Field(default=0.025, ge=0.01, le=0.08, allow_inf_nan=False)
    max_cps: float = Field(default=9, ge=3, le=25, allow_inf_nan=False)
    target_lufs: float = Field(default=-23, ge=-36, le=-9, allow_inf_nan=False)
    loudness_tolerance: float = Field(default=3, ge=1, le=12, allow_inf_nan=False)
    expected_aspect: Literal["any", "16:9", "9:16", "1:1"] = "any"
    glossary: str = Field(default="", max_length=3000)

    @field_validator("glossary")
    @classmethod
    def validate_glossary(cls, text):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 50:
            raise ValueError("易错词最多50条")
        seen = set()
        for line in lines:
            parts = [p.strip() for p in line.split("=>")]
            if len(parts) != 2 or any(not p or len(p) > 60 for p in parts) or parts[0] == parts[1] or parts[0] in seen:
                raise ValueError("易错词格式应为：错误写法=>正确写法；错误写法不得重复")
            seen.add(parts[0])
        return text


class DecisionRequest(BaseModel):
    status: Literal["pending", "resolved", "dismissed"]
    note: str = Field(default="", max_length=1000)
