# Professional Ops + Drone Capability Matrix (MVP)

This plan enables full professional capability (passive, active testing, and evidence workflows) while keeping strict authorization, approvals, and auditability.

## 1) Capability Matrix (Feature Availability)

| Capability | Passive (Observe) | Active (Controlled Test) | Evidence / Audit | Reuse in Current Codebase | MVP Build Additions |
|---|---|---|---|---|---|
| Drone detection and classification | Detect likely drone entities from WiFi/BLE/RF metadata | Trigger controlled test sessions to validate detector quality | Store detections with source, confidence, and timestamps | `/wifi/v2/*`, `/api/bluetooth/*`, `/subghz/*`, `utils/tscm/detector.py` | New detector adapters in `utils/drone/` and aggregation API |
| Remote ID intelligence | Parse and display drone/operator identifiers and telemetry | Run controlled replay/simulation inputs for validation | Persist decoded records and parser provenance | `routes/wifi_v2.py`, `routes/bluetooth_v2.py`, `utils/event_pipeline.py` | `utils/drone/remote_id.py`, `/drone-ops/remote-id/*` endpoints |
| C2 and video link analysis | Identify likely C2/video channels and protocol patterns | Controlled injection/exercise mode for authorized ranges | Save link assessments and confidence history | `routes/listening_post.py`, `routes/subghz.py`, `static/js/components/signal-guess.js` | `utils/drone/link_analysis.py`, `/drone-ops/links/*` |
| Multi-agent geolocation | Estimate emitter/drone position from distributed observations | Active test mode to validate location solver error bounds | Capture estimate history with confidence ellipse | `routes/controller.py` location endpoints | `/drone-ops/geolocate/*` wrapper over controller location APIs |
| Operator/drone correlation | Correlate drone, operator, and nearby device candidates | Active proximity probes in controlled tests | Store correlation graph and confidence deltas | `/correlation`, `/analytics/target`, TSCM identity clustering | `utils/drone/correlation.py`, `/drone-ops/correlations/*` |
| Geofence and rules | Alert on zone breach and route/altitude anomalies | Zone-based active scenario testing | Immutable breach timeline and alert acknowledgements | `utils/geofence.py`, `/analytics/geofences`, `/alerts/*` | Drone-specific alert templates and rule presets |
| Incident command workflow | Build incident timeline from detections/alerts/tracks | Execute approved active tasks per playbook | Case package with linked artifacts and operator notes | TSCM cases and notes in `routes/tscm.py`, `utils/database.py` | Drone case types + incident board UI in Drone Ops mode |
| Replay and reporting | In-app replay of full incident event stream | Replay active test sessions for after-action review | Export signed package (JSONL + summary + hashes) | `/recordings/*`, Analytics replay UI, TSCM report generation | Evidence manifest + integrity hashing + chain-of-custody log |
| Active action controls | N/A | Full active actions available when armed/approved | Every action requires explicit reason and audit record | Existing active surfaces in `/wifi/deauth`, `/subghz/transmit` | Approval workflow (`two-person`) + command gate middleware |
| Access control and approvals | Role-based read access | Role + arming + approval enforced per action class | Full action audit trail with actor, approver, and case ID | `users.role` and session role in `app.py` | `utils/authz.py`, approval/audit tables, route decorators |

## 2) Architecture Mapping to Existing Routes/UI

## Backend routes to reuse directly

- Detection feeds:
  - `routes/wifi_v2.py` (`/wifi/v2/stream`, `/wifi/v2/networks`, `/wifi/v2/clients`, `/wifi/v2/probes`)
  - `routes/bluetooth_v2.py` (`/api/bluetooth/stream`, `/api/bluetooth/devices`)
  - `routes/subghz.py` (`/subghz/stream`, receive/decode status)
- Correlation and analytics:
  - `routes/correlation.py`
  - `routes/analytics.py` (`/analytics/target`, `/analytics/patterns`, `/analytics/geofences`)
