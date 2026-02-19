"""
Screen Bank Loader - loads and provides access to screening screens.
"""

import json
from pathlib import Path
from typing import Any

from app.exceptions import ScreenNotFoundError, DataLoadingError
from app.logging_config import get_logger

logger = get_logger(__name__)


class ScreenBankLoader:
    """Loads and provides access to the screen bank."""
    
    def __init__(self, screen_bank_path: str | Path | None = None, routing_rules_path: str | Path | None = None):
        self._screen_bank: dict[str, Any] = {}
        self._routing_rules: dict[str, Any] = {}
        self._screens_by_id: dict[str, dict[str, Any]] = {}
        self._loaded = False
        
        # Default paths
        data_dir = Path(__file__).parent.parent / "data"
        self._screen_bank_path = Path(screen_bank_path) if screen_bank_path else data_dir / "screen_bank.json"
        self._routing_rules_path = Path(routing_rules_path) if routing_rules_path else data_dir / "routing_rules.json"
    
    def load(self) -> None:
        """Load screen bank and routing rules from files."""
        try:
            # Load screen bank
            if self._screen_bank_path.exists():
                with open(self._screen_bank_path, "r", encoding="utf-8") as f:
                    self._screen_bank = json.load(f)
                logger.info(
                    "screen_bank_loaded",
                    path=str(self._screen_bank_path),
                    screens_count=len(self._screen_bank.get("screens", []))
                )
            else:
                logger.warning("screen_bank_not_found", path=str(self._screen_bank_path))
                self._screen_bank = {"screens": [], "metadata": {}}
            
            # Load routing rules
            if self._routing_rules_path.exists():
                with open(self._routing_rules_path, "r", encoding="utf-8") as f:
                    self._routing_rules = json.load(f)
                logger.info(
                    "routing_rules_loaded",
                    path=str(self._routing_rules_path)
                )
            else:
                logger.warning("routing_rules_not_found", path=str(self._routing_rules_path))
                self._routing_rules = {}
            
            # Build index by screen_id
            self._build_screen_index()
            self._loaded = True
            
        except json.JSONDecodeError as e:
            logger.error("json_decode_error", exc_info=e)
            raise DataLoadingError("screen_bank.json", f"Invalid JSON: {e}")
        except Exception as e:
            logger.error("data_loading_error", exc_info=e)
            raise DataLoadingError("screen_bank", str(e))
    
    def _build_screen_index(self) -> None:
        """Build an index of screens by screen_id."""
        self._screens_by_id = {}
        for screen in self._screen_bank.get("screens", []):
            screen_id = screen.get("screen_id")
            if screen_id:
                self._screens_by_id[screen_id] = screen
        logger.debug("screen_index_built", screens_indexed=len(self._screens_by_id))
    
    @property
    def is_loaded(self) -> bool:
        """Check if data is loaded."""
        return self._loaded
    
    def get_screen_by_id(self, screen_id: str) -> dict[str, Any]:
        """
        Get screen by its ID.
        
        Args:
            screen_id: Screen identifier (e.g., "EE_01")
            
        Returns:
            Screen dictionary
            
        Raises:
            ScreenNotFoundError: If screen is not found
        """
        if not self._loaded:
            self.load()
        
        screen = self._screens_by_id.get(screen_id)
        if screen is None:
            logger.warning("screen_not_found", screen_id=screen_id)
            raise ScreenNotFoundError(screen_id)
        
        return screen
    
    def get_all_screens(self) -> list[dict[str, Any]]:
        """Get all screens."""
        if not self._loaded:
            self.load()
        return self._screen_bank.get("screens", [])
    
    def get_screens_by_continuum(self, continuum: str) -> list[dict[str, Any]]:
        """Get all screens for a specific continuum."""
        if not self._loaded:
            self.load()
        return [
            screen for screen in self._screen_bank.get("screens", [])
            if screen.get("continuum") == continuum
        ]
    
    def get_first_screen(self) -> dict | None:
        """Get the first screen (B0_01 - intro screen)."""
        try:
            return self.get_screen_by_id("B0_01")
        except ScreenNotFoundError:
            # Fallback to first screen
            screens = self._screen_bank.get("screens", [])
            return screens[0] if screens else None
    
    def get_routing_rules(self) -> dict[str, Any]:
        """Get routing rules."""
        if not self._loaded:
            self.load()
        return self._routing_rules
    
    def get_screen_bank_for_claude(self) -> str:
        """Get screen bank as JSON string for Claude API."""
        if not self._loaded:
            self.load()
        return json.dumps(self._screen_bank, ensure_ascii=False, indent=2)
    
    def get_routing_rules_for_claude(self) -> str:
        """Get routing rules as JSON string for Claude API."""
        if not self._loaded:
            self.load()
        return json.dumps(self._routing_rules, ensure_ascii=False, indent=2)


# Global instance
screen_bank_loader = ScreenBankLoader()
