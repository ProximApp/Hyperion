from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mypayment.cruds_mypayment import get_structure_by_id
from app.core.mypayment.schemas_mypayment import Structure
from app.core.users.models_users import CoreUser
from app.dependencies import get_db, is_user
from app.types.core_data import BaseCoreData
from app.utils.tools import get_core_data


class MyPaymentBankAccountHolder(BaseCoreData):
    """Bank account holder information for MyPayment."""

    holder_structure_id: UUID


class MyPaymentBankAccountInformationComplete(MyPaymentBankAccountHolder):
    holder_structure: Structure


async def is_user_bank_account_holder(
    user: CoreUser = Depends(is_user()),
    db: AsyncSession = Depends(get_db),
) -> CoreUser:
    """Check if the user is a bank account holder."""
    account_holder = await get_core_data(
        MyPaymentBankAccountHolder,
        db=db,
    )
    structure = await get_structure_by_id(
        db=db,
        structure_id=account_holder.holder_structure_id,
    )
    if not structure:
        raise HTTPException(
            status_code=404,
            detail="Structure not found for the bank account holder",
        )
    if structure.manager_user_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="User is not the bank account holder",
        )
    return user
