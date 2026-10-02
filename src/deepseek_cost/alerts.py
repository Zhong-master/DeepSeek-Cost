"""低余量提醒的判定逻辑（纯函数，方便单测）。"""

from __future__ import annotations


def decide_alert(
    total: float | None,
    threshold: float,
    alerted: bool,
    last_alert_ts: float,
    repeat_seconds: float,
    now: float,
    notify_enabled: bool = True,
) -> tuple[bool, bool, float]:
    """返回 (是否应该弹提醒, 新的 alerted, 新的 last_alert_ts)。

    规则：
    * 余额 >= 阈值：解除提醒状态（下一次跌破会重新提醒）
    * 余额 < 阈值：首次跌破立即提醒；持续偏低时每 repeat_seconds 提醒一次，避免刷屏
    """
    if total is None or not notify_enabled:
        return False, alerted, last_alert_ts
    if total >= threshold:
        return False, False, last_alert_ts
    if not alerted:
        return True, True, now
    if repeat_seconds <= 0:
        return False, True, last_alert_ts
    if (now - last_alert_ts) >= repeat_seconds:
        return True, True, now
    return False, True, last_alert_ts
