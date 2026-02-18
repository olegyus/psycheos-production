"""
PsycheOS Interpreter Bot - Orchestrator
State Machine + Session Management + API Integration
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from anthropic import Anthropic

import config
from prompts import assemble_prompt
from policy_engine import PolicyEngine
from structured_results import validate_structured_results, format_to_txt


class SessionState:
    """Manages session state."""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = 'INTAKE'
        self.mode = 'STANDARD'
        self.iteration_count = 0
        self.repair_attempts = 0
        
        self.material_type = 'unknown'
        self.completeness = 'unknown'
        self.accumulated_material = []
        self.clarifications_received = []
        
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'session_id': self.session_id,
            'state': self.state,
            'mode': self.mode,
            'iteration_count': self.iteration_count,
            'repair_attempts': self.repair_attempts,
            'material_type': self.material_type,
            'completeness': self.completeness,
            'accumulated_material': self.accumulated_material,
            'clarifications_received': self.clarifications_received,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionState':
        """Load from dictionary."""
        session = cls(data['session_id'])
        session.state = data.get('state', 'INTAKE')
        session.mode = data.get('mode', 'STANDARD')
        session.iteration_count = data.get('iteration_count', 0)
        session.repair_attempts = data.get('repair_attempts', 0)
        session.material_type = data.get('material_type', 'unknown')
        session.completeness = data.get('completeness', 'unknown')
        session.accumulated_material = data.get('accumulated_material', [])
        session.clarifications_received = data.get('clarifications_received', [])
        session.created_at = data.get('created_at', datetime.now(timezone.utc).isoformat())
        session.updated_at = data.get('updated_at', session.created_at)
        return session
    
    def save(self):
        """Save session to file."""
        session_file = config.SESSIONS_DIR / f"{self.session_id}.json"
        self.updated_at = datetime.now(timezone.utc).isoformat()
        session_file.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))


class Orchestrator:
    """
    Main orchestrator managing state machine and API calls.
    """
    
    def __init__(self):
        """Initialize Orchestrator."""
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.policy_engine = PolicyEngine()
        print("✓ Orchestrator initialized")
    
    def create_session(self, user_id: int) -> SessionState:
        """Create new session."""
        session_id = f"int_{user_id}_{uuid.uuid4().hex[:8]}"
        session = SessionState(session_id)
        session.save()
        print(f"✓ Session created: {session_id}")
        return session
    
    def load_session(self, session_id: str) -> Optional[SessionState]:
        """Load existing session."""
        session_file = config.SESSIONS_DIR / f"{session_id}.json"
        if not session_file.exists():
            return None
        
        data = json.loads(session_file.read_text())
        return SessionState.from_dict(data)
    
    def _extract_user_message(self, response_text: str) -> str:
        """Extract clean user-facing message from Claude response."""
        try:
            # Try to parse JSON
            if '```json' in response_text:
                json_start = response_text.find('```json') + 7
                json_end = response_text.find('```', json_start)
                json_str = response_text[json_start:json_end].strip()
            elif '{' in response_text:
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                json_str = response_text[json_start:json_end]
            else:
                # No JSON found, return as is
                return response_text
            
            # Parse JSON
            data = json.loads(json_str)
            
            # Extract user-facing message from various possible fields
            # IMPORTANT: Always return STRING, never dict
            
            if 'clarifying_question' in data and data['clarifying_question']:
                return str(data['clarifying_question'])
            
            if 'acknowledgment' in data:
                if isinstance(data['acknowledgment'], dict) and 'text' in data['acknowledgment']:
                    return str(data['acknowledgment']['text'])
                elif isinstance(data['acknowledgment'], str):
                    return str(data['acknowledgment'])
            
            if 'message' in data:
                return str(data['message'])
            
            if 'question' in data:
                return str(data['question'])
            
            # Check for metadata fields (is_required, max_length_chars)
            if 'is_required' in data or 'max_length_chars' in data:
                if 'text' in data:
                    return str(data['text'])
            
            # If no specific message field found, return text from phenomenological_summary if present
            if 'phenomenological_summary' in data and isinstance(data['phenomenological_summary'], dict):
                if 'text' in data['phenomenological_summary']:
                    return str(data['phenomenological_summary']['text'])
            
            # Generic text field extraction
            if isinstance(data, dict) and 'text' in data:
                return str(data['text'])
            
            # Last resort: return original text
            return response_text
                
        except Exception as e:
            print(f"⚠ Failed to parse response JSON: {e}")
            return response_text
    
    def process_message(self, session: SessionState, message: str) -> str:
        """
        Process user message based on current state.
        
        Returns:
            Response text or path to generated file
        """
        print(f"→ Processing in state: {session.state}")
        
        # Add message to accumulated material
        session.accumulated_material.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'content': message
        })
        
        # Route based on state
        if session.state == 'INTAKE':
            return self._handle_intake(session, message)
        
        elif session.state == 'MATERIAL_CHECK':
            return self._handle_material_check(session, message)
        
        elif session.state == 'CLARIFICATION_LOOP':
            return self._handle_clarification(session, message)
        
        elif session.state == 'INTERPRETATION_GENERATION':
            return self._handle_interpretation(session, message)
        
        else:
            return "Неизвестное состояние сессии. Начните заново с /start"
    
    def _handle_intake(self, session: SessionState, message: str) -> str:
        """Handle INTAKE state."""
        context = {
            'session_id': session.session_id,
            'mode': session.mode,
            'iteration_count': session.iteration_count,
            'max_iterations': config.MAX_CLARIFICATION_ITERATIONS,
            'material_type': session.material_type,
            'completeness': session.completeness
        }
        
        system_prompt = assemble_prompt('INTAKE', context)
        
        response = self.client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system_prompt,
            messages=[
                {"role": "user", "content": message}
            ]
        )
        
        response_text = response.content[0].text
        clean_message = self._extract_user_message(response_text)
        
        # Check if this is a clarifying question (short and has ?)
        if '?' in clean_message and len(clean_message) < 200:
            # Stay in INTAKE for answer
            session.save()
            return clean_message
        else:
            # Material received, proceed directly to interpretation
            session.state = 'INTERPRETATION_GENERATION'
            session.save()
            # Auto-trigger interpretation
            return self._handle_interpretation(session, message)
    
    def _handle_material_check(self, session: SessionState, message: str) -> str:
        """Handle MATERIAL_CHECK state - immediately proceed to interpretation."""
        session.state = 'INTERPRETATION_GENERATION'
        session.save()
        # Auto-trigger interpretation
        return self._handle_interpretation(session, message)
        
        
    def _handle_clarification(self, session: SessionState, message: str) -> str:
        """Handle CLARIFICATION_LOOP state."""
        session.clarifications_received.append(message)
        session.iteration_count += 1
        
        context = {
            'session_id': session.session_id,
            'mode': session.mode,
            'iteration_count': session.iteration_count,
            'max_iterations': config.MAX_CLARIFICATION_ITERATIONS,
            'material_type': session.material_type,
            'completeness': session.completeness
        }
        
        system_prompt = assemble_prompt('CLARIFICATION_LOOP', context)
        material_text = "\n\n".join([m['content'] for m in session.accumulated_material])
        clarifications = "\n".join([f"- {c}" for c in session.clarifications_received])
        
        full_context = f"""Символический материал:
{material_text}

