"""Read-only systemd observation at its external subprocess seam."""
import subprocess
import pytest

from research_reporting.runtime import collect_health


def test_health_reads_selected_units_and_uses_same_host_monotonic_clock():
    calls = []
    def systemd(argv, **kwargs):
        calls.append((argv, kwargs))
        text = ('Result=success\nExecMainStartTimestampMonotonic=1000000\n'
                'ExecMainStatus=0\nActiveState=inactive\n') if argv[3].endswith('.service') else 'ActiveState=active\n'
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr='')
    actual = collect_health(runner=systemd, monotonic_ns=lambda: 5_000_000_000)
    assert actual == {'timer_active': True, 'service_active_state': 'inactive',
                      'service_result': 'success', 'service_exit_status': 0,
                      'service_elapsed_seconds': 4.0}
    assert [row[0] for row in calls] == [
        ['systemctl', '--user', 'show', 't2-evaluation.service', '--property=ActiveState,Result,ExecMainStatus,ExecMainStartTimestampMonotonic'],
        ['systemctl', '--user', 'show', 't2-evaluation.timer', '--property=ActiveState'],
    ]
    assert all(row[1] == {'capture_output': True, 'text': True, 'timeout': 15, 'check': False} for row in calls)


def test_never_started_service_has_no_elapsed_runtime():
    def systemd(argv, **kwargs):
        text = ('Result=success\nExecMainStartTimestampMonotonic=0\nExecMainStatus=0\nActiveState=inactive\n') if argv[3].endswith('.service') else 'ActiveState=active\n'
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr='')
    actual = collect_health(runner=systemd, monotonic_ns=lambda: 5_000_000_000)
    assert actual['service_elapsed_seconds'] is None


@pytest.mark.parametrize('response', [
    subprocess.CompletedProcess([], 1, stdout='PRIVATE_UPSTREAM_BODY', stderr='PRIVATE_ERROR'),
    subprocess.CompletedProcess([], 0, stdout=None, stderr=''),
    subprocess.CompletedProcess([], 0, stdout='malformed', stderr=''),
    subprocess.CompletedProcess([], 0, stdout='ActiveState=inactive\n', stderr=''),
    subprocess.CompletedProcess([], 0, stdout='Result=success\nExecMainStartTimestampMonotonic=1000000\nExecMainStatus=0\nActiveState=inactive\nActiveState=failed\n', stderr=''),
    subprocess.CompletedProcess([], 0, stdout='Result=success\nExecMainStartTimestampMonotonic=1000000\nExecMainStatus=bad\nActiveState=inactive\n', stderr=''),
])
def test_unavailable_or_invalid_systemd_evidence_fails_with_sanitized_public_error(response):
    def systemd(argv, **kwargs):
        return response
    with pytest.raises(RuntimeError, match='health_unavailable') as caught:
        collect_health(runner=systemd, monotonic_ns=lambda: 5_000_000_000)
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('changes,instant,timer', [
    ({'ActiveState': 'unexpected'}, 5_000_000_000, 'active'),
    ({'Result': 'PRIVATE ERROR'}, 5_000_000_000, 'active'),
    ({}, 5_000_000_000, 'unexpected'),
    ({'ExecMainStartTimestampMonotonic': '-1'}, 5_000_000_000, 'active'),
    ({'ExecMainStatus': '-1'}, 5_000_000_000, 'active'),
    ({}, -1, 'active'), ({}, True, 'active'),
    ({}, 999_999_999, 'active'),
])
def test_invalid_runtime_states_or_clock_regression_are_unavailable(changes, instant, timer):
    fields = {'Result':'success', 'ExecMainStartTimestampMonotonic':'1000000', 'ExecMainStatus':'0', 'ActiveState':'inactive'}
    fields.update(changes)
    def systemd(argv, **kwargs):
        text = ''.join(key+'='+value+'\n' for key,value in fields.items()) if argv[3].endswith('.service') else 'ActiveState='+timer+'\n'
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr='')
    with pytest.raises(RuntimeError, match='health_unavailable'):
        collect_health(runner=systemd, monotonic_ns=lambda: instant)


@pytest.mark.parametrize('instant,elapsed', [(1_000_000_000, 0.0), (1_000_001_000, 0.000001)])
def test_monotonic_start_equality_and_following_microsecond(instant, elapsed):
    def systemd(argv, **kwargs):
        text = 'Result=success\nExecMainStartTimestampMonotonic=1000000\nExecMainStatus=0\nActiveState=activating\n' if argv[3].endswith('.service') else 'ActiveState=active\n'
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr='')
    assert collect_health(runner=systemd, monotonic_ns=lambda: instant)['service_elapsed_seconds'] == elapsed


def test_default_subprocess_and_clock_use_real_external_executable(tmp_path, monkeypatch):
    import os
    fake = tmp_path/'systemctl'
    fake.write_text('''#!/usr/bin/env python3
import sys
print('Result=success\\nExecMainStartTimestampMonotonic=0\\nExecMainStatus=0\\nActiveState=inactive' if sys.argv[3].endswith('.service') else 'ActiveState=active')
''')
    fake.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path)+os.pathsep+os.environ['PATH'])
    assert collect_health()['service_elapsed_seconds'] is None


@pytest.mark.parametrize('failure', [FileNotFoundError('PRIVATE'), subprocess.TimeoutExpired(['systemctl'], 15)])
def test_runtime_transport_failures_are_sanitized(failure):
    def systemd(argv, **kwargs):
        raise failure
    with pytest.raises(RuntimeError, match='health_unavailable') as caught:
        collect_health(runner=systemd)
    assert 'PRIVATE' not in str(caught.value)
