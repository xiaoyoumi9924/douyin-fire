"""Account-local friend organization; grouping never changes delivery identity."""
from copy import deepcopy
from app.config import ConfigError
from app.rules import names, rule_for


def group_label(value):
    if not isinstance(value,str):raise ConfigError('分组名称必须是文字')
    value=value.strip()
    if len(value)>20 or any(ord(char)<32 or ord(char)==127 for char in value):raise ConfigError('分组名称最多 20 个字符，不能包含换行或控制字符')
    return value


def groups_for(config):
    groups=config.get('friend_groups',{})
    if not isinstance(groups,dict):raise ConfigError('好友分组格式不正确')
    current=set(names(config))
    result={name:group_label(group) for name,group in groups.items() if name in current}
    return {name:group for name,group in result.items() if group}


def apply_batch(config,selected,operation,group=None):
    if not isinstance(selected,list) or not selected or len(selected)>50 or any(not isinstance(name,str) for name in selected):raise ConfigError('请勾选 1–50 位好友')
    selected=list(dict.fromkeys(selected))
    if any(name not in names(config) for name in selected):raise ConfigError('好友名单已变化，请刷新后重新选择')
    if operation not in {'group','pause','resume'}:raise ConfigError('不支持此批量操作')
    updated=deepcopy(config)
    if operation=='group':
        label=group_label(group)
        groups=groups_for(config)
        for name in selected:
            if label:groups[name]=label
            else:groups.pop(name,None)
        if len(set(groups.values()))>12:raise ConfigError('每个账户最多保留 12 个分组')
        updated['friend_groups']=groups
    else:
        updated.setdefault('friend_rules',{})
        for name in selected:
            rule=rule_for(config,name)
            rule['enabled']=operation=='resume'
            updated['friend_rules'][name]=rule
    return updated