Полученные уточнения:
{clarifications}"""
        
        response = self.client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system_prompt,
            messages=[
                {"role": "user", "content": full_context}
            ]
        )
        
        response_text = response.content[0].text
        clean_message = self._extract_user_message(response_text)
        
        if session.iteration_count >= config.MAX_CLARIFICATION_ITERATIONS:
            session.mode = 'LOW_DATA'
            session.state = 'INTERPRETATION_GENERATION'
            session.save()
            return "Получено достаточно уточнений. Перехожу к интерпретации."
        
        session.save()
        return clean_message
    
    def _handle_interpretation(self, session: SessionState, message: str) -> str:
        """Handle INTERPRETATION_GENERATION state."""
        context = {
            'session_id': session.session_id,
            'mode': session.mode,
            'iteration_count': session.iteration_count,
            'max_iterations': config.MAX_CLARIFICATION_ITERATIONS,
            'material_type': session.material_type,
            'completeness': session.completeness
        }
        
        prompt_state = 'LOW_DATA_MODE' if session.mode == 'LOW_DATA' else 'INTERPRETATION_GENERATION'
        system_prompt = assemble_prompt(prompt_state, context)
        
        material_text = "\n\n".join([m['content'] for m in session.accumulated_material])
        
        if session.clarifications_received:
            clarifications = "\n".join([f"- {c}" for c in session.clarifications_received])
            full_context = f"""Символический материал:
{material_text}

