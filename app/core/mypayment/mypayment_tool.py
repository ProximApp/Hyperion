
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.checkout.payment_tool import CheckoutTool
from app.core.mypayment import (
    schemas_mypayment,
    utils_mypayment,
)
from app.core.mypayment.types_mypayment import (
    PaymentType,
)
from app.core.users import schemas_users
from app.core.utils.config import Settings
from app.utils.communication.notifications import NotificationTool


class MyPaymentTool:
    """
    Utility class to interact with MyPayment core module

    The dependency `get_mypayment_tool` should be used to get an instance of this class, which will ensure that all dependencies are properly injected.
    """

    def __init__(
        self,
        db: AsyncSession,
        checkout_tool: CheckoutTool,
        notification_tool: NotificationTool,
        settings: Settings,
    ):
        self.db = db
        self.checkout_tool = checkout_tool
        self.notification_tool = notification_tool
        self.settings = settings

    async def request_payment(
        self,
        payment_type: PaymentType,
        payment_info: schemas_mypayment.PaymentInfo,
        user: schemas_users.CoreUser,
    ) -> schemas_mypayment.PaymentRequestInfo:
        return await utils_mypayment.request_payment(
            payment_type=payment_type,
            payment_info=payment_info,
            user=user,
            db=self.db,
            checkout_tool=self.checkout_tool,
            notification_tool=self.notification_tool,
            settings=self.settings,
        )
