"""变更单号识别（grayroll-05 独验收口径）。

命运取舍（甲路径）拍板：发布说明必须含可识别变更单号，缺单号整笔拒写。
正则是后端写口、历史检索与并发脚本 release_ticket_regex_05 的同一口径，
前端 frontend/js/ticket.js 保持同义镜像（UI 提示用，以后端为准）。

兼容常见单号形态：
- 标准前缀-数字：CHG-20261126-05、OPS_123456、rl-0501
- 前缀直连数字：CHG2026112605
- 井号单号：#12345
- 中文/英文标签显式引出：变更单号：2026112605、Ticket CHG-0501
"""
from __future__ import annotations

import re

CHANGE_TICKET_RE = re.compile(
    r"(?:变更单号|变更编号|变更单|单号|change[_\s-]?ticket|ticket\s*no\.?|ticket|issue|jira)"
    r"\s*[:：#]?\s*"
    r"(?=[A-Za-z0-9._-]{1,40}\d)"
    r"(?P<label>[A-Za-z0-9][A-Za-z0-9._-]{1,40})"
    r"|(?P<std>[A-Z]{2,12}[-_][A-Z0-9]*\d{2,}(?:[-_]\d{2,})*)"
    r"|(?P<bare>[A-Z]{2,12}\d{3,})"
    r"|(?P<hash>\x23[0-9]{3,})",
    re.IGNORECASE,
)


def extract_change_ticket(text: object) -> str:
    """从说明文本抽取变更单号；抽不到返回空串。"""
    if not isinstance(text, str) or not text:
        return ""
    m = CHANGE_TICKET_RE.search(text)
    if not m:
        return ""
    named = m.groupdict()
    for key in ("label", "std", "bare", "hash"):
        if named.get(key):
            return named[key]
    return m.group(0)


def has_change_ticket(text: object) -> bool:
    return bool(extract_change_ticket(text))
