from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.core.associations.schemas_associations import Association
from app.modules.calendar.types_calendar import Decision


# Schema de base. Contiens toutes les données communes à tous les schemas
class EventBase(BaseModel):
    name: str
    start: datetime
    end: datetime
    all_day: bool
    location: str
    description: str
    recurrence_rule: str | None = None

    association_id: UUID


class EventComplete(EventBase):
    id: UUID
    association: Association
    decision: Decision


class EventEdit(BaseModel):
    name: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool | None = None
    location: str | None = None
    description: str | None = None
    recurrence_rule: str | None = None


class IcalSecret(BaseModel):
    secret: str
