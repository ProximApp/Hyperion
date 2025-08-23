from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.users.models_users import CoreUser
from app.dependencies import get_db, is_user
from app.types.core_data import BaseCoreData
from app.utils.tools import get_core_data


class MyPaymentInvoiceCoordinates(BaseCoreData):
    """Invoice coordinates for MyPayment."""

    name: str = ""
    address_street: str = ""
    address_city: str = ""
    address_zipcode: str = ""
    address_country: str = ""
    siret: str | None = None


class MyPaymentBankAccountInformation(BaseCoreData):
    """Bank account holder information for MyPayment."""

    holder_user_id: str = ""
    holder_coordinates: MyPaymentInvoiceCoordinates = MyPaymentInvoiceCoordinates()


async def is_user_bank_account_holder(
    user: CoreUser = Depends(is_user()),
    db: AsyncSession = Depends(get_db),
) -> CoreUser:
    """Check if the user is a bank account holder."""
    account_holder = await get_core_data(
        MyPaymentBankAccountInformation,
        db=db,
    )
    if account_holder.holder_user_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="User is not the bank account holder",
        )
    return user
