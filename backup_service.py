# -*- coding: utf-8 -*-
"""配置备份服务：支持 Telnet / SSH"""
import os
import re
import time
import socket
import telnetlib
import datetime
import threading
from typing import Optional, Callable, List, Tuple

# SSH 端口，由调用方传入
DEFAULT_SSH_PORT = 22
DEFAULT_TELNET_PORT = 23


def _clean_routeros_backup_content(content: str) -> str:
    """去掉 RouterOS 备份中的终端回显和 ANSI 转义，只保留 /export 的配置正文。若未发现配置内容则返回说明（避免保存整段日志）。"""
    # 去掉 ANSI 转义（如 [m [36m [K [32;1m）
    content = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', content)
    lines = content.splitlines()
    # 找到第一条「配置开始」行：RouterOS /export 以 # 日期 或 / 开头
    start = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if s.startswith('# ') and ('by RouterOS' in s or re.match(r'#\s*\d{1,2}/', s)):
            start = i
            break
        if s.startswith('/') and not s.startswith('</'):
            start = i
            break
    if start >= 0:
        return '\n'.join(lines[start:])
    # 未发现 /export 配置（可能超时、或会话被系统日志刷屏），不保存整段无关内容
    return "# RouterOS backup: 未收到 /export 配置内容（可能超时或当前会话被系统日志刷屏）。\n# 建议：在设备上关闭或限制 Telnet，仅用 SSH 备份；或临时关闭 system logging 到 console。"


def _routeros_default_config():
    """RouterOS（MikroTik）默认配置：使用已验证可用的 :foreach /export + output_success 方案。"""
    return {
        'backup_config': {
            'init_commands': [],
            # 含 "success\n"，结尾输出 output_success
            'backup_command': ':foreach i in=(:put[/export]&:put[:put (("output_").("success\n"))]) do={$i}',
            'prompt': 'output_success'
        },
        'connection_config': {
            # RouterOS SSH 默认显示 Login:，用 ogin 可同时匹配 Login:/login:
            'login_prompt': 'ogin',
            'password_prompt': 'assword',
            'prompts': [r'.*\]\s*>\s*']
        }
    }


def _get_device_driver(dev_type: str, type_config: Optional[dict] = None):
    """
    获取设备驱动实例
    
    Args:
        dev_type: 设备类型代码
        type_config: 来自 DeviceTypeConfig 的配置字典（包含 backup_config / connection_config / driver_type / driver_module）
    
    Returns:
        BaseDeviceDriver 实例，如果失败则返回 None
    """
    try:
        from device_drivers import get_driver

        # 优先使用从 DeviceTypeConfig 传入的显性配置
        if isinstance(type_config, dict) and type_config.get('backup_config') is not None:
            return get_driver(dev_type, type_config)

        # 若未传入配置（极端情况），为了兼容老数据，仍保留一份内置默认映射
        default_configs = {
            'CISCO': {
                'backup_config': {
                    'init_commands': ["\x03", "terminal length 0"],
                    'backup_command': 'show run',
                    'prompt': '\r\nend\r\n'
                },
                'connection_config': {
                    'login_prompt': 'sername',
                    'password_prompt': 'assword'
                }
            },
            'JUNIPER': {
                'backup_config': {
                    'init_commands': ['set cli screen-length 0'],
                    'backup_command': 'show configuration | display set | no-more',
                    'prompt': '\r\n{master}'
                },
                'connection_config': {
                    'login_prompt': 'sername',
                    'password_prompt': 'assword'
                }
            },
            'HUAWEI': {
                'backup_config': {
                    'init_commands': ['screen-length 0 temporary'],
                    'backup_command': 'disp cur',
                    'prompt': ']'
                },
                'connection_config': {
                    'login_prompt': 'sername',
                    'password_prompt': 'assword'
                }
            },
            'H3C': {
                'backup_config': {
                    'init_commands': ['screen-length disable'],
                    'backup_command': 'display current-configuration',
                    'prompt': ']'
                },
                'connection_config': {
                    'login_prompt': 'sername',
                    'password_prompt': 'assword'
                }
            },
            'ROS': _routeros_default_config(),
            'ROUTEROS': _routeros_default_config(),
        }

        dev_type_upper = (dev_type or '').strip().upper()
        config = default_configs.get(dev_type_upper)
        if config:
            return get_driver(dev_type, config)

        return None
    except Exception as e:
        import logging
        logging.warning(f"Failed to get device driver for {dev_type}: {e}")
        return None


