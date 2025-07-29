import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.groups.groups_type import AccountType
from app.core.schools import cruds_schools
from app.core.schools.schools_type import SchoolType
from app.core.utils.config import Settings


async def get_account_type_and_school_id_from_email(
    email: str,
    db: AsyncSession,
    settings: Settings,
) -> tuple[AccountType, UUID]:
    """Return the account type from the email"""
    if (
        settings.school.staff_email_regex is not None
        and settings.school.staff_email_regex.match(
            email,
        )
    ):
        return AccountType.staff, SchoolType.centrale_lyon.value
    if settings.school.student_email_regex.match(
        email,
    ):
        return AccountType.student, SchoolType.centrale_lyon.value
    if (
        settings.school.former_student_email_regex is not None
        and settings.school.former_student_email_regex.match(
            email,
        )
    ):
        return AccountType.former_student, SchoolType.centrale_lyon.value
    schools = await cruds_schools.get_schools(db)

    schools = [school for school in schools if school.id not in SchoolType.list()]
    school = next(
        (school for school in schools if re.match(school.email_regex, email)),
        None,
    )
    if school:
        return AccountType.other_school_student, school.id
    return AccountType.external, SchoolType.no_school.value
