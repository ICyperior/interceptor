"""
Rules engine for ISMS anomaly detection.

Provides a configurable rules system for detecting spectrum anomalies,
cellular environment changes, and suspicious RF patterns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger('intercept.isms.rules')


@dataclass
class Finding:
    """A detected anomaly or observation."""
    finding_type: str
    severity: str  # 'info', 'warn', 'high'
    description: str
    band: str | None = None
    frequency: float | None = None
    details: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'finding_type': self.finding_type,
            'severity': self.severity,
            'description': self.description,
            'band': self.band,
            'frequency': self.frequency,
            'details': self.details,
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class Rule:
    """An anomaly detection rule."""
    name: str
    description: str
    severity: str  # 'info', 'warn', 'high'
    check: Callable[[dict], bool]
    message_template: str
    category: str = 'general'
    enabled: bool = True

    def evaluate(self, context: dict) -> Finding | None:
        """
        Evaluate rule against context.

        Args:
            context: Dictionary with detection context data

        Returns:
            Finding if rule triggered, None otherwise
        """
        if not self.enabled:
            return None

        try:
            if self.check(context):
                # Format message with context values
                try:
                    message = self.message_template.format(**context)
                except KeyError:
                    message = self.message_template

                return Finding(
                    finding_type=self.name,
                    severity=self.severity,
                    description=message,
                    band=context.get('band'),
                    frequency=context.get('frequency') or context.get('freq_mhz'),
                    details=context,
                )
        except Exception as e:
            logger.debug(f"Rule {self.name} evaluation error: {e}")

        return None


# Built-in anomaly detection rules
ISMS_RULES: list[Rule] = [
    Rule(
        name='burst_detected',
        description='Short RF burst above noise floor',
        severity='warn',
        category='spectrum',
        check=lambda ctx: ctx.get('burst_count', 0) > 0,
        message_template='Detected {burst_count} burst(s) in {band}',
    ),
    Rule(
        name='periodic_burst',
        description='Repeated periodic bursts consistent with beacon',
        severity='warn',
        category='spectrum',
        check=lambda ctx: (
            ctx.get('burst_count', 0) >= 3 and
            ctx.get('burst_interval_stdev', float('inf')) < 2.0
        ),
        message_template='Periodic bursts detected (~{burst_interval_avg:.1f}s interval) in {band}',
    ),
    Rule(
        name='new_peak_frequency',
        description='New peak frequency not in baseline',
        severity='info',
        category='spectrum',
        check=lambda ctx: ctx.get('is_new_peak', False),
        message_template='New peak at {freq_mhz:.3f} MHz ({power_db:.1f} dB)',
    ),
    Rule(
        name='strong_signal_indoors',
        description='Strong signal above indoor threshold',
        severity='warn',
        category='spectrum',
        check=lambda ctx: ctx.get('power_db', -100) > ctx.get('indoor_threshold', -40),
        message_template='Strong signal ({power_db:.1f} dB) at {freq_mhz:.3f} MHz',
    ),
    Rule(
        name='noise_floor_increase',
        description='Significant noise floor increase from baseline',
        severity='warn',
        category='spectrum',
        check=lambda ctx: ctx.get('noise_delta', 0) > 6,  # >6dB increase
        message_template='Noise floor increased by {noise_delta:.1f} dB in {band}',
    ),
    Rule(
        name='noise_floor_decrease',
        description='Significant noise floor decrease from baseline',
        severity='info',
        category='spectrum',
        check=lambda ctx: ctx.get('noise_delta', 0) < -6,  # >6dB decrease
        message_template='Noise floor decreased by {noise_delta:.1f} dB in {band}',
    ),
    Rule(
        name='high_activity_band',
        description='Unusually high activity in band',
        severity='info',
        category='spectrum',
        check=lambda ctx: ctx.get('activity_score', 0) > 80,
        message_template='High activity ({activity_score:.0f}%) in {band}',
    ),
    Rule(
        name='activity_increase',
        description='Activity score increased from baseline',
        severity='info',
        category='spectrum',
        check=lambda ctx: ctx.get('activity_delta', 0) > 30,  # >30% increase
        message_template='Activity increased by {activity_delta:.0f}% in {band}',
    ),
    Rule(
        name='new_cell_detected',
        description='New cell tower not in baseline',
        severity='info',
        category='cellular',
        check=lambda ctx: ctx.get('is_new_cell', False),
        message_template='New cell: {plmn} {radio} CID {cell_id}',
    ),
    Rule(
        name='cell_disappeared',
        description='Previously seen cell no longer detected',
        severity='info',
        category='cellular',
        check=lambda ctx: ctx.get('is_missing_cell', False),
        message_template='Cell no longer seen: {plmn} CID {cell_id}',
    ),
    Rule(
        name='new_operator',
        description='New network operator detected',
        severity='warn',
        category='cellular',
        check=lambda ctx: ctx.get('is_new_operator', False),
        message_template='New operator detected: {operator} ({plmn})',
    ),
    Rule(
        name='signal_strength_change',
        description='Significant change in cell signal strength',
        severity='info',
        category='cellular',
        check=lambda ctx: abs(ctx.get('rsrp_delta', 0)) > 10,  # >10dB change
        message_template='Signal change: {plmn} CID {cell_id} ({rsrp_delta:+.0f} dB)',
    ),
    Rule(
        name='suspicious_ism_activity',
        description='Unusual activity in ISM band',
        severity='warn',
        category='spectrum',
        check=lambda ctx: (
            ctx.get('band', '').startswith('ISM') and
            ctx.get('activity_score', 0) > 60 and
            ctx.get('is_new_peak', False)
        ),
        message_template='Suspicious ISM activity at {freq_mhz:.3f} MHz',
    ),
]


class RulesEngine:
    """Engine for evaluating ISMS detection rules."""

    def __init__(self, rules: list[Rule] | None = None):
        """
        Initialize rules engine.

        Args:
            rules: List of rules to use (defaults to ISMS_RULES)
        """
        self.rules = rules if rules is not None else ISMS_RULES.copy()
        self._custom_rules: list[Rule] = []

    def add_rule(self, rule: Rule) -> None:
        """Add a custom rule."""
        self._custom_rules.append(rule)

    def remove_rule(self, rule_name: str) -> bool:
        """Remove a rule by name."""
        for rules_list in [self.rules, self._custom_rules]:
            for i, rule in enumerate(rules_list):
                if rule.name == rule_name:
                    rules_list.pop(i)
                    return True
        return False

    def enable_rule(self, rule_name: str) -> bool:
        """Enable a rule by name."""
        for rule in self.rules + self._custom_rules:
            if rule.name == rule_name:
                rule.enabled = True
                return True
        return False

    def disable_rule(self, rule_name: str) -> bool:
        """Disable a rule by name."""
        for rule in self.rules + self._custom_rules:
            if rule.name == rule_name:
                rule.enabled = False
                return True
        return False

    def get_rules_by_category(self, category: str) -> list[Rule]:
        """Get all rules in a category."""
        return [
            r for r in self.rules + self._custom_rules
            if r.category == category and r.enabled
        ]

    def evaluate(self, context: dict) -> list[Finding]:
        """
        Evaluate all rules against context.

        Args:
            context: Dictionary with detection context data

        Returns:
            List of Finding objects for triggered rules
        """
        findings = []

        for rule in self.rules + self._custom_rules:
            finding = rule.evaluate(context)
            if finding:
                findings.append(finding)
                logger.debug(f"Rule '{rule.name}' triggered: {finding.description}")

        return findings

    def evaluate_spectrum(
        self,
        band_name: str,
        noise_floor: float,
        peak_freq: float,
        peak_power: float,
        activity_score: float,
        baseline_noise: float | None = None,
        baseline_activity: float | None = None,
        baseline_peaks: list[float] | None = None,
        burst_count: int = 0,
        burst_interval_avg: float | None = None,
        burst_interval_stdev: float | None = None,
        indoor_threshold: float = -40,
    ) -> list[Finding]:
        """
        Evaluate spectrum-related rules.

        Args:
            band_name: Name of the band
            noise_floor: Current noise floor in dB
            peak_freq: Peak frequency in MHz
            peak_power: Peak power in dB
            activity_score: Activity score 0-100
            baseline_noise: Baseline noise floor for comparison
            baseline_activity: Baseline activity score for comparison
            baseline_peaks: List of baseline peak frequencies
            burst_count: Number of bursts detected
            burst_interval_avg: Average interval between bursts
            burst_interval_stdev: Standard deviation of burst intervals
            indoor_threshold: Power threshold for indoor signal detection

        Returns:
            List of Finding objects
        """
        context = {
            'band': band_name,
            'freq_mhz': peak_freq,
            'power_db': peak_power,
            'noise_floor': noise_floor,
            'activity_score': activity_score,
            'burst_count': burst_count,
            'indoor_threshold': indoor_threshold,
        }

        # Calculate deltas from baseline
        if baseline_noise is not None:
            context['noise_delta'] = noise_floor - baseline_noise

        if baseline_activity is not None:
            context['activity_delta'] = activity_score - baseline_activity

        # Check if peak is new
        if baseline_peaks is not None:
            # Consider peak "new" if not within 0.1 MHz of any baseline peak
            context['is_new_peak'] = all(
                abs(peak_freq - bp) > 0.1 for bp in baseline_peaks
            )
        else:
            context['is_new_peak'] = False

        # Add burst timing info
        if burst_interval_avg is not None:
            context['burst_interval_avg'] = burst_interval_avg
        if burst_interval_stdev is not None:
            context['burst_interval_stdev'] = burst_interval_stdev

        return self.evaluate(context)

    def evaluate_cellular(
        self,
        plmn: str,
        cell_id: int,
        radio: str,
        rsrp: int | None = None,
        operator: str | None = None,
        baseline_cells: list[dict] | None = None,
        baseline_operators: list[str] | None = None,
        previous_rsrp: int | None = None,
    ) -> list[Finding]:
        """
        Evaluate cellular-related rules.

        Args:
            plmn: PLMN code (MCC-MNC)
            cell_id: Cell ID
            radio: Radio type (GSM, UMTS, LTE, NR)
            rsrp: Signal strength in dBm
            operator: Operator name
            baseline_cells: List of baseline cell dicts for comparison
            baseline_operators: List of baseline operator PLMNs
            previous_rsrp: Previous RSRP reading for this cell

        Returns:
            List of Finding objects
        """
        context = {
            'plmn': plmn,
            'cell_id': cell_id,
            'radio': radio,
            'operator': operator or plmn,
        }

        if rsrp is not None:
            context['rsrp'] = rsrp

        # Check if cell is new
        if baseline_cells is not None:
            context['is_new_cell'] = not any(
                c.get('cell_id') == cell_id and c.get('plmn') == plmn
                for c in baseline_cells
            )
        else:
            context['is_new_cell'] = False

        # Check if operator is new
        if baseline_operators is not None:
            context['is_new_operator'] = plmn not in baseline_operators

        # Calculate RSRP delta
        if rsrp is not None and previous_rsrp is not None:
            context['rsrp_delta'] = rsrp - previous_rsrp

        return self.evaluate(context)


def create_default_engine() -> RulesEngine:
    """Create a rules engine with default rules."""
    return RulesEngine(ISMS_RULES.copy())
