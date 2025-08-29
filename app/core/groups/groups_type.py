from enum import Enum


class GroupType(str, Enum):
    """
    In Hyperion, each user may have multiple groups. Belonging to a group gives access to a set of specific endpoints.
    Usually, one or a few groups are associated to some rights over their corresponding module. For example a member of amap group is allowed to administrate the amap module

    A group may also allow using Hyperion OAuth/Openid connect capabilities to sign in to a specific external platform.

    Being member of admin only gives rights over admin specific endpoints. For example, an admin won't be able to administrate amap module
    """

    # Core group
    admin = "0a25cb76-4b63-4fd3-b939-da6d9feabf28"

    # Module related groups
    admin_amap = "70db65ee-d533-4f6b-9ffa-a4d70a17b7ef"
    admin_calendar = "b0357687-2211-410a-9e2a-144519eeaafa"
    admin_cdr = "c1275229-46b2-4e53-a7c4-305513bb1a2a"
    admin_cinema = "ce5f36e6-5377-489f-9696-de70e2477300"
    admin_feed = "59e3c4c2-e60f-44b6-b0d2-fa1b248423bb"
    admin_ph = "4ec5ae77-f955-4309-96a5-19cc3c8be71c"
    admin_phonebook = "d3f91313-d7e5-49c6-b01f-c19932a7e09b"
    admin_raid = "e9e6e3d3-9f5f-4e9b-8e5f-9f5f4e9b8e5f"
    admin_recommandation = "389215b2-ea45-4991-adc1-4d3e471541cf"
    admin_seed_library = "09153d2a-14f4-49a4-be57-5d0f265261b9"
    admin_vote = "2ca57402-605b-4389-a471-f2fea7b27db5"

    def __str__(self):
        return f"{self.name}<{self.value}>"


class AccountType(str, Enum):
    """
    Various account types that can be created in Hyperion.
    These values should match GroupType's. They are the lower level groups in Hyperion
    """

    student = "student"
    former_student = "former_student"
    staff = "staff"
    association = "association"
    external = "external"
    other_school_student = "other_school_student"
    demo = "demo"

    def __str__(self):
        return f"{self.name}<{self.value}>"


def get_school_account_types() -> list[AccountType]:
    return [
        AccountType.student,
        AccountType.former_student,
        AccountType.staff,
        AccountType.association,
        AccountType.demo,
    ]


def get_schools_account_types() -> list[AccountType]:
    return [
        AccountType.student,
        AccountType.former_student,
        AccountType.staff,
        AccountType.association,
        AccountType.other_school_student,
        AccountType.demo,
    ]


def get_account_types_except_externals() -> list[AccountType]:
    return [
        AccountType.student,
        AccountType.former_student,
        AccountType.staff,
        AccountType.association,
        AccountType.demo,
        AccountType.other_school_student,
    ]
