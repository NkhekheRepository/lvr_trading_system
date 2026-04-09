"""
Learning Processor - Continuously improves models from trade outcomes.

Processes trade outcome events and updates models:
- Edge estimation confidence
- Reality gap adjustments
- Filter effectiveness
- Regime-aware parameters
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime
import json

from core.event import Event, EventType
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class LearningProcessor(BaseProcessor):
    """
    Continual learning from trade outcomes.
    
    Learning dimensions:
    - Edge estimation accuracy
    - Filter effectiveness scores
    - Regime-specific parameters
    - Reality gap tracking
    - Model updates
    """
    
    MIN_TRADES_FOR_UPDATE = 10
    LEARNING_RATE = 0.1
    DECAY_FACTOR = 0.95
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        model_path: str = "models/",
    ):
        super().__init__(bus, state, config)
        self.model_path = model_path
        self._filter_scores: dict[str, list[float]] = {}
        self._regime_params: dict[str, dict] = {}
        self._trade_outcomes: dict[str, list[dict]] = {}
        self._model_version = 0
        
    def event_types(self) -> list[EventType]:
        return [
            EventType.ORDER_FILLED,
            EventType.TRADE_DECISION,
            EventType.REALITY_GAP,
        ]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if event.type == EventType.ORDER_FILLED:
            return await self._process_outcome(event)
        elif event.type == EventType.REALITY_GAP:
            return await self._process_gap(event)
        
        return None
    
    async def _process_outcome(self, event: Event) -> Optional[Event]:
        symbol = event.symbol
        payload = event.payload
        
        trade = {
            'event_id': event.event_id,
            'order_id': payload.get('order_id', ''),
            'side': payload.get('side', ''),
            'quantity': payload.get('filled_quantity', 0),
            'price': payload.get('avg_fill_price', 0),
            'slippage_bps': payload.get('slippage_bps', 0),
            'timestamp': event.timestamp,
        }
        
        self._trade_outcomes.setdefault(symbol, []).append(trade)
        if len(self._trade_outcomes[symbol]) > 100:
            self._trade_outcomes[symbol] = self._trade_outcomes[symbol][-100:]
        
        if len(self._trade_outcomes[symbol]) >= self.MIN_TRADES_FOR_UPDATE:
            await self._update_models(symbol)
        
        return None
    
    async def _process_gap(self, event: Event) -> Optional[Event]:
        symbol = event.symbol
        payload = event.payload
        
        gap_pct = payload.get('gap_pct', 0)
        adjustment_factor = payload.get('adjustment_factor', 1.0)
        
        await self._update_edge_adjustment(symbol, adjustment_factor)
        
        return None
    
    async def _update_models(self, symbol: str) -> None:
        trades = self._trade_outcomes.get(symbol, [])
        if len(trades) < self.MIN_TRADES_FOR_UPDATE:
            return
        
        alpha_state = await self._get_alpha_state(symbol)
        if alpha_state:
            await self._update_filter_scores(symbol, alpha_state)
        
        await self._update_regime_params(symbol)
        
        self._model_version += 1
        
        if self._model_version % 10 == 0:
            await self._save_model()
            
            model_event = Event.create(
                event_type=EventType.MODEL_UPDATED,
                payload={
                    'version': self._model_version,
                    'symbols_updated': [symbol],
                    'timestamp': int(datetime.now().timestamp() * 1000),
                },
                source=self.config.name if self.config else "learning_processor",
            )
            
            return model_event
        
        return None
    
    async def _get_alpha_state(self, symbol: str) -> Optional[dict]:
        if not self.state:
            return None
            
        alpha = await self.state.get(f"alpha:{symbol}")
        return alpha.value if alpha else None
    
    async def _update_filter_scores(
        self,
        symbol: str,
        alpha_state: dict
    ) -> None:
        filters_passed = alpha_state.get('filters_passed', [])
        filters_failed = alpha_state.get('filters_failed', [])
        
        all_filters = set(filters_passed + filters_failed)
        
        trades = self._trade_outcomes.get(symbol, [])
        recent_trades = trades[-self.MIN_TRADES_FOR_UPDATE:]
        
        wins = sum(1 for t in recent_trades if t.get('pnl', 0) > 0)
        win_rate = wins / len(recent_trades) if recent_trades else 0.5
        
        for filter_name in all_filters:
            scores = self._filter_scores.setdefault(filter_name, [])
            
            is_effective = filter_name in filters_passed and win_rate > 0.5
            score = 1.0 if is_effective else 0.0
            
            scores.append(score)
            if len(scores) > 50:
                scores = scores[-50:]
            
            self._filter_scores[filter_name] = scores
    
    async def _update_regime_params(self, symbol: str) -> None:
        if not self.state:
            return
            
        regime = await self.state.get(f"regime:{symbol}")
        if not regime or not regime.value:
            return
            
        market_regime = regime.value.get('market_regime', 'unknown')
        
        params = self._regime_params.setdefault(market_regime, {
            'edge_bias': 0.0,
            'confidence_scale': 1.0,
            'trade_count': 0,
        })
        
        params['trade_count'] += 1
        
        trades = self._trade_outcomes.get(symbol, [])
        if trades:
            recent_pnl = sum(t.get('pnl', 0) for t in trades[-10:])
            params['edge_bias'] = (
                params['edge_bias'] * self.DECAY_FACTOR +
                recent_pnl * self.LEARNING_RATE
            )
            
            if params['trade_count'] > 20:
                params['confidence_scale'] = min(1.2, max(0.8,
                    1.0 + params['edge_bias'] * 10
                ))
        
        await self.state.set(
            key=f"regime_params:{market_regime}",
            value=params,
            trace_id=self.config.name if self.config else "learning_processor",
        )
    
    async def _update_edge_adjustment(
        self,
        symbol: str,
        adjustment_factor: float
    ) -> None:
        if not self.state:
            return
            
        edge_state = await self.state.get(f"edge:{symbol}")
        current_adjustment = 1.0
        
        if edge_state and edge_state.value:
            current_adjustment = edge_state.value.get('adjustment_factor', 1.0)
        
        new_adjustment = (
            current_adjustment * (1 - self.LEARNING_RATE) +
            adjustment_factor * self.LEARNING_RATE
        )
        
        await self.state.set(
            key=f"edge:{symbol}",
            value=edge_state.value if edge_state and edge_state.value else {},
            trace_id=self.config.name if self.config else "learning_processor",
        )
        
        logger.info(
            f"Updated edge adjustment for {symbol}: {current_adjustment:.3f} -> {new_adjustment:.3f}"
        )
    
    async def _save_model(self) -> None:
        model_data = {
            'version': self._model_version,
            'filter_scores': {
                k: sum(v) / len(v) if v else 0
                for k, v in self._filter_scores.items()
            },
            'regime_params': self._regime_params,
            'timestamp': int(datetime.now().timestamp() * 1000),
        }
        
        logger.info(
            f"Saving model v{self._model_version}",
            extra={'model_data': model_data}
        )