class Executor:
    """Telnet 执行器 - 支持 Cisco/Juniper/Huawei/H3C/RouterOS"""
    prompt_login = "sername"
    prompt_password = "assword"
    # Junos 提示符通常为 user@host>，这里使用通用的 '>' 作为结束标记
    prompt_junos = ">"
    prompt_cisco = "\r\nend\r\n"
    prompt_ros = "output_success"
    prompt_huawei = "\r\nreturn\r\n"
    # H3C 提示符同样使用通用的 ">" 作为结束标记，避免因具体字样差异导致长时间等待
    prompt_h3c = ">"
    prompt_failed_login = "Login incorrect"

    def __init__(self, hostname: str, username: str, password: str):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.connected = False
        self._tn = None
        self.os = None
        self.eol = "\r\n"

    def connect_and_show_run(self, dev_type: str, command: Optional[str] = None) -> str:
        self._tn = telnetlib.Telnet(self.hostname, timeout=10)
        login_return = self._tn.read_until(self.prompt_login.encode(), timeout=10)
        if not login_return:
            raise ValueError("No login prompt")

        self._run_and_expect(self.username, self.prompt_password, 10)
        self._run_and_expect(
            self.password,
            [".*#$", "^<.*>$", "^[.*].*>", ".*> $"],
            10
        )

        dev_type = dev_type.upper()
        if dev_type == "JUNIPER":
            self._init_junos()
            cmd = command or "show configuration | display set | no-more"
        elif dev_type == "ROS":
            self._init_ros()
            cmd = command or ':foreach i in=(:put[/export]&:put[:put (("output_").("success\n"))]) do={$i}'
        elif dev_type == "HUAWEI":
            self._init_huawei()
            cmd = command or "disp cur"
        elif dev_type == "H3C":
            self._init_h3c()
            cmd = command or "display current-configuration"
        elif dev_type == "CISCO":
            self._init_cisco()
            cmd = command or "show run"
        else:
            raise ValueError(f"Unknown device type: {dev_type}")

        result = self._cmd(cmd, dev_type)
        self.close()
        return result

    def close(self):
        self.connected = False
        self.os = None
        if self._tn:
            try:
                self._tn.close()
            except Exception:
                pass
            self._tn = None

    def _init_cisco(self):
        self.connected = True
        self.os = "CISCO"
        self._run("\x03")
        self._run("terminal length 0")

    def _init_junos(self):
        self.connected = True
        self.os = "JUNIPER"
        self._run("set cli screen-length 0")

    def _init_ros(self):
        self.connected = True
        self.os = "ROS"

    def _init_huawei(self):
        self.connected = True
        self.os = "HUAWEI"
        self._run("screen-length 0 temporary")

    def _init_h3c(self):
        self.connected = True
        self.os = "H3C"
        self._run("screen-length disable")

    def _cmd(self, command: str, dev_type: str) -> str:
        prompts = {
            "JUNIPER": self.prompt_junos,
            "ROS": self.prompt_ros,
            "CISCO": self.prompt_cisco,
            "HUAWEI": self.prompt_huawei,
            "H3C": self.prompt_h3c,
        }
        prompt = prompts.get(dev_type, self.prompt_cisco)
        self._tn.read_until(prompt.encode(), timeout=3)
        self._tn.read_until(prompt.encode(), timeout=3)
        self._run(command)
        retval = self._tn.read_until(prompt.encode(), 90)
        return retval.decode('utf-8', errors='replace')

    def _run(self, cmd: str):
        data = cmd if isinstance(cmd, bytes) else cmd.encode()
        self._tn.write(data + self.eol.encode())

    def _run_and_expect(self, cmd: str, expect, timer: int):
        self._run(cmd)
        if isinstance(expect, list):
            import re
            cleaned = [re.compile(x).pattern.encode() for x in expect]
        else:
            cleaned = [expect.encode() if isinstance(expect, str) else expect]
        self._tn.expect(cleaned, timer)


