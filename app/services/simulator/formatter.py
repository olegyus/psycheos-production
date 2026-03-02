"""Парсинг и форматирование ответов Claude v1.1 → Telegram.

v1.1: Парсит структурированный вывод супервизора:
  SIGNAL, ACTIVE_LAYER, MATCH, CASCADE_PROB, DELTA, CRISIS WARNING
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from app.services.simulator.schemas import (
    IterationLog, DeltaValues, SignalType,
)


@dataclass
class ParsedResponse:
    """Разобранный ответ Claude v1.1."""
    client_text: str = ""
    supervisor_block: str = ""
    signal: Optional[str] = None
    signal_type: SignalType = SignalType.GREEN
    fsm_state: Optional[str] = None
    active_layer: Optional[str] = None
    match_score: float = 0.0
    cascade_prob: float = 0.0
    delta_trust: int = 0
    delta_tension: int = 0
    delta_uncertainty: int = 0
    delta_defense: int = 0
    delta_cognitive: int = 0
    crisis_warning: bool = False
    signal_reason: str = ""
    raw: str = ""


def parse_claude_response(raw: str) -> ParsedResponse:
    """Разбирает ответ Claude на блок клиента и структурированный блок супервизора."""

    result = ParsedResponse(raw=raw)

    parts = re.split(r'\n-{3,}\n', raw, maxsplit=1)

    if len(parts) >= 2:
        result.client_text = parts[0].strip()
        supervisor_raw = parts[1].strip()
        supervisor_raw = re.sub(r'\n-{3,}\s*$', '', supervisor_raw).strip()
        result.supervisor_block = supervisor_raw
    else:
        result.client_text = raw.strip()
        return result

    block = result.supervisor_block

    signal_match = re.search(r'SIGNAL:\s*(🟢|🟡|🔴)', block)
    if signal_match:
        result.signal = signal_match.group(1)
        _signal_map = {"🟢": SignalType.GREEN, "🟡": SignalType.YELLOW, "🔴": SignalType.RED}
        result.signal_type = _signal_map.get(result.signal, SignalType.GREEN)

    fsm_match = re.search(r'SUPERVISOR\s*\[S(\d)', block)
    if fsm_match:
        result.fsm_state = f"S{fsm_match.group(1)}"

    layer_match = re.search(r'ACTIVE_LAYER:\s*(L\d)', block)
    if layer_match:
        result.active_layer = layer_match.group(1)

    match_match = re.search(r'MATCH:\s*([\d.]+)', block)
    if match_match:
        try:
            result.match_score = float(match_match.group(1))
        except ValueError:
            pass

    cascade_match = re.search(r'CASCADE_PROB:\s*([\d.]+)', block)
    if cascade_match:
        try:
            result.cascade_prob = float(cascade_match.group(1))
        except ValueError:
            pass

    delta_match = re.search(
        r'DELTA:\s*trust=([+\-]?\d+)\s+tension_L0=([+\-]?\d+)\s+'
        r'uncertainty=([+\-]?\d+)\s+defense=([+\-]?\d+)\s+cognitive=([+\-]?\d+)',
        block,
    )
    if delta_match:
        result.delta_trust = int(delta_match.group(1))
        result.delta_tension = int(delta_match.group(2))
        result.delta_uncertainty = int(delta_match.group(3))
        result.delta_defense = int(delta_match.group(4))
        result.delta_cognitive = int(delta_match.group(5))

    result.crisis_warning = "CRISIS WARNING" in block

    lines = block.split("\n")
    reason_lines = []
    started = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("📊", "SIGNAL:", "ACTIVE_LAYER:", "MATCH:",
                                "CASCADE_PROB:", "DELTA:", "⚠️")):
            started = True
            continue
        if started and stripped and not stripped.startswith("---"):
            reason_lines.append(stripped)
    result.signal_reason = " ".join(reason_lines[:2])

    return result


def build_iteration_log(
    parsed: ParsedResponse,
    replica_id: int,
    specialist_input: str = "",
) -> IterationLog:
    """Строит структурированный IterationLog из ParsedResponse."""
    return IterationLog(
        replica_id=replica_id,
        specialist_input=specialist_input[:200],
        fsm_before=parsed.fsm_state or "",
        fsm_after=parsed.fsm_state or "",
        active_layer_before=parsed.active_layer or "",
        active_layer_after=parsed.active_layer or "",
        signal=parsed.signal_type,
        signal_reason=parsed.signal_reason[:300],
        regulatory_match_score=parsed.match_score,
        delta=DeltaValues(
            trust=parsed.delta_trust,
            tension_L0=parsed.delta_tension,
            uncertainty=parsed.delta_uncertainty,
            defense_activation=parsed.delta_defense,
            cognitive_access=parsed.delta_cognitive,
        ),
        cascade_probability=parsed.cascade_prob,
        crisis_warning=parsed.crisis_warning,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═══════════════════════════════════════════════════════════════════════════

def format_for_telegram(parsed: ParsedResponse) -> str:
    """Форматирует разобранный ответ для Telegram (HTML)."""

    parts = []

    client_escaped = _escape_html(parsed.client_text)
    parts.append(f"🗣 <b>Клиент:</b>\n{client_escaped}")

    if parsed.supervisor_block:
        supervisor_escaped = _escape_html(parsed.supervisor_block)
        parts.append(f"\n{'─' * 30}\n{supervisor_escaped}")

    if parsed.crisis_warning:
        parts.append(f"\n🚨 <b>CRISIS WARNING</b> — Рекомендуется стабилизация L0")

    return "\n".join(parts)


def format_intro(
    case_name: str,
    client_info: str,
    crisis: str,
    goal: str,
    mode: str,
    first_reply: str,
    cci: float = 0.0,
) -> str:
    """Форматирует вводное сообщение при старте сессии."""
    # Guard against None values passed from callers
    cci = cci or 0.0
    case_name = case_name or ""
    client_info = client_info or ""
    crisis = crisis or ""
    goal = goal or ""
    mode = mode or ""

    parsed = parse_claude_response(first_reply)

    header = (
        f"🔬 <b>PsycheOS Simulator v1.1</b>\n\n"
        f"📋 <b>Кейс:</b> {_escape_html(case_name)}\n"
        f"👤 <b>Клиент:</b> {_escape_html(client_info)}\n"
        f"⚠️ <b>Кризис:</b> {_escape_html(crisis)}\n"
        f"🎯 <b>Цель:</b> {_escape_html(goal)}\n"
        f"📖 <b>Режим:</b> {_escape_html(mode)}\n"
        f"📊 <b>CCI:</b> {cci:.2f}\n\n"
        f"{'─' * 30}\n"
        f"Сессия начинается. Клиент входит в кабинет.\n"
        f"{'─' * 30}\n\n"
    )

    body = format_for_telegram(parsed)

    return header + body


def _escape_html(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
