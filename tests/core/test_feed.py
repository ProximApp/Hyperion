import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from fastapi.testclient import TestClient

from app.core.feed import models_feed
from app.core.feed.permissions_feed import FeedPermissions
from app.core.feed.types_feed import NewsStatus
from app.core.users import models_users
from tests.commons import (
    add_object_to_db,
    create_api_access_token,
    create_groups_with_permissions,
    create_user_with_groups,
)

published_news_1: models_feed.News
published_news_2: models_feed.News
pending_news: models_feed.News

user_access_feed: models_users.CoreUser
user_manage_feed: models_users.CoreUser

user_access_feed_token: str
user_manage_feed_token: str


@pytest_asyncio.fixture(scope="module", autouse=True)
async def init_objects() -> None:
    global user_access_feed, user_manage_feed
    group_access_feed = await create_groups_with_permissions(
        [FeedPermissions.access_feed],
        "Access feed",
    )
    user_access_feed = await create_user_with_groups(
        [group_access_feed.id],
    )
    group_manage_feed = await create_groups_with_permissions(
        [FeedPermissions.manage_feed],
        "Manage feed",
    )
    user_manage_feed = await create_user_with_groups(
        [group_manage_feed.id],
    )

    global user_access_feed_token, user_manage_feed_token
    user_access_feed_token = create_api_access_token(user_access_feed)
    user_manage_feed_token = create_api_access_token(user_manage_feed)

    global published_news_1, published_news_2
    published_news_1 = models_feed.News(
        id=uuid.uuid4(),
        title="Test news 1",
        start=datetime.now(UTC),
        end=None,
        entity="test_entity",
        location=None,
        news_related_module_root="test_module",
        news_related_module_object_id=uuid.uuid4(),
        action_start=None,
        module="test_module",
        module_object_id=uuid.uuid4(),
        image_directory="test_directory",
        image_id=uuid.uuid4(),
        status=NewsStatus.PUBLISHED,
    )
    await add_object_to_db(published_news_1)

    published_news_2 = models_feed.News(
        id=uuid.uuid4(),
        title="Test news 2",
        start=datetime.now(UTC) + timedelta(days=1),
        end=None,
        entity="test_entity",
        location=None,
        news_related_module_root="test_module",
        news_related_module_object_id=uuid.uuid4(),
        action_start=None,
        module="test_module",
        module_object_id=uuid.uuid4(),
        image_directory="test_directory",
        image_id=uuid.uuid4(),
        status=NewsStatus.PUBLISHED,
    )
    await add_object_to_db(published_news_2)

    global pending_news
    pending_news = models_feed.News(
        id=uuid.uuid4(),
        title="Pending news",
        start=datetime.now(UTC) + timedelta(days=1),
        end=None,
        entity="test_entity",
        location=None,
        news_related_module_root="test_module",
        news_related_module_object_id=uuid.uuid4(),
        action_start=None,
        module="test_module",
        module_object_id=uuid.uuid4(),
        image_directory="test_directory",
        image_id=uuid.uuid4(),
        status=NewsStatus.WAITING_APPROVAL,
    )
    await add_object_to_db(pending_news)


def test_get_published_news(client: TestClient) -> None:

    response = client.get(
        "/feed/news",
        headers={"Authorization": f"Bearer {user_access_feed_token}"},
    )
    assert response.status_code == 200
    news_list = response.json()
    assert len(news_list) == 2
    assert news_list[0]["id"] == str(published_news_1.id)
    assert news_list[1]["id"] == str(published_news_2.id)


def test_get_published_news_desc(client: TestClient) -> None:

    response = client.get(
        "/feed/news",
        params={"order": "desc"},
        headers={"Authorization": f"Bearer {user_access_feed_token}"},
    )
    assert response.status_code == 200
    news_list = response.json()
    assert len(news_list) == 2
    assert news_list[0]["id"] == str(published_news_2.id)
    assert news_list[1]["id"] == str(published_news_1.id)


def test_get_published_news_limit(client: TestClient) -> None:

    response = client.get(
        "/feed/news",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {user_access_feed_token}"},
    )
    assert response.status_code == 200
    news_list = response.json()
    assert len(news_list) == 1
    assert news_list[0]["id"] == str(published_news_1.id)


def test_get_published_news_start_before(client: TestClient) -> None:

    response = client.get(
        "/feed/news",
        params={
            "start_before": (published_news_2.start - timedelta(hours=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {user_access_feed_token}"},
    )
    assert response.status_code == 200
    news_list = response.json()
    assert len(news_list) == 1
    assert news_list[0]["id"] == str(published_news_1.id)


def test_get_published_news_start_after(client: TestClient) -> None:

    response = client.get(
        "/feed/news",
        params={
            "start_after": (published_news_2.start - timedelta(hours=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {user_access_feed_token}"},
    )
    assert response.status_code == 200
    news_list = response.json()
    assert len(news_list) == 1
    assert news_list[0]["id"] == str(published_news_2.id)
