import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import (
    HTTPException,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.checkout import schemas_checkout
from app.core.permissions.type_permissions import ModulePermissions
from app.core.users import models_users
from app.modules.cdr import coredata_cdr, cruds_cdr, models_cdr
from app.modules.cdr.types_cdr import (
    CdrLogActionType,
    PaymentType,
)
from app.utils.tools import (
    get_core_data,
    has_user_permission,
    is_user_member_of_any_group,
)

hyperion_error_logger = logging.getLogger("hyperion.error")


class CdrPermissions(ModulePermissions):
    manage_cdr = "manage_cdr"


async def validate_payment(
    checkout_payment: schemas_checkout.CheckoutPayment,
    db: AsyncSession,
) -> None:
    paid_amount = checkout_payment.paid_amount
    checkout_id = checkout_payment.checkout_id

    checkout = await cruds_cdr.get_checkout_by_checkout_id(
        db=db,
        checkout_id=checkout_id,
    )
    if not checkout:
        hyperion_error_logger.error(
            "CDR payment callback: user checkout not found.",
            extra={
                "checkout_id": checkout_id,
            },
        )
        raise ValueError(f"User checkout {checkout_id} not found.")  # noqa: TRY003

    # Retrieve current CDR year from core data instead of requiring it as a dependency
    cdr_year = await get_core_data(coredata_cdr.CdrYear, db)

    db_payment = models_cdr.Payment(
        id=uuid4(),
        user_id=checkout.user_id,
        total=paid_amount,
        payment_type=PaymentType.helloasso,
        year=cdr_year.year,
    )
    db_action = models_cdr.CdrAction(
        id=uuid4(),
        subject_id=checkout.user_id,
        action_type=CdrLogActionType.payment_add,
        action=str(checkout_payment.__dict__),
        timestamp=datetime.now(UTC),
    )

    cruds_cdr.create_payment(db=db, payment=db_payment)
    cruds_cdr.create_action(db=db, action=db_action)
    await db.flush()


async def is_user_in_a_seller_group(
    seller_id: UUID,
    user: models_users.CoreUser,
    db: AsyncSession,
):
    """
    Check if the user is in the group related to a seller or CDR Admin.
    """
    seller = await cruds_cdr.get_seller_by_id(db, seller_id=seller_id)

    if not seller:
        raise HTTPException(
            status_code=404,
            detail="Seller not found.",
        )

    if is_user_member_of_any_group(
        user=user,
        allowed_groups=[str(seller.group_id)],
    ) or await has_user_permission(
        user,
        CdrPermissions.manage_cdr,
        db,
    ):
        return user

    raise HTTPException(
        status_code=403,
        detail="Unauthorized, user is not in this seller group.",
    )


async def check_request_consistency(
    db: AsyncSession,
    seller_id: UUID | None = None,
    product_id: UUID | None = None,
    variant_id: UUID | None = None,
    document_id: UUID | None = None,
) -> models_cdr.CdrProduct | None:
    """
    Check that given ids are consistent, ie. product's seller_id is the given seller_id.
    """
    db_product: models_cdr.CdrProduct | None = None
    if seller_id:
        db_seller = await cruds_cdr.get_seller_by_id(db=db, seller_id=seller_id)
        if not db_seller:
            raise HTTPException(
                status_code=404,
                detail="Invalid seller_id",
            )
    if product_id:
        db_product = await cruds_cdr.get_product_by_id(db=db, product_id=product_id)
        if not db_product:
            raise HTTPException(
                status_code=404,
                detail="Invalid product_id",
            )
        if seller_id and seller_id != db_product.seller_id:
            raise HTTPException(
                status_code=403,
                detail="Product is not related to this seller.",
            )
    if variant_id:
        db_variant = await cruds_cdr.get_product_variant_by_id(
            db=db,
            variant_id=variant_id,
        )
        if not db_variant:
            raise HTTPException(
                status_code=404,
                detail="Invalid variant_id",
            )
        if product_id and product_id != db_variant.product_id:
            raise HTTPException(
                status_code=403,
                detail="Variant is not related to this product.",
            )
    if document_id:
        db_document = await cruds_cdr.get_document_by_id(db=db, document_id=document_id)
        if not db_document:
            raise HTTPException(
                status_code=404,
                detail="Invalid document_id",
            )
        if seller_id and seller_id != db_document.seller_id:
            raise HTTPException(
                status_code=403,
                detail="Document is not related to this seller.",
            )
    return db_product
