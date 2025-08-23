from uuid import UUID

from app.core.mypayment.schemas_mypayment import Structure
from app.types.core_data import BaseCoreData


class MyPaymentBankAccountHolder(BaseCoreData):
    """Bank account holder information for MyPayment."""

    holder_structure_id: UUID


class MyPaymentBankAccountInformationComplete(MyPaymentBankAccountHolder):
    holder_structure: Structure
