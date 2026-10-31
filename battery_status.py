# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated battery telemetry; percentage and voltage are board estimates."""
import struct

PACKET = struct.Struct('!BBBHHHIB')

def decode_battery(data):
    if len(data) != PACKET.size:
        raise ValueError('电量数据长度错误')
    schema, flags, percent, raw, pin_mv, millivolts, uptime, reset = PACKET.unpack(data)
    if schema != 1 or flags & ~63 or raw > 4095 or pin_mv > 3300:
        raise ValueError('电量数据格式错误')
    if flags & 48 == 48 or (flags & 48 and not flags & 8):
        raise ValueError('充电状态矛盾')
    valid = bool(flags & 1)
    if valid and (percent > 100 or not 2500 <= millivolts <= 4500 or not flags & 2
                  or bool(flags & 4) != (percent <= 15)):
        raise ValueError('电量数据范围错误')
    if not valid and percent != 255:
        raise ValueError('无效电量不能显示百分比')
    return dict(available=valid, estimated=True, percent=percent if valid else None,
                millivolts=millivolts if valid else None, low=bool(flags & 4) if valid else False,
                raw=raw, pin_mv=pin_mv, uptime_ms=uptime, reset_reason=reset,
                charge_state=('charging' if flags & 16 else 'complete' if flags & 32 else 'idle') if flags & 8 else 'unknown')

def battery_text(battery, connected, fresh):
    if not connected:
        return '电量：待连接'
    if not fresh:
        return '电量：等待读取'
    charge={'charging':'充电中 · ','complete':'充电已完成 · '}.get(battery.get('charge_state'),'')
    if not battery.get('available'):
        return charge+'电量暂不可读' if charge else '电量：暂不可读'
    prefix = '电量偏低 · ' if battery.get('low') else ''
    return f'{charge}{prefix}电池约 {battery["percent"]}% · {battery["millivolts"]/1000:.2f} V（估算）'
