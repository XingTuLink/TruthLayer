"""Human-readable narratives for drift findings (#33).

Pure functions over the detector ``detail`` payloads — no DB, no ORM.
The same wording powers CLI ``drift list/show`` and the HTML/JSON reports so
the message a human reads stays consistent everywhere.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from truthlayer.domain.enums import ResolutionDecision


class Narrative(NamedTuple):
    title: str
    why: str
    recommendation: str
    suggested_decisions: tuple[str, ...]


_WHY = {
    "conflict": (
        "两个不同来源对同一事项给出了不一致的取值，且生效时间重叠、不存在显式取代关系，"
        "AI 无法自行判断应当引用哪一个，可能据此给出互相矛盾的回答。"
    ),
    "confirmed_stale": (
        "该事实已超过有效期，或其来源文档已被更新版本显式取代；继续引用会向用户传递"
        "已失效的政策、价格或规则。"
    ),
    "reused_stale_value": (
        "现行文档中的取值与一份已被显式取代的旧版文档逐字相同，而版本链最新版"
        "已经改为另一个值；该来源很可能沿用了已失效的旧数据，AI 据此回答会向用户"
        "传递过期的价格或标准。"
    ),
    "possibly_stale": (
        "该事实超过配置的复核阈值后仍未见到更新来源，可能已经失效。当前仅有年龄信号、"
        "置信度较低，需要人工确认而非自动改判。"
    ),
    "superseded": (
        "存在显式声明的新版本文档，旧版本不应再作为 AI 的知识来源；其中未被新版本覆盖的"
        "内容可能继续造成错误引用。"
    ),
    "duplicate": (
        "两个名称异写的实体共享鉴别性事实却尚未归并，可能导致 AI 重复回答、口径分裂，"
        "或在统计与权限判断时把同一对象当成两个对象。"
    ),
}

_RECOMMEND = {
    "conflict": (
        "两个来源对同一事项给出了不同的取值，且目前无法自行判断该信谁。"
        "请核对两份来源的权威度与生效时间，确认应当采用哪一个；"
        "若两者本就并存（例如不同层级、不同分档规则），说明并非冲突，可标记为误报。"
    ),
    "confirmed_stale": (
        "请打开更新版的来源文档，确认其中的替代取值并采纳为新的事实依据；"
        "若经核实旧规则仍然有效，保留原有记录即可。"
    ),
    "reused_stale_value": (
        "请核对版本链最新版文档中的现行取值，更正现行文档后重新扫描；"
        "若经核实两个数值在该场景下本就并存（例如不同渠道、不同口径），"
        "可标记为误报，系统会记住该判断。"
    ),
    "possibly_stale": (
        "请业务负责人复核该来源文件：若确认仍有效，保留即可；"
        "若已更新，请采纳新值或推动修订文档；若无需继续跟踪，可标记为误报。"
    ),
    "superseded": (
        "请确认新版文档已完整覆盖旧版内容并完成切换；"
        "若新旧版本需要同时生效，则保留旧记录。"
    ),
    "duplicate": (
        "请核实两个对象是否为同一个实体：若是，合并为一条记录；"
        "若不是，标记为误报，系统会记住该判断。"
    ),
}

_SUGGESTED = {
    "conflict": (
        ResolutionDecision.ACCEPT_NEWER.value,
        ResolutionDecision.KEEP_OLD.value,
        ResolutionDecision.FALSE_POSITIVE.value,
    ),
    "confirmed_stale": (
        ResolutionDecision.ACCEPT_NEWER.value,
        ResolutionDecision.KEEP_OLD.value,
        ResolutionDecision.FALSE_POSITIVE.value,
    ),
    "reused_stale_value": (
        ResolutionDecision.ACCEPT_NEWER.value,
        ResolutionDecision.FALSE_POSITIVE.value,
    ),
    "possibly_stale": (
        ResolutionDecision.KEEP_OLD.value,
        ResolutionDecision.ACCEPT_NEWER.value,
        ResolutionDecision.FALSE_POSITIVE.value,
    ),
    "superseded": (
        ResolutionDecision.ACCEPT_NEWER.value,
        ResolutionDecision.KEEP_OLD.value,
    ),
    "duplicate": (
        ResolutionDecision.ACCEPT_NEWER.value,
        ResolutionDecision.FALSE_POSITIVE.value,
    ),
}


def narrative(drift_type: str, detail: dict[str, Any]) -> Narrative:
    why = _WHY.get(drift_type, "检测器发现了需要人工确认的知识异常。")
    recommendation = _RECOMMEND.get(
        drift_type, "请核对来源与证据后做出处置（resolve）或忽略（ignore）。"
    )
    suggested = _SUGGESTED.get(drift_type, ())
    return Narrative(
        title=short_title(drift_type, detail),
        why=why,
        recommendation=recommendation,
        suggested_decisions=suggested,
    )


_GROUP_TITLE = {
    "confirmed_stale": (
        "文档《{filename}》已被新版本取代，{count} 条事实随文档失效"
    ),
    "possibly_stale": (
        "文档《{filename}》长期未复核，{count} 条事实可能已过期"
    ),
}

_GROUP_WHY = {
    "confirmed_stale": (
        "这份文档已在版本链中被更新版显式取代；下列事实逐条卡片只是同一份"
        "文档失效的重复信号，AI 若仍引用其中任一条，都会向用户传递已失效的"
        "政策、价格或规则。"
    ),
    "possibly_stale": (
        "这份文档超过配置的复核阈值后仍未见到更新来源；下列事实共享同一个"
        "年龄信号，逐条卡片只是重复，真正需要的是对整份文档时效性的一次"
        "业务确认。"
    ),
}

_GROUP_RECOMMEND = {
    "confirmed_stale": (
        "请以更新版文档为准核对这份文档；展开明细可下钻到个别事实，"
        "逐条保留或标记误报。"
    ),
    "possibly_stale": (
        "请业务负责人复核整份文档：确认仍有效可逐条保留，已更新则采纳新值，"
        "无需继续跟踪可标记误报；展开明细可下钻到单条事实。"
    ),
}


def group_narrative(drift_type: str, filename: str, count: int) -> Narrative:
    """Document-level wording for R11 rollups (same type, one source doc)."""
    return Narrative(
        title=_GROUP_TITLE.get(
            drift_type,
            "文档《{filename}》存在 {count} 条同类问题",
        ).format(filename=filename, count=count),
        why=_GROUP_WHY.get(
            drift_type, "下列事实来自同一份文档，属于同一个文档级问题。"
        ),
        recommendation=_GROUP_RECOMMEND.get(
            drift_type,
            "请核对来源与证据后做出处置（resolve）或忽略（ignore）。",
        ),
        suggested_decisions=tuple(_SUGGESTED.get(drift_type, ())),
    )


def short_title(drift_type: str, detail: dict[str, Any]) -> str:
    """One-line, evidence-safe summary used by ``drift list`` and report cards."""
    subject = detail.get("subject")
    predicate = detail.get("predicate")
    head = f"{subject} / {predicate}" if subject or predicate else "(未命名事实)"

    if drift_type == "conflict":
        return (
            f"{head}：{detail.get('old_value')!s} 与 {detail.get('new_value')!s} 冲突"
            f"（{detail.get('old_source')} ↔ {detail.get('new_source')}）"
        )
    if drift_type == "confirmed_stale":
        return f"{head} 已确认过期 [{detail.get('reason')}]"
    if drift_type == "reused_stale_value":
        return (
            f"{head} 逐字引用已失效值 {detail.get('reused_value')!s}"
            f"（现行版应为 {detail.get('head_value')!s}；"
            f"{detail.get('old_source')} → {detail.get('new_source')}）"
        )
    if drift_type == "possibly_stale":
        age = detail.get("age_days")
        suffix = f"，已 {age} 天未复核" if age is not None else ""
        return f"{head} 长期未更新{suffix}（warning）"
    if drift_type == "superseded":
        return (
            f"文档 {detail.get('old_source')} 已被 "
            f"{detail.get('new_source')} 取代"
        )
    if drift_type == "duplicate":
        return (
            f"{detail.get('entity_a')} ≡ {detail.get('entity_b')}："
            f"{detail.get('predicate')}={detail.get('object')!s} 疑似重复实体"
        )
    return f"{head} [{drift_type}]"


def plain_title(drift_type: str, detail: dict[str, Any]) -> str:
    """Business-facing headline for the report card.

    Unlike :func:`short_title` (which favors the internal detail payload and is
    used by ``drift list``), this strips reason codes / internal symbols so a
    non-technical reader understands the problem at a glance. Pure function —
    no DB, no ORM.
    """
    subject = detail.get("subject") or "(未命名事项)"
    predicate = detail.get("predicate") or "相关信息"

    if drift_type == "conflict":
        old, new = detail.get("old_value"), detail.get("new_value")
        return (
            f"「{subject}」的「{predicate}」在不同来源中取值不一致："
            f"{old!s} 与 {new!s}"
        )
    if drift_type == "confirmed_stale":
        return f"「{subject}」的「{predicate}」已过期，不应再被 AI 引用"
    if drift_type == "reused_stale_value":
        return (
            f"「{subject}」的「{predicate}」正在沿用已失效的旧值"
            f"{detail.get('reused_value')!s}，版本链最新版已改为"
            f"{detail.get('head_value')!s}"
        )
    if drift_type == "possibly_stale":
        age = detail.get("age_days")
        suffix = f"（已 {age} 天未复核）" if age is not None else ""
        return f"「{subject}」的「{predicate}」长期未复核，可能已经过期{suffix}"
    if drift_type == "superseded":
        return (
            f"文档「{detail.get('old_source')}」已被「{detail.get('new_source')}」"
            f"取代，旧版不应再作为依据"
        )
    if drift_type == "duplicate":
        return (
            f"「{detail.get('entity_a')}」与「{detail.get('entity_b')}」"
            f"疑似是同一个对象"
        )
    return f"「{subject}」的「{predicate}」存在需要人工确认的异常"
