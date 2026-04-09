"""
Regime Processor - Detects market regimes using Kronos.

Processes FEATURES_COMPUTED events and produces REGIME_DETECTED events
with market regime classification and risk parameters.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType, RegimePayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class RegimeProcessor(BaseProcessor):
    """
    Detects market regimes using features and Kronos model.
    
    Regime dimensions:
    - Market regime (trending, ranging, volatile)
    - Volatility regime (low, medium, high)
    - Liquidity regime (adequate, stressed, scarce)
    """
    
    REGIME_THRESHOLDS = {
        'trending': 0.6,
        'volatile': 0.7,
        'stressed_liquidity': -0.5,
    }
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        use_kronos: bool = True,
    ):
        super().__init__(bus, state, config)
        self.use_kronos = use_kronos
        self._regime_cache: dict[str, dict] = {}
        self._last_update: dict[str, int] = {}
        self._cache_ttl_ms = 60000
        
    def event_types(self) -> list[EventType]:
        return [EventType.FEATURES_COMPUTED]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        
        if self._is_cache_valid(symbol):
            return None
            
        payload = event.payload
        
        ofi = payload.get('ofi', 0)
        volatility = payload.get('volatility', 0)
        microstructure_score = payload.get('microstructure_score', 0.5)
        spread_zscore = payload.get('spread_zscore', 0)
        
        market_regime = self._classify_market_regime(ofi, spread_zscore)
        
        volatility_regime = self._classify_volatility_regime(volatility)
        
        liquidity_regime = self._classify_liquidity_regime(
            ofi, microstructure_score, spread_zscore
        )
        
        risk_score = self._compute_risk_score(
            market_regime, volatility_regime, liquidity_regime
        )
        
        use_caution = risk_score > 0.6 or volatility_regime == 'high'
        
        max_position_scale = self._compute_position_scale(
            risk_score, volatility_regime, liquidity_regime
        )
        
        confidence = self._compute_confidence(microstructure_score, payload)
        
        regime_payload = RegimePayload(
            market_regime=market_regime,
            volatility_regime=volatility_regime,
            liquidity_regime=liquidity_regime,
            risk_score=risk_score,
            use_caution=use_caution,
            max_position_scale=max_position_scale,
            confidence=confidence,
        )
        
        output_event = Event.create(
            event_type=EventType.REGIME_DETECTED,
            symbol=symbol,
            payload=asdict(regime_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "regime_processor",
        )
        
        self._update_cache(symbol, regime_payload)
        await self._update_regime_state(symbol, regime_payload)
        
        return output_event
    
    def _is_cache_valid(self, symbol: str) -> bool:
        if symbol not in self._last_update:
            return False
            
        age = datetime.now().timestamp() * 1000 - self._last_update[symbol]
        return age < self._cache_ttl_ms
    
    def _classify_market_regime(
        self,
        ofi: float,
        spread_zscore: float
    ) -> str:
        ofi_magnitude = abs(ofi)
        spread_magnitude = abs(spread_zscore)
        
        if spread_magnitude > self.REGIME_THRESHOLDS['volatile']:
            return 'volatile'
        elif ofi_magnitude > self.REGIME_THRESHOLDS['trending']:
            return 'trending'
        else:
            return 'ranging'
    
    def _classify_volatility_regime(self, volatility: float) -> str:
        if volatility < 0.1:
            return 'low'
        elif volatility < 0.3:
            return 'medium'
        else:
            return 'high'
    
    def _classify_liquidity_regime(
        self,
        ofi: float,
        microstructure_score: float,
        spread_zscore: float
    ) -> str:
        liquidity_score = microstructure_score - abs(spread_zscore) * 0.1
        
        if liquidity_score < 0.3 or ofi < self.REGIME_THRESHOLDS['stressed_liquidity']:
            return 'scarce'
        elif liquidity_score < 0.5:
            return 'stressed'
        else:
            return 'adequate'
    
    def _compute_risk_score(
        self,
        market_regime: str,
        volatility_regime: str,
        liquidity_regime: str
    ) -> float:
        regime_scores = {
            'volatile': 0.3,
            'trending': 0.15,
            'ranging': 0.05,
        }
        
        vol_scores = {
            'high': 0.3,
            'medium': 0.15,
            'low': 0.0,
        }
        
        liq_scores = {
            'scarce': 0.3,
            'stressed': 0.15,
            'adequate': 0.0,
        }
        
        return min(1.0, 
            regime_scores.get(market_regime, 0) +
            vol_scores.get(volatility_regime, 0) +
            liq_scores.get(liquidity_regime, 0)
        )
    
    def _compute_position_scale(
        self,
        risk_score: float,
        volatility_regime: str,
        liquidity_regime: str
    ) -> float:
        base_scale = 1.0
        
        risk_reduction = risk_score * 0.5
        
        vol_reduction = {
            'high': 0.3,
            'medium': 0.1,
            'low': 0.0,
        }.get(volatility_regime, 0)
        
        liq_reduction = {
            'scarce': 0.3,
            'stressed': 0.1,
            'adequate': 0.0,
        }.get(liquidity_regime, 0)
        
        return max(0.1, base_scale - risk_reduction - vol_reduction - liq_reduction)
    
    def _compute_confidence(
        self,
        microstructure_score: float,
        payload: dict
    ) -> float:
        data_quality = payload.get('quality_metrics', {}).get('spread_history_len', 0)
        data_factor = min(1.0, data_quality / 50)
        
        return microstructure_score * 0.7 + data_factor * 0.3
    
    def _update_cache(self, symbol: str, regime: RegimePayload) -> None:
        self._regime_cache[symbol] = asdict(regime)
        self._last_update[symbol] = int(datetime.now().timestamp() * 1000)
    
    async def _update_regime_state(
        self,
        symbol: str,
        regime: RegimePayload
    ) -> None:
        if not self.state:
            return
            
        state_key = f"regime:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'market_regime': regime.market_regime,
                'volatility_regime': regime.volatility_regime,
                'liquidity_regime': regime.liquidity_regime,
                'risk_score': regime.risk_score,
                'use_caution': regime.use_caution,
                'max_position_scale': regime.max_position_scale,
                'confidence': regime.confidence,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id=self.config.name if self.config else "regime_processor",
        )
