"""Tenant scoping. Build a ``TenantScope`` only from a verified token, never from input."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Select

T = TypeVar("T")


@dataclass(frozen=True)
class TenantScope:
    """The tenant every query in a request is limited to.

    Created by ``get_current_user`` / ``get_tenant_scope`` from the access token. Query
    helpers take it as an argument so a tenant id can never come from a request body.
    """

    tenant_id: uuid.UUID

    def apply(self, stmt: Select[T], model: Any) -> Select[T]:  # noqa: ANN401 - any mapped class
        """Add ``WHERE model.tenant_id = :tenant_id``."""
        return stmt.where(model.tenant_id == self.tenant_id)
