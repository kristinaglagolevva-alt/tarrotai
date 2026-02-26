from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: str = "1") -> bool:
    raw = str(os.getenv(name, default)).strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class FeatureFlags:
    memory_v1: bool
    tickets_v2: bool
    nudges_v1: bool


FEATURE_FLAGS = FeatureFlags(
    memory_v1=_flag("FEATURE_MEMORY_V1", "1"),
    tickets_v2=_flag("FEATURE_TICKETS_V2", "1"),
    nudges_v1=_flag("FEATURE_NUDGES_V1", "1"),
)