def _backup_via_ssh(
    ip: str,
    hostname: str,
    dev_type: str,
    username: str,
    password: str,
    store_path: str,
    log_callback: Callable,
    ssh_port: int = DEFAULT_SSH_PORT,
    timeout_seconds: int = 30,
    app_context=None,
    type_configs: Optional[dict] = None,
) -> None:
    """单台设备通过 SSH 备份配置（使用驱动模式）。"""
    try:
        import paramiko
    except ImportError:
        log_callback(ip, hostname, dev_type, "Fail", "未安装 paramiko，无法使用 SSH", None, None)
        return
    
    # 获取设备驱动
    cfg = None
    if isinstance(type_configs, dict):
        cfg = type_configs.get(dev_type) or type_configs.get((dev_type or '').upper())
    driver = _get_device_driver(dev_type, cfg)
    if not driver:
        log_callback(ip, hostname, dev_type, "Fail", "未知设备类型或驱动加载失败", None, None)
        return
    
    start = datetime.datetime.now()
    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=ip,
            port=ssh_port,
            username=username,
            password=password,
            timeout=timeout_seconds,
            allow_agent=False,
            look_for_keys=False,
        )
        channel = client.invoke_shell(width=256)
        time.sleep(0.8)
        channel.recv(65535)

        def send(cmd: str):
            channel.send(cmd + "\r\n")
            time.sleep(0.3)

        # 使用驱动获取初始化命令
        init_commands = driver.get_init_commands()
        for cmd in init_commands:
            send(cmd)
            time.sleep(0.3)
            channel.recv(65535)
        
        # 使用驱动获取备份命令
        backup_cmd = driver.get_backup_command() or ''
        dev_type_upper_ssh = (dev_type or '').strip().upper()
        if dev_type_upper_ssh in ('ROS', 'ROUTEROS'):
            # RouterOS：整条命令一行发送（去掉内部 \n，避免 "success\n" 被拆成两行），与 Telnet 逻辑一致
            send(backup_cmd.replace('\r', '').replace('\n', '').strip())
        else:
            backup_lines = [c.strip() for c in backup_cmd.replace('\r\n', '\n').split('\n') if c.strip()]
            for cmd in backup_lines:
                send(cmd)
                time.sleep(0.5)

        # 给设备时间开始输出（RouterOS /export 可能稍慢）
        time.sleep(2)
        result = b""
        prompt = driver.get_prompt().encode() if driver.get_prompt() else None
        # 用带超时的 recv 持续读，避免 recv_ready() 漏数据或节奏不对导致收不全
        channel.settimeout(1.0)
        for _ in range(120):
            try:
                data = channel.recv(65535)
                if data:
                    result += data
            except socket.timeout:
                pass
            if prompt and prompt in result:
                time.sleep(0.5)
                try:
                    result += channel.recv(65535)
                except (socket.timeout, Exception):
                    pass
                break
            if not prompt and len(result) > 100 and (b"#" in result or b">" in result or b"end" in result or b"return" in result or b"{" in result):
                time.sleep(0.5)
                try:
                    result += channel.recv(65535)
                except (socket.timeout, Exception):
                    pass
                break
        client.close()
        end = datetime.datetime.now()
        duration = int((end - start).total_seconds())
        content = result.decode('utf-8', errors='replace')
        # 若驱动使用特定字符串作为结束标记（如 RouterOS 的 output_success），则不把该标记及其后内容写入文件
        if prompt:
            marker = prompt.decode('utf-8', errors='replace').strip()
            if marker and marker in content:
                content = content.split(marker)[0].rstrip()
        if (dev_type or '').strip().upper() in ('ROS', 'ROUTEROS'):
            content = _clean_routeros_backup_content(content)
        with open(store_path, 'w') as f:
            f.write(content)
        log_callback(ip, hostname, dev_type, "OK", None, duration, store_path)
    except socket.timeout:
        if client:
            try:
                client.close()
            except Exception:
                pass
        log_callback(ip, hostname, dev_type, "Fail_Network", None, None, None)
    except Exception as e:
        if client:
            try:
                client.close()
            except Exception:
                pass
        msg = str(e)
        if "Authentication" in msg or "password" in msg.lower() or "login" in msg.lower():
            log_callback(ip, hostname, dev_type, "Fail_Login", msg, None, None)
        else:
            log_callback(ip, hostname, dev_type, "Fail", msg, None, None)