- Multi-agent geolocation:
  - `routes/controller.py` (`/controller/api/location/estimate`, `/controller/api/location/observe`, `/controller/api/location/all`)
- Alerts and recording:
  - `routes/alerts.py` and `utils/alerts.py`
  - `routes/recordings.py` and `utils/recording.py`
  - Shared pipeline in `utils/event_pipeline.py`
- Case and reporting substrate:
  - `routes/tscm.py` (`/tscm/cases`, `/tscm/report/pdf`, playbooks)
  - TSCM persistence in `utils/database.py`

## New backend module for MVP

- Add `routes/drone_ops.py` with URL prefix `/drone-ops`.
- Add `utils/drone/` package:
  - `aggregator.py` (normalize events from WiFi/BLE/RF)
  - `remote_id.py` (parsers + confidence attribution)
  - `link_analysis.py` (C2/video heuristics)
  - `geo.py` (adapter to controller location estimation)
  - `policy.py` (arming, approval, role checks)

## Frontend integration points

- Navigation:
  - `templates/partials/nav.html` add `droneops` entry (Intel group).
- Mode panel:
  - `templates/partials/modes/droneops.html`
  - `static/js/modes/droneops.js`
  - `static/css/modes/droneops.css`
- Main mode loader wiring:
  - `templates/index.html` for new panel include + script/css registration.
- Cross-mode widgets:
  - Reuse `static/js/components/signal-cards.js`, `timeline-heatmap.js`, `activity-timeline.js`.

## 3) Proposed MVP API Surface (`/drone-ops`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/drone-ops/status` | `GET` | Health, active session, arming state, policy snapshot |
| `/drone-ops/session/start` | `POST` | Start passive/active Drone Ops session with metadata |
| `/drone-ops/session/stop` | `POST` | Stop session and finalize summary |
| `/drone-ops/detections` | `GET` | Current detection list with filters |
| `/drone-ops/stream` | `GET` (SSE) | Unified live stream (detections, tracks, alerts, approvals) |
| `/drone-ops/remote-id/decode` | `POST` | Decode submitted frame payload (test and replay support) |
| `/drone-ops/tracks` | `GET` | Track list and selected track history |
| `/drone-ops/geolocate/estimate` | `POST` | Request geolocation estimate from distributed observations |
| `/drone-ops/correlations` | `GET` | Drone/operator/device correlation graph |
| `/drone-ops/incidents` | `POST` / `GET` | Create/list incidents |
| `/drone-ops/incidents/<id>` | `GET` / `PUT` | Incident detail and status updates |
| `/drone-ops/incidents/<id>/artifacts` | `POST` | Attach notes, detections, tracks, alerts, recordings |
| `/drone-ops/actions/arm` | `POST` | Arm active action plane with reason + case/incident ID |
| `/drone-ops/actions/request` | `POST` | Submit active action requiring approval policy |
| `/drone-ops/actions/approve/<id>` | `POST` | Secondary approval (if required) |
| `/drone-ops/actions/execute/<id>` | `POST` | Execute approved action via gated dispatcher |

## 4) Data Model Additions (SQLite, MVP)

Add to `utils/database.py`:

- `drone_sessions`
  - `id`, `started_at`, `stopped_at`, `mode` (`passive`/`active`), `operator`, `metadata`
- `drone_detections`
  - `id`, `session_id`, `first_seen`, `last_seen`, `source`, `identifier`, `confidence`, `payload_json`
- `drone_tracks`
  - `id`, `detection_id`, `timestamp`, `lat`, `lon`, `altitude_m`, `speed_mps`, `heading_deg`, `quality`
- `drone_correlations`
  - `id`, `drone_identifier`, `operator_identifier`, `method`, `confidence`, `evidence_json`, `created_at`
- `drone_incidents`
  - `id`, `title`, `status`, `severity`, `opened_by`, `opened_at`, `closed_at`, `summary`
- `drone_incident_artifacts`
  - `id`, `incident_id`, `artifact_type`, `artifact_ref`, `added_by`, `added_at`, `metadata`