Полученные уточнения:
{clarifications}

Создайте структурированную интерпретацию в формате JSON."""
        else:
            full_context = f"""Символический материал:
{material_text}

Создайте структурированную интерпретацию в формате JSON."""
        
        print("→ Calling Claude API for interpretation...")
        
        response = self.client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system_prompt,
            messages=[
                {"role": "user", "content": full_context}
            ]
        )
        
        response_text = response.content[0].text
        
        # Extract JSON from response
        try:
            # Try to find JSON in response
            if '```json' in response_text:
                json_start = response_text.find('```json') + 7
                json_end = response_text.find('```', json_start)
                json_str = response_text[json_start:json_end].strip()
            elif '{' in response_text:
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                json_str = response_text[json_start:json_end]
            else:
                raise ValueError("No JSON found in response")
            
            # Try to parse
            try:
                output = json.loads(json_str)
            except json.JSONDecodeError as e:
                print(f"⚠ JSON parse error: {e}")
                print(f"⚠ Attempting to fix truncated JSON...")
                
                # Try to fix common issues
                # 1. Add missing closing braces
                open_braces = json_str.count('{')
                close_braces = json_str.count('}')
                if open_braces > close_braces:
                    json_str += '}' * (open_braces - close_braces)
                
                # 2. Remove incomplete last field
                last_comma = json_str.rfind(',')
                if last_comma > 0:
                    json_str = json_str[:last_comma] + '\n}'
                
                # Retry parse
                output = json.loads(json_str)
            
            print("✓ JSON parsed successfully")
            
        except Exception as e:
            print(f"✗ Failed to parse JSON: {e}")
            print(f"✗ Response length: {len(response_text)} chars")
            print(f"✗ First 500 chars: {response_text[:500]}")
            
            # Fallback: try LOW_DATA mode
            if session.mode != 'LOW_DATA':
                print("→ Falling back to LOW_DATA mode...")
                session.mode = 'LOW_DATA'
                session.repair_attempts = 0
                session.save()
                
                return "⚠ Произошла ошибка обработки. Повторяю в упрощённом режиме..."
            
            return f"❌ Критическая ошибка: не удалось получить структурированный результат.\n\nОшибка: {str(e)}\n\nПопробуйте начать новую сессию с /new"
        
        # Validate with Policy Engine
        print("→ Validating with Policy Engine...")
        validation_result = self.policy_engine.validate(output)
        
        if not validation_result['valid']:
            print(f"⚠ Violations found: {len(validation_result['violations'])}")
            
            if session.repair_attempts < config.MAX_REPAIR_ATTEMPTS:
                session.repair_attempts += 1
                print(f"→ Attempting repair (attempt {session.repair_attempts})...")
                
                output, repair_report = self.policy_engine.repair(output, validation_result)
                
                if repair_report['repaired']:
                    print(f"✓ Repaired: {repair_report['changes']}")
                
                validation_result = self.policy_engine.validate(output)
                
                if not validation_result['valid']:
                    print("⚠ Some violations remain after repair")
            else:
                print("⚠ Max repair attempts reached")
        else:
            print("✓ Validation passed")
        
        # Validate structure
        valid, errors = validate_structured_results(output)
        if not valid:
            print(f"✗ Structure validation failed: {errors}")
            return f"Ошибка валидации структуры: {errors}"
        
        # Generate TXT file
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_filename = f"interpretation_{session.session_id}_{timestamp}.txt"
        output_path = config.OUTPUTS_DIR / output_filename
        
        print(f"→ Generating TXT file: {output_filename}")
        format_to_txt(output, output_path)
        
        # Save JSON as well
        json_path = config.OUTPUTS_DIR / output_filename.replace('.txt', '.json')
        json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
        
        session.state = 'COMPLETED'
        session.save()
        
        print(f"✓ Interpretation complete!")
        print(f"  TXT: {output_path}")
        print(f"  JSON: {json_path}")
        
        return str(output_path)


if __name__ == '__main__':
    print("✓ Orchestrator module loaded")