def run_backup_task(
    devices: List[Tuple],
    configs_dir: str,
    default_username: str,
    default_password: str,
    exclude_pattern: str,
    log_callback: Callable,
    default_connection_type: str = "TELNET",
    ssh_port: int = DEFAULT_SSH_PORT,
    telnet_port: int = DEFAULT_TELNET_PORT,
    timeout_seconds: int = 30,
    app_context=None,
    type_configs: Optional[dict] = None,
) -> None:
    """
    devices: [(ip, hostname, dev_type), ...] 或含 (username, password, connection_type, ssh_port, telnet_port)
    connection_type: TELNET | SSH，缺省用 default_connection_type
    """
    def _to_bytes(v):
        return v.encode() if isinstance(v, str) else v

    # telnetlib.expect 需要 compiled regex (匹配 bytes)
    def _run_and_expect_fixed(tn, cmd, expect, timer):
        tn.write(_to_bytes(cmd) + b"\r\n")
        if isinstance(expect, list):
            cleaned = [re.compile(pat.encode() if isinstance(pat, str) else pat) for pat in expect]
        else:
            p = expect if isinstance(expect, bytes) else expect.encode()
            cleaned = [re.compile(re.escape(p))]
        tn.expect(cleaned, timer)

    def do_one(item):
        ip, hostname, dev_type = item[0], item[1], item[2]
        if exclude_pattern and re.match(exclude_pattern, hostname):
            return
        conn_type = (item[5] if len(item) >= 6 and item[5] else default_connection_type).upper()
        if conn_type not in ("TELNET", "SSH"):
            conn_type = default_connection_type.upper() or "TELNET"
        if len(item) >= 5 and item[3] and item[4]:
            dev_username, dev_password = item[3], item[4]
        else:
            dev_username, dev_password = default_username, default_password
        dev_ssh_port = item[6] if len(item) >= 7 and item[6] is not None else ssh_port
        dev_telnet_port = item[7] if len(item) >= 8 and item[7] is not None else telnet_port

        suffix = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix = hostname.split(".", 1)[0] if "." in hostname else hostname
        store_dir = os.path.join(configs_dir, prefix, hostname)
        os.makedirs(store_dir, exist_ok=True)
        store_path = os.path.join(store_dir, f"{hostname}_{suffix}.txt")

        if conn_type == "SSH":
            _backup_via_ssh(ip, hostname, dev_type, dev_username, dev_password, store_path, log_callback, dev_ssh_port, timeout_seconds, app_context, type_configs)
            return

        start = datetime.datetime.now()
        try:
            # 获取设备驱动（用于登录提示符）
            cfg = None
            if isinstance(type_configs, dict):
                cfg = type_configs.get(dev_type) or type_configs.get((dev_type or '').upper())
            driver = _get_device_driver(dev_type, cfg)
            login_prompt = b"sername"
            password_prompt = b"assword"
            if driver:
                login_prompt = driver.get_login_prompt().encode()
                password_prompt = driver.get_password_prompt().encode()
            
            tn = telnetlib.Telnet(ip, dev_telnet_port, timeout=timeout_seconds)
            tn.read_until(login_prompt, timeout=timeout_seconds)
            _run_and_expect_fixed(tn, dev_username, password_prompt, timeout_seconds)
            
            # 使用驱动获取提示符列表
            prompts = [rb".*#$", rb"^<.*>$", rb"^[\[].*\]>", rb".*> $"]
            if driver:
                prompts = [p.encode() if isinstance(p, str) else p for p in driver.get_prompts()]
            
            _run_and_expect_fixed(
                tn, dev_password,
                prompts,
                timeout_seconds
            )

            # 获取设备驱动
            driver = _get_device_driver(dev_type, cfg)
            if not driver:
                log_callback(ip, hostname, dev_type, "Fail", "Unknown device type or driver failed", None, None)
                tn.close()
                return
            
            # 使用驱动获取初始化命令
            init_commands = driver.get_init_commands()
            for cmd in init_commands:
                tn.write(cmd.encode() + b"\r\n")
                time.sleep(0.3)
                # 等待提示符（使用通用提示符列表）
                prompts = driver.get_prompts()
                if prompts:
                    compiled_prompts = [re.compile(p.encode() if isinstance(p, str) else p) for p in prompts]
                    tn.expect(compiled_prompts, timeout=3)
            
            # RouterOS：发备份命令前先两次短超时 read_until 清掉当前提示符，再发命令并 read_until(output_success, 90)
            dev_type_upper = (dev_type or '').strip().upper()
            if dev_type_upper in ('ROS', 'ROUTEROS'):
                prompt_str = driver.get_prompt()
                prompt_bytes = (prompt_str or 'output_success').encode()
                try:
                    tn.read_until(prompt_bytes, 3)
                    tn.read_until(prompt_bytes, 3)
                except Exception:
                    pass
            
            # 使用驱动获取备份命令
            backup_cmd = driver.get_backup_command()
            tn.write(backup_cmd.encode() + b"\r\n")
            
            # 使用驱动获取提示符
            prompt_str = driver.get_prompt()
            prompt = prompt_str.encode() if prompt_str else b"#"
            result = tn.read_until(prompt, max(90, timeout_seconds))
            tn.close()
            end = datetime.datetime.now()
            duration = int((end - start).total_seconds())
            content = result.decode('utf-8', errors='replace')
            if prompt_str and (prompt_str.strip() == 'output_success') and ('output_success' in content):
                content = content.split('output_success')[0].rstrip()
            if dev_type_upper in ('ROS', 'ROUTEROS'):
                content = _clean_routeros_backup_content(content)
            with open(store_path, 'w') as f:
                f.write(content)
            # 回调: (ip, hostname, dev_type, status, message, duration, config_path)
            log_callback(ip, hostname, dev_type, "OK", None, duration, store_path)

        except socket.timeout:
            log_callback(ip, hostname, dev_type, "Fail_Network", None, None, None)
        except Exception as e:
            err_msg = str(e)
            if "Login" in err_msg or "assword" in err_msg or "incorrect" in err_msg.lower():
                log_callback(ip, hostname, dev_type, "Fail_Login", err_msg, None, None)
            else:
                log_callback(ip, hostname, dev_type, "Fail", err_msg, None, None)

    for item in devices:
        do_one(item)