- `action_requests`
  - `id`, `incident_id`, `action_type`, `requested_by`, `requested_at`, `status`, `payload_json`
- `action_approvals`
  - `id`, `request_id`, `approved_by`, `approved_at`, `decision`, `notes`
- `action_audit_log`
  - `id`, `request_id`, `event_type`, `actor`, `timestamp`, `details_json`
- `evidence_manifests`
  - `id`, `incident_id`, `created_at`, `hash_algo`, `manifest_json`, `signature`

Note: existing `recording_sessions` and `alert_events` remain the primary event substrate; drone tables link to those records for case assembly.

## 5) Authorization and Arming Model (All Features Available)

All features remain implemented and reachable in code. Execution path depends on policy state.

- Roles (extend `users.role` semantics):
  - `viewer`: read-only
  - `analyst`: passive + evidence operations
  - `operator`: passive + active request submission
  - `supervisor`: approval authority
  - `admin`: full control + policy management
- Active command state machine:
  - `DISARMED` (default): active commands denied
  - `ARMED` (time-bound): request creation allowed with incident ID and reason
  - `APPROVED`: dual-approval actions executable
  - `EXECUTED`: immutable audit records written
- Enforcement:
  - Add decorators in `utils/authz.py`:
    - `@require_role(...)`
    - `@require_armed`
    - `@require_two_person_approval`

## 6) MVP Delivery Plan (6 Weeks)

## Phase 0 (Week 1): Scaffolding

- Add `routes/drone_ops.py` blueprint and register in `routes/__init__.py`.
- Add `utils/drone/` package with aggregator skeleton.
- Add Drone Ops UI placeholders (`droneops.html`, `droneops.js`, `droneops.css`) and nav wiring.
- Add DB migration/create statements for drone tables.

Exit criteria:
- Drone Ops mode loads, API health endpoint returns, empty-state UI renders.

## Phase 1 (Weeks 1-2): Passive Drone Ops

- Unified ingest from WiFi/BLE/SubGHz streams.
- Detection cards, live timeline, map tracks, geofence alert hooks.
- Remote ID decode endpoint and parser confidence model.
- Alert rule presets for drone intrusions.

Exit criteria:
- Passive session can detect/classify, map updates in real time, alerts generated.

## Phase 2 (Weeks 3-4): Correlation + Geolocation + Incident Workflow

- Correlation graph (drone/operator/nearby device candidates).
- Multi-agent geolocation adapter using controller location endpoints.
- Incident creation and artifact linking.
- Replay integration using existing recordings/events APIs.

Exit criteria:
- Operator can open incident, attach artifacts, replay key timeline, export preliminary report.

## Phase 3 (Weeks 5-6): Active Actions + Evidence Integrity

- Arming and approval workflows (`action_requests`, `action_approvals`).
- Active action dispatcher with role/policy checks.
- Evidence manifest export with hashes and chain-of-custody entries.
- Audit dashboards for who requested/approved/executed.

Exit criteria:
- Active commands require approvals, all operations are auditable and exportable.

## 7) Immediate Build Backlog (First Sprint)

1. Create `routes/drone_ops.py` with `status`, `session/start`, `session/stop`, `stream`.
2. Add drone tables in `utils/database.py` and lightweight DAO helpers.
3. Add mode shell UI files and wire mode into `templates/index.html` and `templates/partials/nav.html`.
4. Implement aggregator wiring to existing WiFi/BT/SubGHz feeds via `utils/event_pipeline.py`.
5. Add `actions/arm` endpoint with role + incident requirement and TTL-based disarm.
6. Add baseline tests:
   - `tests/test_drone_ops_routes.py`
   - `tests/test_drone_ops_policy.py`
   - `tests/test_drone_ops_remote_id.py`

## 8) Risk Controls

- False attribution risk: every correlation/geolocation output carries confidence and evidence provenance.
- Policy bypass risk: active command execution path only through centralized dispatcher.
- Evidence integrity risk: hash all exported artifacts and include manifest references.
- Operational safety risk: require explicit incident linkage and arming expiration.
