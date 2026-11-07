"""server.properties 可视化读写：**保留注释、空行、键顺序与未知键**。

实现方式：按行解析成 [{kind: 'comment'|'blank'|'pair', ...}] 的"行模型"，
写回时只替换被改动的 pair 行的值，其余原样输出。
"""
import io
import os
import re
import shutil
import time

# 常用键的元数据（中文标签/类型/说明），未列出的键走"未知键"通道
SCHEMA = [
    ('motd', '服务器标语（MOTD）', 'text', '显示在多人游戏列表里的名字，支持 § 颜色代码'),
    ('server-port', '服务端端口', 'int', 'TCP 监听端口，默认 25565'),
    ('server-ip', '绑定 IP', 'text', '留空表示监听全部网卡'),
    ('max-players', '最大玩家数', 'int', ''),
    ('online-mode', '正版验证', 'bool', 'true=只允许正版账号登录（离线服请设 false）'),
    ('white-list', '开启白名单', 'bool', ''),
    ('enforce-whitelist', '强制白名单', 'bool', '白名单移除的在线玩家会被踢出'),
    ('gamemode', '默认游戏模式', 'enum:survival,creative,adventure,spectator', ''),
    ('difficulty', '难度', 'enum:peaceful,easy,normal,hard', ''),
    ('hardcore', '极限模式', 'bool', ''),
    ('pvp', '允许 PVP', 'bool', ''),
    ('level-name', '世界名称', 'text', ''),
    ('level-seed', '世界种子', 'text', ''),
    ('level-type', '世界类型', 'text', '如 minecraft:normal / flat / large_biomes'),
    ('view-distance', '视距（区块）', 'int', ''),
    ('simulation-distance', '模拟距离（区块）', 'int', ''),
    ('spawn-protection', '出生点保护半径', 'int', ''),
    ('allow-flight', '允许飞行', 'bool', ''),
    ('allow-nether', '允许下界', 'bool', ''),
    ('allow-command-block', '允许命令方块', 'bool', ''),
    ('enable-command-block', '启用命令方块', 'bool', ''),
    ('spawn-monsters', '生成怪物', 'bool', ''),
    ('spawn-animals', '生成动物', 'bool', ''),
    ('spawn-npcs', '生成村民', 'bool', ''),
    ('generate-structures', '生成结构', 'bool', ''),
    ('max-world-size', '世界最大半径', 'int', ''),
    ('enable-rcon', '启用 RCON', 'bool', '开启后面板可通过 RCON 取玩家列表/TPS'),
    ('rcon.port', 'RCON 端口', 'int', ''),
    ('rcon.password', 'RCON 密码', 'text', ''),
    ('query.port', 'Query 端口', 'int', ''),
    ('enable-query', '启用 Query', 'bool', ''),
    ('enable-status', '在服务器列表显示状态', 'bool', ''),
    ('player-idle-timeout', '挂机踢出（分钟）', 'int', '0=不踢'),
    ('op-permission-level', 'OP 权限等级', 'int', '1~4'),
    ('function-permission-level', '函数权限等级', 'int', ''),
    ('max-tick-time', '单 tick 超时(ms)', 'int', '-1 关闭看门狗'),
    ('network-compression-threshold', '网络压缩阈值', 'int', ''),
    ('sync-chunk-writes', '同步区块写入', 'bool', ''),
    ('force-gamemode', '强制游戏模式', 'bool', ''),
    ('hide-online-players', '隐藏在线玩家', 'bool', ''),
    ('prevent-proxy-connections', '阻止代理连接', 'bool', ''),
    ('rate-limit', '速率限制', 'int', ''),
    ('text-filtering-config', '文本过滤配置', 'text', ''),
]

SCHEMA_MAP = {k: (label, typ, desc) for k, label, typ, desc in SCHEMA}


