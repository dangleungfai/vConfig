# -*- coding: utf-8 -*-
"""通用设备驱动 - 基于数据库配置"""
from .base import BaseDeviceDriver
from typing import List


class GenericDeviceDriver(BaseDeviceDriver):
    """通用设备驱动 - 从数据库配置读取命令和提示符"""
    
    def get_init_commands(self) -> List[str]:
        """从配置中读取初始化命令"""
        return self.backup_config.get('init_commands', [])
    
    def get_backup_command(self) -> str:
        """从配置中读取备份命令"""
        return self.backup_config.get('backup_command', 'show running-config')
    
    def get_prompt(self) -> str:
        """从配置中读取提示符"""
        return self.backup_config.get('prompt', '#')
    
    def get_login_prompt(self) -> str:
        """从连接配置读取登录提示符"""
        return self.connection_config.get('login_prompt', 'sername')
    
    def get_password_prompt(self) -> str:
        """从连接配置读取密码提示符"""
        return self.connection_config.get('password_prompt', 'assword')
    
    def get_prompts(self) -> List[str]:
        """从连接配置读取提示符列表"""
        prompts = self.connection_config.get('prompts', [])
        if prompts:
            return prompts
        return [".*#$", "^<.*>$", "^[.*].*>", ".*> $"]
