"""
Regime Classifier - Unified market regime classification.

Combines:
- Kronos model predictions
- Microstructure analysis
- Statistical features
- Volatility regime detection
"""

import numpy as np
from enum import Enum
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import logging

from .kronos_integration import KronosModel, KronosConfig
from .microstructure import MicrostructureDetector, MicrostructureState

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime types."""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGE_BOUND = "range_bound"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    CRISIS = "crisis"
    UNKNOWN = "unknown"


class VolatilityRegime(Enum):
    """Volatility regime."""
    LOW = "low"
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    EXTREME = "extreme"


class LiquidityRegime(Enum):
    """Liquidity regime."""
    ABUNDANT = "abundant"
    NORMAL = "normal"
    SCARCE = "scarce"
    STRESSED = "stressed"


@dataclass
class RegimeState:
    market_regime: MarketRegime
    volatility_regime: VolatilityRegime
    liquidity_regime: LiquidityRegime
    
    combined_confidence: float
    
    volatility_forecast: float
    volatility_current: float
    
    trend_strength: float
    momentum_score: float
    
    risk_score: float
    
    use_caution: bool
    max_position_scale: float
    
    timestamp: datetime
    source: str


class RegimeClassifier:
    """
    Unified regime classifier.
    
    Combines multiple signals:
    - Kronos model predictions
    - Microstructure state
    - Statistical features
    - Historical patterns
    """
    
    def __init__(
        self,
        symbol: str,
        use_kronos: bool = True,
        lookback_bars: int = 500,
        vol_window: int = 100,
    ):
        self.symbol = symbol
        self.use_kronos = use_kronos
        self.lookback_bars = lookback_bars
        self.vol_window = vol_window
        
        self.microstructure = MicrostructureDetector(symbol)
        
        self.kronos: Optional[KronosModel] = None
        if use_kronos:
            try:
                self.kronos = KronosModel()
                if not self.kronos.load():
                    logger.warning("Kronos failed to load, continuing without it")
                    self.kronos = None
            except Exception as e:
                logger.warning(f"Kronos initialization failed: {e}")
                self.kronos = None
                
        self.price_history: list[float] = []
        self.volume_history: list[float] = []
        
        self.regime_history: list[RegimeState] = []
        
        self._initialize_baselines()
        
    def _initialize_baselines(self) -> None:
        """Initialize baseline statistics."""
        self.baseline_volatility: Optional[float] = None
        self.baseline_depth: Optional[float] = None
        
    def update(
        self,
        bid_price: float,
        ask_price: float,
        bid_volume: float,
        ask_volume: float,
        trade_price: Optional[float] = None,
        trade_volume: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> RegimeState:
        """
        Update regime state with new market data.
        
        Returns current regime classification.
        """
        timestamp = timestamp or datetime.now()
        
        mid_price = (bid_price + ask_price) / 2
        self.price_history.append(mid_price)
        if len(self.price_history) > self.lookback_bars:
            self.price_history.pop(0)
            
        if trade_volume is not None:
            self.volume_history.append(trade_volume)
            if len(self.volume_history) > self.lookback_bars:
                self.volume_history.pop(0)
                
        microstructure_state = self.microstructure.update(
            bid_price, ask_price,
            bid_volume, ask_volume,
            trade_price, trade_volume,
            timestamp,
        )
        
        regime_state = self._classify_regime(
            microstructure_state,
            timestamp,
        )
        
        self.regime_history.append(regime_state)
        if len(self.regime_history) > 100:
            self.regime_history.pop(0)
            
        return regime_state
        
    def _classify_regime(
        self,
        ms_state: MicrostructureState,
        timestamp: datetime,
    ) -> RegimeState:
        """Classify current regime combining all signals."""
        
        volatility_current = self._compute_current_volatility()
        volatility_forecast = self._compute_volatility_forecast()
        
        vol_regime = self._classify_volatility_regime(volatility_current)
        liq_regime = self._classify_liquidity_regime(ms_state)
        
        market_regime, trend_strength, momentum = self._classify_market_regime(
            volatility_current,
            ms_state,
        )
        
        kronos_prediction = None
        source = "microstructure"
        
        if self.kronos is not None and len(self.price_history) >= 20:
            try:
                price_arr = np.array(self.price_history)
                volume_arr = np.array(self.volume_history) if self.volume_history else np.zeros_like(price_arr)
                
                kronos_prediction = self.kronos.predict_regime(
                    price_arr,
                    volume_arr,
                    {'symbol': self.symbol, 'exchange': 'binance'},
                )
                
                source = "kronos_microstructure"
                
                if kronos_prediction:
                    market_regime = self._merge_kronos_regime(
                        market_regime,
                        kronos_prediction.get('regime', 'normal'),
                        kronos_prediction.get('regime_confidence', 0.5),
                    )
                    
            except Exception as e:
                logger.warning(f"Kronos prediction failed: {e}")
                
        risk_score = self._compute_risk_score(
            market_regime,
            vol_regime,
            liq_regime,
            ms_state,
        )
        
        use_caution = risk_score > 0.6 or vol_regime in [VolatilityRegime.HIGH, VolatilityRegime.EXTREME]
        
        max_position_scale = self._compute_position_scale(
            risk_score,
            vol_regime,
            ms_state,
        )
        
        confidence = self._compute_confidence(
            market_regime,
            ms_state,
            kronos_prediction,
        )
        
        return RegimeState(
            market_regime=market_regime,
            volatility_regime=vol_regime,
            liquidity_regime=liq_regime,
            combined_confidence=confidence,
            volatility_forecast=volatility_forecast,
            volatility_current=volatility_current,
            trend_strength=trend_strength,
            momentum_score=momentum,
            risk_score=risk_score,
            use_caution=use_caution,
            max_position_scale=max_position_scale,
            timestamp=timestamp,
            source=source,
        )
        
    def _compute_current_volatility(self) -> float:
        """Compute current realized volatility."""
        if len(self.price_history) < 10:
            return 0.2
            
        prices = np.array(self.price_history[-self.vol_window:])
        returns = np.diff(prices) / prices[:-1]
        
        if len(returns) < 2:
            return 0.2
            
        return np.std(returns) * np.sqrt(252 * 1440)
        
    def _compute_volatility_forecast(self) -> float:
        """Forecast future volatility using simple exponential smoothing."""
        current_vol = self._compute_current_volatility()
        
        if len(self.regime_history) < 10:
            return current_vol
            
        past_vols = [r.volatility_current for r in self.regime_history[-10:]]
        
        ewma_vol = past_vols[0]
        alpha = 0.3
        
        for vol in past_vols[1:]:
            ewma_vol = alpha * vol + (1 - alpha) * ewma_vol
            
        return ewma_vol
        
    def _classify_volatility_regime(self, volatility: float) -> VolatilityRegime:
        """Classify volatility regime."""
        if volatility < 0.05:
            return VolatilityRegime.LOW
        elif volatility < 0.10:
            return VolatilityRegime.NORMAL
        elif volatility < 0.20:
            return VolatilityRegime.ELEVATED
        elif volatility < 0.40:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.EXTREME
            
    def _classify_liquidity_regime(self, ms_state: MicrostructureState) -> LiquidityRegime:
        """Classify liquidity regime."""
        if ms_state.liquidity_regime == 'abundant':
            return LiquidityRegime.ABUNDANT
        elif ms_state.liquidity_regime == 'scarce':
            return LiquidityRegime.SCARCE
        elif ms_state.pressure_index > 0.7:
            return LiquidityRegime.STRESSED
        else:
            return LiquidityRegime.NORMAL
            
    def _classify_market_regime(
        self,
        volatility: float,
        ms_state: MicrostructureState,
    ) -> tuple[MarketRegime, float, float]:
        """Classify market regime from technical signals."""
        if len(self.price_history) < 20:
            return MarketRegime.UNKNOWN, 0.0, 0.0
            
        prices = np.array(self.price_history[-20:])
        returns = np.diff(prices) / prices[:-1]
        
        momentum = np.sum(returns)
        
        trend_strength = abs(np.polyfit(range(len(returns)), returns, 1)[0]) * 100
        
        if len(self.price_history) >= 50:
            prices_long = np.array(self.price_history[-50:])
            returns_long = np.diff(prices_long) / prices_long[:-1]
            
            rolling_std = np.array([np.std(returns_long[max(0,i-10):i+1]) 
                                   for i in range(len(returns_long))])
            
            vol_trend = np.polyfit(range(len(rolling_std)), rolling_std, 1)[0]
        else:
            vol_trend = 0
            
        if volatility > 0.40:
            return MarketRegime.CRISIS, 0.0, momentum
        elif volatility > 0.25:
            return MarketRegime.HIGH_VOLATILITY, trend_strength, momentum
        elif abs(momentum) > 0.02 and trend_strength > 0.01:
            if momentum > 0:
                return MarketRegime.TRENDING_UP, trend_strength, momentum
            else:
                return MarketRegime.TRENDING_DOWN, trend_strength, momentum
        else:
            return MarketRegime.RANGE_BOUND, trend_strength, momentum
            
    def _merge_kronos_regime(
        self,
        tech_regime: MarketRegime,
        kronos_regime: str,
        confidence: float,
    ) -> MarketRegime:
        """Merge Kronos prediction with technical analysis."""
        if confidence < 0.6:
            return tech_regime
            
        kronos_map = {
            'high_volatility': MarketRegime.HIGH_VOLATILITY,
            'low_volatility': MarketRegime.LOW_VOLATILITY,
            'trending_up': MarketRegime.TRENDING_UP,
            'trending_down': MarketRegime.TRENDING_DOWN,
            'normal': MarketRegime.RANGE_BOUND,
        }
        
        kronos_matched = kronos_map.get(kronos_regime, tech_regime)
        
        return kronos_matched if confidence > 0.7 else tech_regime
        
    def _compute_risk_score(
        self,
        market_regime: MarketRegime,
        vol_regime: VolatilityRegime,
        liq_regime: LiquidityRegime,
        ms_state: MicrostructureState,
    ) -> float:
        """Compute composite risk score (0-1)."""
        risk = 0.0
        
        if market_regime == MarketRegime.CRISIS:
            risk += 0.4
        elif market_regime == MarketRegime.HIGH_VOLATILITY:
            risk += 0.25
            
        vol_scores = {
            VolatilityRegime.LOW: 0.0,
            VolatilityRegime.NORMAL: 0.1,
            VolatilityRegime.ELEVATED: 0.2,
            VolatilityRegime.HIGH: 0.35,
            VolatilityRegime.EXTREME: 0.5,
        }
        risk += vol_scores.get(vol_regime, 0.2)
        
        if liq_regime == LiquidityRegime.STRESSED:
            risk += 0.2
        elif liq_regime == LiquidityRegime.SCARCE:
            risk += 0.1
            
        risk += abs(ms_state.pressure_index) * 0.15
        
        return min(risk, 1.0)
        
    def _compute_position_scale(
        self,
        risk_score: float,
        vol_regime: VolatilityRegime,
        ms_state: MicrostructureState,
    ) -> float:
        """Compute maximum position scale (0-1.5)."""
        base_scale = 1.0 - risk_score * 0.5
        
        if vol_regime == VolatilityRegime.HIGH:
            base_scale *= 0.5
        elif vol_regime == VolatilityRegime.EXTREME:
            base_scale *= 0.25
            
        if ms_state.resilience < 0.3:
            base_scale *= 0.5
            
        return max(base_scale, 0.1)
        
    def _compute_confidence(
        self,
        market_regime: MarketRegime,
        ms_state: MicrostructureState,
        kronos_pred: Optional[dict],
    ) -> float:
        """Compute confidence in regime classification."""
        base_confidence = 0.6
        
        base_confidence += len(self.price_history) / 1000 * 0.2
        
        base_confidence += ms_state.resilience * 0.1
        
        if kronos_pred is not None:
            base_confidence += kronos_pred.get('regime_confidence', 0.5) * 0.2
            
        if market_regime == MarketRegime.UNKNOWN:
            base_confidence *= 0.5
            
        return min(base_confidence, 0.95)
        
    def get_calibration_params(self) -> dict:
        """Get risk calibration parameters for current regime."""
        if not self.regime_history:
            return {
                'position_scale': 1.0,
                'stop_loss_multiplier': 2.0,
                'max_leverage': 3.0,
                'volatility_target': 0.12,
            }
            
        current = self.regime_history[-1]
        
        params = {
            'position_scale': current.max_position_scale,
            'stop_loss_multiplier': 2.0 if not current.use_caution else 1.5,
            'max_leverage': 3.0 if not current.use_caution else 1.5,
            'volatility_target': 0.12 - current.risk_score * 0.04,
        }
        
        if self.kronos is not None:
            kronos_params = self.kronos.get_calibration_params(
                current.market_regime.value
            )
            params.update(kronos_params)
            
        return params
