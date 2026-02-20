"""Tests for Drone Ops policy helpers and service policy behavior."""

from utils.drone.policy import required_approvals_for_action
from utils.drone.service import DroneOpsService


def test_required_approvals_policy_helper():
    assert required_approvals_for_action('passive_scan') == 1
    assert required_approvals_for_action('wifi_deauth_test') == 2


def test_service_required_approvals_matches_policy_helper():
    assert DroneOpsService.required_approvals('passive_capture') == required_approvals_for_action('passive_capture')
    assert DroneOpsService.required_approvals('active_test') == required_approvals_for_action('active_test')


def test_service_arm_disarm_policy_state():
    service = DroneOpsService()

    armed = service.arm_actions(
        actor='operator-1',
        reason='controlled testing',
        incident_id=42,
        duration_seconds=5,
    )
    assert armed['armed'] is True
    assert armed['armed_by'] == 'operator-1'
    assert armed['arm_reason'] == 'controlled testing'
    assert armed['arm_incident_id'] == 42
    assert armed['armed_until'] is not None

    disarmed = service.disarm_actions(actor='operator-1', reason='test complete')
    assert disarmed['armed'] is False
    assert disarmed['armed_by'] is None
    assert disarmed['arm_reason'] is None
    assert disarmed['arm_incident_id'] is None
