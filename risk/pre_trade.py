"""
Pre-trade Risk Checks - Cost validation and stress testing.

Adds:
- Pre-trade cost vs edge validation
- Scenario-based stress testing
- Multi-factor risk scoring
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class StressScenario(Enum):
    """Stress testing scenarios."""
    VOLATILITY_SPIKE = "volatility_spike"
    LIQUIDITY_CRISIS = "liquidity_crisis"
    CORRELATION_BREAKDOWN = "correlation_breakdown"
    MARKET_GAP = "market_gap"
    SLIPPAGE_AMPLIFICATION = "slippage_amplification"


@dataclass
class CostValidationResult:
    is_valid: bool
    expected_cost_bps: float
    expected_edge_bps: float
    net_edge_bps: float
    cost_breakdown: dict
    rejection_reason: Optional[str] = None


@dataclass
class StressTestResult:
    scenario: StressScenario
    max_loss_pct: float
    probability_weighted_loss: float
    passes: bool
    recommendations: list[str]


@dataclass
class PreTradeRiskResult:
    approved: bool
    cost_validation: CostValidationResult
    stress_results: list[StressTestResult]
    composite_risk_score: float
    rejection_reason: Optional[str] = None
    warnings: list[str] = None


class PreTradeRiskChecker:
    """
    Pre-trade risk validation with cost and stress testing.
    
    Validates:
    - Pre-trade cost vs expected edge
    - Stress test scenarios
    - Composite risk scoring
    """
    
    def __init__(
        self,
        min_net_edge_bps: float = 2.0,
        max_stress_loss_pct: float = 5.0,
        cost_confidence_threshold: float = 0.5,
    ):
        self.min_net_edge_bps = min_net_edge_bps
        self.max_stress_loss_pct = max_stress_loss_pct
        self.cost_confidence_threshold = cost_confidence_threshold
        
        self.scenario_weights = {
            StressScenario.VOLATILITY_SPIKE: 0.3,
            StressScenario.LIQUIDITY_CRISIS: 0.25,
            StressScenario.MARKET_GAP: 0.25,
            StressScenario.SLIPPAGE_AMPLIFICATION: 0.2,
        }
        
    def validate_cost_edge(
        self,
        expected_edge_bps: float,
        expected_cost_bps: float,
        confidence: float = 1.0,
        volatility: float = 0.2,
        spread_bps: float = 5.0,
    ) -> CostValidationResult:
        """
        Validate that expected edge exceeds expected costs.
        
        Returns CostValidationResult with breakdown.
        """
        net_edge = expected_edge_bps - expected_cost_bps
        
        cost_breakdown = {
            'spread_cost': spread_bps / 2,
            'market_impact': expected_cost_bps * 0.3,
            'timing_risk': expected_cost_bps * 0.2,
            'other_costs': expected_cost_bps * 0.1,
        }
        
        adjusted_min_edge = self.min_net_edge_bps / confidence if confidence > 0 else self.min_net_edge_bps * 2
        
        if net_edge < adjusted_min_edge:
            return CostValidationResult(
                is_valid=False,
                expected_cost_bps=expected_cost_bps,
                expected_edge_bps=expected_edge_bps,
                net_edge_bps=net_edge,
                cost_breakdown=cost_breakdown,
                rejection_reason=f"Net edge {net_edge:.2f}bps below threshold {adjusted_min_edge:.2f}bps"
            )
            
        if expected_cost_bps > expected_edge_bps * 0.8:
            return CostValidationResult(
                is_valid=True,
                expected_cost_bps=expected_cost_bps,
                expected_edge_bps=expected_edge_bps,
                net_edge_bps=net_edge,
                cost_breakdown=cost_breakdown,
                rejection_reason="Warning: costs > 80% of edge"
            )
            
        return CostValidationResult(
            is_valid=True,
            expected_cost_bps=expected_cost_bps,
            expected_edge_bps=expected_edge_bps,
            net_edge_bps=net_edge,
            cost_breakdown=cost_breakdown,
        )
        
    def run_stress_tests(
        self,
        position_size_pct: float,
        entry_price: float,
        expected_edge_bps: float,
        volatility: float,
        liquidity_score: float,
    ) -> list[StressTestResult]:
        """
        Run scenario-based stress tests.
        
        Returns list of StressTestResult for each scenario.
        """
        results = []
        
        results.append(self._stress_volatility_spike(
            position_size_pct, entry_price, expected_edge_bps, volatility
        ))
        
        results.append(self._stress_liquidity_crisis(
            position_size_pct, entry_price, expected_edge_bps, liquidity_score
        ))
        
        results.append(self._stress_market_gap(
            position_size_pct, entry_price, expected_edge_bps, volatility
        ))
        
        results.append(self._stress_slippage_amplification(
            position_size_pct, entry_price, expected_edge_bps, volatility
        ))
        
        return results
        
    def _stress_volatility_spike(
        self,
        position_pct: float,
        entry_price: float,
        edge_bps: float,
        current_vol: float,
    ) -> StressTestResult:
        """Stress test volatility spike scenario."""
        vol_multiplier = 3.0
        stressed_vol = current_vol * vol_multiplier
        
        max_adverse_move = stressed_vol * np.sqrt(1/252) * 2
        
        max_loss = position_pct * max_adverse_move * 100
        
        weighted_loss = max_loss * self.scenario_weights[StressScenario.VOLATILITY_SPIKE]
        
        recommendations = []
        if max_loss > self.max_stress_loss_pct:
            recommendations.append(f"Reduce position: projected loss {max_loss:.1f}% exceeds limit")
            recommendations.append("Consider defensive sizing during high-volatility periods")
            
        return StressTestResult(
            scenario=StressScenario.VOLATILITY_SPIKE,
            max_loss_pct=max_loss,
            probability_weighted_loss=weighted_loss,
            passes=max_loss <= self.max_stress_loss_pct,
            recommendations=recommendations,
        )
        
    def _stress_liquidity_crisis(
        self,
        position_pct: float,
        entry_price: float,
        edge_bps: float,
        liquidity_score: float,
    ) -> StressTestResult:
        """Stress test liquidity crisis scenario."""
        if liquidity_score > 0.7:
            stressed_slippage = 5.0
        elif liquidity_score > 0.4:
            stressed_slippage = 15.0
        else:
            stressed_slippage = 30.0
            
        stressed_slippage *= 2
        
        max_loss = position_pct * stressed_slippage / 100 * 100
        
        weighted_loss = max_loss * self.scenario_weights[StressScenario.LIQUIDITY_CRISIS]
        
        recommendations = []
        if max_loss > self.max_stress_loss_pct:
            recommendations.append(f"Reduce position: stressed slippage would cost {max_loss:.1f}%")
            recommendations.append("Wait for liquidity to improve")
            
        return StressTestResult(
            scenario=StressScenario.LIQUIDITY_CRISIS,
            max_loss_pct=max_loss,
            probability_weighted_loss=weighted_loss,
            passes=max_loss <= self.max_stress_loss_pct,
            recommendations=recommendations,
        )
        
    def _stress_market_gap(
        self,
        position_pct: float,
        entry_price: float,
        edge_bps: float,
        current_vol: float,
    ) -> StressTestResult:
        """Stress test market gap scenario."""
        gap_size = current_vol * 3
        
        max_loss = position_pct * gap_size * 100
        
        weighted_loss = max_loss * self.scenario_weights[StressScenario.MARKET_GAP]
        
        recommendations = []
        if max_loss > self.max_stress_loss_pct:
            recommendations.append(f"Position vulnerable to gaps: {max_loss:.1f}% potential loss")
            recommendations.append("Consider reducing size or adding stops")
            
        return StressTestResult(
            scenario=StressScenario.MARKET_GAP,
            max_loss_pct=max_loss,
            probability_weighted_loss=weighted_loss,
            passes=max_loss <= self.max_stress_loss_pct,
            recommendations=recommendations,
        )
        
    def _stress_slippage_amplification(
        self,
        position_pct: float,
        entry_price: float,
        edge_bps: float,
        current_vol: float,
    ) -> StressTestResult:
        """Stress test slippage amplification scenario."""
        base_slippage = 2.0
        amplified_slippage = base_slippage * (1 + position_pct * 5) * (1 + current_vol * 3)
        
        max_loss = position_pct * amplified_slippage / 100 * 100
        
        weighted_loss = max_loss * self.scenario_weights[StressScenario.SLIPPAGE_AMPLIFICATION]
        
        recommendations = []
        if max_loss > self.max_stress_loss_pct:
            recommendations.append(f"Large orders amplify slippage: {max_loss:.1f}% loss")
            recommendations.append("Consider TWAP/VWAP execution for large orders")
            
        return StressTestResult(
            scenario=StressScenario.SLIPPAGE_AMPLIFICATION,
            max_loss_pct=max_loss,
            probability_weighted_loss=weighted_loss,
            passes=max_loss <= self.max_stress_loss_pct,
            recommendations=recommendations,
        )
        
    def compute_composite_risk(
        self,
        cost_validation: CostValidationResult,
        stress_results: list[StressTestResult],
        regime_risk: float = 0.5,
        liquidity_risk: float = 0.5,
    ) -> float:
        """
        Compute composite risk score (0-1, higher = riskier).
        """
        cost_risk = 0.0
        if not cost_validation.is_valid:
            cost_risk = 0.5
        elif cost_validation.net_edge_bps < 5:
            cost_risk = 0.3
            
        stress_failures = sum(1 for r in stress_results if not r.passes)
        stress_risk = min(stress_failures * 0.25, 0.5)
        
        weighted_stress_loss = sum(r.probability_weighted_loss for r in stress_results)
        stress_loss_risk = min(weighted_stress_loss / 10, 0.3)
        
        composite = (
            cost_risk * 0.3 +
            stress_risk * 0.2 +
            stress_loss_risk * 0.2 +
            regime_risk * 0.15 +
            liquidity_risk * 0.15
        )
        
        return min(composite, 1.0)
        
    def pre_trade_check(
        self,
        position_size_pct: float,
        entry_price: float,
        expected_edge_bps: float,
        expected_cost_bps: float,
        cost_confidence: float,
        volatility: float,
        spread_bps: float,
        liquidity_score: float,
        regime_risk: float = 0.5,
    ) -> PreTradeRiskResult:
        """
        Perform full pre-trade risk check.
        
        Returns PreTradeRiskResult with all validations.
        """
        warnings = []
        
        cost_validation = self.validate_cost_edge(
            expected_edge_bps,
            expected_cost_bps,
            cost_confidence,
            volatility,
            spread_bps,
        )
        
        if not cost_validation.is_valid:
            return PreTradeRiskResult(
                approved=False,
                cost_validation=cost_validation,
                stress_results=[],
                composite_risk_score=1.0,
                rejection_reason=cost_validation.rejection_reason,
                warnings=warnings,
            )
            
        stress_results = self.run_stress_tests(
            position_size_pct,
            entry_price,
            expected_edge_bps,
            volatility,
            liquidity_score,
        )
        
        failing_stress = [r for r in stress_results if not r.passes]
        if failing_stress:
            warnings.extend([f"Stress test failed: {r.scenario.value}" for r in failing_stress])
            
        composite_risk = self.compute_composite_risk(
            cost_validation,
            stress_results,
            regime_risk,
            1.0 - liquidity_score,
        )
        
        approved = (
            cost_validation.is_valid and
            composite_risk < 0.7 and
            len(failing_stress) <= 1
        )
        
        rejection_reason = None
        if not approved:
            if composite_risk >= 0.7:
                rejection_reason = f"Composite risk score {composite_risk:.2f} too high"
            elif len(failing_stress) > 1:
                rejection_reason = f"{len(failing_stress)} stress tests failed"
                
        return PreTradeRiskResult(
            approved=approved,
            cost_validation=cost_validation,
            stress_results=stress_results,
            composite_risk_score=composite_risk,
            rejection_reason=rejection_reason,
            warnings=warnings,
        )