def parse(text: str) -> dict:
    """解析为行模型。"""
    lines = []
    for raw in text.split('\n'):
        s = raw.rstrip('\r')
        st = s.strip()
        if not st:
            lines.append({'kind': 'blank', 'raw': s})
        elif st.startswith('#') or st.startswith('!'):
            lines.append({'kind': 'comment', 'raw': s})
        elif '=' in st and not st.startswith('='):
            key, val = st.split('=', 1)
            lines.append({'kind': 'pair', 'key': key.strip(), 'value': val, 'raw': s,
                          'prefix': s[:len(s) - len(s.lstrip())]})
        else:
            lines.append({'kind': 'comment', 'raw': s})
    props, order = {}, []
    for ln in lines:
        if ln['kind'] == 'pair':
            props[ln['key']] = ln['value']
            if ln['key'] not in order:
                order.append(ln['key'])
    return {'lines': lines, 'props': props, 'order': order}


def render(model: dict, updates: dict) -> str:
    """按模型重新输出；updates 里出现过的键替换值，未改动原样保留。"""
    out = []
    written = set()
    for ln in model['lines']:
        if ln['kind'] == 'pair' and ln['key'] in updates:
            out.append(f"{ln['key']}={updates[ln['key']]}")
            written.add(ln['key'])
        else:
            out.append(ln['raw'])
    # 新增键追加在末尾（保留未知键的同时支持补键）
    extra = [k for k in updates if k not in written]
    if extra:
        if out and out[-1].strip():
            out.append('')
        out.append('# 由 芮拓MC面板追加')
        for k in extra:
            out.append(f'{k}={updates[k]}')
    text = '\n'.join(out)
    if not text.endswith('\n'):
        text += '\n'
    return text


def read_props(path: str) -> dict:
    if not os.path.isfile(path):
        return {'exists': False, 'props': {}, 'order': [], 'lines': []}
    with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    model = parse(text)
    model['exists'] = True
    model['path'] = path
    return model


def write_props(path: str, updates: dict, backup: bool = True) -> dict:
    """读 → 改 → 写；写前备份 server.properties.bak-<ts>。校验键值合法性。"""
    err = validate(updates)
    if err:
        return {'ok': False, 'error': err}
    model = read_props(path)
    if not model['exists']:
        base = '\n'.join([f'# 由 芮拓MC面板生成 {time.strftime("%Y-%m-%d %H:%M:%S")}'])
        model = parse(base + '\n')
    if backup and os.path.isfile(path):
        try:
            shutil.copy2(path, path + '.bak-' + time.strftime('%Y%m%d-%H%M%S'))
        except Exception:
            pass
    text = render(model, {k: str(v) for k, v in updates.items()})
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)
    return {'ok': True, 'written': len(updates), 'path': path}


_BOOLS = {'true': 'true', 'false': 'false'}


def validate(updates: dict) -> str:
    """键值校验：类型不符返回错误文案。"""
    for k, v in updates.items():
        if not re.match(r'^[A-Za-z0-9._\-]+$', str(k)):
            return f'非法键名：{k}'
        sval = str(v)
        if '\n' in sval or '\r' in sval:
            return f'键 {k} 的值不能包含换行'
        meta = SCHEMA_MAP.get(k)
        if not meta:
            continue
        typ = meta[1]
        if typ == 'bool':
            if sval.lower() not in _BOOLS:
                return f'{k} 必须是 true 或 false'
        elif typ == 'int':
            try:
                int(sval)
            except ValueError:
                return f'{k} 必须是整数'
        elif typ.startswith('enum:'):
            allowed = typ.split(':', 1)[1].split(',')
            if sval not in allowed:
                return f'{k} 只能是：{"、".join(allowed)}'
    return ''


def annotate(model: dict) -> list:
    """给前端：按行输出 + 元数据 + 是否已知键。"""
    rows = []
    for i, ln in enumerate(model['lines']):
        if ln['kind'] == 'pair':
            meta = SCHEMA_MAP.get(ln['key'])
            rows.append({'i': i, 'kind': 'pair', 'key': ln['key'], 'value': ln['value'],
                         'label': meta[0] if meta else '', 'type': meta[1] if meta else 'text',
                         'desc': meta[2] if meta else '', 'known': bool(meta)})
        else:
            rows.append({'i': i, 'kind': ln['kind'], 'raw': ln['raw']})
    return rows
