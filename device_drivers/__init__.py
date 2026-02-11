# -*- coding: utf-8 -*-
"""设备驱动模块 - 支持可扩展的设备类型"""
import importlib
import os
from typing import Optional, Dict
from .base import BaseDeviceDriver
from .generic import GenericDeviceDriver

# 驱动注册表：type_code -> driver_class
_DRIVERS: Dict[str, type] = {}


def register_driver(type_code: str, driver_class: type):
    """
    注册设备驱动
    
    Args:
        type_code: 设备类型代码（如 'Cisco', 'Juniper'）
        driver_class: 驱动类（必须继承 BaseDeviceDriver）
    """
    if not issubclass(driver_class, BaseDeviceDriver):
        raise ValueError(f"Driver class must inherit from BaseDeviceDriver")
    _DRIVERS[type_code.upper()] = driver_class


def get_driver(type_code: str, config: dict) -> BaseDeviceDriver:
    """
    获取设备驱动实例
    
    Args:
        type_code: 设备类型代码
        config: 设备类型配置字典（包含 backup_config 和 connection_config）
    
    Returns:
        BaseDeviceDriver 实例
    """
    type_code_upper = type_code.upper()
    driver_type = (config.get('driver_type') or 'generic').strip() or 'generic'
    driver_module = config.get('driver_module')

    # 1. 仅当 driver_type 显式为 builtin 时，才使用已注册的内置驱动
    if driver_type == 'builtin':
        driver_class = _DRIVERS.get(type_code_upper)
        if driver_class:
            return driver_class(config)
    
    # 2. 如果配置中指定了自定义驱动模块，尝试加载
    if driver_type == 'custom' and driver_module:
        try:
            module = importlib.import_module(driver_module)
            if hasattr(module, 'get_driver_class'):
                driver_class = module.get_driver_class()
                if driver_class:
                    return driver_class(config)
        except (ImportError, AttributeError) as e:
            import logging
            logging.warning(f"Failed to load custom driver {driver_module}: {e}")
    
    # 3. 默认使用通用驱动（基于配置）
    return GenericDeviceDriver(config)


def load_custom_drivers():
    """自动加载 custom 目录下的自定义驱动"""
    custom_dir = os.path.join(os.path.dirname(__file__), 'custom')
    if not os.path.exists(custom_dir):
        return
    
    for filename in os.listdir(custom_dir):
        if filename.endswith('.py') and filename != '__init__.py':
            module_name = filename[:-3]
            try:
                module = importlib.import_module(f'device_drivers.custom.{module_name}')
                if hasattr(module, 'register'):
                    module.register()
            except Exception as e:
                import logging
                logging.warning(f'Failed to load custom driver {module_name}: {e}')
