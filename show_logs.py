import csv, os
from pathlib import Path
from datetime import datetime

LOG_FILE = Path('logs/recovery_log.csv')

def show_clean_logs():
    print()
    print("=" * 80)
    print("   SLA MONITORING & AUTOMATED RECOVERY SYSTEM - AUDIT LOG")
    print(f"   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    if not LOG_FILE.exists() or os.path.getsize(LOG_FILE) == 0:
        print("\n   No events yet. Run python main.py and wait 1-2 minutes.\n")
        return

    with open(LOG_FILE, 'r') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("\n   Log file is empty.\n")
        return

    resolved = sum(1 for r in rows if r['status'] == 'Resolved')
    failures = {}
    for r in rows:
        ft = r['failure_type']
        failures[ft] = failures.get(ft, 0) + 1

    print(f"\n   SUMMARY")
    print(f"   Total Recovery Events : {len(rows)}")
    print(f"   Successfully Resolved : {resolved}")
    print(f"   Failed/Unresolved     : {len(rows) - resolved}")
    print(f"\n   Failure Types Detected:")
    for ft, count in failures.items():
        print(f"     - {ft:<35} {count} occurrence(s)")

    print()
    print("=" * 80)
    print("   DETAILED EVENTS")
    print("=" * 80)

    for i, row in enumerate(rows, 1):
        status = "[RESOLVED]" if row['status'] == 'Resolved' else "[NOT RESOLVED]"
        print(f"\n   Event #{i}  {status}")
        print(f"   Time         : {row['timestamp']}")
        print(f"   Pod          : {row['pod_name']}")
        print(f"   Failure      : {row['failure_type']}")
        print(f"   Root Cause   : {row['root_cause']}")
        print(f"   Action Taken : {row['action_taken']}")
        print(f"   Result       : {row['status']}")

    print()
    print("=" * 80)
    print(f"   END OF LOG  |  Total: {len(rows)} events  |  Resolved: {resolved}")
    print("=" * 80)

show_clean_logs()