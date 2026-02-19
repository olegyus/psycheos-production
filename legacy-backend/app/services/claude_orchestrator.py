"""
Claude Orchestrator - handles Claude API integration for routing and output generation.
"""

import json
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings
from app.exceptions import ClaudeAPIError, ClaudeRoutingError, ClaudeOutputGenerationError
from app.services.screen_bank_loader import screen_bank_loader
from app.logging_config import get_logger
import httpx

logger = get_logger(__name__)


class ClaudeOrchestrator:
    """Orchestrates Claude API calls for routing decisions and output generation."""
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(default_encoding = "utf-8")
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key, http_client = self.http_client)
        self._system_prompt: str | None = None
        self._system_prompt_path = Path(__file__).parent.parent / "data" / "system_prompt.txt"
    
    def _load_system_prompt(self) -> str:
        """Load system prompt from file."""
        if self._system_prompt is not None:
            return self._system_prompt
        
        try:
            if self._system_prompt_path.exists():
                with open(self._system_prompt_path, "r", encoding="utf-8") as f:
                    self._system_prompt = f.read()
                logger.info("system_prompt_loaded", path=str(self._system_prompt_path))
            else:
                logger.warning("system_prompt_not_found", path=str(self._system_prompt_path))
                self._system_prompt = self._get_default_system_prompt()
            
            return self._system_prompt
        except Exception as e:
            logger.error("system_prompt_load_error", exc_info=e)
            return self._get_default_system_prompt()
    
    def _get_default_system_prompt(self) -> str:
        """Get default system prompt if file not found."""
        return """You are PsycheOS, a psychological screening assistant.
Your task is to guide the screening process by selecting appropriate screens
based on the client's responses and generating final screening outputs.

When routing:
- Analyze the session state and responses
- Select the most appropriate next screen based on routing rules
- Return JSON with action and next_screen

When generating output:
- Analyze all responses
- Generate comprehensive screening output
- Create interview protocol for the therapist

Always respond in valid JSON format."""
    
    async def select_next_screen(self, session_state: dict) -> dict[str, Any]:
        """
        Select the next screen based on session state.
        
        Args:
            session_state: Current session state with responses
            
        Returns:
            Dict with "action" ("next_screen" or "finalize") and optionally "next_screen"
        """
        system_prompt = self._load_system_prompt()
        
        # Build the routing request
        screen_bank = screen_bank_loader.get_screen_bank_for_claude()
        routing_rules = screen_bank_loader.get_routing_rules_for_claude()
        
        user_message = f"""Analyze the current session state and determine the next screen to show.

SESSION STATE:
{json.dumps(session_state, ensure_ascii=False, indent=2)}

SCREEN BANK:
{screen_bank}

ROUTING RULES:
{routing_rules}

Based on the session state and routing rules, determine what to do next.
If the screening should continue, return the next screen to show.
If enough data has been collected (typically 15-20 screens), indicate to finalize.

Respond with a JSON object in this exact format:
{{"action": "next_screen", "next_screen": {{"screen_id": "XX_00"}}}}
or
{{"action": "finalize"}}

Important:
- Choose screens that haven't been shown yet
- Follow the routing rules for domain coverage
- Ensure diverse exploration of psychological dimensions
- After 15-20 screens, consider finalizing

Respond ONLY with the JSON object, no additional text."""

        try:
            response = await self.client.messages.create(
                model=settings.claude_model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            
            response_text = response.content[0].text.strip()
            logger.debug("claude_routing_response", response=response_text)
            
            # Parse response
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError:
                # Try to extract JSON from response
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    raise ClaudeRoutingError(
                        "Invalid JSON in routing response",
                        response_text
                    )
            
            # Validate response
            if "action" not in result:
                raise ClaudeRoutingError("Missing 'action' in routing response", result)
            
            if result["action"] == "next_screen" and "next_screen" not in result:
                raise ClaudeRoutingError(
                    "Missing 'next_screen' for next_screen action",
                    result
                )
            
            logger.info(
                "routing_decision",
                action=result.get("action"),
                next_screen_id=result.get("next_screen", {}).get("screen_id"),
            )
            
            return result
            
        except ClaudeRoutingError:
            raise
        except Exception as e:
            logger.error("claude_routing_error", exc_info=e)
            raise ClaudeAPIError(f"Routing request failed: {e}", e)
    
    async def generate_output(self, session_state: dict) -> dict[str, Any]:
        """
        Generate final screening output and interview protocol.
        
        Args:
            session_state: Final session state with all responses
            
        Returns:
            Dict with "screening_output" and "interview_protocol"
        """
        system_prompt = self._load_system_prompt()
        
        # Simplified prompt for more reliable JSON generation
        user_message = f"""Сгенерируй screening_output и interview_protocol на основе данных сессии.

ДАННЫЕ СЕССИИ:
{json.dumps(session_state, ensure_ascii=False, indent=2)}

Верни JSON строго в таком формате (без markdown, без ```):

{{
  "screening_output": {{
    "metadata": {{
      "session_id": "{session_state.get('session_id', '')}",
      "screens_completed": {session_state.get('screens_shown', 0)},
      "data_quality": "good"
    }},
    "continuum_profile": {{
      "economy_exploration": {{
        "position": <число 0-100 или null>,
        "confidence": <число 0-1>,
        "interpretation_note": "<краткое описание на русском>"
      }},
      "protection_contact": {{
        "position": <число или null>,
        "confidence": <число 0-1>,
        "interpretation_note": "<краткое описание>"
      }},
      "retention_movement": {{
        "position": <число или null>,
        "confidence": <число 0-1>,
        "interpretation_note": "<краткое описание>"
      }},
      "survival_development": {{
        "position": <число или null>,
        "confidence": <число 0-1>,
        "interpretation_note": "<краткое описание>"
      }}
    }},
    "interview_markers": {{
      "areas_of_tension": ["<зона1>", "<зона2>"],
      "recommended_focus": "<рекомендация для терапевта>"
    }}
  }},
  "interview_protocol": {{
    "general_profile": {{
      "summary": "<1-2 предложения о клиенте>"
    }},
    "working_hypotheses": [
      "<гипотеза 1>",
      "<гипотеза 2>"
    ],
    "question_directions": {{
      "experience_questions": ["<вопрос 1>"],
      "context_questions": ["<вопрос 2>"]
    }},
    "recommended_session_focus": "<с чего начать первую сессию>"
  }}
}}

ВАЖНО:
- Отвечай ТОЛЬКО JSON, без пояснений
- Все тексты на русском языке
- Используй данные из continuum_scores для position и confidence
- Не добавляй markdown форматирование"""

        try:
            response = await self.client.messages.create(
                model=settings.claude_model,
                max_tokens=2048,  # Reduced for faster, more reliable response
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            
            response_text = response.content[0].text.strip()
            logger.debug("claude_output_response", response_length=len(response_text))
            
            # Clean response - remove markdown code blocks if present
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()
            
            # Try to parse JSON
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.warning("json_parse_failed_trying_fix", error=str(e))
                
                # Try to extract JSON object
                import re
                json_match = re.search(r'\{[\s\S]*\}', response_text)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        # Return fallback output
                        logger.error("json_extraction_failed", response_preview=response_text[:500])
                        result = self._create_fallback_output(session_state)
                else:
                    result = self._create_fallback_output(session_state)
            
            # Validate required fields
            if "screening_output" not in result:
                result["screening_output"] = self._create_fallback_output(session_state)["screening_output"]
            if "interview_protocol" not in result:
                result["interview_protocol"] = self._create_fallback_output(session_state)["interview_protocol"]
            
            logger.info(
                "output_generated",
                has_screening_output=bool(result.get("screening_output")),
                has_interview_protocol=bool(result.get("interview_protocol")),
            )
            
            return result
            
        except Exception as e:
            logger.error("claude_output_error", exc_info=e)
            # Return fallback instead of raising
            return self._create_fallback_output(session_state)
    
    def _create_fallback_output(self, session_state: dict) -> dict[str, Any]:
        """Create fallback output when Claude fails."""
        continuum_scores = session_state.get("continuum_scores", {})
        
        def get_continuum_data(name: str) -> dict:
            data = continuum_scores.get(name, {})
            return {
                "position": data.get("position"),
                "confidence": data.get("confidence", 0),
                "interpretation_note": "Данные получены, требуется анализ на сессии."
            }
        
        return {
            "screening_output": {
                "metadata": {
                    "session_id": session_state.get("session_id", ""),
                    "screens_completed": session_state.get("screens_shown", 0),
                    "data_quality": "acceptable"
                },
                "continuum_profile": {
                    "economy_exploration": get_continuum_data("economy_exploration"),
                    "protection_contact": get_continuum_data("protection_contact"),
                    "retention_movement": get_continuum_data("retention_movement"),
                    "survival_development": get_continuum_data("survival_development"),
                },
                "interview_markers": {
                    "areas_of_tension": ["Требуется анализ на сессии"],
                    "recommended_focus": "Начните с обсуждения общего состояния клиента."
                }
            },
            "interview_protocol": {
                "general_profile": {
                    "summary": "Клиент прошёл скрининг. Детальный анализ рекомендуется провести на сессии."
                },
                "working_hypotheses": [
                    "Гипотезы будут сформированы на основе беседы с клиентом."
                ],
                "question_directions": {
                    "experience_questions": ["Расскажите, что привело вас на терапию?"],
                    "context_questions": ["Как давно вы замечаете эти переживания?"]
                },
                "recommended_session_focus": "Установление контакта и прояснение запроса клиента."
            }
        }
    
    def get_first_screen(self) -> dict[str, Any] | None:
        """Get the first screen to show (without Claude, from screen bank)."""
        return screen_bank_loader.get_first_screen()


# Global instance
claude_orchestrator = ClaudeOrchestrator()
