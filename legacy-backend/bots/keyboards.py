"""
Keyboard builders for PsycheOS Client Bot.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_start_screening_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard to start screening."""
    builder = InlineKeyboardBuilder()
    builder.button(text="▶️ Начать", callback_data="start_screening")
    return builder.as_markup()


def build_info_keyboard(screen_id: str) -> InlineKeyboardMarkup:
    """Build keyboard for info screens (just 'Next' button)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Далее →", callback_data=f"response:{screen_id}:next")
    return builder.as_markup()


def build_slider_keyboard(screen_id: str) -> InlineKeyboardMarkup:
    """
    Build inline keyboard for slider (0-10) responses.
    
    Layout:
    [0] [1] [2] [3] [4] [5]
    [6] [7] [8] [9] [10]
    """
    builder = InlineKeyboardBuilder()
    
    # First row: 0-5
    for value in range(0, 6):
        builder.button(
            text=str(value),
            callback_data=f"response:{screen_id}:{value}"
        )
    
    # Second row: 6-10
    for value in range(6, 11):
        builder.button(
            text=str(value),
            callback_data=f"response:{screen_id}:{value}"
        )
    
    builder.adjust(6, 5)
    
    return builder.as_markup()


def build_single_choice_keyboard(
    screen_id: str,
    options: list[str]
) -> InlineKeyboardMarkup:
    """
    Build inline keyboard for single choice responses.
    Short letter buttons (A, B, C, D) — options shown in message text.
    """
    builder = InlineKeyboardBuilder()
    letters = ["A", "B", "C", "D", "E", "F", "G", "H"]
    
    for idx, _ in enumerate(options):
        if idx < len(letters):
            builder.button(
                text=letters[idx],
                callback_data=f"response:{screen_id}:{idx}"
            )
    
    # All buttons in one row (up to 4)
    builder.adjust(min(len(options), 4))
    
    return builder.as_markup()


def build_multi_choice_keyboard(
    screen_id: str,
    options: list[str],
    selected: list[int] | None = None
) -> InlineKeyboardMarkup:
    """
    Build inline keyboard for multi-choice responses.
    Letter buttons with checkboxes — options shown in message text.
    """
    builder = InlineKeyboardBuilder()
    selected = selected or []
    letters = ["A", "B", "C", "D", "E", "F", "G", "H"]
    
    for idx, _ in enumerate(options):
        if idx < len(letters):
            checkbox = "✓" if idx in selected else " "
            builder.button(
                text=f"[{checkbox}] {letters[idx]}",
                callback_data=f"multi:{screen_id}:{idx}"
            )
    
    # Add 'Done' button on new row
    builder.button(
        text="✔️ Готово",
        callback_data=f"multi_done:{screen_id}"
    )
    
    # 4 buttons per row, Done on separate row
    builder.adjust(min(len(options), 4), 1)
    
    return builder.as_markup()


def build_continue_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard to continue after pause."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Продолжить", callback_data="continue_screening")
    return builder.as_markup()


def parse_response_callback(callback_data: str) -> tuple[str, str] | None:
    """
    Parse response callback data.
    
    Args:
        callback_data: Callback data string (e.g., "response:B1_01:5")
        
    Returns:
        Tuple of (screen_id, value) or None if invalid
    """
    parts = callback_data.split(":")
    if len(parts) >= 3 and parts[0] == "response":
        screen_id = parts[1]
        value = parts[2]
        return screen_id, value
    return None


def parse_multi_callback(callback_data: str) -> tuple[str, int] | None:
    """
    Parse multi-choice toggle callback.
    
    Args:
        callback_data: e.g., "multi:B0_03:2"
        
    Returns:
        Tuple of (screen_id, option_index) or None
    """
    parts = callback_data.split(":")
    if len(parts) >= 3 and parts[0] == "multi":
        screen_id = parts[1]
        try:
            idx = int(parts[2])
            return screen_id, idx
        except ValueError:
            return None
    return None


def parse_multi_done_callback(callback_data: str) -> str | None:
    """
    Parse multi-choice done callback.
    
    Args:
        callback_data: e.g., "multi_done:B0_03"
        
    Returns:
        screen_id or None
    """
    parts = callback_data.split(":")
    if len(parts) >= 2 and parts[0] == "multi_done":
        return parts[1]
    return None