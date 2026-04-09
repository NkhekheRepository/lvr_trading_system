"""
Credentials Manager - Secure credentials management.

Handles API keys, secrets, and sensitive configuration.
"""

import os
import json
import base64
from typing import Optional, Any
from dataclasses import dataclass
from cryptography.fernet import Fernet
from pathlib import Path

from observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExchangeCredentials:
    """Exchange API credentials."""
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    testnet: bool = False


@dataclass
class DatabaseCredentials:
    """Database credentials."""
    host: str
    port: int
    database: str
    username: str
    password: str


class CredentialsManager:
    """
    Manages credentials securely.
    
    Features:
    - Environment variable loading
    - File-based storage
    - Encryption at rest
    - Access control
    """
    
    ENV_PREFIX = "LVR_"
    
    def __init__(
        self,
        encryption_key: Optional[bytes] = None,
        credentials_file: Optional[Path] = None,
    ):
        self._encryption_key = encryption_key or os.environ.get(
            f'{self.ENV_PREFIX}ENCRYPTION_KEY', ''
        ).encode()
        
        if self._encryption_key:
            self._cipher = Fernet(
                base64.urlsafe_b64encode(self._encryption_key[:32].ljust(32))
            )
        else:
            self._cipher = None
        
        self._credentials_file = credentials_file
        self._credentials_cache: dict[str, Any] = {}
    
    def load_from_environment(self) -> None:
        """Load credentials from environment variables."""
        env_credentials = {}
        
        for key, value in os.environ.items():
            if key.startswith(self.ENV_PREFIX):
                cred_key = key[len(self.ENV_PREFIX):].lower()
                env_credentials[cred_key] = value
        
        if env_credentials:
            self._credentials_cache.update(env_credentials)
            logger.info("Loaded credentials from environment", count=len(env_credentials))
    
    def load_from_file(self, path: Optional[Path] = None) -> None:
        """Load credentials from encrypted file."""
        file_path = path or self._credentials_file
        
        if file_path is None or not file_path.exists():
            logger.warning(f"Credentials file not found: {file_path}")
            return
        
        try:
            with open(file_path, 'r') as f:
                encrypted_data = f.read()
            
            if self._cipher:
                decrypted = self._cipher.decrypt(encrypted_data.encode())
                credentials = json.loads(decrypted)
            else:
                credentials = json.loads(encrypted_data)
            
            self._credentials_cache.update(credentials)
            logger.info("Loaded credentials from file", count=len(credentials))
            
        except Exception as e:
            logger.error(f"Failed to load credentials from file: {e}")
    
    def save_to_file(
        self,
        path: Optional[Path] = None,
        credentials: Optional[dict] = None,
    ) -> None:
        """Save credentials to encrypted file."""
        file_path = path or self._credentials_file
        
        if file_path is None:
            raise ValueError("No credentials file path specified")
        
        creds = credentials or self._credentials_cache
        
        try:
            if self._cipher:
                data = json.dumps(creds)
                encrypted = self._cipher.encrypt(data.encode())
            else:
                encrypted = json.dumps(creds)
            
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w') as f:
                f.write(encrypted.decode() if isinstance(encrypted, bytes) else encrypted)
            
            logger.info("Saved credentials to file")
            
        except Exception as e:
            logger.error(f"Failed to save credentials to file: {e}")
            raise
    
    def get_exchange_credentials(self, exchange: str = 'binance') -> ExchangeCredentials:
        """Get exchange credentials."""
        prefix = f"exchange_{exchange}_"
        
        api_key = self._get_credential(f"{prefix}api_key")
        api_secret = self._get_credential(f"{prefix}api_secret")
        passphrase = self._get_credential(f"{prefix}passphrase")
        testnet = self._get_credential(f"{prefix}testnet", default='false').lower() == 'true'
        
        return ExchangeCredentials(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            testnet=testnet,
        )
    
    def get_database_credentials(self, db_name: str = 'trading') -> DatabaseCredentials:
        """Get database credentials."""
        prefix = f"database_{db_name}_"
        
        return DatabaseCredentials(
            host=self._get_credential(f"{prefix}host", default='localhost'),
            port=int(self._get_credential(f"{prefix}port", default='5432')),
            database=self._get_credential(f"{prefix}database", default=db_name),
            username=self._get_credential(f"{prefix}username"),
            password=self._get_credential(f"{prefix}password"),
        )
    
    def get_redis_credentials(self) -> dict:
        """Get Redis credentials."""
        return {
            'host': self._get_credential('redis_host', default='localhost'),
            'port': int(self._get_credential('redis_port', default='6379')),
            'password': self._get_credential('redis_password'),
            'db': int(self._get_credential('redis_db', default='0')),
        }
    
    def _get_credential(
        self,
        key: str,
        default: Optional[str] = None,
    ) -> str:
        """Get a credential from cache or environment."""
        if key in self._credentials_cache:
            return self._credentials_cache[key]
        
        env_key = f"{self.ENV_PREFIX}{key.upper()}"
        value = os.environ.get(env_key, default)
        
        if value:
            self._credentials_cache[key] = value
        
        return value or ''
    
    def set_credential(self, key: str, value: str) -> None:
        """Set a credential in the cache."""
        self._credentials_cache[key] = value
    
    def get_all_credential_keys(self) -> list[str]:
        """Get all credential keys (without values)."""
        return list(self._credentials_cache.keys())
    
    def clear_cache(self) -> None:
        """Clear the credentials cache."""
        self._credentials_cache.clear()
        logger.info("Cleared credentials cache")
    
    def validate_credentials(self) -> tuple[bool, list[str]]:
        """Validate that required credentials are present."""
        missing = []
        
        required_exchange = ['exchange_binance_api_key', 'exchange_binance_api_secret']
        for key in required_exchange:
            if not self._get_credential(key):
                missing.append(key)
        
        required_db = ['database_trading_username', 'database_trading_password']
        for key in required_db:
            if not self._get_credential(key):
                missing.append(key)
        
        is_valid = len(missing) == 0
        
        if not is_valid:
            logger.warning("Missing credentials", missing=missing)
        
        return is_valid, missing


def get_credentials_manager() -> CredentialsManager:
    """Get or create a credentials manager instance."""
    return CredentialsManager()
