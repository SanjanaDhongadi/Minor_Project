import subprocess, json, logging
from typing import Optional

logger = logging.getLogger(__name__)

def run_kubectl(args, capture_output=True):
    cmd = ['kubectl'] + args
    try:
        result = subprocess.run(cmd, capture_output=capture_output, text=True, timeout=30)
        success = result.returncode == 0
        parsed_json = None
        if success and result.stdout.strip() and '-o' in args and 'json' in args:
            try:
                parsed_json = json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        return {'success': success, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip(), 'json': parsed_json}
    except subprocess.TimeoutExpired:
        return {'success': False, 'stdout': '', 'stderr': 'Timeout', 'json': None}
    except FileNotFoundError:
        return {'success': False, 'stdout': '', 'stderr': 'kubectl not found', 'json': None}

def get_all_pods(namespace='default'):
    result = run_kubectl(['get', 'pods', '-n', namespace, '-o', 'json'])
    if not result['success'] or not result['json']:
        return []
    pods = []
    for item in result['json'].get('items', []):
        metadata = item.get('metadata', {})
        status = item.get('status', {})
        spec = item.get('spec', {})
        container_statuses = status.get('containerStatuses', [])
        containers_info = []
        for cs in container_statuses:
            waiting = cs.get('state', {}).get('waiting', {})
            last_state = cs.get('lastState', {})
            terminated = last_state.get('terminated', {})
            containers_info.append({'name': cs.get('name'), 'ready': cs.get('ready', False), 'restart_count': cs.get('restartCount', 0), 'image': cs.get('image'), 'reason': waiting.get('reason', '') or terminated.get('reason', ''), 'state': list(cs.get('state', {}).keys())[0] if cs.get('state') else 'unknown'})
        phase = status.get('phase', 'Unknown')
        conditions = status.get('conditions', [])
        ready_condition = next((c for c in conditions if c.get('type') == 'Ready'), {})
        pods.append({'name': metadata.get('name'), 'namespace': metadata.get('namespace', namespace), 'labels': metadata.get('labels', {}), 'phase': phase, 'ready': ready_condition.get('status') == 'True', 'containers': containers_info, 'restart_count': sum(c['restart_count'] for c in containers_info), 'node': spec.get('nodeName', 'unknown'), 'start_time': status.get('startTime'), 'pod_ip': status.get('podIP'), 'conditions': conditions, 'raw': item})
    return pods

def get_pod_logs(pod_name, namespace='default', tail_lines=50, previous=False):
    args = ['logs', pod_name, '-n', namespace, f'--tail={tail_lines}']
    if previous:
        args.append('--previous')
    result = run_kubectl(args)
    return result['stdout'] if result['success'] else result['stderr']

def get_pod_events(pod_name, namespace='default'):
    result = run_kubectl(['get', 'events', '-n', namespace, '--field-selector', f'involvedObject.name={pod_name}', '-o', 'json'])
    if not result['success'] or not result['json']:
        return []
    return [{'type': i.get('type'), 'reason': i.get('reason'), 'message': i.get('message'), 'count': i.get('count', 1), 'first_time': i.get('firstTimestamp'), 'last_time': i.get('lastTimestamp')} for i in result['json'].get('items', [])]

def get_deployment_for_pod(pod_name, namespace='default'):
    result = run_kubectl(['get', 'pod', pod_name, '-n', namespace, '-o', 'json'])
    if not result['success'] or not result['json']:
        return None
    owner_refs = result['json'].get('metadata', {}).get('ownerReferences', [])
    rs_name = next((r.get('name') for r in owner_refs if r.get('kind') == 'ReplicaSet'), None)
    if not rs_name:
        return None
    rs_result = run_kubectl(['get', 'replicaset', rs_name, '-n', namespace, '-o', 'json'])
    if not rs_result['success'] or not rs_result['json']:
        return None
    rs_owners = rs_result['json'].get('metadata', {}).get('ownerReferences', [])
    return next((r.get('name') for r in rs_owners if r.get('kind') == 'Deployment'), None)

def restart_pod(pod_name, namespace='default'):
    logger.info(f'Restarting pod: {pod_name}')
    result = run_kubectl(['delete', 'pod', pod_name, '-n', namespace, '--grace-period=0', '--force'])
    return result['success']

def rollout_restart_deployment(deployment_name, namespace='default'):
    logger.info(f'Rolling restart: {deployment_name}')
    result = run_kubectl(['rollout', 'restart', 'deployment', deployment_name, '-n', namespace])
    return result['success']

def get_pod_metrics(namespace='default'):
    result = run_kubectl(['top', 'pods', '-n', namespace, '--no-headers'])
    if not result['success']:
        return []
    metrics = []
    for line in result['stdout'].splitlines():
        parts = line.split()
        if len(parts) >= 3:
            metrics.append({'name': parts[0], 'cpu': parts[1], 'memory': parts[2]})
    return metrics

def check_kubectl_available():
    result = run_kubectl(['cluster-info'])
    return result['success']
