import yaml, logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'sla_config.yaml'

class SLAConfig:
    def __init__(self, data):
        self._data = data
    @property
    def min_uptime_percent(self): return self._data['sla']['min_uptime_percent']
    @property
    def max_response_time_ms(self): return self._data['sla']['max_response_time_ms']
    @property
    def max_error_rate_percent(self): return self._data['sla']['max_error_rate_percent']
    @property
    def max_cpu_percent(self): return self._data['sla']['max_cpu_percent']
    @property
    def max_memory_percent(self): return self._data['sla']['max_memory_percent']
    @property
    def max_restart_count(self): return self._data['sla']['max_restart_count']
    @property
    def post_recovery_wait_seconds(self): return self._data['sla']['post_recovery_wait_seconds']
    @property
    def namespaces(self):
        ns = self._data['monitoring'].get('namespaces', [])
        return ns if ns else ['default']
    @property
    def poll_interval_seconds(self): return self._data['monitoring']['poll_interval_seconds']
    @property
    def history_window(self): return self._data['monitoring']['history_window']
    @property
    def recovery_actions(self): return self._data['recovery']['actions']
    @property
    def max_recovery_attempts(self): return self._data['recovery']['max_recovery_attempts']
    def get(self, key, default=None): return self._data.get(key, default)

def load_config(path=DEFAULT_CONFIG_PATH):
    if not Path(path).exists():
        raise FileNotFoundError(f'Config file not found: {path}')
    with open(path, 'r', encoding='utf-8-sig') as f:
        data = yaml.safe_load(f)
    return SLAConfig(data)
