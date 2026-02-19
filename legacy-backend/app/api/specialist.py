"""
Specialist API endpoints.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.specialist import Specialist
from app.models.transaction import TransactionType
from app.schemas.schemas import (
    SpecialistCreate,
    SpecialistResponse,
    SpecialistBalance,
    TokenAddRequest,
    TokenAddResponse,
    TransactionResponse,
    TransactionListResponse,
    ErrorResponse,
)
from app.services.token_manager import TokenManager
from app.config import settings
from app.exceptions import (
    SpecialistNotFoundError,
    SpecialistAlreadyExistsError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/specialist", tags=["specialist"])


@router.post(
    "/register",
    response_model=SpecialistResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ErrorResponse, "description": "Specialist already exists"},
    }
)
async def register_specialist(
    data: SpecialistCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new specialist (psychologist).
    
    Gives free tokens on registration based on settings.
    """
    logger.info(
        "register_specialist_request",
        telegram_id=data.telegram_id,
        username=data.username,
    )
    
    # Check if already exists
    stmt = select(Specialist).where(Specialist.telegram_id == data.telegram_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        logger.warning("specialist_already_exists", telegram_id=data.telegram_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Specialist already registered"
        )
    
    # Create specialist
    specialist = Specialist(
        telegram_id=data.telegram_id,
        username=data.username,
        name=data.name,
        email=data.email,
        tokens_balance=settings.free_tokens_on_register,
        tokens_purchased=settings.free_tokens_on_register,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        last_active_at=datetime.utcnow(),
    )
    
    db.add(specialist)
    await db.flush()
    
    # Create bonus transaction for free tokens
    if settings.free_tokens_on_register > 0:
        token_manager = TokenManager(db)
        await token_manager.add_tokens(
            telegram_id=data.telegram_id,
            amount=settings.free_tokens_on_register,
            description="Welcome bonus tokens",
            transaction_type=TransactionType.BONUS,
        )
    
    logger.info(
        "specialist_registered",
        specialist_id=specialist.id,
        telegram_id=specialist.telegram_id,
        free_tokens=settings.free_tokens_on_register,
    )
    
    return specialist


@router.get(
    "/{telegram_id}",
    response_model=SpecialistResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Specialist not found"},
    }
)
async def get_specialist(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get specialist profile by Telegram ID."""
    logger.debug("get_specialist_request", telegram_id=telegram_id)
    
    stmt = select(Specialist).where(Specialist.telegram_id == telegram_id)
    result = await db.execute(stmt)
    specialist = result.scalar_one_or_none()
    
    if specialist is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specialist not found"
        )
    
    # Update last active
    specialist.last_active_at = datetime.utcnow()
    await db.flush()
    
    return specialist


@router.get(
    "/{telegram_id}/balance",
    response_model=SpecialistBalance,
    responses={
        404: {"model": ErrorResponse, "description": "Specialist not found"},
    }
)
async def get_balance(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get token balance for specialist."""
    logger.debug("get_balance_request", telegram_id=telegram_id)
    
    token_manager = TokenManager(db)
    
    try:
        balance = await token_manager.get_balance(telegram_id)
        return SpecialistBalance(telegram_id=telegram_id, **balance)
    except SpecialistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specialist not found"
        )


@router.post(
    "/{telegram_id}/tokens/add",
    response_model=TokenAddResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Specialist not found"},
    }
)
async def add_tokens(
    telegram_id: int,
    data: TokenAddRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Add tokens to specialist's balance.
    
    For MVP this is a manual operation. In production, integrate with payment provider.
    """
    logger.info(
        "add_tokens_request",
        telegram_id=telegram_id,
        amount=data.amount,
    )
    
    token_manager = TokenManager(db)
    
    try:
        transaction = await token_manager.add_tokens(
            telegram_id=telegram_id,
            amount=data.amount,
            description=data.description,
            transaction_type=TransactionType.PURCHASE,
        )
        
        # Get updated balance
        balance = await token_manager.get_balance(telegram_id)
        
        return TokenAddResponse(
            telegram_id=telegram_id,
            tokens_added=data.amount,
            new_balance=balance["tokens_balance"],
            transaction_id=transaction.id,
        )
    except SpecialistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specialist not found"
        )


@router.get(
    "/{telegram_id}/transactions",
    response_model=TransactionListResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Specialist not found"},
    }
)
async def get_transactions(
    telegram_id: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Get transaction history for specialist."""
    logger.debug(
        "get_transactions_request",
        telegram_id=telegram_id,
        limit=limit,
        offset=offset,
    )
    
    token_manager = TokenManager(db)
    
    try:
        transactions, total = await token_manager.get_transactions(
            telegram_id=telegram_id,
            limit=limit,
            offset=offset,
        )
        
        return TransactionListResponse(
            transactions=[
                TransactionResponse.model_validate(t) for t in transactions
            ],
            total=total,
        )
    except SpecialistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specialist not found"
        )
