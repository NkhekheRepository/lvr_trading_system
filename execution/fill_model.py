"""
Fill probability model for execution simulation.
"""

import numpy as np

EPS = 1e-10


class FillModel:
    """
    Fill probability model based on queue position and flow rate.
    
    Formula: fill_prob = flow_rate / (queue_ahead + 1)
    """

    def __init__(self, base_flow_rate: float = 0.5):
        self.base_flow_rate = base_flow_rate
        self._flow_history = []

    def compute_fill_probability(
        self,
        queue_ahead: int,
        order_size: float,
        market_depth: float,
        flow_rate: float = None
    ) -> float:
        """
        Compute probability of fill.
        
        Args:
            queue_ahead: Number of orders ahead in queue
            order_size: Size of our order
            market_depth: Available depth at price level
            flow_rate: Current market flow rate (optional)
            
        Returns:
            Fill probability between 0 and 1
        """
        if flow_rate is None:
            flow_rate = self.base_flow_rate

        queue_factor = 1.0 / (queue_ahead + 1)

        size_factor = 1.0
        if market_depth > EPS:
            size_factor = min(order_size / market_depth, 1.0)

        prob = flow_rate * queue_factor * size_factor

        self._update_flow_history(flow_rate)

        return float(np.clip(prob, 0, 1))

    def _update_flow_history(self, flow_rate: float) -> None:
        """Track flow rate history."""
        self._flow_history.append(flow_rate)
        if len(self._flow_history) > 100:
            self._flow_history = self._flow_history[-50:]

    def get_average_flow_rate(self) -> float:
        """Get average flow rate from history."""
        if not self._flow_history:
            return self.base_flow_rate
        return np.mean(self._flow_history[-20:])

    def estimate_time_to_fill(
        self,
        queue_ahead: int,
        order_size: float,
        market_depth: float
    ) -> float:
        """
        Estimate time to fill in seconds.
        
        Returns estimated seconds until fill.
        """
        prob = self.compute_fill_probability(queue_ahead, order_size, market_depth)
        if prob <= 0:
            return float('inf')
        
        avg_flow = self.get_average_flow_rate()
        expected_attempts = 1.0 / prob
        time_per_attempt = 0.1
        
        return expected_attempts * time_per_attempt


class AdaptiveFillModel(FillModel):
    """
    Adaptive fill model that adjusts based on observed fills.
    """

    def __init__(
        self,
        base_flow_rate: float = 0.5,
        adaptation_rate: float = 0.1
    ):
        super().__init__(base_flow_rate)
        self.adaptation_rate = adaptation_rate
        self._observed_fills = 0
        self._expected_fills = 0

    def record_expected_fill(self) -> None:
        """Record that we expected a fill."""
        self._expected_fills += 1

    def record_actual_fill(self) -> None:
        """Record an actual fill."""
        self._observed_fills += 1

    def adapt(self) -> float:
        """
        Adapt flow rate based on observed vs expected fills.
        
        Returns new flow rate.
        """
        if self._expected_fills < 10:
            return self.base_flow_rate

        observed_rate = self._observed_fills / self._expected_fills
        adjustment = self.adaptation_rate * (observed_rate - 0.5)

        new_rate = self.base_flow_rate + adjustment
        self.base_flow_rate = float(np.clip(new_rate, 0.1, 0.9))

        self._observed_fills = 0
        self._expected_fills = 0

        return self.base_flow_rate
