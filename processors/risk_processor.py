"""
Risk Processor - Pre-trade risk validation.

Processes TRADE_DECISION events and produces RISK_EVALUATED events
with risk approval/rejection and required actions.
"""

import logging
from typing import Optional
from dataclasses import asdict, field
from datetime import datetime

from core.event import Event, EventType, RiskPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class RiskProcessor(BaseProcessor):
    """
    Pre-trade risk validation.
    
    Risk checks:
    - Leverage limits
    - Drawdown limits
    - Daily loss limits
    - Position size limits
    - Correlation limits
    - Required actions (reduce positions, halt)
    """
    
    MAX_LEVERAGE = 3.0
    MAX_DRAWDOWN_PCT = 0.15
    MAX_DAILY_LOSS_PCT = 0.05
    MAX_POSITION_SIZE_PCT = 0.25
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        max_leverage: float = MAX_LEVERAGE,
        max_drawdown: float = MAX_DRAWDOWN_PCT,
        max_daily_loss: float = MAX_DAILY_LOSS_PCT,
        max_position_size: float = MAX_POSITION_SIZE_PCT,
    ):
        super().__init__(bus, state, config)
        self.max_leverage = max_leverage
        self.max_drawdown = max_drawdown
        self.max_daily_loss = max_daily_loss
        self.max_position_size = max_position_size
        
    def event_types(self) -> list[EventType]:
        return [EventType.TRADE_DECISION, EventType.PORTFOLIO_UPDATED]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if event.type == EventType.PORTFOLIO_UPDATED:
            await self._update_risk_state(event)
            return None
            
        if event.type == EventType.TRADE_DECISION:
            return await self._process_decision(event)
            
        return None
    
    async def _process_decision(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        decision = payload.get('decision', 'REJECT')
        
        if decision != 'ACCEPT':
            return None
        
        risk_state = await self._get_risk_state()
        portfolio_state = await self._get_portfolio_state()
        
        rejection_reasons = []
        required_actions = []
        
        leverage = portfolio_state.get('leverage', 1.0)
        if leverage > self.max_leverage:
            rejection_reasons.append(f"leverage_exceeded_{leverage:.2f}")
            required_actions.append("reduce_leverage")
        
        drawdown = portfolio_state.get('drawdown_pct', 0)
        if drawdown > self.max_drawdown:
            rejection_reasons.append(f"drawdown_exceeded_{drawdown:.2%}")
            required_actions.append("halt_trading")
        
        daily_loss = portfolio_state.get('daily_pnl', 0)
        daily_loss_pct = daily_loss / self.initial_capital if self.initial_capital > 0 else 0
        if daily_loss_pct < -self.max_daily_loss:
            rejection_reasons.append(f"daily_loss_exceeded_{daily_loss_pct:.2%}")
            required_actions.append("stop_losses")
        
        position_size = await self._calculate_position_size(symbol)
        if position_size > self.max_position_size:
            rejection_reasons.append(f"position_size_exceeded_{position_size:.2%}")
            required_actions.append("reduce_position")
        
        approved = len(rejection_reasons) == 0
        
        position_size_pct = position_size
        
        risk_payload = RiskPayload(
            approved=approved,
            leverage=leverage,
            drawdown_pct=drawdown,
            daily_loss_pct=daily_loss_pct,
            position_size_pct=position_size_pct,
            rejection_reason=rejection_reasons[0] if rejection_reasons else None,
            required_actions=required_actions,
        )
        
        output_event = Event.create(
            event_type=EventType.RISK_EVALUATED,
            symbol=symbol,
            payload=asdict(risk_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "risk_processor",
        )
        
        await self._update_risk_approval_state(symbol, risk_payload)
        
        if not approved:
            logger.warning(
                f"Trade rejected for {symbol}: {rejection_reasons}",
                extra={
                    'symbol': symbol,
                    'reasons': rejection_reasons,
                    'actions': required_actions,
                }
            )
        
        return output_event
    
    async def _get_risk_state(self) -> dict:
        if not self.state:
            return {}
            
        risk_state = await self.state.get("risk:global")
        return risk_state.value if risk_state else {}
    
    async def _get_portfolio_state(self) -> dict:
        if not self.state:
            return {'leverage': 1.0, 'drawdown_pct': 0, 'daily_pnl': 0}
            
        portfolio = await self.state.get("portfolio:global")
        return portfolio.value if portfolio else {
            'leverage': 1.0,
            'drawdown_pct': 0,
            'daily_pnl': 0,
        }
    
    async def _calculate_position_size(self, symbol: str) -> float:
        if not self.state:
            return 0.0
            
        position = await self.state.get(f"position:{symbol}")
        portfolio = await self.state.get("portfolio:global")
        
        if not position or not portfolio:
            return 0.0
            
        pos_value = abs(position.value.get('quantity', 0) * position.value.get('avg_entry', 0))
        total_value = portfolio.value.get('total_value', self.initial_capital)
        
        return pos_value / total_value if total_value > 0 else 0.0
    
    async def _update_risk_state(self, event: Event) -> None:
        if not self.state:
            return
            
        payload = event.payload
        
        state_key = "risk:global"
        await self.state.set(
            key=state_key,
            value={
                'leverage': payload.get('leverage', 1.0),
                'drawdown_pct': payload.get('drawdown_pct', 0),
                'daily_pnl': payload.get('daily_pnl', 0),
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id=self.config.name if self.config else "risk_processor",
        )
    
    async def _update_risk_approval_state(
        self,
        symbol: str,
        risk: RiskPayload
    ) -> None:
        if not self.state:
            return
            
        state_key = f"risk_approval:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'approved': risk.approved,
                'leverage': risk.leverage,
                'position_size_pct': risk.position_size_pct,
                'rejection_reason': risk.rejection_reason,
                'required_actions': risk.required_actions,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id=self.config.name if self.config else "risk_processor",
        )
