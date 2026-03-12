import csv, json, logging, os
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / 'logs'
LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE_CSV = LOGS_DIR / 'recovery_log.csv'
LOG_FILE_JSON = LOGS_DIR / 'events.jsonl'
CSV_HEADERS = ['timestamp','pod_name','namespace','failure_type','root_cause','action_taken','status','details']

def setup_logging(level='INFO'):
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    fh = logging.FileHandler(LOGS_DIR / 'sla_monitor.log')
    fh.setLevel(log_level)
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    logging.getLogger().addHandler(fh)
    return logging.getLogger('sla_monitor')

def _ensure_csv_headers():
    if not LOG_FILE_CSV.exists():
        with open(LOG_FILE_CSV, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=CSV_HEADERS).writeheader()

def log_failure_event(pod_name, namespace, failure_type, root_cause, action_taken, status, details=''):
    _ensure_csv_headers()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    row = {'timestamp':timestamp,'pod_name':pod_name,'namespace':namespace,'failure_type':failure_type,'root_cause':root_cause,'action_taken':action_taken,'status':status,'details':details}
    with open(LOG_FILE_CSV, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=CSV_HEADERS).writerow(row)
    with open(LOG_FILE_JSON, 'a') as f:
        f.write(json.dumps(row) + '\n')
    logging.getLogger('sla_monitor.events').info(f"[EVENT] pod={pod_name} failure={failure_type} action={action_taken} status={status}")

def log_sla_violation(pod_name, namespace, metric, value, threshold):
    logging.getLogger('sla_monitor.sla').warning(f"[SLA VIOLATION] pod={pod_name} metric={metric} value={value:.2f} threshold={threshold:.2f}")
    event = {'timestamp':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'event_type':'sla_violation','pod_name':pod_name,'namespace':namespace,'metric':metric,'value':value,'threshold':threshold}
    with open(LOG_FILE_JSON, 'a') as f:
        f.write(json.dumps(event) + '\n')

def print_log_table():
    if not LOG_FILE_CSV.exists():
        print('No log entries yet.')
        return
    print('\n' + '='*110)
    print(f"{'TIMESTAMP':<20} {'POD NAME':<25} {'FAILURE TYPE':<20} {'ROOT CAUSE':<25} {'ACTION':<25} {'STATUS':<10}")
    print('='*110)
    with open(LOG_FILE_CSV, 'r') as f:
        for row in csv.DictReader(f):
            print(f"{row['timestamp']:<20} {row['pod_name']:<25} {row['failure_type']:<20} {row['root_cause']:<25} {row['action_taken']:<25} {row['status']:<10}")
    print('='*110 + '\n')
