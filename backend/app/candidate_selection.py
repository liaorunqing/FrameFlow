from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class CandidateScore(BaseModel):
    path: str
    automatic_score: int = Field(ge=0, le=100)
    product_consistency: int = Field(default=0, ge=0, le=100)
    hand_physics: int = Field(default=0, ge=0, le=100)
    story_match: bool = False
    unexpected_cuts: int = 0
    actual_cost_cny: float = 0
    disqualified: bool = False
    disqualification_reason: str = ""

    @property
    def selection_score(self) -> float:
        if self.disqualified or not self.story_match or self.unexpected_cuts:
            return -1
        return round(
            self.automatic_score * 0.40
            + self.product_consistency * 0.40
            + self.hand_physics * 0.20,
            2,
        )


class CandidateSelection(BaseModel):
    selected_path: str = ""
    selected_score: float = -1
    requires_human_review: bool = True
    candidates: list[CandidateScore]
    reason: str


def select_candidate(candidates: list[CandidateScore]) -> CandidateSelection:
    """Rank already-paid candidates without triggering a new provider call."""
    if not candidates:
        return CandidateSelection(candidates=[], reason="没有候选片段。")
    for candidate in candidates:
        if not Path(candidate.path).is_file():
            candidate.disqualified = True
            candidate.disqualification_reason = "候选文件不存在"
    ranked = sorted(candidates, key=lambda item: item.selection_score, reverse=True)
    winner = ranked[0]
    if winner.selection_score < 0:
        return CandidateSelection(
            candidates=ranked,
            reason="所有候选均触发事实、连续性或文件完整性阻断。",
        )
    return CandidateSelection(
        selected_path=winner.path,
        selected_score=winner.selection_score,
        candidates=ranked,
        reason="按商品一致性、动作物理与故事匹配加权选出；仍需人工终审。",
    )
