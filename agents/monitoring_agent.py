"""
agents/monitoring_agent.py

AGENT 1: Monitoring & Prediction Agent

Responsibilities:
  - Continuously polls Kubernetes pods via kubectl
  - Collects CPU, memory, restart count, pod phase, etc.
  - Uses pod_health_model to classify each pod's state
  - Detects SLA violations against thresholds in sla_config.yaml
  - Triggers the Recovery Agent when issues are detected
  - Keeps a rolling history for anomaly trend detection
"""

import time
import logging
import pickle
from pathlib import Path
from datetime import datetime
from collections import defaultdict, deque

# Project imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.k8s_client import get_all_pods, get_pod_metrics, get_pod_events
from utils.logger import log_sla_violation, setup_logging
from utils.config_loader import load_config

logger = logging.getLogger("sla_monitor.monitoring")

MODEL_PATH  = Path(__file__).parent.parent / "models" / "pod_health_model.pkl"
SCALE_MODEL = Path(__file__).parent.parent / "models" / "autoscale_model.pkl"

HEALTH_FEATURES = [
    "cpu_percent", "memory_percent", "restart_count",
    "response_time_ms", "error_rate_percent", "pod_phase_encoded",
    "container_ready", "oom_killed", "network_errors", "disk_pressure"
]

PHASE_MAP = {
    "Running": 0, "Pending": 1, "Failed": 2,
    "CrashLoopBackOff": 3, "Unknown": 4
}

SCALE_FEATURES = [
    "cpu_percent", "memory_percent", "requests_per_sec",
    "current_replicas", "response_time_ms", "queue_depth"
]


