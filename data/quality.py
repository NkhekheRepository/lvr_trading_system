"""
Data Quality Monitor - Validation and monitoring for data quality metrics.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta
from collections import deque
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class QualityFlag(Enum):
    GOOD = "good"
    STALE = "stale"
    GAPS = "gaps"
    ANOMALY = "anomaly"
    HIGH_LATENCY = "high_latency"
    UNSTABLE = "unstable"


@dataclass
class QualityMetrics:
    completeness: float
    timeliness: float
    accuracy: float
    consistency: float
    overall_score: float
    
    flags: list[QualityFlag]
    issues: list[str]
    timestamp: datetime


@dataclass
class DataPoint:
    timestamp: datetime
    value: float
    source: str
    latency_ms: float = 0.0


class DataQualityMonitor:
    """
    Monitors data quality across multiple dimensions.
    
    Tracks:
    - Completeness (missing data points)
    - Timeliness (data age, staleness)
    - Accuracy (price sanity checks)
    - Consistency (variance, jumps)
    """
    
    def __init__(
        self,
        symbol: str,
        expected_frequency_ms: float = 100,
        max_age_seconds: float = 5.0,
        max_jump_bps: float = 50.0,
        window_size: int = 100,
    ):
        self.symbol = symbol
        
        self.expected_frequency_ms = expected_frequency_ms
        self.max_age_seconds = max_age_seconds
        self.max_jump_bps = max_jump_bps
        self.window_size = window_size
        
        self.price_history: deque[DataPoint] = deque(maxlen=window_size)
        self.volume_history: deque[DataPoint] = deque(maxlen=window_size)
        
        self.gaps: list[tuple[datetime, datetime]] = []
        self.anomalies: list[tuple[datetime, str]] = []
        
        self.last_update: Optional[datetime] = None
        self.updates_per_minute: float = 0.0
        self._update_count = 0
        self._last_rate_calc = datetime.now()
        
    def record_price(
        self,
        price: float,
        timestamp: Optional[datetime] = None,
        source: str = "unknown",
        latency_ms: float = 0.0,
    ) -> QualityMetrics:
        """Record a price update and return quality metrics."""
        timestamp = timestamp or datetime.now()
        
        self._detect_and_record_gap(timestamp)
        
        if len(self.price_history) > 0:
            last_price = self.price_history[-1].value
            jump_bps = abs(price - last_price) / last_price * 10000
            
            if jump_bps > self.max_jump_bps:
                self.anomalies.append((timestamp, f"Large jump: {jump_bps:.2f}bps"))
                logger.warning(
                    f"{self.symbol}: Price jump detected {jump_bps:.2f}bps"
                )
                
        self.price_history.append(DataPoint(
            timestamp=timestamp,
            value=price,
            source=source,
            latency_ms=latency_ms,
        ))
        
        self.last_update = timestamp
        self._update_count += 1
        
        self._update_rate()
        
        return self.compute_metrics()
        
    def record_volume(
        self,
        volume: float,
        timestamp: Optional[datetime] = None,
        source: str = "unknown",
    ) -> None:
        """Record a volume update."""
        timestamp = timestamp or datetime.now()
        
        self.volume_history.append(DataPoint(
            timestamp=timestamp,
            value=volume,
            source=source,
        ))
        
    def _detect_and_record_gap(self, timestamp: datetime) -> None:
        """Detect gaps in data stream."""
        if self.last_update is not None:
            gap_duration = (timestamp - self.last_update).total_seconds()
            
            expected_interval = self.expected_frequency_ms / 1000
            
            if gap_duration > expected_interval * 10:
                self.gaps.append((self.last_update, timestamp))
                
                if len(self.gaps) > 100:
                    self.gaps.pop(0)
                    
    def _update_rate(self) -> None:
        """Update messages per minute calculation."""
        now = datetime.now()
        elapsed = (now - self._last_rate_calc).total_seconds()
        
        if elapsed >= 10:
            self.updates_per_minute = (self._update_count / elapsed) * 60
            self._update_count = 0
            self._last_rate_calc = now
            
    def compute_metrics(self) -> QualityMetrics:
        """Compute current quality metrics."""
        flags: list[QualityFlag] = []
        issues: list[str] = []
        
        completeness = self._compute_completeness()
        if completeness < 0.8:
            flags.append(QualityFlag.GAPS)
            issues.append(f"Low completeness: {completeness:.1%}")
            
        timeliness = self._compute_timeliness()
        if timeliness < 0.7:
            flags.append(QualityFlag.STALE)
            issues.append(f"Data stale: {timeliness:.1%}")
            
        accuracy = self._compute_accuracy()
        if accuracy < 0.9:
            flags.append(QualityFlag.ANOMALY)
            issues.append(f"Accuracy concerns: {accuracy:.1%}")
            
        consistency = self._compute_consistency()
        if consistency < 0.8:
            flags.append(QualityFlag.UNSTABLE)
            issues.append(f"Unstable data: {consistency:.1%}")
            
        overall_score = (
            completeness * 0.3 +
            timeliness * 0.3 +
            accuracy * 0.2 +
            consistency * 0.2
        )
        
        return QualityMetrics(
            completeness=completeness,
            timeliness=timeliness,
            accuracy=accuracy,
            consistency=consistency,
            overall_score=overall_score,
            flags=flags,
            issues=issues,
            timestamp=datetime.now(),
        )
        
    def _compute_completeness(self) -> float:
        """Compute data completeness score."""
        if not self.last_update:
            return 0.0
            
        elapsed = (datetime.now() - self.last_update).total_seconds()
        
        if elapsed > self.max_age_seconds * 2:
            return 0.0
        elif elapsed > self.max_age_seconds:
            return 0.5
            
        expected_points = elapsed / (self.expected_frequency_ms / 1000)
        actual_points = len([p for p in self.price_history 
                           if (datetime.now() - p.timestamp).total_seconds() < elapsed])
        
        if expected_points <= 0:
            return 1.0
            
        return min(actual_points / expected_points, 1.0)
        
    def _compute_timeliness(self) -> float:
        """Compute data timeliness score."""
        if not self.last_update:
            return 0.0
            
        age = (datetime.now() - self.last_update).total_seconds()
        
        if age > self.max_age_seconds:
            return max(0.0, 1.0 - (age - self.max_age_seconds) / 60)
            
        return 1.0
        
    def _compute_accuracy(self) -> float:
        """Compute accuracy score based on anomaly rate."""
        if len(self.price_history) < 10:
            return 1.0
            
        recent_anomalies = [
            a for a in self.anomalies 
            if (datetime.now() - a[0]).total_seconds() < 300
        ]
        
        anomaly_rate = len(recent_anomalies) / max(len(self.price_history), 1)
        
        return max(0.0, 1.0 - anomaly_rate * 10)
        
    def _compute_consistency(self) -> float:
        """Compute consistency score based on variance stability."""
        if len(self.price_history) < 10:
            return 1.0
            
        prices = np.array([p.value for p in self.price_history])
        
        returns = np.diff(prices) / prices[:-1]
        
        if len(returns) < 2:
            return 1.0
            
        return_std = np.std(returns)
        
        if return_std > 0.01:
            return max(0.0, 1.0 - return_std * 10)
            
        return 1.0
        
    def get_latency_stats(self) -> dict:
        """Get latency statistics."""
        if not self.price_history:
            return {'avg': 0, 'p50': 0, 'p95': 0, 'p99': 0}
            
        latencies = [p.latency_ms for p in self.price_history]
        
        return {
            'avg': np.mean(latencies),
            'p50': np.percentile(latencies, 50),
            'p95': np.percentile(latencies, 95),
            'p99': np.percentile(latencies, 99),
        }
        
    def get_gap_summary(self) -> dict:
        """Get summary of data gaps."""
        total_gap_time = sum(
            (end - start).total_seconds() 
            for start, end in self.gaps
        )
        
        return {
            'count': len(self.gaps),
            'total_seconds': total_gap_time,
            'recent_gaps': self.gaps[-5:] if len(self.gaps) > 0 else [],
        }
        
    def is_healthy(self) -> bool:
        """Quick health check."""
        metrics = self.compute_metrics()
        
        return (
            metrics.overall_score >= 0.7 and
            QualityFlag.STALE not in metrics.flags and
            QualityFlag.ANOMALY not in metrics.flags
        )
        
    def reset(self) -> None:
        """Reset all statistics."""
        self.price_history.clear()
        self.volume_history.clear()
        self.gaps.clear()
        self.anomalies.clear()
        self.last_update = None
        self.updates_per_minute = 0.0
        self._update_count = 0
