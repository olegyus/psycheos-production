"""
PsycheOS Interpreter Bot - Policy Engine
Validation and automatic repair of outputs
"""
import re
from typing import Dict, Any, List, Tuple


class PolicyEngine:
    """Validates and repairs Interpreter outputs."""
    
    def __init__(self):
        """Initialize with compiled regex patterns."""
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
        
        # Replacement mappings
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
    
    def validate(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate output against all rules.
        
        Returns:
            {
                'valid': bool,
                'violations': [list of violation dicts],
                'critical_count': int,
                'error_count': int
            }
        """
        violations = []
        
        # R001: Hypothesis count
        v = self._check_hypothesis_count(output)
        if v:
            violations.append(v)
        
        # R002: Diagnostic language
        v = self._check_diagnostic_language(output)
        if v:
            violations.append(v)
        
        # R003: Trauma claims
        v = self._check_trauma_claims(output)
        if v:
            violations.append(v)
        
        # R004: Pathology language
        v = self._check_pathology_language(output)
        if v:
            violations.append(v)
        
        # R006: Uncertainty required
        v = self._check_uncertainty(output)
        if v:
            violations.append(v)
        
        # R010: Mode constraints
        v = self._check_mode_constraints(output)
        if v:
            violations.append(v)
        
        critical_count = sum(1 for v in violations if v['severity'] == 'CRITICAL')
        error_count = sum(1 for v in violations if v['severity'] == 'ERROR')
        
        return {
            'valid': len(violations) == 0,
            'violations': violations,
            'critical_count': critical_count,
            'error_count': error_count
        }
    
    def _check_hypothesis_count(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R001: Check hypothesis count limits."""
        mode = output.get('meta', {}).get('mode', 'STANDARD')
        count = len(output.get('interpretative_hypotheses', []))
        max_allowed = 1 if mode == 'LOW_DATA' else 3
        
        if count > max_allowed:
            return {
                'rule_id': 'R001',
                'severity': 'ERROR',
                'message': f'Hypothesis count {count} exceeds {max_allowed} for {mode} mode',
                'count': count,
                'max': max_allowed
            }
        return None
    
    def _check_diagnostic_language(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R002: Check for diagnostic language."""
        violations = []
        
        for idx, hyp in enumerate(output.get('interpretative_hypotheses', [])):
            text = hyp.get('hypothesis_text', '') + ' ' + hyp.get('limitations', '')
            
            for pattern in self.diagnostic_patterns:
                if pattern.search(text):
                    violations.append({
                        'hypothesis_index': idx,
                        'term': pattern.pattern
                    })
        
        if violations:
            return {
                'rule_id': 'R002',
                'severity': 'CRITICAL',
                'message': f'Diagnostic language detected ({len(violations)} instances)',
                'violations': violations
            }
        return None
    
    def _check_trauma_claims(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R003: Check for definitive trauma claims."""
        violations = []
        
        for idx, hyp in enumerate(output.get('interpretative_hypotheses', [])):
            text = hyp.get('hypothesis_text', '')
            
            for pattern in self.trauma_patterns:
                if pattern.search(text):
                    violations.append({
                        'hypothesis_index': idx,
                        'term': pattern.pattern
                    })
        
        if violations:
            return {
                'rule_id': 'R003',
                'severity': 'ERROR',
                'message': f'Definitive trauma claims ({len(violations)} instances)',
                'violations': violations
            }
        return None
    
    def _check_pathology_language(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R004: Check for pathologizing language."""
        violations = []
        
        for idx, hyp in enumerate(output.get('interpretative_hypotheses', [])):
            text = hyp.get('hypothesis_text', '') + ' ' + hyp.get('limitations', '')
            
            for pattern in self.pathology_patterns:
                if pattern.search(text):
                    violations.append({
                        'hypothesis_index': idx,
                        'term': pattern.pattern
                    })
        
        if violations:
            return {
                'rule_id': 'R004',
                'severity': 'ERROR',
                'message': f'Pathology language detected ({len(violations)} instances)',
                'violations': violations
            }
        return None
    
    def _check_uncertainty(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R006: Check for substantive uncertainty."""
        profile = output.get('uncertainty_profile', {})
        
        if profile.get('overall_confidence') == 'high':
            if not profile.get('data_gaps') and not profile.get('ambiguities'):
                return {
                    'rule_id': 'R006',
                    'severity': 'ERROR',
                    'message': 'High confidence without substantive uncertainty'
                }
        
        return None
    
    def _check_mode_constraints(self, output: Dict[str, Any]) -> Dict[str, Any] | None:
        """R010: Check mode-specific constraints."""
        mode = output.get('meta', {}).get('mode', 'STANDARD')
        violations = []
        
        if mode == 'LOW_DATA':
            count = len(output.get('interpretative_hypotheses', []))
            if count > 1:
                violations.append({'constraint': 'hypothesis_count', 'actual': count})
            
            confidence = output.get('uncertainty_profile', {}).get('overall_confidence')
            if confidence != 'low':
                violations.append({'constraint': 'confidence_level', 'actual': confidence})
        
        if violations:
            return {
                'rule_id': 'R010',
                'severity': 'CRITICAL',
                'message': f'{mode} mode constraints violated',
                'violations': violations
            }
        return None
    
    def repair(self, output: Dict[str, Any], validation_result: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Attempt to repair violations.
        
        Returns:
            (repaired_output, repair_report)
        """
        if validation_result['valid']:
            return output, {'repaired': False, 'changes': []}
        
        repaired = output.copy()
        changes = []
        
        for violation in validation_result['violations']:
            rule_id = violation['rule_id']
            
            if rule_id == 'R001':
                repaired = self._repair_hypothesis_count(repaired, violation)
                changes.append(f"Reduced hypothesis count to {violation['max']}")
            
            elif rule_id == 'R002':
                repaired = self._repair_diagnostic_language(repaired)
                changes.append("Removed diagnostic language")
            
            elif rule_id == 'R003':
                repaired = self._repair_trauma_claims(repaired)
                changes.append("Added modality to trauma statements")
            
            elif rule_id == 'R004':
                repaired = self._repair_pathology_language(repaired)
                changes.append("Neutralized pathology language")
            
            elif rule_id == 'R006':
                repaired = self._repair_uncertainty(repaired)
                changes.append("Enhanced uncertainty profile")
            
            elif rule_id == 'R010':
                repaired = self._repair_mode_constraints(repaired)
                changes.append("Enforced mode constraints")
        
        # Update policy flags
        if 'policy_flags' in repaired:
            repaired['policy_flags']['repair_applied'] = True
            repaired['policy_flags']['violations'] = [
                {'rule': v['rule_id'], 'severity': v['severity']}
                for v in validation_result['violations']
            ]
        
        return repaired, {'repaired': True, 'changes': changes}
    
    def _repair_hypothesis_count(self, output: Dict[str, Any], violation: Dict) -> Dict[str, Any]:
        """Reduce hypothesis count to allowed maximum."""
        max_allowed = violation['max']
        if 'interpretative_hypotheses' in output:
            output['interpretative_hypotheses'] = output['interpretative_hypotheses'][:max_allowed]
        return output
    
    def _repair_diagnostic_language(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Replace diagnostic terms with neutral language."""
        if 'interpretative_hypotheses' not in output:
            return output
        
        for hyp in output['interpretative_hypotheses']:
            for term, replacement in self.diagnostic_replacements.items():
                hyp['hypothesis_text'] = re.sub(
                    rf'\b{re.escape(term)}\b',
                    replacement,
                    hyp['hypothesis_text'],
                    flags=re.IGNORECASE
                )
        
        if 'policy_flags' in output:
            output['policy_flags']['contains_diagnosis'] = False
        
        return output
    
    def _repair_trauma_claims(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Add modality to trauma claims."""
        if 'interpretative_hypotheses' not in output:
            return output
        
        for hyp in output['interpretative_hypotheses']:
            hyp['hypothesis_text'] = re.sub(
                r'\bтравма присутствует\b',
                'потенциально сложные переживания могут присутствовать',
                hyp['hypothesis_text'],
                flags=re.IGNORECASE
            )
            hyp['hypothesis_text'] = re.sub(
                r'\b(явно|очевидно) травм\w+',
                'потенциально значимые переживания',
                hyp['hypothesis_text'],
                flags=re.IGNORECASE
            )
        
        if 'policy_flags' in output:
            output['policy_flags']['contains_trauma_claim'] = False
        
        return output
    
    def _repair_pathology_language(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Replace pathology terms with neutral language."""
        if 'interpretative_hypotheses' not in output:
            return output
        
        for hyp in output['interpretative_hypotheses']:
            for term, replacement in self.pathology_replacements.items():
                hyp['hypothesis_text'] = re.sub(
                    rf'\b{re.escape(term)}\b',
                    replacement,
                    hyp['hypothesis_text'],
                    flags=re.IGNORECASE
                )
        
        if 'policy_flags' in output:
            output['policy_flags']['contains_pathology_language'] = False
        
        return output
    
    def _repair_uncertainty(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Add substantive uncertainty acknowledgment."""
        if 'uncertainty_profile' not in output:
            return output
        
        profile = output['uncertainty_profile']
        
        if not profile.get('data_gaps'):
            profile['data_gaps'] = [
                "Текущие жизненные обстоятельства клиента",
                "Исторический контекст символических элементов",
                "Феноменологические детали субъективного опыта"
            ]
        
        if not profile.get('ambiguities'):
            profile['ambiguities'] = [
                "Символические значения культурно и персонально вариативны",
                "Существуют множественные валидные интерпретации этого материала"
            ]
        
        if profile.get('overall_confidence') == 'high':
            profile['overall_confidence'] = 'moderate'
        
        if 'policy_flags' in output:
            output['policy_flags']['uncertainty_present'] = True
        
        return output
    
    def _repair_mode_constraints(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Enforce mode-specific constraints."""
        mode = output.get('meta', {}).get('mode', 'STANDARD')
        
        if mode == 'LOW_DATA':
            # Keep only first hypothesis
            if 'interpretative_hypotheses' in output:
                if len(output['interpretative_hypotheses']) > 1:
                    output['interpretative_hypotheses'] = [output['interpretative_hypotheses'][0]]
            
            # Force confidence to low
            if 'uncertainty_profile' in output:
                output['uncertainty_profile']['overall_confidence'] = 'low'
        
        return output


if __name__ == '__main__':
    print("✓ Policy Engine module loaded")
    print("  Rules: R001, R002, R003, R004, R006, R010")
    
    # Test
    engine = PolicyEngine()
    test_output = {
        'meta': {'mode': 'STANDARD'},
        'interpretative_hypotheses': [],
        'uncertainty_profile': {'overall_confidence': 'low', 'data_gaps': ['test'], 'ambiguities': []},
        'policy_flags': {}
    }
    
    result = engine.validate(test_output)
    print(f"  Test validation: {'PASS' if result['valid'] else 'FAIL'}")
