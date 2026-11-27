"""Universal rule evaluation engine (scalar, collection diff, new items) & anti-spam state machine."""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from omni_monitor.models import AlertPolicy, MonitorTask, Observation, RuleConfig, TaskState

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}

_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_SAFE_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
}


class _AttrDict(dict):
    """Allows dot-access inside safe expressions (e.g. `metrics.change_pct`)."""

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        val = self.get(item)
        if isinstance(val, dict):
            return _AttrDict(val)
        return val


def safe_eval_expr(expression: str, context: dict[str, Any]) -> Any:
    """Safely evaluate a restricted Python expression without calling builtin eval()."""
    tree = ast.parse(expression, mode="eval")
    wrapped_ctx = {
        k: (_AttrDict(v) if isinstance(v, dict) else v) for k, v in context.items()
    }

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in wrapped_ctx:
                return wrapped_ctx[node.id]
            raise ValueError(f"Unknown variable in rule expression: {node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ValueError("Private attribute access is forbidden")
            obj = _eval(node.value)
            if isinstance(obj, dict):
                val = obj.get(node.attr)
                return _AttrDict(val) if isinstance(val, dict) else val
            return getattr(obj, node.attr)
        if isinstance(node, ast.Subscript):
            obj = _eval(node.value)
            key = _eval(node.slice)
            return obj[key]
        if isinstance(node, ast.List):
            return [_eval(elt) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(_eval(elt) for elt in node.elts)
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
        if isinstance(node, ast.BinOp):
            op_fn = _BIN_OPS.get(type(node.op))
            if not op_fn:
                raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
            return op_fn(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(_eval(v) for v in node.values)
            if isinstance(node.op, ast.Or):
                return any(_eval(v) for v in node.values)
            raise ValueError(f"Unsupported bool op: {type(node.op).__name__}")
        if isinstance(node, ast.Compare):
            left = _eval(node.left)
            for op, comp in zip(node.ops, node.comparators):
                op_fn = _CMP_OPS.get(type(op))
                if not op_fn:
                    raise ValueError(f"Unsupported compare op: {type(op).__name__}")
                right = _eval(comp)
                if not op_fn(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
                raise ValueError("Only whitelisted pure functions are allowed")
            args = [_eval(a) for a in node.args]
            return _SAFE_FUNCS[node.func.id](*args)
        raise ValueError(f"Disallowed syntax in expression: {type(node).__name__}")

    return _eval(tree)


def _extract_field_value(obs: Observation | None, field_name: str) -> Any:
    if obs is None:
        return None
    if field_name == "value":
        return obs.value
    if field_name == "state":
        return obs.state
    if field_name == "fingerprint":
        return obs.fingerprint
    if field_name.startswith("metrics."):
        key = field_name.split(".", 1)[1]
        return obs.metrics.get(key)
    return obs.metrics.get(field_name)


@dataclass
class EvaluationResult:
    matched: bool
    title: str = ""
    summary: str = ""
    severity: str = "normal"
    detail_blocks: list[dict[str, Any]] = field(default_factory=list)
    scalar_value: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _build_scalar_blocks(
    task: MonitorTask,
    curr: Observation,
    condition_text: str,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    kv_items = list(curr.display_fields)
    if not kv_items:
        val_str = (
            f"{curr.value:g} {curr.unit}".strip()
            if curr.value is not None
            else str(curr.state or "-")
        )
        kv_items.append({"label": "当前值", "value": val_str})
    kv_items.append({"label": "触发条件", "value": condition_text})
    if curr.source_name:
        kv_items.append({"label": "数据源", "value": curr.source_name})
    blocks.append({"type": "kv_grid", "items": kv_items})

    if curr.items:
        diff_items = []
        for key, item in list(curr.items.items())[:30]:
            diff_items.append(
                {
                    "code": str(item.get("code") or key),
                    "name": str(item.get("name") or key),
                    "event": str(item.get("event") or "MATCH"),
                    "before_text": str(item.get("before_text") or ""),
                    "after_text": str(item.get("after_text") or ""),
                    "is_watchlist": bool(item.get("is_watchlist", False)),
                    "url": item.get("url"),
                }
            )
        if diff_items:
            blocks.append(
                {
                    "type": "diff_list",
                    "title": f"{task.name} 命中标的明细",
                    "items": diff_items,
                }
            )

    if history and len(history) >= 2 and not curr.items:
        points = [
            [pt["timestamp"], pt["value"]]
            for pt in history[-60:]
            if pt.get("value") is not None
        ]
        if points:
            chart_block: dict[str, Any] = {
                "type": "line_chart",
                "title": f"{task.name} 近期走势",
                "unit": curr.unit,
                "points": points,
            }
            if task.rule.target is not None:
                chart_block["target_line"] = task.rule.target
            blocks.append(chart_block)

    if curr.source_url:
        blocks.append(
            {
                "type": "actions",
                "links": [{"label": "查看原始行情/来源", "url": curr.source_url}],
            }
        )
    return blocks


def _classify_qdii_change(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Semantic classification for QDII purchase status / daily limit transitions."""
    b_cat = before.get("limit_category")
    a_cat = after.get("limit_category")
    b_lim = before.get("daily_limit")
    a_lim = after.get("daily_limit")

    if b_cat == "QUOTA_SUSPENDED" and a_cat in ("LIMITED", "UNLIMITED"):
        return "REOPENED"
    if b_cat != "QUOTA_SUSPENDED" and a_cat == "QUOTA_SUSPENDED":
        return "QUOTA_SUSPENDED"
    if b_cat == "LIMITED" and a_cat == "UNLIMITED":
        return "LIMIT_UP"
    if b_cat == "UNLIMITED" and a_cat == "LIMITED":
        return "LIMIT_DOWN"
    if isinstance(b_lim, (int, float)) and isinstance(a_lim, (int, float)):
        if a_lim > b_lim:
            return "LIMIT_UP"
        if a_lim < b_lim:
            return "LIMIT_DOWN"
    return "CHANGED"


def _format_item_limit(item: dict[str, Any]) -> str:
    cat = item.get("limit_category")
    lim = item.get("daily_limit")
    if cat == "UNLIMITED":
        return "无限额"
    if cat == "QUOTA_SUSPENDED":
        return "暂停申购"
    if cat == "HOLIDAY_SUSPENDED":
        return "休市暂停"
    if isinstance(lim, (int, float)):
        if lim >= 10000:
            return f"限额 {lim / 10000:g}万"
        return f"限额 {lim:g}元"
    return str(item.get("purchase_status") or "-")


def _passes_item_filters(item: dict[str, Any], rule: RuleConfig) -> bool:
    if rule.filter_currency:
        curr = str(item.get("currency") or "")
        if curr and curr not in rule.filter_currency:
            return False
    if rule.filter_keywords:
        haystack = f"{item.get('name', '')} {item.get('title', '')} {item.get('fund_type', '')}".upper()
        if not any(kw.upper() in haystack for kw in rule.filter_keywords):
            return False
    return True


def evaluate_rule(
    task: MonitorTask,
    curr: Observation,
    prev: Observation | None,
    history: list[dict[str, Any]] | None = None,
    seen_keys: set[str] | None = None,
) -> EvaluationResult:
    """Evaluate a MonitorTask's rule against the latest Observation."""
    rule = task.rule
    severity = task.alert_policy.severity

    # 1. Threshold
    if rule.type == "threshold":
        val = _extract_field_value(curr, rule.field)
        if not isinstance(val, (int, float)):
            return EvaluationResult(matched=False)
        matched = False
        cond_text = ""
        if rule.op == "gt" and rule.target is not None:
            matched = val > rule.target
            cond_text = f"{rule.field} > {rule.target:g}"
        elif rule.op == "gte" and rule.target is not None:
            matched = val >= rule.target
            cond_text = f"{rule.field} >= {rule.target:g}"
        elif rule.op == "lt" and rule.target is not None:
            matched = val < rule.target
            cond_text = f"{rule.field} < {rule.target:g}"
        elif rule.op == "lte" and rule.target is not None:
            matched = val <= rule.target
            cond_text = f"{rule.field} <= {rule.target:g}"
        elif rule.op == "between" and rule.min_value is not None and rule.max_value is not None:
            matched = rule.min_value <= val <= rule.max_value
            cond_text = f"{rule.min_value:g} <= {rule.field} <= {rule.max_value:g}"
        elif rule.op == "outside" and rule.min_value is not None and rule.max_value is not None:
            matched = val < rule.min_value or val > rule.max_value
            cond_text = f"{rule.field} 超出 [{rule.min_value:g}, {rule.max_value:g}]"

        if not matched:
            return EvaluationResult(matched=False, scalar_value=float(val))
        top_summary = str(curr.metrics.get("top_summary") or "").strip()
        summary = (
            top_summary
            if top_summary
            else f"当前 {val:g} {curr.unit}（满足 {cond_text}）".strip()
        )
        return EvaluationResult(
            matched=True,
            title=f"{task.name}: 命中 {val:g} {curr.unit}".strip() if top_summary else f"{task.name} 触发阈值",
            summary=summary,
            severity=severity,
            detail_blocks=_build_scalar_blocks(task, curr, cond_text, history),
            scalar_value=float(val),
        )

    # 2. Cross (edge-triggered breakout)
    if rule.type == "cross":
        curr_val = _extract_field_value(curr, rule.field)
        prev_val = _extract_field_value(prev, rule.field)
        if not isinstance(curr_val, (int, float)) or not isinstance(prev_val, (int, float)) or rule.target is None:
            return EvaluationResult(
                matched=False,
                scalar_value=float(curr_val) if isinstance(curr_val, (int, float)) else None,
            )
        if rule.op == "cross_above":
            matched = prev_val < rule.target <= curr_val
            cond_text = f"上穿 {rule.target:g}"
        elif rule.op == "cross_below":
            matched = prev_val > rule.target >= curr_val
            cond_text = f"下穿 {rule.target:g}"
        else:
            matched = False
            cond_text = ""

        if not matched:
            return EvaluationResult(matched=False, scalar_value=float(curr_val))
        return EvaluationResult(
            matched=True,
            title=f"{task.name} {cond_text}",
            summary=f"从 {prev_val:g} 变动至 {curr_val:g} {curr.unit}".strip(),
            severity=severity,
            detail_blocks=_build_scalar_blocks(task, curr, cond_text, history),
            scalar_value=float(curr_val),
        )

    # 3. Delta (% change over time window)
    if rule.type == "delta":
        curr_val = _extract_field_value(curr, rule.field)
        if not isinstance(curr_val, (int, float)) or not history or rule.change_pct is None:
            return EvaluationResult(
                matched=False,
                scalar_value=float(curr_val) if isinstance(curr_val, (int, float)) else None,
            )
        window = rule.window_seconds or 3600
        cutoff = curr.timestamp - timedelta(seconds=window)
        # Find earliest point within the window
        baseline = None
        for pt in history:
            pt_ts = datetime.fromisoformat(pt["timestamp"])
            if pt_ts >= cutoff and pt.get("value") not in (None, 0):
                baseline = float(pt["value"])
                break
        if baseline is None:
            return EvaluationResult(matched=False, scalar_value=float(curr_val))

        pct = (curr_val - baseline) / baseline * 100.0
        matched = pct >= rule.change_pct if rule.change_pct >= 0 else pct <= rule.change_pct
        if not matched:
            return EvaluationResult(matched=False, scalar_value=float(curr_val))

        cond_text = f"{window // 60}分钟涨跌幅 {'≥' if rule.change_pct >= 0 else '≤'} {rule.change_pct:+.2f}%"
        return EvaluationResult(
            matched=True,
            title=f"{task.name} 剧烈波动 ({pct:+.2f}%)",
            summary=f"基准 {baseline:g} → 当前 {curr_val:g} {curr.unit} ({pct:+.2f}%)".strip(),
            severity=severity,
            detail_blocks=_build_scalar_blocks(task, curr, cond_text, history),
            scalar_value=float(curr_val),
        )

    # 4. State / fingerprint change
    if rule.type == "state_change":
        if prev is None:
            return EvaluationResult(matched=False)
        curr_val = _extract_field_value(curr, rule.field)
        prev_val = _extract_field_value(prev, rule.field)
        if curr_val == prev_val:
            return EvaluationResult(matched=False)
        return EvaluationResult(
            matched=True,
            title=f"{task.name} 状态发生变化",
            summary=f"{rule.field}: {prev_val} → {curr_val}",
            severity=severity,
            detail_blocks=_build_scalar_blocks(task, curr, f"{rule.field} 变化", history),
        )

    # 5. Safe expression
    if rule.type == "expr" and rule.expression:
        ctx = {
            "value": curr.value,
            "metrics": curr.metrics,
            "state": curr.state,
            "prev_value": prev.value if prev else None,
            "prev_metrics": prev.metrics if prev else {},
        }
        try:
            matched = bool(safe_eval_expr(rule.expression, ctx))
        except Exception:
            return EvaluationResult(matched=False, scalar_value=curr.value)
        if not matched:
            return EvaluationResult(matched=False, scalar_value=curr.value)
        return EvaluationResult(
            matched=True,
            title=f"{task.name} 命中自定义条件",
            summary=f"当前 {curr.value} {curr.unit}（表达式：{rule.expression}）".strip(),
            severity=severity,
            detail_blocks=_build_scalar_blocks(task, curr, rule.expression, history),
            scalar_value=curr.value,
        )

    # 6. Collection Diff (e.g. full QDII daily purchase limit universe)
    if rule.type == "collection_diff":
        if prev is None or prev.items is None or curr.items is None:
            # First run establishes baseline without false alerts
            return EvaluationResult(matched=False)

        prev_items = prev.items
        curr_items = curr.items
        watch_fields = rule.watch_fields or ["limit_category", "daily_limit"]
        ignore_cats = set(rule.ignore_categories)
        watchlist = set(rule.watchlist_codes)

        changes: list[dict[str, Any]] = []
        watchlist_hits = 0

        for key, after in curr_items.items():
            if not _passes_item_filters(after, rule):
                continue
            a_cat = after.get("limit_category")
            if a_cat in ignore_cats:
                continue

            before = prev_items.get(key)
            if before is None:
                event = "ADDED"
                if rule.only_events and event not in rule.only_events:
                    continue
                is_watch = key in watchlist
                if is_watch:
                    watchlist_hits += 1
                changes.append(
                    {
                        "code": key,
                        "name": after.get("name", key),
                        "event": event,
                        "before_text": "新增",
                        "after_text": _format_item_limit(after),
                        "is_watchlist": is_watch,
                    }
                )
                continue

            b_cat = before.get("limit_category")
            # Ignore transitions leaving holiday/non-distributed categories
            if b_cat in ignore_cats:
                continue

            if all(before.get(f) == after.get(f) for f in watch_fields):
                continue

            event = _classify_qdii_change(before, after)
            if rule.only_events and event not in rule.only_events:
                continue

            is_watch = key in watchlist
            if is_watch:
                watchlist_hits += 1
            changes.append(
                {
                    "code": key,
                    "name": after.get("name", key),
                    "event": event,
                    "before_text": _format_item_limit(before),
                    "after_text": _format_item_limit(after),
                    "is_watchlist": is_watch,
                }
            )

        if not changes:
            return EvaluationResult(matched=False)

        # Prioritize watchlist items first, then positive reopenings/increases
        event_rank = {"REOPENED": 0, "LIMIT_UP": 1, "QUOTA_SUSPENDED": 2, "LIMIT_DOWN": 3, "ADDED": 4, "CHANGED": 5}
        changes.sort(key=lambda c: (0 if c["is_watchlist"] else 1, event_rank.get(c["event"], 9), c["code"]))

        effective_severity = "high" if watchlist_hits > 0 else severity
        top = changes[0]
        if len(changes) == 1:
            title = f"{task.name}: {top['name']} ({top['event']})"
            summary = f"{top['code']} {top['before_text']} → {top['after_text']}"
        else:
            title = f"{task.name}: {len(changes)} 项额度/状态变动"
            if watchlist_hits:
                title = f"⭐ {title}（含 {watchlist_hits} 只自选）"
            preview = "；".join(
                f"{c['name']}({c['before_text']}→{c['after_text']})" for c in changes[:2]
            )
            summary = preview + (f" 等共 {len(changes)} 项" if len(changes) > 2 else "")

        detail_blocks = [
            {
                "type": "kv_grid",
                "items": [
                    {"label": "变动数量", "value": f"{len(changes)} 项"},
                    {"label": "命中自选", "value": f"{watchlist_hits} 项"},
                    {"label": "当前监控池", "value": f"{len(curr_items)} 项"},
                ],
            },
            {
                "type": "diff_list",
                "title": "详细变动清单",
                "items": changes[:50],
            },
        ]
        return EvaluationResult(
            matched=True,
            title=title,
            summary=summary,
            severity=effective_severity,
            detail_blocks=detail_blocks,
            metadata={"changes_count": len(changes), "watchlist_hits": watchlist_hits},
        )

    # 7. New items (e.g. newly published T-1 QDII limit announcements)
    if rule.type == "new_items":
        if curr.items is None:
            return EvaluationResult(matched=False)
        if seen_keys is not None:
            if not seen_keys and (prev is None or prev.items is None):
                # First run builds baseline so existing historical announcements don't spam
                return EvaluationResult(matched=False)
            prev_keys = set(seen_keys)
            if prev is not None and prev.items is not None:
                prev_keys.update(prev.items.keys())
        else:
            if prev is None or prev.items is None:
                return EvaluationResult(matched=False)
            prev_keys = set(prev.items.keys())

        new_entries: list[dict[str, Any]] = []
        watchlist = set(rule.watchlist_codes)
        watchlist_hits = 0

        for key, item in curr.items.items():
            if key in prev_keys:
                continue
            if not _passes_item_filters(item, rule):
                continue
            code = str(item.get("code") or key)
            is_watch = code in watchlist
            if is_watch:
                watchlist_hits += 1
            new_entries.append({**item, "id": key, "is_watchlist": is_watch})

        if not new_entries:
            return EvaluationResult(matched=False)

        new_entries.sort(key=lambda x: (0 if x["is_watchlist"] else 1, x.get("publish_date", "")), reverse=False)
        top = new_entries[0]
        title = (
            f"新限购公告: {top.get('fund_name') or top.get('code')}"
            if len(new_entries) == 1
            else f"发现 {len(new_entries)} 条新 QDII 限购公告"
        )
        eff_str = f"生效:{top['effective_date']} · " if top.get("effective_date") else ""
        lim_str = f"{top['limit_summary']} · " if top.get("limit_summary") and top.get("limit_summary") != top.get("action") else ""
        summary = f"{eff_str}{lim_str}{top.get('title', '')}"[:120]

        detail_blocks: list[dict[str, Any]] = [
            {
                "type": "diff_list",
                "title": "最新限购/暂停公告",
                "items": [
                    {
                        "code": e.get("code", ""),
                        "name": e.get("fund_name") or e.get("title", ""),
                        "event": e.get("action", "NOTICE"),
                        "before_text": f"生效:{e.get('effective_date') or '见正文'}",
                        "after_text": e.get("limit_summary") or e.get("title", "")[:40],
                        "is_watchlist": e["is_watchlist"],
                        "url": e.get("pdf_url"),
                    }
                    for e in new_entries[:30]
                ],
            }
        ]
        if top.get("snippet"):
            detail_blocks.append(
                {"type": "markdown", "title": "公告摘要", "content": top["snippet"]}
            )
        links = [
            {"label": f"查看 {e.get('code', '')} 公告原文 PDF", "url": e["pdf_url"]}
            for e in new_entries[:5]
            if e.get("pdf_url")
        ]
        if links:
            detail_blocks.append({"type": "actions", "links": links})

        return EvaluationResult(
            matched=True,
            title=title,
            summary=summary,
            severity="high" if watchlist_hits > 0 else severity,
            detail_blocks=detail_blocks,
            metadata={"new_count": len(new_entries)},
        )

    return EvaluationResult(matched=False)


def _should_rearm_hysteresis(
    rule: RuleConfig, policy: AlertPolicy, scalar_value: float | None
) -> bool:
    if policy.rearm_value is None or scalar_value is None:
        return True
    if rule.op in ("gt", "gte", "cross_above"):
        return scalar_value <= policy.rearm_value
    if rule.op in ("lt", "lte", "cross_below"):
        return scalar_value >= policy.rearm_value
    # Fallback when using expr or other rule types
    if rule.target is not None:
        return (
            scalar_value <= policy.rearm_value
            if policy.rearm_value < rule.target
            else scalar_value >= policy.rearm_value
        )
    return True


def apply_alert_policy(
    policy: AlertPolicy,
    state: TaskState,
    result: EvaluationResult,
    now: datetime,
    rule: RuleConfig | None = None,
) -> tuple[bool, TaskState, bool]:
    """
    Apply anti-spam state machine (cooldown / hysteresis / once).
    Returns: (should_send_alert, updated_state, should_disable_task)
    """
    next_state = state.model_copy(deep=True)
    next_state.last_checked_at = now

    if not result.matched:
        if policy.mode == "hysteresis" and next_state.status == "TRIGGERED":
            if rule is None or _should_rearm_hysteresis(rule, policy, result.scalar_value):
                next_state.status = "IDLE"
        return False, next_state, False

    # Condition matched!
    if policy.mode == "once":
        next_state.status = "TRIGGERED"
        next_state.last_triggered_at = now
        return True, next_state, True

    if policy.mode == "hysteresis":
        if next_state.status == "TRIGGERED":
            return False, next_state, False
        next_state.status = "TRIGGERED"
        next_state.last_triggered_at = now
        return True, next_state, False

    # Default: cooldown mode
    if (
        next_state.last_triggered_at is not None
        and (now - next_state.last_triggered_at).total_seconds() < policy.cooldown_seconds
    ):
        return False, next_state, False

    next_state.status = "TRIGGERED"
    next_state.last_triggered_at = now
    return True, next_state, False
