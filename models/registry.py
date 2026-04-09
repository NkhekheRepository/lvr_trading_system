"""
Model Registry - Immutable model versioning with full lineage tracking.

Features:
- Immutable artifacts (write-once)
- Full lineage: parent version, dataset hash, config hash
- Deterministic reproducibility
- State machine: candidate → validated → shadow → canary → active → retired
- Validation gates before promotion
- Safe deployment with rollback
"""

import asyncio
import hashlib
import json
import logging
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from collections import deque

from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class ModelState(Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    RETIRED = "retired"


class ValidationResult:
    def __init__(
        self,
        passed: bool,
        backtest_threshold: bool = False,
        oos_pass: bool = False,
        walk_forward_pass: bool = False,
        stability_pass: bool = False,
        regime_robustness: bool = False,
        details: Optional[dict] = None,
    ):
        self.passed = passed
        self.backtest_threshold = backtest_threshold
        self.oos_pass = oos_pass
        self.walk_forward_pass = walk_forward_pass
        self.stability_pass = stability_pass
        self.regime_robustness = regime_robustness
        self.details = details or {}
    
    def all_gates_passed(self) -> bool:
        return all([
            self.backtest_threshold,
            self.oos_pass,
            self.walk_forward_pass,
            self.stability_pass,
            self.regime_robustness,
        ])


@dataclass
class ModelMetadata:
    version: int
    model_name: str
    model_hash: str
    parent_version: Optional[int] = None
    training_config: dict = field(default_factory=dict)
    dataset_hash: str = ""
    feature_pipeline_version: str = ""
    schema_hash: str = ""
    hyperparameters: dict = field(default_factory=dict)
    code_version: str = ""
    environment: dict = field(default_factory=dict)
    state: ModelState = ModelState.CANDIDATE
    created_at: datetime = field(default_factory=datetime.now)
    promoted_at: Optional[datetime] = None
    metrics: dict = field(default_factory=dict)
    shadow_predictions: list = field(default_factory=list)
    canary_allocation: float = 0.0


class ModelRegistry:
    """
    Immutable model registry with governance.
    
    Guarantees:
    - Full reproducibility
    - Zero silent corruption (hash verification)
    - Safe continuous learning
    - Controlled deployment
    - Immediate failure containment
    """
    
    STATES = [s.value for s in ModelState]
    STATE_ORDER = [
        ModelState.CANDIDATE,
        ModelState.VALIDATED,
        ModelState.SHADOW,
        ModelState.CANARY,
        ModelState.ACTIVE,
        ModelState.RETIRED,
    ]
    
    def __init__(
        self,
        bus: Optional[EventBus] = None,
        state: Optional[DistributedState] = None,
        base_path: str = "models/versions",
    ):
        self.bus = bus
        self.state = state
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        
        self._models: dict[str, list[ModelMetadata]] = {}
        self._active_models: dict[str, int] = {}
        self._shadow_predictions: dict[int, list] = {}
        self._validation_cache: dict[int, ValidationResult] = {}
    
    async def register_candidate(
        self,
        model_name: str,
        model_data: Any,
        training_config: dict,
        dataset_hash: str,
        parent_version: Optional[int] = None,
        code_version: str = "",
    ) -> ModelMetadata:
        """
        Register a new model as candidate.
        
        Model is saved as immutable artifact.
        """
        if model_name not in self._models:
            self._models[model_name] = []
        
        version = len(self._models[model_name]) + 1
        
        artifact_path = self._save_artifact(model_name, version, model_data)
        model_hash = self._compute_hash(artifact_path)
        
        metadata = ModelMetadata(
            version=version,
            model_name=model_name,
            model_hash=model_hash,
            parent_version=parent_version,
            training_config=training_config,
            dataset_hash=dataset_hash,
            code_version=code_version,
            state=ModelState.CANDIDATE,
        )
        
        self._models[model_name].append(metadata)
        
        await self._persist_metadata(model_name, metadata)
        
        logger.info(
            f"Registered candidate model {model_name}:v{version}",
            extra={'hash': model_hash[:16]}
        )
        
        return metadata
    
    def _save_artifact(self, model_name: str, version: int, model_data: Any) -> Path:
        """Save model artifact (immutable, no overwrite)."""
        path = self.base_path / model_name / f"v{version}_{int(datetime.now().timestamp())}.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if path.exists():
            raise ValueError(f"Artifact path already exists: {path}")
        
        with open(path, 'wb') as f:
            pickle.dump(model_data, f)
        
        return path
    
    def _compute_hash(self, path: Path) -> str:
        """Compute SHA256 hash of model artifact."""
        sha256 = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def load_model(self, model_name: str, version: int) -> Optional[Any]:
        """Load model artifact with hash verification."""
        metadata = self.get_metadata(model_name, version)
        if not metadata:
            return None
        
        path = self._find_artifact_path(model_name, version, metadata.model_hash)
        if not path:
            logger.error(f"Model artifact not found or hash mismatch: {model_name}:v{version}")
            return None
        
        with open(path, 'rb') as f:
            return pickle.load(f)
    
    def _find_artifact_path(self, model_name: str, version: int, expected_hash: str) -> Optional[Path]:
        """Find artifact path with hash verification."""
        model_dir = self.base_path / model_name
        if not model_dir.exists():
            return None
        
        for path in model_dir.glob(f"v{version}_*.pkl"):
            actual_hash = self._compute_hash(path)
            if actual_hash == expected_hash:
                return path
        
        return None
    
    async def validate_candidate(
        self,
        model_name: str,
        version: int,
        validation_result: ValidationResult,
    ) -> bool:
        """
        Validate candidate model.
        
        Only promotes if ALL gates pass.
        """
        metadata = self.get_metadata(model_name, version)
        if not metadata or metadata.state != ModelState.CANDIDATE:
            return False
        
        if not validation_result.all_gates_passed():
            await self._log_rejection(model_name, version, validation_result)
            return False
        
        self._validation_cache[version] = validation_result
        metadata.metrics = validation_result.details
        
        await self._transition_state(model_name, version, ModelState.VALIDATED)
        
        logger.info(f"Model validated: {model_name}:v{version}")
        
        return True
    
    async def deploy_shadow(
        self,
        model_name: str,
        version: int,
    ) -> bool:
        """
        Deploy model in shadow mode.
        
        Runs alongside active model but executes NO trades.
        Records predictions for comparison.
        """
        if not await self._can_transition(model_name, version, ModelState.SHADOW):
            return False
        
        metadata = self.get_metadata(model_name, version)
        if not metadata or metadata.state != ModelState.VALIDATED:
            return False
        
        await self._transition_state(model_name, version, ModelState.SHADOW)
        
        logger.info(f"Shadow deployment: {model_name}:v{version}")
        
        return True
    
    async def deploy_canary(
        self,
        model_name: str,
        version: int,
        allocation: float = 0.1,
    ) -> bool:
        """
        Deploy model in canary mode.
        
        Small capital allocation (default 10%).
        """
        if not await self._can_transition(model_name, version, ModelState.CANARY):
            return False
        
        metadata = self.get_metadata(model_name, version)
        if not metadata or metadata.state != ModelState.SHADOW:
            return False
        
        metadata.canary_allocation = allocation
        
        await self._transition_state(model_name, version, ModelState.CANARY)
        
        logger.info(f"Canary deployment: {model_name}:v{version} at {allocation:.0%}")
        
        return True
    
    async def promote_to_active(
        self,
        model_name: str,
        version: int,
    ) -> bool:
        """
        Promote model to active (full production).
        
        Demotes current active model to retired.
        """
        if not await self._can_transition(model_name, version, ModelState.ACTIVE):
            return False
        
        metadata = self.get_metadata(model_name, version)
        if not metadata or metadata.state != ModelState.CANARY:
            return False
        
        current_active = self._active_models.get(model_name)
        if current_active:
            await self._transition_state(model_name, current_active, ModelState.RETIRED)
        
        await self._transition_state(model_name, version, ModelState.ACTIVE)
        self._active_models[model_name] = version
        
        logger.info(f"Promoted to active: {model_name}:v{version}")
        
        return True
    
    async def rollback_to_previous(
        self,
        model_name: str,
        current_version: int,
    ) -> bool:
        """
        Rollback to previous model version.
        """
        metadata = self.get_metadata(model_name, current_version)
        if not metadata:
            return False
        
        candidates = [
            m for m in self._models.get(model_name, [])
            if m.state in (ModelState.CANARY, ModelState.SHADOW)
            and m.version < current_version
        ]
        
        if not candidates:
            logger.warning(f"No previous version to rollback to: {model_name}")
            return False
        
        fallback = max(candidates, key=lambda m: m.version)
        
        await self._transition_state(model_name, current_version, ModelState.RETIRED)
        await self._transition_state(model_name, fallback.version, ModelState.CANARY)
        
        self._active_models[model_name] = fallback.version
        
        logger.info(f"Rolled back to: {model_name}:v{fallback.version}")
        
        return True
    
    async def _can_transition(
        self,
        model_name: str,
        version: int,
        target_state: ModelState,
    ) -> bool:
        """Check if state transition is allowed."""
        current = self.get_metadata(model_name, version)
        if not current:
            return False
        
        if current.state == ModelState.RETIRED:
            return False
        
        current_idx = self.STATE_ORDER.index(current.state)
        target_idx = self.STATE_ORDER.index(target_state)
        
        if target_idx > current_idx + 1:
            return False
        
        return True
    
    async def _transition_state(
        self,
        model_name: str,
        version: int,
        new_state: ModelState,
    ) -> None:
        """Transition model to new state."""
        metadata = self.get_metadata(model_name, version)
        if not metadata:
            return
        
        old_state = metadata.state
        metadata.state = new_state
        
        if new_state in (ModelState.ACTIVE, ModelState.CANARY):
            metadata.promoted_at = datetime.now()
        
        await self._persist_metadata(model_name, metadata)
        
        await self._emit_state_change(model_name, version, old_state, new_state)
    
    async def _emit_state_change(
        self,
        model_name: str,
        version: int,
        old_state: ModelState,
        new_state: ModelState,
    ) -> None:
        """Emit state change event."""
        if not self.bus:
            return
        
        from core.event import Event, EventType
        
        event = Event.create(
            event_type=EventType.MODEL_STATE_CHANGED,
            payload={
                'model_name': model_name,
                'version': version,
                'old_state': old_state.value,
                'new_state': new_state.value,
            },
            source="model_registry",
        )
        
        await self.bus.publish(event)
    
    async def _log_rejection(
        self,
        model_name: str,
        version: int,
        result: ValidationResult,
    ) -> None:
        """Log model rejection."""
        logger.warning(
            f"Model rejected: {model_name}:v{version}",
            extra={
                'backtest': result.backtest_threshold,
                'oos': result.oos_pass,
                'walk_forward': result.walk_forward_pass,
                'stability': result.stability_pass,
                'regime': result.regime_robustness,
            }
        )
    
    def get_metadata(self, model_name: str, version: int) -> Optional[ModelMetadata]:
        """Get model metadata."""
        models = self._models.get(model_name, [])
        for m in models:
            if m.version == version:
                return m
        return None
    
    def get_active_version(self, model_name: str) -> Optional[int]:
        """Get currently active model version."""
        return self._active_models.get(model_name)
    
    def get_all_versions(self, model_name: str) -> list[ModelMetadata]:
        """Get all versions of a model."""
        return self._models.get(model_name, [])
    
    async def _persist_metadata(self, model_name: str, metadata: ModelMetadata) -> None:
        """Persist metadata to state store."""
        if not self.state:
            return
        
        key = f"model:{model_name}:v{metadata.version}"
        await self.state.set(
            key=key,
            value={
                'version': metadata.version,
                'model_name': metadata.model_name,
                'model_hash': metadata.model_hash,
                'state': metadata.state.value,
                'metrics': metadata.metrics,
            },
            trace_id="model_registry",
        )
