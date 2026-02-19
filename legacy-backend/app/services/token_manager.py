"""
Token Manager - handles token operations for specialists.
"""

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.specialist import Specialist
from app.models.transaction import TokenTransaction, TransactionType
from app.exceptions import (
    SpecialistNotFoundError,
    InsufficientTokensError,
    InvalidTokenOperationError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


class TokenManager:
    """Manages token operations for specialists."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_balance(self, telegram_id: int) -> dict[str, int]:
        """
        Get token balance for a specialist.
        
        Returns:
            Dictionary with balance, spent, and purchased tokens
        """
        specialist = await self._get_specialist(telegram_id)
        
        logger.debug(
            "balance_retrieved",
            telegram_id=telegram_id,
            balance=specialist.tokens_balance
        )
        
        return {
            "tokens_balance": specialist.tokens_balance,
            "tokens_spent": specialist.tokens_spent,
            "tokens_purchased": specialist.tokens_purchased,
        }
    
    async def add_tokens(
        self,
        telegram_id: int,
        amount: int,
        description: str | None = None,
        transaction_type: TransactionType = TransactionType.PURCHASE,
        payment_amount_usd: float | None = None,
        payment_provider: str | None = None,
        payment_id: str | None = None,
    ) -> TokenTransaction:
        """
        Add tokens to specialist's balance.
        
        Args:
            telegram_id: Specialist's Telegram ID
            amount: Number of tokens to add (must be positive)
            description: Optional description
            transaction_type: Type of transaction (purchase, bonus, refund)
            payment_amount_usd: Payment amount in USD (for purchases)
            payment_provider: Payment provider name
            payment_id: External payment ID
            
        Returns:
            Created TokenTransaction
        """
        if amount <= 0:
            raise InvalidTokenOperationError("Amount must be positive")
        
        specialist = await self._get_specialist(telegram_id)
        
        # Add tokens to balance
        specialist.tokens_balance += amount
        if transaction_type == TransactionType.PURCHASE:
            specialist.tokens_purchased += amount
        
        # Create transaction record
        transaction = TokenTransaction(
            specialist_id=specialist.id,
            amount=amount,
            transaction_type=transaction_type.value,
            payment_amount_usd=payment_amount_usd,
            payment_provider=payment_provider,
            payment_id=payment_id,
            description=description or f"Added {amount} tokens",
            created_at=datetime.utcnow(),
        )
        
        self.db.add(transaction)
        await self.db.flush()
        
        logger.info(
            "tokens_added",
            telegram_id=telegram_id,
            amount=amount,
            new_balance=specialist.tokens_balance,
            transaction_type=transaction_type.value,
            transaction_id=transaction.id,
        )
        
        return transaction
    
    async def spend_token(
        self,
        telegram_id: int,
        session_id: str,
        description: str | None = None,
    ) -> TokenTransaction:
        """
        Spend 1 token for a session.
        
        Args:
            telegram_id: Specialist's Telegram ID
            session_id: Session ID for which token is spent
            description: Optional description
            
        Returns:
            Created TokenTransaction
            
        Raises:
            InsufficientTokensError: If specialist doesn't have enough tokens
        """
        specialist = await self._get_specialist(telegram_id)
        
        if specialist.tokens_balance < 1:
            logger.warning(
                "insufficient_tokens",
                telegram_id=telegram_id,
                balance=specialist.tokens_balance,
            )
            raise InsufficientTokensError(
                required=1,
                available=specialist.tokens_balance,
                specialist_id=specialist.id
            )
        
        # Spend token
        specialist.tokens_balance -= 1
        specialist.tokens_spent += 1
        
        # Create transaction record
        transaction = TokenTransaction(
            specialist_id=specialist.id,
            amount=-1,
            transaction_type=TransactionType.SPEND.value,
            session_id=session_id,
            description=description or f"Spent for session {session_id}",
            created_at=datetime.utcnow(),
        )
        
        self.db.add(transaction)
        await self.db.flush()
        
        logger.info(
            "token_spent",
            telegram_id=telegram_id,
            session_id=session_id,
            new_balance=specialist.tokens_balance,
            transaction_id=transaction.id,
        )
        
        return transaction
    
    async def refund_token(
        self,
        telegram_id: int,
        session_id: str,
        description: str | None = None,
    ) -> TokenTransaction:
        """
        Refund 1 token for a cancelled/failed session.
        
        Args:
            telegram_id: Specialist's Telegram ID
            session_id: Session ID for which token is refunded
            description: Optional description
            
        Returns:
            Created TokenTransaction
        """
        specialist = await self._get_specialist(telegram_id)
        
        # Refund token
        specialist.tokens_balance += 1
        specialist.tokens_spent -= 1  # Reduce spent counter
        
        # Create transaction record
        transaction = TokenTransaction(
            specialist_id=specialist.id,
            amount=1,
            transaction_type=TransactionType.REFUND.value,
            session_id=session_id,
            description=description or f"Refund for session {session_id}",
            created_at=datetime.utcnow(),
        )
        
        self.db.add(transaction)
        await self.db.flush()
        
        logger.info(
            "token_refunded",
            telegram_id=telegram_id,
            session_id=session_id,
            new_balance=specialist.tokens_balance,
            transaction_id=transaction.id,
        )
        
        return transaction
    
    async def get_transactions(
        self,
        telegram_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TokenTransaction], int]:
        """
        Get transaction history for a specialist.
        
        Returns:
            Tuple of (transactions list, total count)
        """
        specialist = await self._get_specialist(telegram_id)
        
        # Get transactions
        stmt = (
            select(TokenTransaction)
            .where(TokenTransaction.specialist_id == specialist.id)
            .order_by(TokenTransaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        transactions = list(result.scalars().all())
        
        # Get total count
        count_stmt = (
            select(TokenTransaction)
            .where(TokenTransaction.specialist_id == specialist.id)
        )
        count_result = await self.db.execute(count_stmt)
        total = len(list(count_result.scalars().all()))
        
        logger.debug(
            "transactions_retrieved",
            telegram_id=telegram_id,
            count=len(transactions),
            total=total,
        )
        
        return transactions, total
    
    async def _get_specialist(self, telegram_id: int) -> Specialist:
        """Get specialist by telegram_id or raise error."""
        stmt = select(Specialist).where(Specialist.telegram_id == telegram_id)
        result = await self.db.execute(stmt)
        specialist = result.scalar_one_or_none()
        
        if specialist is None:
            raise SpecialistNotFoundError(telegram_id)
        
        return specialist
