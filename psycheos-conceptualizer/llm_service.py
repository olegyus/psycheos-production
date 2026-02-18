"""LLM Service for Claude API integration."""

import logging
from typing import Optional, Dict, Any
from anthropic import Anthropic

from config import get_config

logger = logging.getLogger(__name__)


class ClaudeService:
    """Service for interacting with Claude API."""
    
    def __init__(self):
        config = get_config()
        self.client = Anthropic(api_key=config.anthropic_api_key)
        self.model = config.anthropic_model
        self.max_tokens = config.max_tokens
    
    def generate(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Generate response from Claude.
        
        Args:
            system_prompt: System instructions
            user_message: User message
            temperature: Sampling temperature
            max_tokens: Max tokens (default from config)
        
        Returns:
            Generated text
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens or self.max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ]
            )
            
            # Extract text from response
            text = response.content[0].text
            
            logger.info(f"Claude generated response ({len(text)} chars)")
            return text
            
        except Exception as e:
            logger.error(f"Claude API error: {e}", exc_info=True)
            raise


# Global instance
_claude_service: Optional[ClaudeService] = None


def get_claude_service() -> ClaudeService:
    """Get or create global Claude service instance."""
    global _claude_service
    if _claude_service is None:
        _claude_service = ClaudeService()
    return _claude_service


__all__ = [
    "ClaudeService",
    "get_claude_service",
]
