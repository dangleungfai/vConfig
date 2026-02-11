# -*- coding: utf-8 -*-
"""内置设备驱动（Cisco, Juniper, Huawei等）"""
from .base import BaseDeviceDriver
from typing import List


class CiscoDriver(BaseDeviceDriver):
    """Cisco设备驱动"""
    
    def get_init_commands(self) -> List[str]:
        # 优先使用 DeviceTypeConfig 中的显性配置，未配置时回退到默认命令
        return self.backup_config.get('init_commands', ["\x03", "terminal length 0"])
    
    def get_backup_command(self) -> str:
        return self.backup_config.get('backup_command', "show run")
    
    def get_prompt(self) -> str:
        return self.backup_config.get('prompt', "\r\nend\r\n")


class JuniperDriver(BaseDeviceDriver):
    """Juniper设备驱动"""
    
    def get_init_commands(self) -> List[str]:
        return self.backup_config.get('init_commands', ["set cli screen-length 0"])
    
    def get_backup_command(self) -> str:
        return self.backup_config.get('backup_command', "show configuration | display set | no-more")
    
    def get_prompt(self) -> str:
        # 默认使用通用的 Junos 提示符结尾字符 ">"，
        # 而不是固定的 "\r\n{master}"，以适配类似 user@host> 的提示符。
        return self.backup_config.get('prompt', ">")


class HuaweiDriver(BaseDeviceDriver):
    """Huawei设备驱动"""
    
    def get_init_commands(self) -> List[str]:
        return self.backup_config.get('init_commands', ["screen-length 0 temporary"])
    
    def get_backup_command(self) -> str:
        return self.backup_config.get('backup_command', "disp cur")
    
    def get_prompt(self) -> str:
        return self.backup_config.get('prompt', ']')


class H3CDriver(BaseDeviceDriver):
    """H3C设备驱动"""
    
    def get_init_commands(self) -> List[str]:
        return self.backup_config.get('init_commands', ["screen-length disable"])
    
    def get_backup_command(self) -> str:
        return self.backup_config.get('backup_command', "display current-configuration")
    
    def get_prompt(self) -> str:
        # 默认使用通用的提示符结束字符 ">"，适配 H3C 上常见的 user@host> / <H3C> 等形式
        return self.backup_config.get('prompt', ">")


class RouterOSDriver(BaseDeviceDriver):
    """RouterOS 设备驱动（MikroTik）。建议连接方式使用 SSH（端口 22）。"""
    
    def get_init_commands(self) -> List[str]:
        return self.backup_config.get('init_commands', [])
    
    def get_backup_command(self) -> str:
        # 含 "success\n"，结尾 output_success
        return self.backup_config.get(
            'backup_command',
            ':foreach i in=(:put[/export]&:put[:put (("output_").("success\n"))]) do={$i}'
        )
    
    def get_prompt(self) -> str:
        # 等 output_success 出现再结束，保证 /export 完整输出
        return self.backup_config.get('prompt', 'output_success')
    
    def get_login_prompt(self) -> str:
        # RouterOS SSH 显示 "Login:"，Telnet 也可能用 "login:"
        return self.connection_config.get('login_prompt', 'ogin')
    
    def get_password_prompt(self) -> str:
        return self.connection_config.get('password_prompt', 'assword')
    
    def get_prompts(self) -> List[str]:
        # 匹配 [user@host] > 形式的提示符
        return self.connection_config.get('prompts', [r'.*\]\s*>\s*'])


# 注册内置驱动
def register_builtin_drivers():
    """注册所有内置驱动"""
    from . import register_driver
    register_driver('CISCO', CiscoDriver)
    register_driver('Cisco', CiscoDriver)
    register_driver('JUNIPER', JuniperDriver)
    register_driver('Juniper', JuniperDriver)
    register_driver('HUAWEI', HuaweiDriver)
    register_driver('Huawei', HuaweiDriver)
    register_driver('H3C', H3CDriver)
    register_driver('ROS', RouterOSDriver)
    register_driver('RouterOS', RouterOSDriver)
