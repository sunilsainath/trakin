"""Versioned API surface.

Import the concrete router modules (not the names) so a partially-initialised
package is not re-entered while it is still being built.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai as ai_module,
)
from app.api.v1 import (
    auth as auth_module,
)
from app.api.v1 import (
    code as code_module,
)
from app.api.v1 import (
    companies as companies_module,
)
from app.api.v1 import (
    documents as documents_module,
)
from app.api.v1 import (
    finance as finance_module,
)
from app.api.v1 import (
    health as health_module,
)
from app.api.v1 import (
    identity as identity_module,
)
from app.api.v1 import (
    insights as insights_module,
)
from app.api.v1 import (
    messages as messages_module,
)
from app.api.v1 import (
    msas as msas_module,
)
from app.api.v1 import (
    search as search_module,
)
from app.api.v1 import (
    social as social_module,
)
from app.api.v1 import (
    work as work_module,
)

api_router = APIRouter(prefix="/api/v1")

# Health first: a load balancer must never depend on the database.
api_router.include_router(health_module.router)
api_router.include_router(auth_module.router)
api_router.include_router(identity_module.router)
api_router.include_router(companies_module.router)

# CODE: projects -> roles -> SOW -> contract -> contract roles
api_router.include_router(code_module.router)

# WORK: assignments, timesheets, leave
api_router.include_router(work_module.router)

# BILLING + PAYMENTS
api_router.include_router(finance_module.router)

# Documents and MSAs
api_router.include_router(documents_module.router)
api_router.include_router(msas_module.router)

# Notifications, dashboard, AI domain intelligence
api_router.include_router(insights_module.router)

# Professional network + messaging
api_router.include_router(social_module.router)
api_router.include_router(messages_module.router)

api_router.include_router(search_module.router)
api_router.include_router(ai_module.router)

__all__ = ["api_router"]
