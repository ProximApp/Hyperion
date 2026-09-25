import importlib
import logging
from pathlib import Path

from app.types.module import CoreModule, Module
from app.utils.auth.providers import AuthPermissions

hyperion_error_logger = logging.getLogger("hyperion.error")

module_list: list[Module] = []
core_module_list: list[CoreModule] = []
all_modules: list[CoreModule] = []

permissions_list: list[str] = []
full_name_permissions_list: list[str] = []

for endpoints_file in Path().glob("app/modules/*/endpoints_*.py"):
    endpoint_module = importlib.import_module(
        ".".join(endpoints_file.with_suffix("").parts),
    )
    module = getattr(endpoint_module, "module", None)
    if module is not None and isinstance(module, Module):
        module_list.append(module)
        all_modules.append(module)
    else:
        hyperion_error_logger.error(
            "Module does not declare a module. It won't be enabled.",
            extra={"module_file": str(endpoints_file)},
        )


for endpoints_file in Path().glob("app/core/*/endpoints_*.py"):
    endpoint_module = importlib.import_module(
        ".".join(endpoints_file.with_suffix("").parts),
    )
    core_module = getattr(endpoint_module, "core_module", None)
    if core_module is not None and isinstance(core_module, CoreModule):
        core_module_list.append(core_module)
        all_modules.append(core_module)
    else:
        hyperion_error_logger.error(
            "Core module does not declare a core module. It won't be enabled.",
            extra={"module_file": str(endpoints_file)},
        )


class DuplicatePermissionsError(Exception):
    def __init__(self, permissions: list[list[str]]):
        arranged_permissions = [
            f"    - '{permission[0].split('.')[1]}': {', '.join(permission)}"
            for permission in permissions
        ]
        super().__init__(
            "Duplicate permissions name found in modules:\n"
            + "\n".join(arranged_permissions),
        )


for each_module in all_modules:
    if each_module.permissions:
        permissions_list.extend(
            each_module.permissions.__members__.keys(),
        )
        full_name_permissions_list.extend(
            [
                f"{each_module.root}.{name}"
                for name in each_module.permissions.__members__
            ],
        )
permissions_list.extend(AuthPermissions.__members__.keys())
full_name_permissions_list.extend(
    [f"Auth.{name}" for name in AuthPermissions.__members__],
)
if len(set(permissions_list)) != len(permissions_list):
    duplicates = list({x for x in permissions_list if permissions_list.count(x) > 1})
    full_name_duplicates = [
        [name for name in full_name_permissions_list if name.split(".")[1] == duplicate]
        for duplicate in duplicates
    ]
    hyperion_error_logger.error(
        "Duplicate permissions found in modules",
        extra={"duplicates": full_name_duplicates},
    )
    raise DuplicatePermissionsError(full_name_duplicates)
