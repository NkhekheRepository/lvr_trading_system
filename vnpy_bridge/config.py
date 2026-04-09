"""
vnpy Bridge Configuration

Configuration management for vnpy_bridge module.
Supports loading from YAML, environment variables, and programmatic config.

Author: LVR Trading System
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import yaml


@dataclass
class VnpyBridgeConfig:
    gateway: GatewayConfig
    feed: FeedConfig
    adapter: AdapterConfig
    
    live_confirmed: bool = False
    
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "trading"
    postgres_user: str = "trading"
    postgres_password: str = ""
    
    enable_audit_log: bool = True
    audit_log_path: str = "/var/log/trading/audit.log"
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if self.live_confirmed and self.adapter.execution_mode.value == "live":
            if not self.binance_api_key:
                errors.append("LIVE mode requires binance_api_key")
            if not self.binance_api_secret:
                errors.append("LIVE mode requires binance_api_secret")
                
        if self.adapter.max_position_per_symbol <= 0:
            errors.append("max_position_per_symbol must be positive")
            
        if self.adapter.max_orders_per_second <= 0:
            errors.append("max_orders_per_second must be positive")
            
        if self.adapter.kronos_min_confidence < 0 or self.adapter.kronos_min_confidence > 1:
            errors.append("kronos_min_confidence must be between 0 and 1")
            
        return errors


@dataclass
class GatewayConfig:
    gateway_name: str = "binance_futures"
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    proxy_host: str = ""
    proxy_port: int = 0
    heartbeat_interval: int = 30
    request_timeout: float = 10.0
    max_retry: int = 3
    retry_delay: float = 1.0


@dataclass
class FeedConfig:
    gateway_name: str = "binance_futures"
    buffer_size: int = 1000
    tick_buffer_per_symbol: int = 100
    reconnect_delay: float = 1.0
    max_reconnect_attempts: int = 10
    heartbeat_interval: float = 30.0
    enable_compression: bool = False


@dataclass
class AdapterConfig:
    safety_level: str = "ENHANCED"
    execution_mode: str = "SIMULATION"
    max_position_per_symbol: float = 100.0
    max_total_exposure: float = 500000.0
    max_orders_per_second: float = 10.0
    max_orders_per_minute: float = 300.0
    max_drawdown_percent: float = 10.0
    health_check_interval: float = 30.0
    enable_kill_switch: bool = True
    require_kronos_validation: bool = True
    kronos_min_confidence: float = 0.6


def load_config(
    config_path: Optional[str] = None,
    env_prefix: str = "LVR_"
) -> VnpyBridgeConfig:
    """
    Load configuration from file and environment.
    
    Priority: Environment variables > Config file > Defaults
    
    Args:
        config_path: Path to YAML config file
        env_prefix: Prefix for environment variables
        
    Returns:
        VnpyBridgeConfig instance
    """
    config_data = {}
    
    if config_path and Path(config_path).exists():
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f) or {}
    
    env_config = _load_from_env(env_prefix)
    config_data = _deep_merge(config_data, env_config)
    
    gateway_config = GatewayConfig(
        gateway_name=config_data.get('gateway_name', 'binance_futures'),
        api_key=config_data.get('binance_api_key', ''),
        api_secret=config_data.get('binance_api_secret', ''),
        testnet=config_data.get('binance_testnet', True),
        proxy_host=config_data.get('proxy_host', ''),
        proxy_port=config_data.get('proxy_port', 0),
        heartbeat_interval=config_data.get('heartbeat_interval', 30),
        request_timeout=config_data.get('request_timeout', 10.0),
        max_retry=config_data.get('max_retry', 3),
        retry_delay=config_data.get('retry_delay', 1.0),
    )
    
    feed_config = FeedConfig(
        gateway_name=config_data.get('gateway_name', 'binance_futures'),
        buffer_size=config_data.get('buffer_size', 1000),
        tick_buffer_per_symbol=config_data.get('tick_buffer_per_symbol', 100),
        reconnect_delay=config_data.get('reconnect_delay', 1.0),
        max_reconnect_attempts=config_data.get('max_reconnect_attempts', 10),
        heartbeat_interval=config_data.get('heartbeat_interval', 30.0),
        enable_compression=config_data.get('enable_compression', False),
    )
    
    adapter_config = AdapterConfig(
        safety_level=config_data.get('safety_level', 'ENHANCED'),
        execution_mode=config_data.get('execution_mode', 'SIMULATION'),
        max_position_per_symbol=config_data.get('max_position_per_symbol', 100.0),
        max_total_exposure=config_data.get('max_total_exposure', 500000.0),
        max_orders_per_second=config_data.get('max_orders_per_second', 10.0),
        max_orders_per_minute=config_data.get('max_orders_per_minute', 300.0),
        max_drawdown_percent=config_data.get('max_drawdown_percent', 10.0),
        health_check_interval=config_data.get('health_check_interval', 30.0),
        enable_kill_switch=config_data.get('enable_kill_switch', True),
        require_kronos_validation=config_data.get('require_kronos_validation', True),
        kronos_min_confidence=config_data.get('kronos_min_confidence', 0.6),
    )
    
    live_confirmed = os.getenv(f"{env_prefix}LIVE_CONFIRMED", "").lower() == "true"
    
    return VnpyBridgeConfig(
        gateway=gateway_config,
        feed=feed_config,
        adapter=adapter_config,
        live_confirmed=live_confirmed,
        binance_api_key=config_data.get('binance_api_key', ''),
        binance_api_secret=config_data.get('binance_api_secret', ''),
        binance_testnet=config_data.get('binance_testnet', True),
        redis_host=config_data.get('redis_host', 'localhost'),
        redis_port=config_data.get('redis_port', 6379),
        redis_db=config_data.get('redis_db', 0),
        postgres_host=config_data.get('postgres_host', 'localhost'),
        postgres_port=config_data.get('postgres_port', 5432),
        postgres_db=config_data.get('postgres_db', 'trading'),
        postgres_user=config_data.get('postgres_user', 'trading'),
        postgres_password=config_data.get('postgres_password', ''),
        enable_audit_log=config_data.get('enable_audit_log', True),
        audit_log_path=config_data.get('audit_log_path', '/var/log/trading/audit.log'),
    )


def _load_from_env(prefix: str) -> Dict[str, Any]:
    """Load configuration from environment variables."""
    config = {}
    
    env_mappings = {
        f"{prefix}BINANCE_API_KEY": "binance_api_key",
        f"{prefix}BINANCE_API_SECRET": "binance_api_secret",
        f"{prefix}BINANCE_TESTNET": "binance_testnet",
        f"{prefix}EXECUTION_MODE": "execution_mode",
        f"{prefix}SAFETY_LEVEL": "safety_level",
        f"{prefix}MAX_POSITION": "max_position_per_symbol",
        f"{prefix}MAX_EXPOSURE": "max_total_exposure",
        f"{prefix}REDIS_HOST": "redis_host",
        f"{prefix}REDIS_PORT": "redis_port",
        f"{prefix}POSTGRES_HOST": "postgres_host",
        f"{prefix}POSTGRES_PORT": "postgres_port",
        f"{prefix}POSTGRES_DB": "postgres_db",
        f"{prefix}POSTGRES_USER": "postgres_user",
        f"{prefix}POSTGRES_PASSWORD": "postgres_password",
    }
    
    for env_var, config_key in env_mappings.items():
        value = os.getenv(env_var)
        if value is not None:
            config[config_key] = value
            
    return config


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def validate_config(config: VnpyBridgeConfig) -> bool:
    """
    Validate configuration.
    
    Args:
        config: Configuration to validate
        
    Returns:
        True if valid, raises ValueError otherwise
    """
    errors = config.validate()
    if errors:
        raise ValueError(f"Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))
    return True


def create_default_config(path: str) -> None:
    """Create a default configuration file."""
    default_config = {
        'gateway_name': 'binance_futures',
        'binance_api_key': '',
        'binance_api_secret': '',
        'binance_testnet': True,
        'execution_mode': 'SIMULATION',
        'safety_level': 'ENHANCED',
        'max_position_per_symbol': 100.0,
        'max_total_exposure': 500000.0,
        'max_orders_per_second': 10.0,
        'max_orders_per_minute': 300.0,
        'max_drawdown_percent': 10.0,
        'health_check_interval': 30.0,
        'enable_kill_switch': True,
        'require_kronos_validation': True,
        'kronos_min_confidence': 0.6,
        'redis_host': 'localhost',
        'redis_port': 6379,
        'postgres_host': 'localhost',
        'postgres_port': 5432,
        'postgres_db': 'trading',
        'postgres_user': 'trading',
        'postgres_password': '',
        'enable_audit_log': True,
        'audit_log_path': '/var/log/trading/audit.log',
    }
    
    with open(path, 'w') as f:
        yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)
    
    print(f"Default configuration created at: {path}")
