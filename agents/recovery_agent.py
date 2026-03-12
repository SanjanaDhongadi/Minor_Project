"""
agents/recovery_agent.py
AGENT 2: Automated Recovery Agent
"""

import time
import logging
import pickle
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.k8s_client import (
    get_all_pods, get_pod_logs, get_pod_events,
    get_deployment_for_pod, restart_pod,
    rollout_restart_deployment, run_kubectl
)
from utils.logger import log_failure_event
from utils.config_loader import load_config

logger = logging.getLogger("sla_monitor.recovery")

MODEL_PATH = Path(__file__).parent.parent / "models" / "recovery_action_model.pkl"

RECOVERY_FEATURES = [
    "failure_type_encoded", "cpu_percent", "memory_percent",
    "restart_count", "uptime_percent", "replica_count", "error_rate_percent"
]

FAILURE_TYPE_MAP = {
    "CrashLoopBackOff": 0, "OOMKilled": 1, "HighCPU": 2,
    "HighMemory": 3, "PodPending": 4, "NetworkError": 5,
    "ContainerNotReady": 6, "DiskPressure": 7, "ConfigError": 8,
    "UnknownFailure": 9
}

def print_box(title, lines):
    """Print a clean bordered box in terminal."""
    width = 62
    print()
    print("+" + "-" * width + "+")
    print("|  {:<{w}}  |".format(title, w=width-4))
    print("+" + "-" * width + "+")
    for key, val in lines:
        entry = "{:<16}: {}".format(key, str(val))
        print("|  {:<{w}}  |".format(entry, w=width-4))
    print("+" + "-" * width + "+")

def print_section(title):
    print()
    print("=" * 66)
    print("  " + title)
    print("=" * 66)