def run_single_backup(
    device_item: Tuple,
    configs_dir: str,
    default_username: str,
    default_password: str,
    log_callback: Callable,
    default_connection_type: str = "TELNET",
    ssh_port: int = DEFAULT_SSH_PORT,
    telnet_port: int = DEFAULT_TELNET_PORT,
    timeout_seconds: int = 30,
    app_context=None,
    type_configs: Optional[dict] = None,
) -> None:
    """单台设备备份（不应用排除规则）"""
    run_backup_task(
        [device_item],
        configs_dir,
        default_username,
        default_password,
        "",
        log_callback,
        default_connection_type=default_connection_type,
        ssh_port=ssh_port,
        telnet_port=telnet_port,
        timeout_seconds=timeout_seconds,
        app_context=app_context,
        type_configs=type_configs,
    )


def test_connection(
    ip: str,
    username: str,
    password: str,
    dev_type: str,
    connection_type: str = "TELNET",
    ssh_port: int = DEFAULT_SSH_PORT,
    telnet_port: int = DEFAULT_TELNET_PORT,
) -> Tuple[bool, str]:
    """测试连接与登录（Telnet 或 SSH），返回 (成功, 消息)"""
    import re
    conn = (connection_type or "TELNET").upper()
    if conn == "SSH":
        try:
            import paramiko
        except ImportError:
            return False, "未安装 paramiko，无法使用 SSH"
        try:
            u = (username or '').strip()
            p = password if password is not None else ''
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            # 强制仅用密码认证，避免 agent/密钥干扰导致被误判为密码错误
            client.connect(
                hostname=ip,
                port=ssh_port,
                username=u,
                password=p,
                timeout=10,
                allow_agent=False,
                look_for_keys=False,
            )
            client.close()
            return True, "SSH 连接成功"
        except Exception as e:
            msg = str(e)
            if "Authentication" in msg or "password" in msg.lower() or "authenticity" in msg.lower():
                return False, "SSH 登录失败（用户名或密码错误）"
            return False, msg or "SSH 连接失败"
    # Telnet
    dev_type = dev_type.upper()
    try:
        tn = telnetlib.Telnet(ip, telnet_port, timeout=10)
    except socket.timeout:
        return False, "连接超时"
    except Exception as e:
        return False, str(e)
    try:
        tn.read_until(b"sername", timeout=10)
        tn.write(username.encode() + b"\r\n")
        tn.expect([re.compile(rb"assword")], 10)
        tn.write(password.encode() + b"\r\n")
        tn.expect([re.compile(rb".*#$"), re.compile(rb"^<.*>$"), re.compile(rb"^[\[].*\]>"), re.compile(rb".*> $")], 10)
        tn.close()
        return True, "Telnet 连接成功"
    except Exception as e:
        try:
            tn.close()
        except Exception:
            pass
        msg = str(e)
        if "Login" in msg or "incorrect" in msg.lower():
            return False, "登录失败（用户名或密码错误）"
        return False, msg or "连接失败"


def run_backup_async(
    devices: List[Tuple],
    configs_dir: str,
    default_username: str,
    default_password: str,
    exclude_pattern: str,
    log_callback: Callable,
    thread_num: int = 10,
    default_connection_type: str = "TELNET",
    ssh_port: int = DEFAULT_SSH_PORT,
    telnet_port: int = DEFAULT_TELNET_PORT,
    timeout_seconds: int = 30,
    app_context=None,
    type_configs: Optional[dict] = None,
) -> None:
    """多线程异步执行备份"""
    import math
    n = len(devices)
    step = max(1, int(math.ceil(float(n) / thread_num)))
    threads = []
    for i in range(thread_num):
        chunk = devices[int(i * step):int((i + 1) * step)]
        if not chunk:
            continue
        t = threading.Thread(
            target=run_backup_task,
            args=(chunk, configs_dir, default_username, default_password, exclude_pattern, log_callback),
            kwargs={
                "default_connection_type": default_connection_type,
                "ssh_port": ssh_port,
                "telnet_port": telnet_port,
                "timeout_seconds": timeout_seconds,
                "app_context": app_context,
                "type_configs": type_configs,
            },
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
