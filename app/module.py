import importlib
import logging
from pathlib import Path

from app.core.utils.config import Settings
from app.types.module import CoreModule, Module

hyperion_error_logger = logging.getLogger("hyperion.error")

_module_list: list[Module] = []
_core_module_list: list[CoreModule] = []


def init_module_list(settings: Settings):
    module_list = []
    for endpoints_file in Path().glob("app/modules/*/endpoints_*.py"):
        endpoint_module = importlib.import_module(
            ".".join(endpoints_file.with_suffix("").parts),
        )
        if hasattr(endpoint_module, "module"):
            module: Module = endpoint_module.module
            module_list.append(module)
        else:
            hyperion_error_logger.error(
                f"Module {endpoints_file} does not declare a module. It won't be enabled.",
            )

    if settings.RESTRICT_TO_MODULES:
        existing_module_roots = [module.root for module in module_list]
        for root in settings.RESTRICT_TO_MODULES:
            if root not in existing_module_roots:
                raise ValueError()
    for module in module_list:
        if (
            settings.RESTRICT_TO_MODULES
            and module.root not in settings.RESTRICT_TO_MODULES
        ):
            continue
        _module_list.append(module)

    for endpoints_file in Path().glob("app/core/*/endpoints_*.py"):
        endpoint_module = importlib.import_module(
            ".".join(endpoints_file.with_suffix("").parts),
        )
        if hasattr(endpoint_module, "core_module"):
            core_module: CoreModule = endpoint_module.core_module
            _core_module_list.append(core_module)
        else:
            hyperion_error_logger.error(
                f"Core module {endpoints_file} does not declare a core module. It won't be enabled.",
            )


def get_module_list() -> list[Module]:
    return _module_list


def get_core_module_list() -> list[CoreModule]:
    return _core_module_list


def get_all_modules() -> list[Module | CoreModule]:
    return get_module_list() + get_core_module_list()
