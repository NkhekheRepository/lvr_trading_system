"""
Data Consensus - Consensus mechanism for multi-source data validation.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class DataSource(Enum):
    BINANCE = "binance"
    BINANCE_US = "binance_us"
    BYBIT = "bybit"
    OKX = "okx"
    PRIMARY_FEED = "primary_feed"
    FALLBACK_FEED = "fallback_feed"


@dataclass
class SourceWeight:
    source: DataSource
    weight: float
    latency_score: float = 1.0
    quality_score: float = 1.0


@dataclass
class ConsensusResult:
    price: float
    spread: float
    confidence: float
    sources_used: list[DataSource]
    is_valid: bool
    outliers: list[tuple[DataSource, float]]
    timestamp: datetime


@dataclass
class SourceReading:
    source: DataSource
    price: float
    bid: float
    ask: float
    volume: float
    timestamp: datetime
    latency_ms: float


class DataValidator:
    """
    Validates data from a single source.
    """
    
    def __init__(
        self,
        max_age_seconds: float = 5.0,
        max_spread_bps: float = 100.0,
        min_volume: float = 0.0,
    ):
        self.max_age_seconds = max_age_seconds
        self.max_spread_bps = max_spread_bps
        self.min_volume = min_volume
        
    def validate(self, reading: SourceReading) -> tuple[bool, str]:
        """
        Validate a single data reading.
        
        Returns (is_valid, reason).
        """
        age = (datetime.now() - reading.timestamp).total_seconds()
        
        if age > self.max_age_seconds:
            return False, f"Data too old: {age:.2f}s"
            
        if reading.bid <= 0 or reading.ask <= 0:
            return False, "Invalid prices"
            
        spread_bps = (reading.ask - reading.bid) / reading.bid * 10000
        
        if spread_bps > self.max_spread_bps:
            return False, f"Spread too wide: {spread_bps:.2f}bps"
            
        if reading.volume < self.min_volume:
            return False, f"Volume too low: {reading.volume}"
            
        if reading.latency_ms > 1000:
            return False, f"Latency too high: {reading.latency_ms:.0f}ms"
            
        return True, "OK"
        

class DataConsensus:
    """
    Consensus mechanism for combining data from multiple sources.
    
    Features:
    - Weighted averaging based on source quality and latency
    - Outlier detection and rejection
    - Confidence scoring
    - Source health tracking
    """
    
    def __init__(
        self,
        outlier_threshold_bps: float = 10.0,
        min_sources: int = 1,
        decay_factor: float = 0.95,
        confidence_threshold: float = 0.7,
    ):
        self.outlier_threshold_bps = outlier_threshold_bps
        self.min_sources = min_sources
        self.decay_factor = decay_factor
        self.confidence_threshold = confidence_threshold
        
        self.source_weights: dict[DataSource, SourceWeight] = {}
        self.source_health: dict[DataSource, list[bool]] = defaultdict(list)
        self.source_latency: dict[DataSource, list[float]] = defaultdict(list)
        
        self.validator = DataValidator()
        
    def set_source_weights(self, weights: list[SourceWeight]) -> None:
        """Set weights for data sources."""
        for sw in weights:
            self.source_weights[sw.source] = sw
            
    def update_source_stats(
        self,
        source: DataSource,
        is_valid: bool,
        latency_ms: float,
    ) -> None:
        """Update source statistics for adaptive weighting."""
        self.source_health[source].append(is_valid)
        if len(self.source_health[source]) > 100:
            self.source_health[source].pop(0)
            
        self.source_latency[source].append(latency_ms)
        if len(self.source_latency[source]) > 100:
            self.source_latency[source].pop(0)
            
        self._update_source_weight(source)
        
    def _update_source_weight(self, source: DataSource) -> None:
        """Update weight for a source based on recent performance."""
        if source not in self.source_health:
            return
            
        health_history = self.source_health[source][-20:]
        health_score = sum(health_history) / len(health_history) if health_history else 0.5
        
        latency_history = self.source_latency[source][-20:]
        avg_latency = np.mean(latency_history) if latency_history else 100
        
        latency_score = max(0.1, 1.0 - (avg_latency / 1000))
        
        base_weight = 1.0
        
        if source in self.source_weights:
            current = self.source_weights[source]
            new_weight = base_weight * (0.5 * health_score + 0.5 * latency_score)
            current.weight = self.decay_factor * current.weight + (1 - self.decay_factor) * new_weight
            current.health_score = health_score
            current.latency_score = latency_score
        else:
            self.source_weights[source] = SourceWeight(
                source=source,
                weight=base_weight * (0.5 * health_score + 0.5 * latency_score),
                health_score=health_score,
                latency_score=latency_score,
            )
            
    def compute_consensus(
        self,
        readings: list[SourceReading],
        timestamp: Optional[datetime] = None,
    ) -> ConsensusResult:
        """
        Compute consensus from multiple data readings.
        
        Returns consensus price and metadata.
        """
        timestamp = timestamp or datetime.now()
        
        valid_readings: list[SourceReading] = []
        invalid_readings: list[tuple[DataSource, str]] = []
        
        for reading in readings:
            is_valid, reason = self.validator.validate(reading)
            
            self.update_source_stats(
                reading.source,
                is_valid,
                reading.latency_ms,
            )
            
            if is_valid:
                valid_readings.append(reading)
            else:
                invalid_readings.append((reading.source, reason))
                logger.debug(f"Invalid reading from {reading.source.value}: {reason}")
                
        if len(valid_readings) < self.min_sources:
            return ConsensusResult(
                price=0.0,
                spread=0.0,
                confidence=0.0,
                sources_used=[],
                is_valid=False,
                outliers=[],
                timestamp=timestamp,
            )
            
        prices = [r.price for r in valid_readings]
        
        median_price = np.median(prices)
        
        weights = []
        for reading in valid_readings:
            sw = self.source_weights.get(reading.source)
            if sw:
                weights.append(sw.weight * sw.health_score * sw.latency_score)
            else:
                weights.append(1.0)
                
        total_weight = sum(weights)
        if total_weight > 0:
            weights = [w / total_weight for w in weights]
        else:
            weights = [1.0 / len(prices)] * len(prices)
            
        weighted_price = sum(r.price * w for r, w in zip(valid_readings, weights))
        
        outliers: list[tuple[DataSource, float]] = []
        filtered_prices: list[tuple[float, DataSource, float]] = []
        
        for reading, weight in zip(valid_readings, weights):
            price_diff_bps = abs(reading.price - median_price) / median_price * 10000
            
            if price_diff_bps > self.outlier_threshold_bps:
                outliers.append((reading.source, price_diff_bps))
            else:
                filtered_prices.append((reading.price, reading.source, weight))
                
        if len(filtered_prices) < self.min_sources:
            filtered_prices = [(r.price, r.source, 1.0) for r in valid_readings]
            outliers.clear()
            
        final_prices = [p[0] for p in filtered_prices]
        final_weights = [p[2] for p in filtered_prices]
        final_sources = [p[1] for p in filtered_prices]
        
        total_w = sum(final_weights)
        if total_w > 0:
            final_weights = [w / total_w for w in final_weights]
            
        consensus_price = sum(p * w for p, w in zip(final_prices, final_weights))
        
        spread = np.mean([r.ask - r.bid for r in valid_readings])
        
        confidence = self._compute_confidence(
            len(filtered_prices),
            outliers,
            final_weights,
        )
        
        return ConsensusResult(
            price=consensus_price,
            spread=spread,
            confidence=confidence,
            sources_used=final_sources,
            is_valid=confidence >= self.confidence_threshold,
            outliers=outliers,
            timestamp=timestamp,
        )
        
    def _compute_confidence(
        self,
        num_sources: int,
        outliers: list[tuple[DataSource, float]],
        weights: list[float],
    ) -> float:
        """Compute consensus confidence score (0-1)."""
        base_confidence = min(num_sources / 3, 1.0) * 0.4
        
        outlier_penalty = len(outliers) * 0.15
        base_confidence -= outlier_penalty
        
        weight_concentration = max(weights) if weights else 0
        weight_penalty = (1 - weight_concentration) * 0.2
        base_confidence -= weight_penalty
        
        return max(0.0, min(1.0, base_confidence + 0.3))
        
    def get_best_source(self, readings: list[SourceReading]) -> Optional[DataSource]:
        """Get the best single source based on weights."""
        if not readings:
            return None
            
        best_source = None
        best_score = -1
        
        for reading in readings:
            sw = self.source_weights.get(reading.source)
            if sw:
                score = sw.weight * sw.health_score * sw.latency_score
            else:
                score = 1.0
                
            if score > best_score:
                best_score = score
                best_source = reading.source
                
        return best_source
        
    def get_source_health_report(self) -> dict[DataSource, dict]:
        """Get health report for all sources."""
        report = {}
        
        for source in DataSource:
            health_history = self.source_health.get(source, [])
            latency_history = self.source_latency.get(source, [])
            sw = self.source_weights.get(source)
            
            report[source.value] = {
                'health_rate': sum(health_history) / len(health_history) if health_history else 0.0,
                'avg_latency_ms': np.mean(latency_history) if latency_history else 0.0,
                'weight': sw.weight if sw else 1.0,
                'total_readings': len(health_history),
            }
            
        return report