class RecoveryAgent:
    """Agent 2: Performs root cause analysis and automated recovery."""

    def __init__(self, config=None):
        self.config = config or load_config()
        self._load_model()
        self.recovery_counts = {}
        logger.info("RecoveryAgent initialized")

    def _load_model(self):
        try:
            with open(MODEL_PATH, "rb") as f:
                bundle = pickle.load(f)
            self.model    = bundle["model"]
            self.label_le = bundle["label_encoder"]
            logger.info("Recovery action model loaded")
        except FileNotFoundError:
            logger.warning("Recovery model not found. Using rule-based fallback.")
            self.model    = None
            self.label_le = None

    # --------------------------------------------------------------------------
    # Main entry: called by MonitoringAgent
    # --------------------------------------------------------------------------

    def handle_failure(self, pod, namespace, ml_state, violations, snapshot):
        pod_name = pod["name"]
        now_str  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print_section("FAILURE DETECTED -- RECOVERY AGENT ACTIVATED")
        print("  Pod Name  : {}".format(pod_name))
        print("  Time      : {}".format(now_str))
        print("  Violation : {}".format(" | ".join(violations)))

        # Check max attempts
        attempts = self.recovery_counts.get(pod_name, 0)
        if attempts >= self.config.max_recovery_attempts:
            print()
            print("  [STOPPED] Max recovery attempts ({}) reached.".format(
                self.config.max_recovery_attempts))
            print("  Manual intervention required.")
            log_failure_event(pod_name, namespace, ml_state,
                              "Too many recovery attempts",
                              "none", "Escalation Required")
            return
        self.recovery_counts[pod_name] = attempts + 1

        # Step 1: Root Cause Analysis
        failure_type, root_cause = self._root_cause_analysis(
            pod, namespace, snapshot, violations)

        print()
        print("  STEP 1 -- ROOT CAUSE ANALYSIS")
        print("  " + "-" * 40)
        print("  Failure Type : {}".format(failure_type))
        print("  Root Cause   : {}".format(root_cause))

        # Step 2: Select action
        action = self._select_action(failure_type, snapshot)
        print()
        print("  STEP 2 -- ACTION SELECTION (ML Model)")
        print("  " + "-" * 40)
        print("  Action Selected : {}".format(action))

        # Step 3: Execute
        print()
        print("  STEP 3 -- EXECUTING RECOVERY ACTION")
        print("  " + "-" * 40)
        success = self._execute_action(action, pod_name, namespace, snapshot)
        print("  Execution Status : {}".format("SUCCESS" if success else "FAILED"))

        # Step 4: Validate
        print()
        print("  STEP 4 -- POST-RECOVERY VALIDATION")
        print("  " + "-" * 40)
        if success:
            validated = self._post_recovery_validation(pod_name, namespace)
            final_status = "Resolved" if validated else "Unresolved - requires attention"
        else:
            final_status = "Recovery action failed"

        # Step 5: Log and print summary
        log_failure_event(
            pod_name=pod_name,
            namespace=namespace,
            failure_type=failure_type,
            root_cause=root_cause,
            action_taken=action,
            status=final_status,
            details=" | ".join(violations)
        )

        print()
        print_box("RECOVERY SUMMARY", [
            ("Time",         now_str),
            ("Pod",          pod_name[-40:]),
            ("Failure Type", failure_type),
            ("Root Cause",   root_cause[:40]),
            ("Action Taken", action),
            ("Final Status", final_status),
            ("Log File",     "logs/recovery_log.csv"),
        ])

        if final_status == "Resolved":
            self.recovery_counts.pop(pod_name, None)

    # --------------------------------------------------------------------------
    # Root Cause Analysis
    # --------------------------------------------------------------------------

    def _root_cause_analysis(self, pod, namespace, snapshot, violations):
        pod_name = pod["name"]
        phase    = pod.get("phase", "Unknown")
        restarts = pod.get("restart_count", 0)
        oom      = snapshot.get("oom_killed", 0)
        cpu      = snapshot.get("cpu_percent", 0)
        mem      = snapshot.get("memory_percent", 0)

        events = get_pod_events(pod_name, namespace)
        event_reasons  = [e.get("reason", "") for e in events]
        event_messages = " ".join([e.get("message", "") for e in events]).lower()
        logs       = get_pod_logs(pod_name, namespace, tail_lines=20)
        logs_lower = logs.lower()

        if phase == "CrashLoopBackOff" or "CrashLoopBackOff" in event_reasons:
            if "oomkilled" in event_messages or oom:
                return "OOMKilled", "Container killed by kernel - memory limit exceeded"
            if "error" in logs_lower or "exception" in logs_lower:
                return "CrashLoopBackOff", "Application error causing container crash"
            if "config" in logs_lower or "configmap" in event_messages:
                return "ConfigError", "Misconfiguration in container environment"
            return "CrashLoopBackOff", "Container repeatedly crashing (restarts={})".format(restarts)

        if oom or "OOMKilled" in event_reasons:
            return "OOMKilled", "Memory limit exceeded - pod memory at {:.1f}%".format(mem)

        if phase == "Pending":
            if "insufficient" in event_messages:
                return "PodPending", "Insufficient cluster resources"
            if "unschedulable" in event_messages:
                return "PodPending", "Pod unschedulable - no matching node"
            return "PodPending", "Pod stuck in Pending state"

        if cpu > self.config.max_cpu_percent:
            return "HighCPU", "CPU {:.1f}% exceeds SLA threshold {}%".format(
                cpu, self.config.max_cpu_percent)

        if mem > self.config.max_memory_percent:
            return "HighMemory", "Memory {:.1f}% exceeds SLA threshold {}%".format(
                mem, self.config.max_memory_percent)

        if phase in ("Failed", "Unknown"):
            if "network" in event_messages or "timeout" in logs_lower:
                return "NetworkError", "Network connectivity issue detected"
            if "disk" in event_messages or "no space" in logs_lower:
                return "DiskPressure", "Disk pressure - node storage full"
            return "UnknownFailure", "Pod in {} state - review events".format(phase)

        if not snapshot.get("container_ready"):
            return "ContainerNotReady", "Container readiness probe failing"

        if restarts > self.config.max_restart_count:
            return "CrashLoopBackOff", "Excessive restarts ({}) detected".format(restarts)

        return "UnknownFailure", "Anomaly detected - no specific root cause found"

    # --------------------------------------------------------------------------
    # Action selection
    # --------------------------------------------------------------------------

    def _select_action(self, failure_type, snapshot):
        if self.model:
            try:
                import pandas as pd
                ft_enc = FAILURE_TYPE_MAP.get(failure_type, 9)
                X = pd.DataFrame([{
                    "failure_type_encoded": ft_enc,
                    "cpu_percent":          snapshot.get("cpu_percent", 0),
                    "memory_percent":       snapshot.get("memory_percent", 0),
                    "restart_count":        snapshot.get("restart_count", 0),
                    "uptime_percent":       70.0,
                    "replica_count":        1,
                    "error_rate_percent":   snapshot.get("error_rate_percent", 0),
                }])
                pred   = self.model.predict(X)[0]
                action = self.label_le.inverse_transform([pred])[0]
                print("  ML Model Decision : {} (for {})".format(action, failure_type))
                return action
            except Exception as e:
                logger.debug("ML action selection failed: {}, using rules".format(e))

        return self._rule_based_action(failure_type, snapshot)

    def _rule_based_action(self, failure_type, snapshot):
        rules = {
            "CrashLoopBackOff":  "rollout_restart_deployment",
            "OOMKilled":         "adjust_resource_limits",
            "HighCPU":           "scale_up_replicas",
            "HighMemory":        "adjust_resource_limits",
            "PodPending":        "scale_up_replicas",
            "NetworkError":      "restart_pod",
            "ContainerNotReady": "restart_pod",
            "DiskPressure":      "clear_disk",
            "ConfigError":       "fix_configuration",
            "UnknownFailure":    "restart_pod",
        }
        action = rules.get(failure_type, "restart_pod")
        print("  Rule-based Decision : {}".format(action))
        return action

    # --------------------------------------------------------------------------
    # Action execution
    # --------------------------------------------------------------------------

    def _execute_action(self, action, pod_name, namespace, snapshot):
        try:
            if action == "restart_pod":
                return self._action_restart_pod(pod_name, namespace)
            elif action == "rollout_restart_deployment":
                return self._action_rollout_restart(pod_name, namespace)
            elif action == "scale_up_replicas":
                return self._action_scale(pod_name, namespace, "up")
            elif action == "scale_down_replicas":
                return self._action_scale(pod_name, namespace, "down")
            elif action == "adjust_resource_limits":
                return self._action_adjust_resources(pod_name, namespace, snapshot)
            elif action == "fix_configuration":
                return self._action_fix_config(pod_name, namespace)
            elif action == "clear_disk":
                return self._action_clear_disk(namespace)
            elif action == "drain_node":
                return self._action_drain_node(pod_name, namespace)
            elif action == "no_action_monitor":
                print("  Action: Monitoring only - no immediate fix needed")
                return True
            else:
                print("  Unknown action - defaulting to restart_pod")
                return self._action_restart_pod(pod_name, namespace)
        except Exception as e:
            logger.error("Action '{}' failed: {}".format(action, e))
            return False

    def _action_restart_pod(self, pod_name, namespace):
        print("  Executing : Delete and restart pod")
        result = restart_pod(pod_name, namespace)
        print("  Result    : {}".format(
            "Pod deleted - Kubernetes will recreate it" if result else "FAILED to restart"))
        return result

    def _action_rollout_restart(self, pod_name, namespace):
        deployment = get_deployment_for_pod(pod_name, namespace)
        if deployment:
            print("  Executing : Rolling restart of deployment '{}'".format(deployment))
            result = rollout_restart_deployment(deployment, namespace)
            print("  Result    : {}".format(
                "Rollout restart initiated" if result else "FAILED"))
            return result
        else:
            print("  No deployment found - falling back to pod restart")
            return self._action_restart_pod(pod_name, namespace)

    def _action_scale(self, pod_name, namespace, direction):
        deployment = get_deployment_for_pod(pod_name, namespace)
        if not deployment:
            print("  Result    : FAILED - no deployment found for {}".format(pod_name))
            return False
        r = run_kubectl(["get", "deployment", deployment, "-n", namespace,
                         "-o", "jsonpath={.spec.replicas}"])
        current   = int(r["stdout"]) if r["success"] and r["stdout"].isdigit() else 1
        new_count = current + 1 if direction == "up" else max(1, current - 1)
        print("  Executing : Scale {} from {} to {} replicas".format(
            deployment, current, new_count))
        result = run_kubectl(["scale", "deployment", deployment,
                               "--replicas={}".format(new_count), "-n", namespace])
        print("  Result    : {}".format(
            "Scaled to {} replicas".format(new_count) if result["success"] else "FAILED"))
        return result["success"]

    def _action_adjust_resources(self, pod_name, namespace, snapshot):
        deployment = get_deployment_for_pod(pod_name, namespace)
        if not deployment:
            print("  Result    : FAILED - no deployment found")
            return False
        mem_pct = snapshot.get("memory_percent", 0)
        cpu_pct = snapshot.get("cpu_percent", 0)
        if mem_pct > 85:
            patch = '{"spec":{"template":{"spec":{"containers":[{"name":"","resources":{"limits":{"memory":"256Mi"},"requests":{"memory":"128Mi"}}}]}}}}'
            print("  Executing : Increase memory limits on {}".format(deployment))
        elif cpu_pct > 80:
            patch = '{"spec":{"template":{"spec":{"containers":[{"name":"","resources":{"limits":{"cpu":"500m"},"requests":{"cpu":"250m"}}}]}}}}'
            print("  Executing : Increase CPU limits on {}".format(deployment))
        else:
            patch = '{"spec":{"template":{"spec":{"containers":[{"name":"","resources":{"limits":{"memory":"256Mi","cpu":"500m"}}}]}}}}'
            print("  Executing : Increase CPU and memory limits on {}".format(deployment))
        result = run_kubectl(["patch", "deployment", deployment, "-n", namespace,
                               "--type=strategic", "-p", patch])
        if result["success"]:
            print("  Result    : Resource limits adjusted successfully")
        else:
            print("  Result    : Patch failed - falling back to rollout restart")
            return rollout_restart_deployment(deployment, namespace)
        return result["success"]

    def _action_fix_config(self, pod_name, namespace):
        print("  Executing : Rollout restart to reload configuration")
        deployment = get_deployment_for_pod(pod_name, namespace)
        if deployment:
            return rollout_restart_deployment(deployment, namespace)
        return self._action_restart_pod(pod_name, namespace)

    def _action_clear_disk(self, namespace):
        print("  Executing : Delete completed/failed pods to free disk space")
        r1 = run_kubectl(["delete", "pods", "-n", namespace,
                           "--field-selector=status.phase=Succeeded", "--ignore-not-found"])
        r2 = run_kubectl(["delete", "pods", "-n", namespace,
                           "--field-selector=status.phase=Failed", "--ignore-not-found"])
        success = r1["success"] and r2["success"]
        print("  Result    : {}".format(
            "Cleaned up terminated pods" if success else "FAILED"))
        return success

    def _action_drain_node(self, pod_name, namespace):
        pods     = get_all_pods(namespace)
        pod_info = next((p for p in pods if p["name"] == pod_name), None)
        if not pod_info:
            return False
        node = pod_info.get("node", "")
        if not node or node == "unknown":
            return self._action_restart_pod(pod_name, namespace)
        print("  Executing : Cordon node {} and reschedule pod".format(node))
        run_kubectl(["cordon", node])
        return self._action_restart_pod(pod_name, namespace)

    # --------------------------------------------------------------------------
    # Post-recovery validation
    # --------------------------------------------------------------------------

    def _post_recovery_validation(self, pod_name, namespace):
        wait = self.config.post_recovery_wait_seconds
        print("  Waiting {}s for pod to stabilize...".format(wait))
        for elapsed in range(0, wait, 5):
            time.sleep(5)
            pods  = get_all_pods(namespace)
            base  = "-".join(pod_name.split("-")[:-2]) if pod_name.count("-") >= 2 else pod_name
            matching     = [p for p in pods if p["name"].startswith(base)]
            running_ready = [p for p in matching
                             if p["phase"] == "Running" and p.get("ready", False)]
            print("  Check at {}s : {}/{} pods Running and Ready".format(
                elapsed + 5, len(running_ready), len(matching)))
            if running_ready:
                print("  Validation  : PASSED - Pod is healthy")
                return True
        print("  Validation  : FAILED - Pod did not recover in {}s".format(wait))
        return False

    # --------------------------------------------------------------------------
    # Autoscaling
    # --------------------------------------------------------------------------

    def handle_autoscale(self, pod_name, namespace, action, snapshot):
        if action == "scale_up":
            print()
            print("  [AUTOSCALE] Scale UP triggered for {}".format(pod_name))
            print("  Reason    : High load detected")
            success = self._action_scale(pod_name, namespace, "up")
            status  = "Resolved" if success else "Failed"
            log_failure_event(pod_name, namespace, "HighLoad",
                              "Load spike detected", "scale_up_replicas", status)

        elif action == "scale_down":
            print()
            print("  [AUTOSCALE] Scale DOWN triggered for {}".format(pod_name))
            print("  Reason    : Pod under-utilized")
            success = self._action_scale(pod_name, namespace, "down")
            status  = "Resolved" if success else "Failed"
            log_failure_event(pod_name, namespace, "LowLoad",
                              "Under-utilization detected", "scale_down_replicas", status)
