import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.associations import cruds_associations
from app.core.associations.models_associations import CoreAssociation
from app.core.feed import cruds_feed, models_feed
from app.core.feed.types_feed import NewsStatus
from app.core.groups.groups_type import AccountType, GroupType
from app.core.schools.schools_type import SchoolType
from app.core.users import cruds_users
from app.core.users.models_users import CoreUser
from app.core.utils import security
from app.modules.advert import cruds_advert, models_advert
from app.modules.calendar import cruds_calendar, models_calendar
from app.modules.calendar.types_calendar import Decision


async def create_data(db: AsyncSession):
    viewer_user_id = str(uuid.uuid4())
    user = CoreUser(
        id=viewer_user_id,
        password_hash=security.get_password_hash(password="password"),
        firstname="Prénom",
        nickname=None,
        name="Nom",
        email="prenom.nom@edu.em-lyon.fr",
        floor=None,
        phone=None,
        promo=None,
        school_id=SchoolType.base_school.value,
        account_type=AccountType.student,
        birthday=None,
        created_on=datetime.now(tz=UTC),
    )
    await cruds_users.create_user(db=db, user=user)

    commuz_association_id = uuid.uuid4()
    cheerup_association_id = uuid.uuid4()
    # await cruds_associations.create_association(
    #     db=db,
    #     association=CoreAssociation(
    #         id=uuid.uuid4(),
    #         name="Admin",
    #         group_id=GroupType.admin.value,
    #     ),
    # )
    logo_folder = "data/associations/logos/"
    await cruds_associations.create_association(
        db=db,
        association=CoreAssociation(
            id=commuz_association_id,
            name="Commuz'",
            group_id=GroupType.admin.value,
        ),
    )
    shutil.copyfile(
        "app/utils/screenshots/commuz_logo.png",
        f"{logo_folder}{commuz_association_id}.png",
    )
    await cruds_associations.create_association(
        db=db,
        association=CoreAssociation(
            id=cheerup_association_id,
            name="CheerUp",
            group_id=GroupType.admin.value,
        ),
    )

    ouverture_des_castings_advert_id = uuid.uuid4()
    ouverture_des_castings_advert = models_advert.Advert(
        title="Ouverture des castings 🎭",
        content="Vous voulez rejoindre la folle aventure de la Commuz' ?",
        id=ouverture_des_castings_advert_id,
        date=datetime.now(UTC) - timedelta(days=2),
        advertiser_id=commuz_association_id,
        post_to_feed=True,
    )
    await cruds_advert.create_advert(
        db=db,
        db_advert=ouverture_des_castings_advert,
    )
    advert_directory = "advert"
    Path(f"data/{advert_directory}").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        "app/utils/screenshots/commuz.png",
        f"data/{advert_directory}/{ouverture_des_castings_advert_id}.png",
    )
    news = models_feed.News(
        id=uuid.uuid4(),
        title=ouverture_des_castings_advert.title,
        start=ouverture_des_castings_advert.date,
        end=None,
        entity="Commuz'",
        location=None,
        action_start=None,
        module="advert",
        module_object_id=ouverture_des_castings_advert_id,
        image_directory=advert_directory,
        image_id=ouverture_des_castings_advert_id,
        status=NewsStatus.PUBLISHED,
    )
    await cruds_feed.create_news(news=news, db=db)
    ccc_id = uuid.uuid4()
    ccc = models_calendar.Event(
        id=ccc_id,
        name="Course contre le cancer",
        association_id=cheerup_association_id,
        applicant_id=viewer_user_id,
        start=datetime.now(UTC),
        end=datetime.now(UTC) + timedelta(days=3),
        all_day=True,
        location="emlyon",
        description=None,
        decision=Decision.approved,
        recurrence_rule=None,
        ticket_url_opening=None,
        ticket_url=None,
    )
    await cruds_calendar.add_event(db, ccc)
    calendar_directory = "calendar"
    Path(f"data/{calendar_directory}").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        "app/utils/screenshots/ccc.jpeg",
        f"data/{calendar_directory}/{ccc_id}.jpeg",
    )
    news = models_feed.News(
        id=uuid.uuid4(),
        title=ccc.name,
        start=ccc.start,
        end=ccc.end,
        entity="CheerUp",
        location=None,
        action_start=None,
        module="calendar",
        module_object_id=ccc_id,
        image_directory=calendar_directory,
        image_id=ccc_id,
        status=NewsStatus.PUBLISHED,
    )
    await cruds_feed.create_news(news=news, db=db)
