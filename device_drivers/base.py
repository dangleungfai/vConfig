# -*- coding: utf-8 -*-
"""设备驱动基类"""
from abc import ABC, abstractmethod
from typing import List, Optional


class BaseDeviceDriver(ABC):
    """设备驱动基类 - 所有设备驱动必须继承此类"""
    
    def __init__(self, config: dict):
        """
        初始化驱动
        
        Args:
            config: 设备类型配置字典，包含 backup_config 和 connection_config
        """
        self.config = config
        self.backup_config = config.get('backup_config', {})
        self.connection_config = config.get('connection_config', {})
    
    @abstractmethod
    def get_init_commands(self) -> List[str]:
        """返回初始化命令列表（如设置终端长度、进入配置模式等）"""
        pass
    
    @abstractmethod
    def get_backup_command(self) -> str:
        """返回备份命令（如 show running-config）"""
        pass
    
    @abstractmethod
    def get_prompt(self) -> str:
        """返回命令提示符（用于等待命令执行完成）"""
        pass
    
    def get_login_prompt(self) -> str:
        """返回登录提示符（默认 'sername'，匹配 username/password）"""
        return self.connection_config.get('login_prompt', 'sername')
    
    def get_password_prompt(self) -> str:
        """返回密码提示符（默认 'assword'，匹配 password）"""
        return self.connection_config.get('password_prompt', 'assword')
    
    def get_prompts(self) -> List[str]:
        """返回所有可能的提示符列表（用于登录后的等待）"""
        prompts = self.connection_config.get('prompts', [])
        if prompts:
            return prompts
        # 默认提示符
        return [".*#$", "^<.*>$", "^[.*].*>", ".*> $"]
