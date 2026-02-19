"""
PsycheOS Interpreter — Policy Engine
Validation and automatic repair of Claude outputs.
"""
import re
from typing import Any, Dict, List, Tuple


class PolicyEngine:
    """Validates and repairs Interpreter outputs against safety rules."""

    def __init__(self) -> None:
        # Diagnostic language patterns
        self.diagnostic_patterns = [
            re.compile(r'\bPTSD\b', re.IGNORECASE),
            re.compile(r'\bдепресси[яи]\b', re.IGNORECASE),
            re.compile(r'\bтревожн\w+ расстройств\w+', re.IGNORECASE),
            re.compile(r'\bОКР\b', re.IGNORECASE),
            re.compile(r'\bбиполярн\w+', re.IGNORECASE),
            re.compile(r'\bшизофрени\w+', re.IGNORECASE),
            re.compile(r'\bдиагноз\b', re.IGNORECASE),
        ]

        # Trauma claim patterns
        self.trauma_patterns = [
            re.compile(r'\b(явно|очевидно|определённо) травм\w+', re.IGNORECASE),
            re.compile(r'\bтравма присутствует\b', re.IGNORECASE),
            re.compile(r'\bбыл\w* травмирован\w*', re.IGNORECASE),
            re.compile(r'\bдетская травма\b', re.IGNORECASE),
        ]

        # Pathology language patterns
        self.pathology_patterns = [
            re.compile(r'\bдисфункциональн\w+', re.IGNORECASE),
            re.compile(r'\bмаладаптивн\w+', re.IGNORECASE),
            re.compile(r'\bпатологическ\w+', re.IGNORECASE),
            re.compile(r'\bсломан\w+', re.IGNORECASE),
            re.compile(r'\bповрежд[её]нн\w+', re.IGNORECASE),
            re.compile(r'\bненормальн\w+', re.IGNORECASE),
        ]

        self.diagnostic_replacements = {
            'PTSD': 'паттерны, которые могут относиться к непереработанным сложным переживаниям',
            'депрессия': 'состояния сниженного настроения',
            'депрессии': 'состояний сниженного настроения',
            'тревожное расстройство': 'паттерны повышенной тревоги',
            'ОКР': 'повторяющиеся паттерны мыслей и поведения',
            'биполярное': 'вариативность настроения',
            'шизофрения': 'сложности обработки реальности',
            'диагноз': 'наблюдаемые паттерны',
        }

        self.pathology_replacements = {
            'дисфункциональный': 'находящийся под напряжением',
            'дисфункциональная': 'находящаяся под напряжением',
            'маладаптивный': 'не служащий в настоящее время',
            'маладаптивная': 'не служащая в настоящее время',
            'патологический': 'заметный паттерн',
            'патологическая': 'заметная структура',
            'сломанный': 'фрагментированный',
            'сломанная': 'фрагментированная',
            'повреждённый': 'затронутый',
            'повреждённая': 'затронутая',
            'ненормальный': 'атипичный',
            'ненормальная': 'атипичная',
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def validate(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate output against all rules.

        Returns:
            {valid, violations, critical_count, error_count}
        """
        violations: List[Dict[str, Any]] = []

        for check in [
            self._check_hypothesis_count,
            self._check_diagnostic_language,
            self._check_trauma_claims,
            self._check_pathology_language,
            self._check_uncertainty,
            self._check_mode_constraints,
        ]:
            v = check(output)
            if v:
                violations.append(v)

        return {
            "valid": len(violations) == 0,
            "violations": violations,
            "critical_count": sum(1 for v in violations if v["severity"] == "CRITICAL"),
            "error_count": sum(1 for v in violations if v["severity"] == "ERROR"),
        }

    def repair(
        self, output: Dict[str, Any], validation_result: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Attempt to repair all violations.

        Returns:
            (repaired_output, repair_report)
        """
        if validation_result["valid"]:
            return output, {"repaired": False, "changes": []}

        repaired = output.copy()
        changes: List[str] = []

        repair_map = {
            "R001": (self._repair_hypothesis_count, "Reduced hypothesis count"),
            "R002": (self._repair_diagnostic_language, "Removed diagnostic language"),
            "R003": (self._repair_trauma_claims, "Added modality to trauma statements"),
            "R004": (self._repair_pathology_language, "Neutralised pathology language"),
            "R006": (self._repair_uncertainty, "Enhanced uncertainty profile"),
            "R010": (self._repair_mode_constraints, "Enforced mode constraints"),
        }

        for violation in validation_result["violations"]:
            rule_id = violation["rule_id"]
            if rule_id in repair_map:
                fn, msg = repair_map[rule_id]
                if rule_id == "R001":
                    repaired = fn(repaired, violation)
                else:
                    repaired = fn(repaired)
                changes.append(msg)

        if "policy_flags" in repaired:
            repaired["policy_flags"]["repair_applied"] = True
            repaired["policy_flags"]["violations"] = [
                {"rule": v["rule_id"], "severity": v["severity"]}
                for v in validation_result["violations"]
            ]

        return repaired, {"repaired": True, "changes": changes}

    # ── Validation checks ──────────────────────────────────────────────────────

    def _check_hypothesis_count(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R001: Hypothesis count limit."""
        mode = output.get("meta", {}).get("mode", "STANDARD")
        count = len(output.get("interpretative_hypotheses", []))
        max_allowed = 1 if mode == "LOW_DATA" else 3

        if count > max_allowed:
            return {
                "rule_id": "R001",
                "severity": "ERROR",
                "message": f"Hypothesis count {count} exceeds {max_allowed} for {mode} mode",
                "count": count,
                "max": max_allowed,
            }
        return None

    def _check_diagnostic_language(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R002: No diagnostic language."""
        found = []
        for idx, hyp in enumerate(output.get("interpretative_hypotheses", [])):
            text = hyp.get("hypothesis_text", "") + " " + hyp.get("limitations", "")
            for pattern in self.diagnostic_patterns:
                if pattern.search(text):
                    found.append({"hypothesis_index": idx, "term": pattern.pattern})

        if found:
            return {
                "rule_id": "R002",
                "severity": "CRITICAL",
                "message": f"Diagnostic language detected ({len(found)} instances)",
                "violations": found,
            }
        return None

    def _check_trauma_claims(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R003: No definitive trauma claims."""
        found = []
        for idx, hyp in enumerate(output.get("interpretative_hypotheses", [])):
            text = hyp.get("hypothesis_text", "")
            for pattern in self.trauma_patterns:
                if pattern.search(text):
                    found.append({"hypothesis_index": idx, "term": pattern.pattern})

        if found:
            return {
                "rule_id": "R003",
                "severity": "ERROR",
                "message": f"Definitive trauma claims ({len(found)} instances)",
                "violations": found,
            }
        return None

    def _check_pathology_language(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R004: No pathologising language."""
        found = []
        for idx, hyp in enumerate(output.get("interpretative_hypotheses", [])):
            text = hyp.get("hypothesis_text", "") + " " + hyp.get("limitations", "")
            for pattern in self.pathology_patterns:
                if pattern.search(text):
                    found.append({"hypothesis_index": idx, "term": pattern.pattern})

        if found:
            return {
                "rule_id": "R004",
                "severity": "ERROR",
                "message": f"Pathology language detected ({len(found)} instances)",
                "violations": found,
            }
        return None

    def _check_uncertainty(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R006: Substantive uncertainty required."""
        profile = output.get("uncertainty_profile", {})
        if profile.get("overall_confidence") == "high":
            if not profile.get("data_gaps") and not profile.get("ambiguities"):
                return {
                    "rule_id": "R006",
                    "severity": "ERROR",
                    "message": "High confidence without substantive uncertainty",
                }
        return None

    def _check_mode_constraints(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R010: Mode-specific constraints."""
        mode = output.get("meta", {}).get("mode", "STANDARD")
        if mode != "LOW_DATA":
            return None

        found = []
        count = len(output.get("interpretative_hypotheses", []))
        if count > 1:
            found.append({"constraint": "hypothesis_count", "actual": count})

        confidence = output.get("uncertainty_profile", {}).get("overall_confidence")
        if confidence != "low":
            found.append({"constraint": "confidence_level", "actual": confidence})

        if found:
            return {
                "rule_id": "R010",
                "severity": "CRITICAL",
                "message": f"LOW_DATA mode constraints violated",
                "violations": found,
            }
        return None

    # ── Repair methods ─────────────────────────────────────────────────────────

    def _repair_hypothesis_count(
        self, output: Dict[str, Any], violation: Dict[str, Any]
    ) -> Dict[str, Any]:
        max_allowed = violation["max"]
        if "interpretative_hypotheses" in output:
            output["interpretative_hypotheses"] = output["interpretative_hypotheses"][:max_allowed]
        return output

    def _repair_diagnostic_language(self, output: Dict[str, Any]) -> Dict[str, Any]:
        for hyp in output.get("interpretative_hypotheses", []):
            for term, replacement in self.diagnostic_replacements.items():
                hyp["hypothesis_text"] = re.sub(
                    rf'\b{re.escape(term)}\b',
                    replacement,
                    hyp["hypothesis_text"],
                    flags=re.IGNORECASE,
                )
        if "policy_flags" in output:
            output["policy_flags"]["contains_diagnosis"] = False
        return output

    def _repair_trauma_claims(self, output: Dict[str, Any]) -> Dict[str, Any]:
        for hyp in output.get("interpretative_hypotheses", []):
            hyp["hypothesis_text"] = re.sub(
                r'\bтравма присутствует\b',
                'потенциально сложные переживания могут присутствовать',
                hyp["hypothesis_text"],
                flags=re.IGNORECASE,
            )
            hyp["hypothesis_text"] = re.sub(
                r'\b(явно|очевидно) травм\w+',
                'потенциально значимые переживания',
                hyp["hypothesis_text"],
                flags=re.IGNORECASE,
            )
        if "policy_flags" in output:
            output["policy_flags"]["contains_trauma_claim"] = False
        return output

    def _repair_pathology_language(self, output: Dict[str, Any]) -> Dict[str, Any]:
        for hyp in output.get("interpretative_hypotheses", []):
            for term, replacement in self.pathology_replacements.items():
                hyp["hypothesis_text"] = re.sub(
                    rf'\b{re.escape(term)}\b',
                    replacement,
                    hyp["hypothesis_text"],
                    flags=re.IGNORECASE,
                )
        if "policy_flags" in output:
            output["policy_flags"]["contains_pathology_language"] = False
        return output

    def _repair_uncertainty(self, output: Dict[str, Any]) -> Dict[str, Any]:
        profile = output.get("uncertainty_profile", {})
        if not profile.get("data_gaps"):
            profile["data_gaps"] = [
                "Текущие жизненные обстоятельства клиента",
                "Исторический контекст символических элементов",
                "Феноменологические детали субъективного опыта",
            ]
        if not profile.get("ambiguities"):
            profile["ambiguities"] = [
                "Символические значения культурно и персонально вариативны",
                "Существуют множественные валидные интерпретации этого материала",
            ]
        if profile.get("overall_confidence") == "high":
            profile["overall_confidence"] = "moderate"
        if "policy_flags" in output:
            output["policy_flags"]["uncertainty_present"] = True
        return output

    def _repair_mode_constraints(self, output: Dict[str, Any]) -> Dict[str, Any]:
        if output.get("meta", {}).get("mode") == "LOW_DATA":
            hyps = output.get("interpretative_hypotheses", [])
            if len(hyps) > 1:
                output["interpretative_hypotheses"] = [hyps[0]]
            if "uncertainty_profile" in output:
                output["uncertainty_profile"]["overall_confidence"] = "low"
        return output
