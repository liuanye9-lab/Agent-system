from __future__ import annotations

from typing import Annotated

from fastapi import Query


DEFAULT_QUERY_LIMIT = 50
MAX_QUERY_LIMIT = 200

LimitQuery = Annotated[int, Query(ge=1, le=MAX_QUERY_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]
