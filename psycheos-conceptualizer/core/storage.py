"""Redis storage for sessions."""

import json
import logging
from typing import Optional
from datetime import datetime
import redis
from .models import SessionState

logger = logging.getLogger(__name__)


class RedisStorage:
    """Redis-based session storage."""
    
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, ttl: int = 86400):
        """
        Initialize Redis storage.
        
        Args:
            host: Redis host
            port: Redis port
            db: Redis database number
            ttl: Time to live in seconds (default 24h)
        """
        self.redis_client = redis.Redis(
            host=host,
            port=port,
            db=db,
            decode_responses=True
        )
        self.ttl = ttl
        self.prefix = "psycheos:session:"
    
    def _get_key(self, session_id: str) -> str:
        """Get Redis key for session."""
        return f"{self.prefix}{session_id}"
    
    def save_session(self, session: SessionState) -> bool:
        """
        Save session to Redis.
        
        Args:
            session: SessionState to save
            
        Returns:
            True if successful
        """
        try:
            key = self._get_key(session.session_id)
            
            # Update timestamp
            session.updated_at = datetime.utcnow()
            
            # Serialize to JSON
            data = session.model_dump_json()
            
            # Save with TTL
            self.redis_client.setex(key, self.ttl, data)
            
            logger.info(f"Session saved: {session.session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save session {session.session_id}: {e}")
            return False
    
    def load_session(self, session_id: str) -> Optional[SessionState]:
        """
        Load session from Redis.
        
        Args:
            session_id: Session ID to load
            
        Returns:
            SessionState or None if not found
        """
        try:
            key = self._get_key(session_id)
            data = self.redis_client.get(key)
            
            if not data:
                logger.info(f"Session not found: {session_id}")
                return None
            
            # Deserialize from JSON
            session = SessionState.model_validate_json(data)
            
            logger.info(f"Session loaded: {session_id}")
            return session
            
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None
    
    def delete_session(self, session_id: str) -> bool:
        """
        Delete session from Redis.
        
        Args:
            session_id: Session ID to delete
            
        Returns:
            True if successful
        """
        try:
            key = self._get_key(session_id)
            result = self.redis_client.delete(key)
            
            if result:
                logger.info(f"Session deleted: {session_id}")
                return True
            else:
                logger.warning(f"Session not found for deletion: {session_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False
    
    def session_exists(self, session_id: str) -> bool:
        """
        Check if session exists.
        
        Args:
            session_id: Session ID to check
            
        Returns:
            True if exists
        """
        try:
            key = self._get_key(session_id)
            return self.redis_client.exists(key) > 0
        except Exception as e:
            logger.error(f"Failed to check session existence {session_id}: {e}")
            return False
    
    def get_ttl(self, session_id: str) -> int:
        """
        Get remaining TTL for session.
        
        Args:
            session_id: Session ID
            
        Returns:
            TTL in seconds, -1 if no expiry, -2 if not exists
        """
        try:
            key = self._get_key(session_id)
            return self.redis_client.ttl(key)
        except Exception as e:
            logger.error(f"Failed to get TTL for {session_id}: {e}")
            return -2
    
    def extend_ttl(self, session_id: str, additional_seconds: int = None) -> bool:
        """
        Extend TTL for session.
        
        Args:
            session_id: Session ID
            additional_seconds: Additional seconds (default: reset to full TTL)
            
        Returns:
            True if successful
        """
        try:
            key = self._get_key(session_id)
            
            if additional_seconds is None:
                # Reset to full TTL
                self.redis_client.expire(key, self.ttl)
            else:
                # Add to current TTL
                current_ttl = self.redis_client.ttl(key)
                if current_ttl > 0:
                    self.redis_client.expire(key, current_ttl + additional_seconds)
                else:
                    self.redis_client.expire(key, additional_seconds)
            
            logger.info(f"TTL extended for session: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to extend TTL for {session_id}: {e}")
            return False
    
    def ping(self) -> bool:
        """
        Check Redis connection.
        
        Returns:
            True if connected
        """
        try:
            return self.redis_client.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False


# Global storage instance (will be initialized in config)
_storage: Optional[RedisStorage] = None


def init_storage(host: str = "localhost", port: int = 6379, db: int = 0, ttl: int = 86400) -> RedisStorage:
    """
    Initialize global storage instance.
    
    Args:
        host: Redis host
        port: Redis port
        db: Redis database
        ttl: TTL in seconds
        
    Returns:
        RedisStorage instance
    """
    global _storage
    _storage = RedisStorage(host=host, port=port, db=db, ttl=ttl)
    return _storage


def get_storage() -> RedisStorage:
    """
    Get global storage instance.
    
    Returns:
        RedisStorage instance
        
    Raises:
        RuntimeError: If storage not initialized
    """
    if _storage is None:
        raise RuntimeError("Storage not initialized. Call init_storage() first.")
    return _storage


__all__ = [
    "RedisStorage",
    "init_storage",
    "get_storage",
]
