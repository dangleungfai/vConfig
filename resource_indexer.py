# -*- coding: utf-8 -*-
"""Build a queryable resource index from backed up device configs."""
import re
from datetime import datetime
from pathlib import Path

from models import ConfigResourceInterface, ConfigResourceParseRun, Device, db


SERVICE_DESCRIPTION_RE = re.compile(r'\b(9809[-_\w]*)\b', re.IGNORECASE)


def latest_config_files(configs_dir):
    """Return one latest config file per device directory."""
    root = Path(configs_dir)
    latest = []
    if not root.exists():
        return latest
    for device_dir in root.glob('*/*'):
        if not device_dir.is_dir():
            continue
        files = [p for p in device_dir.iterdir() if p.is_file()]
        if not files:
            continue
        latest.append(max(files, key=lambda p: p.stat().st_mtime))
    return latest


def rebuild_config_resource_index(configs_dir, app_context=None):
    """Rebuild the interface-level resource index from latest backup files."""
    ctx = app_context() if app_context else None
    if ctx:
        ctx.push()
    run = ConfigResourceParseRun(status='running')
    try:
        db.session.add(run)
        db.session.commit()
        files = latest_config_files(configs_dir)
        devices = {d.hostname: d for d in Device.query.all()}
        rows = []
        for path in files:
            hostname = path.parent.name
            device = devices.get(hostname)
            try:
                text = path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
            parsed = parse_config_resources(
                text,
                gateway=hostname,
                device_profile=(device.device_type if device else ''),
                device_model='',
            )
            for item in parsed:
                item.update({
                    'device_id': device.id if device else None,
                    'config_path': str(path),
                    'config_mtime': mtime,
                })
                rows.append(ConfigResourceInterface(**item))

        ConfigResourceInterface.query.delete(synchronize_session=False)
        if rows:
            db.session.bulk_save_objects(rows)
        run.status = 'success'
        run.finished_at = datetime.utcnow()
        run.scanned_files = len(files)
        run.indexed_interfaces = len(rows)
        db.session.commit()
        db.session.refresh(run)
        return run
    except Exception as exc:
        db.session.rollback()
        try:
            run.status = 'failed'
            run.finished_at = datetime.utcnow()
            run.error = str(exc)
            db.session.add(run)
            db.session.commit()
        except Exception:
            db.session.rollback()
        raise
    finally:
        if ctx:
            ctx.pop()


def parse_config_resources(text, gateway, device_profile='', device_model=''):
    """Parse interface resources from common Cisco/Huawei/H3C style configs."""
    lines = text.splitlines()
    bgp_remote_as = _parse_bgp_remote_as(lines)
    items = []
    for name, block in _iter_interface_blocks(lines):
        item = _parse_interface_block(block)
        if not _has_resource_signal(item):
            continue
        vrf = item.get('vrf_name') or ''
        remote_as_values = bgp_remote_as.get(vrf) or []
        desc = item.get('interface_description') or ''
        items.append({
            'device_profile': device_profile or '',
            'device_model': device_model or '',
            'gateway': gateway,
            'interface_name': name,
            'interface_description': desc,
            'vrf_name': vrf,
            'pe_address': item.get('pe_address') or '',
            'secondary_ip': ','.join(item.get('secondary_ip') or []),
            'vlan_id': item.get('vlan_id') or '',
            'tunnel_source': item.get('tunnel_source') or '',
            'tunnel_destination': item.get('tunnel_destination') or '',
            'bandwidth': item.get('bandwidth') or '',
            'qos_policy': item.get('qos_policy') or '',
            'remote_as': ','.join(remote_as_values),
            'backup_name': _flag_from_description(desc, ('backup', '备')),
            'load_balance': _flag_from_description(desc, ('balance', '负载')),
            'customer_info': _customer_info_from_description(desc),
        })
    return items


def _iter_interface_blocks(lines):
    current_name = None
    current = []
    for raw in lines:
        line = raw.rstrip()
        match = re.match(r'^\s*interface\s+(.+?)\s*$', line, re.IGNORECASE)
        if match:
            if current_name:
                yield current_name, current
            current_name = match.group(1).strip()
            current = []
            continue
        if current_name:
            if line.strip() in ('!', '#'):
                yield current_name, current
                current_name = None
                current = []
            else:
                current.append(line.strip())
    if current_name:
        yield current_name, current


def _parse_interface_block(block):
    item = {'secondary_ip': []}
    for line in block:
        low = line.lower()
        if low.startswith('description '):
            item['interface_description'] = line.split(None, 1)[1].strip()
        elif low.startswith('ip vrf forwarding '):
            item['vrf_name'] = line.split(None, 3)[3].strip()
        elif low.startswith('ip binding vpn-instance '):
            item['vrf_name'] = line.split(None, 3)[3].strip()
        elif low.startswith('vpn-instance '):
            parts = line.split(None, 1)
            if len(parts) > 1:
                item['vrf_name'] = parts[1].strip()
        elif low.startswith('ip address '):
            parts = line.split()
            if len(parts) >= 3:
                ip = parts[2]
                if 'secondary' in low:
                    item['secondary_ip'].append(ip)
                else:
                    item['pe_address'] = ip
        elif low.startswith('encapsulation dot1q '):
            parts = line.split()
            if len(parts) >= 3:
                item['vlan_id'] = parts[2]
        elif low.startswith('vlan-type dot1q '):
            parts = line.split()
            if len(parts) >= 3:
                item['vlan_id'] = parts[2]
        elif low.startswith('bandwidth '):
            item['bandwidth'] = line.split(None, 1)[1].strip()
        elif low.startswith('tunnel source '):
            item['tunnel_source'] = line.split(None, 2)[2].strip()
        elif low.startswith('tunnel destination '):
            item['tunnel_destination'] = line.split(None, 2)[2].strip()
        elif 'service-policy' in low or low.startswith('qos '):
            item['qos_policy'] = line
    return item


def _parse_bgp_remote_as(lines):
    remote_as = {}
    in_bgp = False
    current_vrf = ''
    for raw in lines:
        line = raw.strip()
        low = line.lower()
        if low.startswith('router bgp') or re.match(r'^bgp\s+\d+', low):
            in_bgp = True
            current_vrf = ''
            continue
        if not in_bgp:
            continue
        if low in ('!', '#'):
            current_vrf = ''
            continue
        m = re.match(r'address-family\s+ipv4\s+vrf\s+(.+)', line, re.IGNORECASE)
        if m:
            current_vrf = m.group(1).strip()
            continue
        if low.startswith('exit-address-family'):
            current_vrf = ''
            continue
        m = re.match(r'neighbor\s+\S+\s+remote-as\s+(\S+)', line, re.IGNORECASE)
        if m:
            key = current_vrf
            remote_as.setdefault(key, [])
            value = m.group(1)
            if value not in remote_as[key]:
                remote_as[key].append(value)
    return remote_as


def _has_resource_signal(item):
    return bool(
        item.get('interface_description')
        or item.get('vrf_name')
        or item.get('pe_address')
        or item.get('vlan_id')
        or item.get('tunnel_source')
        or item.get('tunnel_destination')
    )


def _customer_info_from_description(desc):
    desc = (desc or '').strip()
    if not desc:
        return ''
    match = SERVICE_DESCRIPTION_RE.search(desc)
    if match:
        return desc[match.end():].strip(' -_:|')
    return desc


def _flag_from_description(desc, words):
    value = (desc or '').lower()
    for word in words:
        if word.lower() in value:
            return '是'
    return ''
