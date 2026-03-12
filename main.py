#!/usr/bin/env python3
"""
main.py — SLA Violation Monitoring and Automated Recovery System

Starts both agents:
  - Agent 1: MonitoringAgent (polls k8s, detects violations, triggers recovery)
  - Agent 2: RecoveryAgent  (performs root cause analysis and fixes)

Usage:
    python main.py                  # Run full system
    python main.py --logs           # Print audit log table and exit
    python main.py --once           # Run one monitoring cycle and exit (for testing)
"""

import sys
import argparse
import logging
logging.disable(logging.INFO)
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import setup_logging, print_log_table
from utils.config_loader import load_config
from utils.k8s_client import check_kubectl_available
from agents.monitoring_agent import MonitoringAgent
from agents.recovery_agent import RecoveryAgent


def parse_args():
    parser = argparse.ArgumentParser(
        description="SLA Violation Monitoring and Automated Recovery for Kubernetes"
    )
    parser.add_argument("--logs",  action="store_true", help="Print audit log and exit")
    parser.add_argument("--once",  action="store_true", help="Run one cycle only (for testing)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main():
    args   = parse_args()
    level  = "DEBUG" if args.debug else "INFO"
    logger = setup_logging(level)

    if args.logs:
        print_log_table()
        return 0

    print("""
╔══════════════════════════════════════════════════════════╗
║     SLA Violation Monitoring & Automated Recovery        ║
║     Kubernetes Intelligent Agent System                  ║
╚══════════════════════════════════════════════════════════╝
""")

    # Validate kubectl connection
    if not check_kubectl_available():
        print("❌ Cannot connect to Kubernetes cluster.")
        print("   Run: minikube start")
        return 1

    # Load config
    config = load_config()
    logger.info(f"Namespaces: {config.namespaces}")
    logger.info(f"Poll interval: {config.poll_interval_seconds}s")

    # Instantiate agents
    recovery_agent  = RecoveryAgent(config=config)
    monitoring_agent = MonitoringAgent(config=config, recovery_agent=recovery_agent)

    print("✅ Both agents initialized")
    print(f"✅ Monitoring namespaces: {config.namespaces}")
    print(f"✅ Poll interval: {config.poll_interval_seconds}s")
    print(f"\nPress Ctrl+C to stop.\n")

    if args.once:
        # Single cycle for testing
        monitoring_agent._run_monitoring_cycle()
        print("\n[--once] Single cycle complete.")
        print_log_table()
        return 0

    # Full continuous monitoring
    monitoring_agent.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