class MonitoringAgent:
    """
    Agent 1: Monitors Kubernetes pods, predicts failures, and detects SLA violations.
    """

    def __init__(self, config=None, recovery_agent=None):
        self.config         = config or load_config()
        self.recovery_agent = recovery_agent   # set after both agents are created
        self.running        = False

        # Rolling history per pod: deque of metric snapshots
        self.pod_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self.config.history_window)
        )

        # Track recovery attempts to avoid loops
        self.recovery_in_progress: set[str] = set()
        self.last_recovery_time: dict[str, float] = {}

        # Load ML models
        self._load_models()

        logger.info("MonitoringAgent initialized")

    def _load_models(self):
        """Load the pre-trained ML models."""
        try:
            with open(MODEL_PATH, "rb") as f:
                bundle = pickle.load(f)
            self.health_model = bundle["model"]
            self.health_le    = bundle["label_encoder"]
            logger.info("Pod health model loaded")
        except FileNotFoundError:
            logger.warning(f"Health model not found at {MODEL_PATH}. Run: python models/train_models.py")
            self.health_model = None
            self.health_le    = None

        try:
            with open(SCALE_MODEL, "rb") as f:
                bundle = pickle.load(f)
            self.scale_model = bundle["model"]
            self.scale_le    = bundle["label_encoder"]
            logger.info("Autoscale model loaded")
        except FileNotFoundError:
            logger.warning("Autoscale model not found. Autoscaling decisions will use rule-based fallback.")
            self.scale_model = None
            self.scale_le    = None

    # --------------------------------------------------------------------------
    # Core monitoring loop
    # --------------------------------------------------------------------------

    def start(self):
        """Start the monitoring loop. Blocks until stop() is called."""
        self.running = True
        poll_interval = self.config.poll_interval_seconds

        logger.info(f"MonitoringAgent started - polling every {poll_interval}s")
        print(f"\n[MonitoringAgent] Started - polling every {poll_interval}s\n")

        while self.running:
            try:
                self._run_monitoring_cycle()
            except KeyboardInterrupt:
                logger.info("MonitoringAgent stopped by user")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Monitoring cycle error: {e}", exc_info=True)

            time.sleep(poll_interval)

    def stop(self):
        self.running = False
        logger.info("MonitoringAgent stopping...")

    def _run_monitoring_cycle(self):
        """Single monitoring cycle: collect -> classify -> check SLA -> trigger recovery."""
        timestamp = datetime.now().strftime("%H:%M:%S")

        for namespace in self.config.namespaces:
            pods = get_all_pods(namespace)
            if not pods:
                logger.debug(f"No pods found in namespace '{namespace}'")
                continue

            # Get resource metrics (requires metrics-server)
            metrics_by_pod = {
                m["name"]: m for m in get_pod_metrics(namespace)
            }

            print(f"\n[{timestamp}] Monitoring {len(pods)} pod(s) in '{namespace}'")
            print(f"  {'POD NAME':<35} {'PHASE':<20} {'RESTARTS':<10} {'ML STATE':<18} {'SLA'}")
            print(f"  {'-'*95}")

            for pod in pods:
                pod_name = pod["name"]

                # Build feature snapshot
                snapshot = self._build_snapshot(pod, metrics_by_pod.get(pod_name, {}))

                # Store in rolling history
                self.pod_history[pod_name].append(snapshot)

                # Classify pod health with ML model
                ml_state = self._classify_health(snapshot)

                # Check SLA thresholds (rule-based)
                sla_ok, violations = self._check_sla(pod_name, namespace, snapshot)

                # Print status line
                sla_icon = "[OK] OK" if sla_ok else "[WARN]️  VIOLATION"
                print(f"  {pod_name:<35} {pod['phase']:<20} {pod['restart_count']:<10} {ml_state:<18} {sla_icon}")
                if violations:
                    for v in violations:
                        print(f"    +- {v}")

                # Trigger recovery if needed
                if not sla_ok or ml_state in ("Failed", "SLA_Violation"):
                    self._trigger_recovery(pod, namespace, ml_state, violations, snapshot)

                # Check autoscaling need
                self._check_autoscale(pod_name, namespace, snapshot,
                                       len([p for p in pods if p["phase"] == "Running"]))

    # --------------------------------------------------------------------------
    # Feature extraction
    # --------------------------------------------------------------------------

    def _build_snapshot(self, pod: dict, metrics: dict) -> dict:
        """
        Build a feature snapshot from pod info + kubectl top metrics.
        Falls back to estimates when metrics-server isn't available.
        """
        # Parse CPU from metrics (e.g. "45m" = 45 millicores ≈ 4.5%)
        cpu_raw = metrics.get("cpu", "0m")
        if cpu_raw.endswith("m"):
            cpu_pct = float(cpu_raw[:-1]) / 10.0  # rough % for 1-core baseline
        else:
            cpu_pct = 0.0

        mem_raw = metrics.get("memory", "0Mi")
        if mem_raw.endswith("Mi"):
            mem_pct = float(mem_raw[:-2]) / 128.0 * 100  # assume 128Mi limit default
        elif mem_raw.endswith("Ki"):
            mem_pct = float(mem_raw[:-2]) / (128 * 1024) * 100
        else:
            mem_pct = 0.0

        # Detect OOM from container state reason
        oom_killed = any(
            c.get("reason") in ("OOMKilled",) for c in pod.get("containers", [])
        )

        # Count container-level state
        containers = pod.get("containers", [])
        not_ready_count = sum(1 for c in containers if not c.get("ready", False))

        phase = pod.get("phase", "Unknown")

        return {
            "pod_name":          pod["name"],
            "namespace":         pod.get("namespace", "default"),
            "cpu_percent":       min(cpu_pct, 100.0),
            "memory_percent":    min(mem_pct, 100.0),
            "restart_count":     pod.get("restart_count", 0),
            "response_time_ms":  0.0,   # populated by response-time probing (Phase 5 extension)
            "error_rate_percent": 0.0,  # populated by log analysis (Phase 5 extension)
            "pod_phase":         phase,
            "pod_phase_encoded": PHASE_MAP.get(phase, 4),
            "container_ready":   int(pod.get("ready", False)),
            "oom_killed":        int(oom_killed),
            "network_errors":    not_ready_count,
            "disk_pressure":     0,
        }

    # --------------------------------------------------------------------------
    # ML classification
    # --------------------------------------------------------------------------

    def _classify_health(self, snapshot: dict) -> str:
        """Use ML model to classify pod state."""
        if self.health_model is None:
            return self._rule_based_health(snapshot)

        try:
            import pandas as pd
            X = pd.DataFrame([{f: snapshot.get(f, 0) for f in HEALTH_FEATURES}])
            pred_encoded = self.health_model.predict(X)[0]
            return self.health_le.inverse_transform([pred_encoded])[0]
        except Exception as e:
            logger.debug(f"ML classification failed: {e}, using rule-based")
            return self._rule_based_health(snapshot)

    def _rule_based_health(self, snapshot: dict) -> str:
        """Fallback rule-based classification when model is unavailable."""
        phase = snapshot.get("pod_phase", "Unknown")
        restarts = snapshot.get("restart_count", 0)
        cpu = snapshot.get("cpu_percent", 0)
        mem = snapshot.get("memory_percent", 0)

        if phase in ("Failed", "CrashLoopBackOff") or restarts >= 5:
            return "Failed"
        if phase == "Unknown" or restarts >= 3 or cpu > 85 or mem > 85:
            return "SLA_Violation"
        if restarts >= 1 or cpu > 70 or mem > 70 or phase == "Pending":
            return "Warning"
        return "Normal"

    # --------------------------------------------------------------------------
    # SLA checking
    # --------------------------------------------------------------------------

    def _check_sla(self, pod_name: str, namespace: str, snapshot: dict) -> tuple[bool, list[str]]:
        """
        Compare snapshot against SLA thresholds.
        Returns (sla_ok: bool, list_of_violation_strings).
        """
        violations = []
        cfg = self.config

        if snapshot["cpu_percent"] > cfg.max_cpu_percent:
            msg = f"CPU {snapshot['cpu_percent']:.1f}% > limit {cfg.max_cpu_percent}%"
            violations.append(msg)
            log_sla_violation(pod_name, namespace, "cpu_percent",
                              snapshot["cpu_percent"], cfg.max_cpu_percent)

        if snapshot["memory_percent"] > cfg.max_memory_percent:
            msg = f"Memory {snapshot['memory_percent']:.1f}% > limit {cfg.max_memory_percent}%"
            violations.append(msg)
            log_sla_violation(pod_name, namespace, "memory_percent",
                              snapshot["memory_percent"], cfg.max_memory_percent)

        if snapshot["restart_count"] > cfg.max_restart_count:
            msg = f"Restarts {snapshot['restart_count']} > max {cfg.max_restart_count}"
            violations.append(msg)
            log_sla_violation(pod_name, namespace, "restart_count",
                              snapshot["restart_count"], cfg.max_restart_count)

        if snapshot["pod_phase"] in ("Failed", "CrashLoopBackOff", "Unknown"):
            msg = f"Pod phase is '{snapshot['pod_phase']}'"
            violations.append(msg)

        if not snapshot["container_ready"] and snapshot["pod_phase"] == "Running":
            violations.append("Container not ready despite Running phase")

        return (len(violations) == 0), violations

    # --------------------------------------------------------------------------
    # Recovery triggering
    # --------------------------------------------------------------------------

    def _trigger_recovery(self, pod: dict, namespace: str,
                           ml_state: str, violations: list[str], snapshot: dict):
        """
        Call the Recovery Agent if:
          - A recovery isn't already in progress for this pod
          - Enough time has passed since the last recovery
        """
        if self.recovery_agent is None:
            logger.warning("No recovery agent connected - cannot trigger recovery")
            return

        pod_name = pod["name"]
        now = time.time()
        cooldown = self.config.post_recovery_wait_seconds * 2  # double the wait as cooldown

        last = self.last_recovery_time.get(pod_name, 0)
        if now - last < cooldown:
            logger.debug(f"Recovery cooldown active for {pod_name} ({cooldown - (now-last):.0f}s remaining)")
            return

        if pod_name in self.recovery_in_progress:
            logger.debug(f"Recovery already in progress for {pod_name}")
            return

        logger.warning(f"Triggering recovery for {pod_name} - state={ml_state}, violations={violations}")
        self.recovery_in_progress.add(pod_name)
        self.last_recovery_time[pod_name] = now

        try:
            self.recovery_agent.handle_failure(pod, namespace, ml_state, violations, snapshot)
        finally:
            self.recovery_in_progress.discard(pod_name)

    # --------------------------------------------------------------------------
    # Autoscaling
    # --------------------------------------------------------------------------

    def _check_autoscale(self, pod_name: str, namespace: str,
                          snapshot: dict, current_replicas: int):
        """Use ML model to decide if scaling is needed and call recovery agent."""
        if self.recovery_agent is None:
            return

        try:
            import pandas as pd
            scale_input = {
                "cpu_percent":      snapshot["cpu_percent"],
                "memory_percent":   snapshot["memory_percent"],
                "requests_per_sec": 0,      # requires ingress metrics (future)
                "current_replicas": current_replicas,
                "response_time_ms": snapshot["response_time_ms"],
                "queue_depth":      0,
            }

            if self.scale_model:
                X = pd.DataFrame([{f: scale_input[f] for f in SCALE_FEATURES}])
                pred = self.scale_model.predict(X)[0]
                action = self.scale_le.inverse_transform([pred])[0]
            else:
                # Rule-based fallback
                if snapshot["cpu_percent"] > 80 or snapshot["memory_percent"] > 80:
                    action = "scale_up"
                elif snapshot["cpu_percent"] < 20 and current_replicas > 2:
                    action = "scale_down"
                else:
                    action = "no_change"

            if action != "no_change":
                logger.info(f"[Autoscale] {pod_name} -> {action}")
                self.recovery_agent.handle_autoscale(pod_name, namespace, action, snapshot)

        except Exception as e:
            logger.debug(f"Autoscale check error for {pod_name}: {e}")

    # --------------------------------------------------------------------------
    # Status summary
    # --------------------------------------------------------------------------

    def get_status_summary(self) -> dict:
        """Return a summary dict of current monitored pods."""
        return {
            "tracked_pods":    list(self.pod_history.keys()),
            "pod_count":       len(self.pod_history),
            "recovery_active": list(self.recovery_in_progress),
        }
