"""File defining the functions called by the endpoints, making queries to the table using the models"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers.structs import CredentialDeviceType

from app.core.auth import models_auth


async def get_authorization_token_by_token(
    db: AsyncSession,
    code: str,
) -> models_auth.AuthorizationCode | None:
    """Return authorization code from database"""
    result = await db.execute(
        select(models_auth.AuthorizationCode).where(
            models_auth.AuthorizationCode.code == code,
        ),
    )
    return result.scalars().first()


async def create_authorization_token(
    db_authorization_code: models_auth.AuthorizationCode,
    db: AsyncSession,
) -> models_auth.AuthorizationCode:
    """Create a new group in database and return it"""

    db.add(db_authorization_code)
    await db.flush()
    return db_authorization_code


async def delete_authorization_token_by_token(
    db: AsyncSession,
    code: str,
) -> models_auth.AuthorizationCode | None:
    """Delete a token from database"""

    await db.execute(
        delete(models_auth.AuthorizationCode).where(
            models_auth.AuthorizationCode.code == code,
        ),
    )
    await db.flush()
    return None


async def get_refresh_token_by_token(
    db: AsyncSession,
    token: str,
) -> models_auth.RefreshToken | None:
    """Return refresh token from database"""
    result = await db.execute(
        select(models_auth.RefreshToken).where(models_auth.RefreshToken.token == token),
    )
    return result.scalars().first()


async def create_refresh_token(
    db_refresh_token: models_auth.RefreshToken,
    db: AsyncSession,
) -> models_auth.RefreshToken:
    """Create a new refresh token in database and return it"""

    db.add(db_refresh_token)
    await db.flush()
    return db_refresh_token


async def revoke_refresh_token_by_token(
    db: AsyncSession,
    token: str,
) -> models_auth.RefreshToken | None:
    """Revoke a refresh token from database"""

    await db.execute(
        update(models_auth.RefreshToken)
        .where(
            models_auth.RefreshToken.token == token,
            models_auth.RefreshToken.revoked_on.is_(None),
        )
        .values(revoked_on=datetime.now(UTC)),
    )
    await db.flush()
    return None


async def revoke_refresh_token_by_client_and_user_id(
    db: AsyncSession,
    client_id: str,
    user_id: str,
) -> None:
    """Revoke a refresh token from database"""

    await db.execute(
        update(models_auth.RefreshToken)
        .where(
            models_auth.RefreshToken.client_id == client_id,
            models_auth.RefreshToken.user_id == user_id,
            models_auth.RefreshToken.revoked_on.is_(None),
        )
        .values(revoked_on=datetime.now(UTC)),
    )
    await db.flush()


async def revoke_refresh_token_by_user_id(
    db: AsyncSession,
    user_id: str,
) -> None:
    """Revoke a refresh token from database"""

    await db.execute(
        update(models_auth.RefreshToken)
        .where(
            models_auth.RefreshToken.user_id == user_id,
            models_auth.RefreshToken.revoked_on.is_(None),
        )
        .values(revoked_on=datetime.now(UTC)),
    )
    await db.flush()


async def create_webauthn_registration_options(
    registration_options_id: UUID,
    user_id: str,
    created_on: datetime,
    challenge: bytes,
    db: AsyncSession,
) -> None:
    """Create a new webauthn registration options in database and return it"""

    db.add(
        models_auth.WebAuthnRegistrationOptions(
            registration_options_id=registration_options_id,
            user_id=user_id,
            created_on=created_on,
            challenge=challenge,
        ),
    )


async def get_webauthn_registration_options_by_id(
    db: AsyncSession,
    registration_options_id: UUID,
) -> models_auth.WebAuthnRegistrationOptions | None:
    """Return webauthn registration options from database"""
    result = await db.execute(
        select(models_auth.WebAuthnRegistrationOptions).where(
            models_auth.WebAuthnRegistrationOptions.registration_options_id
            == registration_options_id,
        ),
    )
    return result.scalars().first()


async def create_webauthn_authentication_options(
    authentication_options_id: UUID,
    created_on: datetime,
    challenge: bytes,
    db: AsyncSession,
) -> None:
    """Create a new webauthn authentication options in database and return it"""

    db.add(
        models_auth.WebAuthnAuthenticationOptions(
            authentication_options_id=authentication_options_id,
            created_on=created_on,
            challenge=challenge,
        ),
    )


async def get_webauthn_authentication_options_by_id(
    db: AsyncSession,
    authentication_options_id: UUID,
) -> models_auth.WebAuthnAuthenticationOptions | None:
    """Return webauthn authentication options from database"""
    result = await db.execute(
        select(models_auth.WebAuthnAuthenticationOptions).where(
            models_auth.WebAuthnAuthenticationOptions.authentication_options_id
            == authentication_options_id,
        ),
    )
    return result.scalars().first()


async def create_webauthn_passkey(
    passkey: models_auth.WebAuthnPasskey,
    db: AsyncSession,
) -> None:

    db.add(
        passkey,
    )


async def get_webauthn_passkeys_by_user_id(
    db: AsyncSession,
    user_id: str,
) -> Sequence[models_auth.WebAuthnPasskey]:
    """Return webauthn passkeys from database"""
    result = await db.execute(
        select(models_auth.WebAuthnPasskey).where(
            models_auth.WebAuthnPasskey.user_id == user_id,
        ),
    )
    return result.scalars().all()


async def get_webauthn_passkey_by_passkey_id(
    db: AsyncSession,
    passkey_id: str,
) -> models_auth.WebAuthnPasskey | None:
    """Return webauthn passkey from database"""
    result = await db.execute(
        select(models_auth.WebAuthnPasskey).where(
            models_auth.WebAuthnPasskey.passkey_id == passkey_id,
        ),
    )
    return result.scalars().first()


async def update_webauthn_passkey(
    db: AsyncSession,
    # The id of the WebAuthnPasskey object, not the passkey_id
    webauthn_passkey_id: UUID,
    new_passkey_sign_count: int,
    new_passkey_device_type: CredentialDeviceType,
    new_passkey_backed_up: bool,
) -> None:
    """Update webauthn passkey user_id in database"""

    await db.execute(
        update(models_auth.WebAuthnPasskey)
        .where(
            models_auth.WebAuthnPasskey.id == webauthn_passkey_id,
        )
        .values(
            passkey_sign_count=new_passkey_sign_count,
            passkey_device_type=new_passkey_device_type,
            passkey_backed_up=new_passkey_backed_up,
        ),
    )
