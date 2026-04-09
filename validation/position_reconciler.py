"""
Position Reconciler - Reconciles positions between systems.

Ensures internal position tracking matches exchange/executor positions.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class PositionReconciler:
    """
    Reconciles positions across systems.
    
    Reconciliation targets:
    - Internal state vs executor
    - Executor vs exchange
    - Pre-trade vs post-trade
    """
    
    MISMATCH_THRESHOLD_PCT = 0.01
    RECONCILIATION_INTERVAL_MS = 30000
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
    ):
        self.bus = bus
        self.state = state
        self._last_reconciliation: dict[str, int] = {}
        self._reconciliation_history: list[dict] = []
        self._pending_reconciliations: dict[str, dict] = {}
        
    async def reconcile_position(
        self,
        symbol: str,
        internal_position: dict,
        executor_position: dict,
        exchange_position: Optional[dict] = None
    ) -> tuple[bool, dict]:
        """
        Perform position reconciliation.
        
        Returns:
            (is_reconciled, reconciliation_details)
        """
        details = {
            'symbol': symbol,
            'timestamp': int(datetime.now().timestamp() * 1000),
            'internal': internal_position,
            'executor': executor_position,
            'exchange': exchange_position,
            'mismatches': [],
            'actions_required': [],
        }
        
        internal_qty = internal_position.get('quantity', 0)
        executor_qty = executor_position.get('quantity', 0)
        
        qty_diff = abs(internal_qty - executor_qty)
        qty_diff_pct = qty_diff / abs(internal_qty) if internal_qty != 0 else 0
        
        if qty_diff_pct > self.MISMATCH_THRESHOLD_PCT:
            details['mismatches'].append({
                'field': 'quantity',
                'internal': internal_qty,
                'executor': executor_qty,
                'difference': qty_diff,
                'difference_pct': qty_diff_pct,
            })
            details['actions_required'].append('correct_internal_position')
        
        internal_entry = internal_position.get('avg_entry', 0)
        executor_entry = executor_position.get('avg_entry', 0)
        
        if internal_entry > 0 and executor_entry > 0:
            entry_diff_pct = abs(internal_entry - executor_entry) / internal_entry
            
            if entry_diff_pct > self.MISMATCH_THRESHOLD_PCT:
                details['mismatches'].append({
                    'field': 'avg_entry',
                    'internal': internal_entry,
                    'executor': executor_entry,
                    'difference_pct': entry_diff_pct,
                })
                details['actions_required'].append('update_entry_price')
        
        if exchange_position:
            exchange_qty = exchange_position.get('quantity', 0)
            
            if abs(exchange_qty - executor_qty) > abs(exchange_qty * 0.001):
                details['mismatches'].append({
                    'field': 'exchange_vs_executor',
                    'exchange': exchange_qty,
                    'executor': executor_qty,
                })
                details['actions_required'].append('investigate_exchange_mismatch')
        
        is_reconciled = len(details['mismatches']) == 0
        
        details['is_reconciled'] = is_reconciled
        
        self._reconciliation_history.append(details)
        if len(self._reconciliation_history) > 100:
            self._reconciliation_history = self._reconciliation_history[-100:]
        
        await self._update_reconciliation_state(symbol, details)
        
        if not is_reconciled:
            await self._emit_mismatch_alert(symbol, details)
        
        return is_reconciled, details
    
    async def check_reconciliation_needed(
        self,
        symbol: str,
        current_time: Optional[int] = None
    ) -> bool:
        """
        Check if reconciliation is needed for a symbol.
        """
        if current_time is None:
            current_time = int(datetime.now().timestamp() * 1000)
        
        last_time = self._last_reconciliation.get(symbol, 0)
        
        if last_time == 0:
            return True
        
        elapsed = current_time - last_time
        
        return elapsed >= self.RECONCILIATION_INTERVAL_MS
    
    async def trigger_reconciliation(
        self,
        symbol: str,
        reason: str
    ) -> None:
        """Trigger a reconciliation check."""
        logger.info(
            f"Reconciliation triggered for {symbol}: {reason}",
            extra={'symbol': symbol, 'reason': reason}
        )
        
        self._pending_reconciliations[symbol] = {
            'reason': reason,
            'timestamp': int(datetime.now().timestamp() * 1000),
        }
        
        if self.bus:
            event = Event.create(
                event_type=EventType.POSITION_RECONCILED,
                symbol=symbol,
                payload={
                    'action': 'reconciliation_triggered',
                    'reason': reason,
                },
                source="position_reconciler",
            )
            await self.bus.publish(event)
    
    async def auto_reconcile(
        self,
        symbol: str,
        internal_state: dict,
        executor_state: dict
    ) -> tuple[bool, dict]:
        """
        Automatically reconcile positions if needed.
        """
        needs_reconciliation = await self.check_reconciliation_needed(symbol)
        
        if not needs_reconciliation:
            return True, {'status': 'skipped', 'reason': 'recently_reconciled'}
        
        is_reconciled, details = await self.reconcile_position(
            symbol,
            internal_state,
            executor_state
        )
        
        self._last_reconciliation[symbol] = int(datetime.now().timestamp() * 1000)
        
        return is_reconciled, details
    
    async def _update_reconciliation_state(
        self,
        symbol: str,
        details: dict
    ) -> None:
        if not self.state:
            return
            
        state_key = f"reconciliation:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'is_reconciled': details['is_reconciled'],
                'mismatch_count': len(details['mismatches']),
                'last_reconciliation': details['timestamp'],
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="position_reconciler",
        )
    
    async def _emit_mismatch_alert(
        self,
        symbol: str,
        details: dict
    ) -> None:
        """Emit position mismatch alert."""
        logger.error(
            f"Position mismatch for {symbol}: {details['mismatches']}",
            extra={
                'symbol': symbol,
                'mismatches': details['mismatches'],
                'actions': details['actions_required'],
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.POSITION_MISMATCH,
                symbol=symbol,
                payload={
                    'mismatches': details['mismatches'],
                    'actions_required': details['actions_required'],
                },
                source="position_reconciler",
            )
            await self.bus.publish(alert_event)
    
    async def get_reconciliation_report(self) -> dict:
        """Get reconciliation status report."""
        total_checks = len(self._reconciliation_history)
        failed_checks = sum(
            1 for r in self._reconciliation_history
            if not r.get('is_reconciled', True)
        )
        
        recent_mismatches = []
        for r in self._reconciliation_history[-20:]:
            if not r.get('is_reconciled', True):
                recent_mismatches.append({
                    'symbol': r['symbol'],
                    'timestamp': r['timestamp'],
                    'count': len(r['mismatches']),
                })
        
        return {
            'total_checks': total_checks,
            'failed_checks': failed_checks,
            'pass_rate': (
                (total_checks - failed_checks) / total_checks
                if total_checks > 0 else 1.0
            ),
            'pending_reconciliations': len(self._pending_reconciliations),
            'recent_mismatches': recent_mismatches,
        }
