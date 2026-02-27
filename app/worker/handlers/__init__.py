"""
Job-type → handler function registry.

Each handler has the signature:
    async def handle_*(job: Job, db: AsyncSession, bots: dict[str, Bot]) -> None
"""
from app.worker.handlers.conceptualizator import (
    handle_concept_hypothesis,
    handle_concept_output,
)
from app.worker.handlers.interpretator import (
    handle_interp_intake,
    handle_interp_photo,
    handle_interp_questions,
    handle_interp_run,
)
from app.worker.handlers.pro import handle_pro_reference
from app.worker.handlers.simulator import (
    handle_sim_launch,
    handle_sim_launch_custom,
    handle_sim_report,
)

REGISTRY: dict = {
    # Pro bot
    "pro_reference": handle_pro_reference,
    # Interpretator bot
    "interp_photo": handle_interp_photo,
    "interp_intake": handle_interp_intake,
    "interp_questions": handle_interp_questions,
    "interp_run": handle_interp_run,
    # Conceptualizator bot
    "concept_hypothesis": handle_concept_hypothesis,
    "concept_output": handle_concept_output,
    # Simulator bot
    "sim_launch": handle_sim_launch,
    "sim_launch_custom": handle_sim_launch_custom,
    "sim_report": handle_sim_report,
}

__all__ = ["REGISTRY"]
