#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""简易配置合规检查规则（基础版）

说明：
- 这是面向 ISP 骨干网设备配置的「基础合规检查」，采用非常保守的字符串规则。
- 仅做静态文本扫描，不区分厂商语法，也不会下发命令。
"""

from typing import List, Dict


def check_config(text: str, hostname: str = "", device_type: str = "") -> Dict:
    """对单份配置进行合规检查，返回结构化结果。

    返回示例：
    {
        "status": "warn",  # ok / warn / fail
        "rules": [
            {"id": "no_telnet", "level": "fail", "passed": false, "message": "..."},
            ...
        ]
    }
    """
    lines = text.splitlines() if text else []
    lower_text = text.lower() if text else ""

    results: List[Dict] = []

    # 规则 1：禁止 telnet（建议仅启用 SSH）
    has_telnet = "telnet" in lower_text
    has_ssh = "ssh" in lower_text
    if has_telnet:
        results.append({
            "id": "no_telnet",
            "level": "fail",
            "passed": False,
            "message": "检测到配置中存在 telnet 相关字符串，建议禁止 telnet，仅允许 SSH 远程管理。",
        })
    else:
        results.append({
            "id": "no_telnet",
            "level": "ok",
            "passed": True,
            "message": "未发现 telnet 相关配置。",
        })

    # 规则 2：是否启用 SSH / 加密的管理方式
    if has_ssh:
        results.append({
            "id": "ssh_present",
            "level": "ok",
            "passed": True,
            "message": "检测到 SSH 相关配置。",
        })
    else:
        results.append({
            "id": "ssh_present",
            "level": "warn",
            "passed": False,
            "message": "未检测到 SSH 相关配置，请确认设备已启用加密的远程管理方式。",
        })

    # 规则 3：SNMP community 不应为 public/private
    bad_snmp = []
    for line in lines:
        L = line.strip().lower()
        if "snmp" in L and "community" in L:
            if "public" in L or "private" in L:
                bad_snmp.append(line.strip())
    if bad_snmp:
        results.append({
            "id": "snmp_community",
            "level": "fail",
            "passed": False,
            "message": "检测到使用弱 SNMP community（public/private），建议修改为复杂随机串。\n示例行：{}".format("; ".join(bad_snmp[:3])),
        })
    else:
        results.append({
            "id": "snmp_community",
            "level": "ok",
            "passed": True,
            "message": "未发现明显弱 SNMP community（public/private）。",
        })

    # 汇总整体状态：有 fail -> fail；否则有 warn -> warn；否则 ok
    overall = "ok"
    levels = [r["level"] for r in results]
    if any(l == "fail" for l in levels):
        overall = "fail"
    elif any(l == "warn" for l in levels):
        overall = "warn"

    return {
        "status": overall,
        "hostname": hostname or "",
        "device_type": device_type or "",
        "rules": results,
    }

