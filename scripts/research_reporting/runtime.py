"""Read-only runtime observations, independent from the evaluator."""
from __future__ import annotations

import re
import subprocess
import time


class HealthError(RuntimeError):
    """Sanitized inability to verify the evaluator's runtime health."""


def _properties(response, expected: set[str]) -> dict[str, str]:
    if type(response.returncode) is not int or response.returncode != 0 or not isinstance(response.stdout, str):
        raise ValueError
    values = {}
    for line in response.stdout.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key in values:
            raise ValueError
        values[key] = value
    if set(values) != expected:
        raise ValueError
    return values


def collect_health(*, runner=None, monotonic_ns=None) -> dict:
    """Read fixed units and clocks on this host; never issue control commands."""
    execute = subprocess.run if runner is None else runner
    clock = time.monotonic_ns if monotonic_ns is None else monotonic_ns
    try:
        service = execute(
            ['systemctl', '--user', 'show', 't2-evaluation.service',
             '--property=ActiveState,Result,ExecMainStatus,ExecMainStartTimestampMonotonic'],
            capture_output=True, text=True, timeout=15, check=False,
        )
        timer = execute(
            ['systemctl', '--user', 'show', 't2-evaluation.timer', '--property=ActiveState'],
            capture_output=True, text=True, timeout=15, check=False,
        )
        values = _properties(service, {'ActiveState', 'Result', 'ExecMainStatus', 'ExecMainStartTimestampMonotonic'})
        timer_values = _properties(timer, {'ActiveState'})
        states = {'active', 'inactive', 'activating', 'deactivating', 'reloading', 'failed', 'maintenance', 'refreshing'}
        if values['ActiveState'] not in states or timer_values['ActiveState'] not in states or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', values['Result']) is None:
            raise ValueError
        started = int(values['ExecMainStartTimestampMonotonic'])
        exit_status = int(values['ExecMainStatus'])
        instant = clock()
        if type(instant) is not int or instant < 0 or started < 0 or exit_status < 0 or started > instant // 1000:
            raise ValueError
        elapsed = None if started == 0 else (instant // 1000 - started) / 1_000_000
        return {'timer_active': timer_values['ActiveState'] == 'active',
                'service_active_state': values['ActiveState'],
                'service_result': values['Result'],
                'service_exit_status': exit_status,
                'service_elapsed_seconds': elapsed}
    except Exception:
        raise HealthError('health_unavailable') from None
