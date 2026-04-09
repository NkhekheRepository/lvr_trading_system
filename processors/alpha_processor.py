"""
Alpha Processor - Generates alpha signals from features.

Processes FEATURES_COMPUTED events and produces ALPHA_SIGNAL events
with direction, strength, confidence, and filter results.
"""

import logging
from typing import Optional
from dataclasses import asdict

from core.event import Event, EventType, AlphaSignalPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class AlphaProcessor(BaseProcessor):
    """
    Generates alpha signals from computed features.
    
    Signal generation based on:
    - OFI (Order Flow Imbalance) for direction
    - Z-scores for statistical edge
    - Regime context for confidence adjustment
    - Filter chain for signal validation
    """
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        ofi_threshold: float = 0.3,
        zscore_threshold: float = 1.5,
        min_confidence: float = 0.5,
    ):
        super().__init__(bus, state, config)
        self.ofi_threshold = ofi_threshold
        self.zscore_threshold = zscore_threshold
        self.min_confidence = min_confidence
        
    def event_types(self) -> list[EventType]:
        return [EventType.FEATURES_COMPUTED]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        ofi = payload.get('ofi', 0)
        depth_zscore = payload.get('depth_zscore', 0)
        spread_zscore = payload.get('spread_zscore', 0)
        microstructure_score = payload.get('microstructure_score', 0.5)
        
        filters_passed = []
        filters_failed = []
        
        if abs(ofi) >= self.ofi_threshold:
            filters_passed.append('ofi_threshold')
        else:
            filters_failed.append('ofi_threshold')
            
        if abs(depth_zscore) >= self.zscore_threshold:
            filters_passed.append('depth_zscore')
        else:
            filters_failed.append('depth_zscore')
            
        if microstructure_score >= 0.4:
            filters_passed.append('microstructure_quality')
        else:
            filters_failed.append('microstructure_quality')
        
        if not filters_passed:
            return None
        
        direction = self._determine_direction(ofi, depth_zscore, spread_zscore)
        strength = self._compute_strength(ofi, depth_zscore, spread_zscore)
        confidence = self._compute_confidence(
            microstructure_score,
            len(filters_passed),
            len(filters_passed) + len(filters_failed)
        )
        
        expected_edge = self._estimate_edge(
            direction, strength, confidence
        )
        
        regime = await self._get_regime(symbol)
        
        signal_payload = AlphaSignalPayload(
            direction=direction,
            strength=strength,
            confidence=confidence,
            expected_edge=expected_edge,
            filters_passed=filters_passed,
            filters_failed=filters_failed,
            regime=regime,
        )
        
        output_event = Event.create(
            event_type=EventType.ALPHA_SIGNAL,
            symbol=symbol,
            payload=asdict(signal_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "alpha_processor",
        )
        
        await self._update_signal_state(symbol, signal_payload)
        
        return output_event
    
    def _determine_direction(
        self,
        ofi: float,
        depth_zscore: float,
        spread_zscore: float
    ) -> int:
        ofi_signal = 1 if ofi > self.ofi_threshold else (-1 if ofi < -self.ofi_threshold else 0)
        depth_signal = 1 if depth_zscore > self.zscore_threshold else (-1 if depth_zscore < -self.zscore_threshold else 0)
        
        if ofi_signal == 0 and depth_signal == 0:
            return 0
            
        if ofi_signal != 0 and depth_signal != 0:
            return ofi_signal if abs(ofi_signal) >= abs(depth_signal) else depth_signal
            
        return ofi_signal if ofi_signal != 0 else depth_signal
    
    def _compute_strength(
        self,
        ofi: float,
        depth_zscore: float,
        spread_zscore: float
    ) -> float:
        ofi_strength = min(1.0, abs(ofi) / self.ofi_threshold)
        depth_strength = min(1.0, abs(depth_zscore) / self.zscore_threshold)
        spread_strength = min(1.0, abs(spread_zscore) / self.zscore_threshold)
        
        return (ofi_strength * 0.5 + depth_strength * 0.3 + spread_strength * 0.2)
    
    def _compute_confidence(
        self,
        microstructure_score: float,
        passed: int,
        total: int
    ) -> float:
        if total == 0:
            return 0.0
            
        filter_ratio = passed / total
        return microstructure_score * filter_ratio
    
    def _estimate_edge(
        self,
        direction: int,
        strength: float,
        confidence: float
    ) -> float:
        if direction == 0 or confidence < self.min_confidence:
            return 0.0
            
        base_edge = 0.0005
        return direction * base_edge * strength * confidence
    
    async def _get_regime(self, symbol: str) -> str:
        if not self.state:
            return "unknown"
            
        regime_data = await self.state.get(f"regime:{symbol}")
        if regime_data and regime_data.value:
            return regime_data.value.get('market_regime', 'unknown')
        return "unknown"
    
    async def _update_signal_state(
        self,
        symbol: str,
        signal: AlphaSignalPayload
    ) -> None:
        if not self.state:
            return
            
        state_key = f"alpha:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'direction': signal.direction,
                'strength': signal.strength,
                'confidence': signal.confidence,
                'expected_edge': signal.expected_edge,
                'regime': signal.regime,
            },
            trace_id=self.config.name if self.config else "alpha_processor",
        )
