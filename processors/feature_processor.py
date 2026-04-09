"""
Feature Processor - Computes trading features from market data.

Processes ORDERBOOK_UPDATE events and produces FEATURES_COMPUTED events
containing OFI, spread, liquidity vacuum, and combined quality metrics.
"""

import logging
from typing import Optional
from dataclasses import asdict

from core.event import Event, EventType, FeaturesPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class FeatureProcessor(BaseProcessor):
    """
    Computes features from order book data.
    
    Features computed:
    - OFI (Order Flow Imbalance)
    - Spread metrics (absolute and z-score)
    - Depth metrics (z-score for depth changes)
    - Liquidity vacuum detection
    - Combined quality score
    """
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        lookback_window: int = 100,
    ):
        super().__init__(bus, state, config)
        self.lookback_window = lookback_window
        self._spread_history: dict[str, list[float]] = {}
        self._depth_history: dict[str, list[float]] = {}
        self._ofi_history: dict[str, list[float]] = {}
        self._price_history: dict[str, list[float]] = {}
        
    def event_types(self) -> list[EventType]:
        return [EventType.ORDERBOOK_UPDATE]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        bids = payload.get('bids', [])
        asks = payload.get('asks', [])
        
        if len(bids) < 2 or len(asks) < 2:
            return None
            
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread = (best_ask - best_bid) / best_bid if best_bid > 0 else 0
        
        bid_depth = sum(float(b[1]) for b in bids[:5])
        ask_depth = sum(float(a[1]) for a in asks[:5])
        total_depth = bid_depth + ask_depth
        
        ofi = (bid_depth - ask_depth) / (bid_depth + ask_depth) if total_depth > 0 else 0
        
        self._spread_history.setdefault(symbol, []).append(spread)
        self._depth_history.setdefault(symbol, []).append(total_depth)
        self._ofi_history.setdefault(symbol, []).append(ofi)
        self._price_history.setdefault(symbol, []).append(best_bid)
        
        for hist in [self._spread_history, self._depth_history, self._ofi_history, self._price_history]:
            if len(hist[symbol]) > self.lookback_window:
                hist[symbol] = hist[symbol][-self.lookback_window:]
        
        spread_zscore = self._compute_zscore(symbol, 'spread')
        depth_zscore = self._compute_zscore(symbol, 'depth')
        
        volatility = self._compute_volatility(symbol)
        
        returns = self._compute_returns(symbol)
        
        microstructure_score = self._compute_microstructure_score(
            spread, ofi, volatility
        )
        
        quality_metrics = {
            'spread_bps': spread * 10000,
            'bid_depth': bid_depth,
            'ask_depth': ask_depth,
            'total_depth': total_depth,
            'spread_history_len': len(self._spread_history.get(symbol, [])),
            'is_stale': False,
        }
        
        features_payload = FeaturesPayload(
            returns=returns,
            depth_zscore=depth_zscore,
            spread_zscore=spread_zscore,
            ofi=ofi,
            volatility=volatility,
            microstructure_score=microstructure_score,
            quality_metrics=quality_metrics,
        )
        
        output_event = Event.create(
            event_type=EventType.FEATURES_COMPUTED,
            symbol=symbol,
            payload=asdict(features_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "feature_processor",
        )
        
        await self._update_state(symbol, features_payload)
        
        return output_event
    
    def _compute_zscore(self, symbol: str, hist_type: str) -> float:
        if hist_type == 'spread':
            hist = self._spread_history.get(symbol, [])
        elif hist_type == 'depth':
            hist = self._depth_history.get(symbol, [])
        elif hist_type == 'ofi':
            hist = self._ofi_history.get(symbol, [])
        else:
            return 0.0
            
        if len(hist) < 10:
            return 0.0
            
        mean = sum(hist) / len(hist)
        variance = sum((x - mean) ** 2 for x in hist) / len(hist)
        std = variance ** 0.5
        
        if std == 0:
            return 0.0
            
        return (hist[-1] - mean) / std
    
    def _compute_volatility(self, symbol: str) -> float:
        prices = self._price_history.get(symbol, [])
        if len(prices) < 2:
            return 0.0
            
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        if not returns:
            return 0.0
            
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return (variance ** 0.5) * (252 * 24) ** 0.5
    
    def _compute_returns(self, symbol: str) -> float:
        prices = self._price_history.get(symbol, [])
        if len(prices) < 2:
            return 0.0
        return (prices[-1] - prices[-2]) / prices[-2]
    
    def _compute_microstructure_score(
        self,
        spread: float,
        ofi: float,
        volatility: float
    ) -> float:
        spread_score = max(0, 1 - spread * 1000)
        
        ofi_score = 1 - abs(ofi)
        
        vol_score = max(0, 1 - volatility)
        
        return (spread_score * 0.4 + ofi_score * 0.3 + vol_score * 0.3)
    
    async def _update_state(self, symbol: str, features: FeaturesPayload) -> None:
        if not self.state:
            return
            
        state_key = f"features:{symbol}"
        current = await self.state.get(state_key)
        
        existing = current.value if current else {}
        existing.update({
            'returns': features.returns,
            'depth_zscore': features.depth_zscore,
            'spread_zscore': features.spread_zscore,
            'ofi': features.ofi,
            'volatility': features.volatility,
            'microstructure_score': features.microstructure_score,
            'updated_at': features.quality_metrics.get('spread_history_len', 0),
        })
        
        await self.state.set(
            key=state_key,
            value=existing,
            trace_id=self.config.name if self.config else "feature_processor",
        )
