"""
Kronos Integration - Wrapper for Kronos foundation model.

Kronos is a transformer-based foundation model for financial markets
available on HuggingFace (e.g., NeoQuasar/Kronos-mini).
"""

import torch
import numpy as np
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class KronosConfig:
    model_name: str = "NeoQuasar/Kronos-mini"
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    max_sequence_length: int = 512
    prediction_horizon: int = 64
    temperature: float = 1.0
    top_k: int = 50
    top_p: float = 0.95
    cache_dir: Optional[str] = None


class KronosModel:
    """
    Kronos foundation model wrapper for market regime inference.
    
    Kronos can be used for:
    - Market regime prediction
    - Volatility forecasting
    - Cross-asset correlation patterns
    - Anomaly detection
    """
    
    def __init__(
        self,
        config: Optional[KronosConfig] = None,
        use_cache: bool = True,
    ):
        self.config = config or KronosConfig()
        self.device = torch.device(self.config.device)
        self.use_cache = use_cache
        
        self.model = None
        self.tokenizer = None
        self.is_loaded = False
        
        self.prediction_cache: dict[str, any] = {}
        self.cache_ttl_seconds = 300
        
    def load(self) -> bool:
        """Load Kronos model from HuggingFace."""
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            
            logger.info(f"Loading Kronos model: {self.config.model_name}")
            
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.config.model_name,
                cache_dir=self.config.cache_dir,
            )
            
            self.model = AutoModelForSeq2SeqLM.from_pretrained(
                self.config.model_name,
                cache_dir=self.config.cache_dir,
            ).to(self.device)
            
            self.model.eval()
            self.is_loaded = True
            
            logger.info("Kronos model loaded successfully")
            return True
            
        except ImportError:
            logger.warning("transformers not installed, using fallback mode")
            return False
        except Exception as e:
            logger.error(f"Failed to load Kronos model: {e}")
            return False
            
    def _prepare_input(
        self,
        price_data: np.ndarray,
        volume_data: np.ndarray,
        metadata: dict,
    ) -> str:
        """Convert market data to text format for Kronos."""
        returns = np.diff(price_data) / price_data[:-1]
        volatility = np.std(returns) * np.sqrt(252) if len(returns) > 1 else 0.0
        
        avg_volume = np.mean(volume_data) if len(volume_data) > 0 else 0.0
        volume_trend = np.polyfit(range(len(volume_data)), volume_data, 1)[0] if len(volume_data) > 5 else 0.0
        
        trend = "bullish" if returns[-1] > 0 else "bearish"
        vol_level = "high" if volatility > 0.3 else "medium" if volatility > 0.15 else "low"
        
        symbol = metadata.get('symbol', 'UNKNOWN')
        exchange = metadata.get('exchange', 'UNKNOWN')
        
        prompt = (
            f"{symbol} on {exchange}: {vol_level} volatility, {trend} trend, "
            f"vol={volatility:.2%}, volume_trend={volume_trend:.2f}, "
            f"recent_returns={','.join([f'{r:.2%}' for r in returns[-5:]])}"
        )
        
        return prompt
        
    def predict_regime(
        self,
        price_data: np.ndarray,
        volume_data: np.ndarray,
        metadata: dict,
    ) -> dict:
        """
        Generate regime predictions using Kronos.
        
        Returns dict with:
            - regime: predicted market regime
            - volatility_forecast: expected volatility
            - regime_confidence: prediction confidence
            - pattern_type: detected pattern
        """
        cache_key = self._make_cache_key(metadata)
        
        if self.use_cache and cache_key in self.prediction_cache:
            cached = self.prediction_cache[cache_key]
            if (datetime.now() - cached['timestamp']).seconds < self.cache_ttl_seconds:
                return cached['prediction']
                
        if not self.is_loaded:
            return self._fallback_prediction(price_data, volume_data, metadata)
            
        try:
            prompt = self._prepare_input(price_data, volume_data, metadata)
            
            inputs = self.tokenizer(
                prompt,
                return_tensors="pt",
                max_length=self.config.max_sequence_length,
                truncation=True,
            ).to(self.device)
            
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=self.config.prediction_horizon,
                    temperature=self.config.temperature,
                    top_k=self.config.top_k,
                    top_p=self.config.top_p,
                    do_sample=True,
                )
                
            response = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            
            prediction = self._parse_kronos_response(response, price_data, volume_data)
            
        except Exception as e:
            logger.warning(f"Kronos inference failed: {e}, using fallback")
            prediction = self._fallback_prediction(price_data, volume_data, metadata)
            
        if self.use_cache:
            self.prediction_cache[cache_key] = {
                'timestamp': datetime.now(),
                'prediction': prediction,
            }
            
        return prediction
        
    def _fallback_prediction(
        self,
        price_data: np.ndarray,
        volume_data: np.ndarray,
        metadata: dict,
    ) -> dict:
        """Fallback prediction using statistical methods."""
        if len(price_data) < 2:
            return {
                'regime': 'unknown',
                'volatility_forecast': 0.2,
                'regime_confidence': 0.3,
                'pattern_type': 'insufficient_data',
            }
            
        returns = np.diff(price_data) / price_data[:-1]
        volatility = np.std(returns) * np.sqrt(252)
        
        if volatility > 0.35:
            regime = 'high_volatility'
        elif volatility > 0.20:
            regime = 'elevated'
        elif volatility > 0.10:
            regime = 'normal'
        else:
            regime = 'low_volatility'
            
        momentum = np.sum(returns[-5:]) if len(returns) >= 5 else 0.0
        
        if momentum > 0.05:
            pattern = 'momentum_up'
        elif momentum < -0.05:
            pattern = 'momentum_down'
        else:
            pattern = 'mean_reversion'
            
        return {
            'regime': regime,
            'volatility_forecast': volatility,
            'regime_confidence': 0.7,
            'pattern_type': pattern,
        }
        
    def _parse_kronos_response(
        self,
        response: str,
        price_data: np.ndarray,
        volume_data: np.ndarray,
    ) -> dict:
        """Parse Kronos text response into structured prediction."""
        response_lower = response.lower()
        
        if 'high volatility' in response_lower or 'volatile' in response_lower:
            regime = 'high_volatility'
        elif 'low volatility' in response_lower or 'stable' in response_lower:
            regime = 'low_volatility'
        elif 'bullish' in response_lower or 'up' in response_lower:
            regime = 'trending_up'
        elif 'bearish' in response_lower or 'down' in response_lower:
            regime = 'trending_down'
        else:
            returns = np.diff(price_data) / price_data[:-1]
            volatility = np.std(returns) * np.sqrt(252) if len(returns) > 1 else 0.2
            regime = 'high_volatility' if volatility > 0.25 else 'normal'
            
        return {
            'regime': regime,
            'volatility_forecast': self._extract_volatility(response),
            'regime_confidence': 0.8,
            'pattern_type': self._extract_pattern(response),
        }
        
    def _extract_volatility(self, response: str) -> float:
        """Extract volatility estimate from response."""
        import re
        numbers = re.findall(r'\d+\.?\d*%', response)
        for num_str in numbers:
            try:
                val = float(num_str.rstrip('%')) / 100
                if 0.01 < val < 1.0:
                    return val
            except ValueError:
                continue
        return 0.2
        
    def _extract_pattern(self, response: str) -> str:
        """Extract pattern type from response."""
        patterns = {
            'momentum': ['momentum', 'trend', 'directional'],
            'reversal': ['reversal', 'mean revert', 'contrarian'],
            'breakout': ['breakout', 'break', 'pivot'],
            'range': ['range', 'consolidation', 'channel'],
        }
        
        response_lower = response.lower()
        for pattern, keywords in patterns.items():
            if any(kw in response_lower for kw in keywords):
                return pattern
        return 'neutral'
        
    def _make_cache_key(self, metadata: dict) -> str:
        """Create cache key from metadata."""
        return f"{metadata.get('symbol', '')}_{metadata.get('exchange', '')}_{metadata.get('timeframe', '1m')}"
        
    def get_calibration_params(self, regime: str) -> dict:
        """Get risk calibration parameters for regime."""
        calibration_map = {
            'high_volatility': {
                'position_scale': 0.5,
                'stop_loss_multiplier': 1.5,
                'max_leverage': 2.0,
                'volatility_target': 0.15,
            },
            'low_volatility': {
                'position_scale': 1.2,
                'stop_loss_multiplier': 2.0,
                'max_leverage': 5.0,
                'volatility_target': 0.10,
            },
            'trending_up': {
                'position_scale': 1.0,
                'stop_loss_multiplier': 2.0,
                'max_leverage': 3.0,
                'volatility_target': 0.12,
            },
            'trending_down': {
                'position_scale': 0.8,
                'stop_loss_multiplier': 1.5,
                'max_leverage': 2.0,
                'volatility_target': 0.15,
            },
            'normal': {
                'position_scale': 1.0,
                'stop_loss_multiplier': 2.0,
                'max_leverage': 3.0,
                'volatility_target': 0.12,
            },
            'unknown': {
                'position_scale': 0.5,
                'stop_loss_multiplier': 2.0,
                'max_leverage': 1.0,
                'volatility_target': 0.10,
            },
        }
        
        return calibration_map.get(regime, calibration_map['normal'])
