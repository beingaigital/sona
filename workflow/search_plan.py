"""Search plan contract (v1).

This module provides a typed, JSON-serializable contract for the "search plan"
artifact used in the event analysis workflow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


SearchPlanVersion = Literal["search_plan_v1"]


@dataclass(slots=True)
class KeywordGroup:
    name: str
    keywords: List[str] = field(default_factory=list)


@dataclass(slots=True)
class SearchPlanV1:
    """Executable search plan derived from query + reference snippets."""

    version: SearchPlanVersion = "search_plan_v1"
    eventIntroduction: str = ""
    searchWords: List[str] = field(default_factory=list)
    timeRange: str = ""

    keywordGroups: List[KeywordGroup] = field(default_factory=list)
    secondaryKeywords: List[str] = field(default_factory=list)
    queryTemplates: List[str] = field(default_factory=list)
    verificationChecklist: List[str] = field(default_factory=list)
    evidenceSnippets: List[str] = field(default_factory=list)

    # Best-effort meta (may vary by data source)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        obj = asdict(self)
        # Flatten dataclass list
        obj["keywordGroups"] = [asdict(g) for g in self.keywordGroups]
        return obj


def coerce_search_plan_v1(obj: Dict[str, Any]) -> Optional[SearchPlanV1]:
    """Best-effort coercion from loose dict to SearchPlanV1."""
    if not isinstance(obj, dict):
        return None
    version = str(obj.get("version", "") or "").strip() or "search_plan_v1"
    if version != "search_plan_v1":
        return None
    groups_raw = obj.get("keywordGroups")
    groups: List[KeywordGroup] = []
    if isinstance(groups_raw, list):
        for item in groups_raw[:12]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            kws = item.get("keywords") if isinstance(item.get("keywords"), list) else []
            kws_clean = [str(x).strip() for x in kws if str(x).strip()]
            if name and kws_clean:
                groups.append(KeywordGroup(name=name, keywords=kws_clean[:24]))

    meta: Dict[str, Any] = {}
    if isinstance(obj.get("_weibo_meta"), dict):
        meta["weibo"] = obj["_weibo_meta"]

    return SearchPlanV1(
        version="search_plan_v1",
        eventIntroduction=str(obj.get("eventIntroduction", "") or ""),
        searchWords=[str(x).strip() for x in (obj.get("searchWords") or []) if str(x).strip()][:24],
        timeRange=str(obj.get("timeRange", "") or ""),
        keywordGroups=groups,
        secondaryKeywords=[str(x).strip() for x in (obj.get("secondaryKeywords") or []) if str(x).strip()][:24],
        queryTemplates=[str(x).strip() for x in (obj.get("queryTemplates") or []) if str(x).strip()][:24],
        verificationChecklist=[
            str(x).strip() for x in (obj.get("verificationChecklist") or []) if str(x).strip()
        ][:24],
        evidenceSnippets=[str(x).strip() for x in (obj.get("evidenceSnippets") or []) if str(x).strip()][:24],
        meta=meta,
    )

