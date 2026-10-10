"""프로세스 관리(procs.py) 단위 테스트. 가짜 실행기를 쓰므로 ros2, ssh 를 실행하지 않는다."""
import asyncio
import signal
import textwrap

import pytest

from backend.config import ProcSpec, load_config
from backend.mock_runner import MockRunner
from backend.procs import (EXITED, FAILED, RUNNING, STARTING, STOPPED, ProcError, ProcessManager, build_script,
                           detect_argv, remote_kill_argv, self_excluding, ssh_argv, start_argv)


def run(coro):
    return asyncio.run(coro)


def local(pid='a', **kw):
    d = dict(id=pid, label=pid, kind='local', command='sleep 100', settle_sec=0.1, stop_timeout_sec=0.2)
    d.update(kw)
    return ProcSpec(**d)


def remote(pid='r', **kw):
    d = dict(id=pid, label=pid, kind='ssh', command='ros2 launch x y.launch.xml', domain=20, source=('/opt/ros/jazzy/setup.bash',),
             detect='ros2 launch x', ssh_host='10.0.0.5', ssh_user='pinky', settle_sec=0.1, stop_timeout_sec=0.2,
             stop_on_exit=False, confirm_stop=True, robot='pinky1')
    d.update(kw)
    return ProcSpec(**d)


async def until(cond, sec=3.0):
    t = asyncio.get_running_loop().time() + sec
    while not cond():
        assert asyncio.get_running_loop().time() < t, '조건이 시간 안에 충족되지 않았다'
        await asyncio.sleep(0.02)


def state(m, pid):
    return next(p for p in m.status()['procs'] if p['id'] == pid)['state']


# ---- 명령 조립 ----
def test_build_script_and_local_argv():
    s = local(domain=50, source=('/opt/ros/jazzy/setup.bash', '/ws/my dir/setup.bash'), command='ros2 run a b')
    assert build_script(s) == "source /opt/ros/jazzy/setup.bash && source '/ws/my dir/setup.bash' && export ROS_DOMAIN_ID=50 && exec ros2 run a b"
    argv, env = start_argv(s)
    assert argv[:2] == ['bash', '-c'] and env == {}
    assert 'ROS_DOMAIN_ID' not in build_script(local())


def test_cwd_comes_first_and_is_quoted():
    s = local(cwd='/home/pinky/my ws', source=('/opt/ros/jazzy/setup.bash', './install/setup.bash'), domain=20, command='ros2 launch a b map:=x.yaml')
    assert build_script(s) == ("cd '/home/pinky/my ws' && source /opt/ros/jazzy/setup.bash && source ./install/setup.bash"
                               " && export ROS_DOMAIN_ID=20 && exec ros2 launch a b map:=x.yaml")
    assert build_script(local()).startswith('exec ') or 'cd ' not in build_script(local())
    inner = start_argv(remote(cwd='/home/pinky/ws'), environ={})[0][-1]
    assert "cd /home/pinky/ws && source" in inner


def test_ssh_start_argv_key_login():
    argv, env = start_argv(remote(ssh_key='/home/u/.ssh/k'), environ={})
    assert argv[0] == 'ssh' and '-tt' in argv and 'BatchMode=yes' in argv and 'StrictHostKeyChecking=yes' in argv
    assert env == {}
    assert argv[argv.index('-i') + 1] == '/home/u/.ssh/k'
    assert argv[argv.index('-p') + 1] == '22'
    assert 'pinky@10.0.0.5' in argv
    remote_cmd = argv[-1]
    assert remote_cmd.startswith('bash -c ') and 'export ROS_DOMAIN_ID=20' in remote_cmd and 'exec ros2 launch x y.launch.xml' in remote_cmd


def test_ssh_remote_command_is_quoted_as_one_argument():
    import shlex
    cmd = start_argv(remote(command="ros2 launch p l.xml name:='a b'"), environ={})[0][-1]
    inner = shlex.split(cmd)           # 원격 셸이 파싱하는 것과 같다
    assert inner[:2] == ['bash', '-c'] and len(inner) == 3 and "name:='a b'" in inner[2]


def test_ssh_password_mode_keeps_secret_out_of_argv():
    spec = remote(ssh_password_env='PINKY1_SSH_PASSWORD')
    argv, env = start_argv(spec, environ={'PINKY1_SSH_PASSWORD': 's3cret!'})
    assert argv[:2] == ['sshpass', '-e'] and env == {'SSHPASS': 's3cret!'}
    assert 'BatchMode=yes' not in argv
    assert not any('s3cret' in a for a in argv)
    # 환경 변수가 없으면 키 로그인으로 돌아간다
    argv2, env2 = start_argv(spec, environ={})
    assert argv2[0] == 'ssh' and 'BatchMode=yes' in argv2 and env2 == {}


def test_detect_pattern_excludes_itself():
    assert self_excluding('domain_bridge .*x') == '[d]omain_bridge .*x'
    assert detect_argv(local(detect='zone_manager_node'))[0] == ['pgrep', '-f', '--', '[z]one_manager_node']
    argv, _ = detect_argv(remote(), environ={})
    assert argv[-1] == "pgrep -f -- '[r]os2 launch x'" and '-tt' not in argv
    argv, _ = remote_kill_argv(remote(stop_pattern='pinky_nav'), 'INT', environ={})
    assert argv[-1] == "pkill -INT -f -- '[p]inky_nav'"


# ---- 시작 / 정지 ----
def test_start_run_stop():
    async def go():
        r = MockRunner(interval=0.03)
        m = ProcessManager([local()], r)
        await m.start('a')
        assert state(m, 'a') == STARTING
        await until(lambda: state(m, 'a') == RUNNING)
        await until(lambda: any('동작 중' in x for x in m.log_lines('a')))
        assert m.log_lines('a')[0].startswith('$ local:')
        await m.stop('a')
        assert state(m, 'a') == STOPPED
        assert [s for _, s in r.signals] == [signal.SIGINT]   # SIGINT 한 번으로 끝났다
        await m.shutdown()
    run(go())


def test_unknown_unset_and_busy():
    async def go():
        m = ProcessManager([local(), local('b', command='')], MockRunner(interval=0.03))
        for fn in (lambda: m.start('zzz'), lambda: m.stop('zzz'), lambda: m.start('b')):
            with pytest.raises(ProcError) as e:
                await fn()
        with pytest.raises(ProcError) as e:
            await m.start('b')
        assert e.value.code == 'unset'
        assert next(p for p in m.status()['procs'] if p['id'] == 'b')['configured'] is False
        await m.start('a')
        with pytest.raises(ProcError) as e:
            await m.start('a')
        assert e.value.code == 'busy'
        with pytest.raises(ProcError):
            await m.stop('b')    # 실행 중이 아님
        await m.shutdown()
    run(go())


def test_duplicate_process_is_not_started():
    async def go():
        r = MockRunner(interval=0.03, external=['a'])
        m = ProcessManager([local(detect='abc')], r)
        with pytest.raises(ProcError) as e:
            await m.start('a')
        assert e.value.code == 'external' and '12345' in str(e.value)
        assert not [c for c in r.calls if c[0] == 'spawn']
        assert state(m, 'a') == STOPPED
    run(go())


def test_detect_failure_refuses_to_start():
    async def go():
        r = MockRunner(interval=0.03, detect_fail=['r'])
        m = ProcessManager([remote()], r)
        with pytest.raises(ProcError) as e:
            await m.start('r')
        assert '확인하지 못했습니다' in str(e.value) and 'No route' in str(e.value)
        assert state(m, 'r') == FAILED and not [c for c in r.calls if c[0] == 'spawn']
    run(go())


def test_immediate_exit_is_failed_with_reason():
    async def go():
        m = ProcessManager([local(settle_sec=1.0)], MockRunner(interval=0.03, fail_start=['a']))
        await m.start('a')
        await until(lambda: state(m, 'a') == FAILED)
        msg = m.status()['procs'][0]['message']
        assert '시작 직후 종료' in msg and '초기화 실패' in msg   # 마지막 로그 줄이 사유로 나온다
        assert m.status()['procs'][0]['exit_code'] == 1
        await m.start('a')    # 실패한 뒤에는 다시 시작할 수 있다
        await m.shutdown()
    run(go())


def test_unexpected_exit_while_running():
    async def go():
        m = ProcessManager([local(settle_sec=0.05)], MockRunner(interval=0.03, die_after={'a': 0.2}))
        await m.start('a')
        await until(lambda: state(m, 'a') == RUNNING)
        await until(lambda: state(m, 'a') == FAILED)
        assert '예기치 않게 종료' in m.status()['procs'][0]['message']
    run(go())


def test_stop_escalates_when_sigint_ignored():
    async def go():
        r = MockRunner(interval=0.03, ignore_sigint=['a'])
        m = ProcessManager([local()], r)
        await m.start('a')
        await until(lambda: state(m, 'a') == RUNNING)
        await m.stop('a')
        assert [s for _, s in r.signals] == [signal.SIGINT, signal.SIGTERM]
        assert state(m, 'a') == STOPPED
    run(go())


def test_stop_requires_confirm_for_robot_processes():
    async def go():
        r = MockRunner(interval=0.03)
        m = ProcessManager([remote()], r)
        await m.start('r')
        with pytest.raises(ProcError) as e:
            await m.stop('r')
        assert e.value.code == 'confirm' and state(m, 'r') in (STARTING, RUNNING)
        await m.stop('r', confirm=True)
        assert state(m, 'r') == STOPPED
        assert r.signals == []   # SSH 는 시그널이 아니라 Ctrl-C(\x03) 로 정지한다
    run(go())


def test_ssh_stop_reports_remote_still_running():
    class StillThere(MockRunner):
        async def capture(self, argv, env=None, timeout=10.0, tag=None):
            self.calls.append(('capture', list(argv), tag))
            if 'pkill' in argv[-1]:
                return 0, ''
            return (1, '') if not [c for c in self.calls if c[0] == 'spawn'] else (0, '777\n')   # 시작 전엔 없음, 정지 뒤엔 남아 있음

    async def go():
        m = ProcessManager([remote()], StillThere(interval=0.03))
        await m.start('r')
        with pytest.raises(ProcError) as e:
            await m.stop('r', confirm=True)
        assert '로봇에서 아직 실행 중' in str(e.value) and '777' in str(e.value)
        assert state(m, 'r') == FAILED
    run(go())


def test_ssh_stop_falls_back_to_remote_pkill_then_signal():
    async def go():
        r = MockRunner(interval=0.03, ignore_sigint=['r'])
        m = ProcessManager([remote()], r)
        await m.start('r')
        await m.stop('r', confirm=True)
        assert any('pkill' in c[1][-1] for c in r.calls if c[0] == 'capture')    # Ctrl-C 가 안 먹으면 원격 pkill
        assert [s for _, s in r.signals] == [signal.SIGTERM]                    # 그래도 안 되면 SSH 를 끊는다
        assert state(m, 'r') == STOPPED
    run(go())


# ---- 전체 시작 / 정지 ----
def specs3():
    return [local('a'), local('b'), local('c', command='')]


def test_start_all_in_order_skips_unset():
    async def go():
        r = MockRunner(interval=0.03)
        m = ProcessManager(specs3(), r)
        m.start_all()
        with pytest.raises(ProcError) as e:
            m.start_all()
        assert e.value.code == 'busy'
        await until(lambda: m.status()['sequence']['state'] != 'running')
        seq = m.status()['sequence']
        assert seq['state'] == 'done'
        assert [(x['id'], x['result']) for x in seq['results']] == [('a', 'started'), ('b', 'started'), ('c', 'unset')]
        assert [c[2] for c in r.calls if c[0] == 'spawn'] == ['a', 'b']
        assert state(m, 'a') == state(m, 'b') == RUNNING
        m.stop_all()
        await until(lambda: m.status()['sequence']['state'] != 'running')
        seq = m.status()['sequence']
        assert [(x['id'], x['result']) for x in seq['results']] == [('b', 'stopped'), ('a', 'stopped')]   # 반대 순서
        await m.shutdown()
    run(go())


def test_start_all_stops_at_first_failure():
    async def go():
        r = MockRunner(interval=0.03, fail_start=['a'])
        m = ProcessManager([local('a', settle_sec=1.0), local('b'), local('c', command='')], r)
        m.start_all()
        await until(lambda: m.status()['sequence']['state'] != 'running')
        seq = m.status()['sequence']
        assert seq['state'] == 'failed' and 'a' in seq['message']
        assert [c[2] for c in r.calls if c[0] == 'spawn'] == ['a']      # 뒤 단계는 시작하지 않는다
    run(go())


def test_start_all_treats_external_as_already_running():
    async def go():
        r = MockRunner(interval=0.03, external=['a'])
        m = ProcessManager([local('a', detect='abc'), local('b')], r)
        m.start_all()
        await until(lambda: m.status()['sequence']['state'] != 'running')
        seq = m.status()['sequence']
        assert seq['state'] == 'done' and seq['results'][0] == {'id': 'a', 'result': 'external'}
        assert state(m, 'b') == RUNNING
        await m.shutdown()
    run(go())


def test_stop_all_needs_confirm_when_robot_processes_run():
    async def go():
        m = ProcessManager([local('a'), remote('r')], MockRunner(interval=0.03))
        await m.start('a')
        m.stop_all()       # 확인이 필요한 프로세스가 없으면 바로 진행
        await until(lambda: m.status()['sequence']['state'] != 'running')
        await m.start('r')
        with pytest.raises(ProcError) as e:
            m.stop_all()
        assert e.value.code == 'confirm'
        m.stop_all(confirm=True)
        await until(lambda: m.status()['sequence']['state'] != 'running')
        assert state(m, 'r') == STOPPED
    run(go())


# ---- 종료 / 상태 ----
def test_shutdown_stops_robot_processes_too_unless_opted_out():
    async def go():
        r = MockRunner(interval=0.03)
        m = ProcessManager([local('a'), remote('r', stop_on_exit=True), remote('keep', stop_on_exit=False)], r)
        for pid in ('a', 'r', 'keep'):
            await m.start(pid)
        await m.shutdown()
        assert state(m, 'a') == STOPPED
        assert state(m, 'r') == STOPPED                  # 로봇 쪽(SSH)도 서버 종료 때 정지한다
        assert state(m, 'keep') in (STARTING, RUNNING)   # stop_on_exit: false 로 둔 것만 남긴다
    run(go())


def test_shutdown_stops_during_start_all_sequence():
    async def go():
        m = ProcessManager([local('a'), local('b', settle_sec=5.0)], MockRunner(interval=0.03))
        m.start_all()
        await until(lambda: state(m, 'a') == RUNNING and state(m, 'b') == STARTING)
        await m.shutdown()                               # 시작 시퀀스 도중에 종료해도 켜진 것은 모두 정지
        assert state(m, 'a') == STOPPED and state(m, 'b') == STOPPED
    run(go())


def test_status_health_and_masking():
    async def go():
        m = ProcessManager([local('a', robot='pinky1', health=True), local('b')], MockRunner(),
                           health=lambda robot: robot == 'pinky1', environ={'PW': 'topsecret'})
        m._secrets = ['topsecret']
        p = m.status()['procs']
        assert p[0]['health'] is True and p[1]['health'] is None
        e = m._e['a']
        m._add_log(e, '\x1b[31mpassword is topsecret\x1b[0m')
        assert m.log_lines('a') == ['password is ***']
    run(go())


def test_on_change_called():
    async def go():
        n = []
        m = ProcessManager([local()], MockRunner(interval=0.03), on_change=lambda: n.append(1))
        await m.start('a')
        await until(lambda: state(m, 'a') == RUNNING)
        assert len(n) >= 2
        await m.shutdown()
    run(go())


# ---- 실제 subprocess (무해한 명령만) ----
def test_real_subprocess_start_stop_kills_whole_group(tmp_path):
    marker = tmp_path / 'child.pid'
    async def go():
        cmd = f"bash -c 'echo $$ > {marker}; sleep 60' & echo started; wait"
        m = ProcessManager([local(command=cmd, settle_sec=0.3, stop_timeout_sec=2.0)])
        await m.start('a')
        await until(lambda: state(m, 'a') == RUNNING)
        await until(lambda: marker.exists() and marker.read_text().strip())
        child = int(marker.read_text())
        import os
        os.kill(child, 0)                # 자식이 살아 있다
        await m.stop('a')
        assert state(m, 'a') == STOPPED
        await asyncio.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)            # 프로세스 그룹 전체가 정리됐다
        assert any('started' in x for x in m.log_lines('a'))
        await m.shutdown()
    run(go())


def test_real_subprocess_exit_codes():
    async def go():
        m = ProcessManager([local('bad', command="bash -c 'echo boom >&2; exit 3'", settle_sec=0.5),
                            local('ok', command="sleep 0.3", settle_sec=0.05),
                            local('quick', command="echo hi", settle_sec=1.0)])
        await m.start('bad')
        await until(lambda: state(m, 'bad') == FAILED)
        p = m.status()['procs'][0]
        assert p['exit_code'] == 3 and 'boom' in p['message']
        await m.start('ok')
        await until(lambda: state(m, 'ok') in (EXITED, RUNNING))
        await until(lambda: state(m, 'ok') == EXITED)    # 정상 실행 중에 코드 0 으로 끝나면 exited
        await m.start('quick')                           # 시작 직후 끝나면 (코드 0 이어도) 시작 실패로 본다
        await until(lambda: state(m, 'quick') == FAILED)
        assert '시작 직후 종료' in m.status()['procs'][2]['message']
        await m.shutdown()
    run(go())


# ---- 설정 검증 ----
def write_cfg(tmp_path, body):
    (tmp_path / 'processes.yaml').write_text(textwrap.dedent(body), encoding='utf-8')
    src = (load_config.__globals__['DEFAULT_CONFIG']).read_text(encoding='utf-8')
    (tmp_path / 'points.yaml').write_text((load_config.__globals__['DEFAULT_CONFIG'].parent / 'points.yaml').read_text(encoding='utf-8'))
    (tmp_path / 'zone.yaml').write_text((load_config.__globals__['DEFAULT_CONFIG'].parent / 'zone.yaml').read_text(encoding='utf-8'))
    (tmp_path / 'robots.yaml').write_text(src.replace('map_yaml: ../../map_view_pc', f'map_yaml: {load_config.__globals__["DEFAULT_CONFIG"].parent}/../../map_view_pc'), encoding='utf-8')
    return tmp_path / 'robots.yaml'


@pytest.mark.parametrize('body,msg', [
    ('processes: []', '비어 있지 않은'),
    ('processes: [{id: A, kind: local, command: x}]', 'id'),
    ('processes: [{id: a, kind: local, command: x}, {id: a, kind: local, command: x}]', '중복'),
    ('processes: [{id: a, kind: telnet, command: x}]', 'kind'),
    ('processes: [{id: a, kind: local, command: "x {oops}"}]', '치환'),
    ('processes: [{id: a, kind: local, command: x, robot: pinky9}]', 'robot'),
    ('processes: [{id: a, kind: local, command: x, health: true}]', 'robot'),
    ('processes: [{id: a, kind: ssh, command: x}]', 'host'),
    ('processes: [{id: a, kind: ssh, command: x, ssh: {host: "-oProxy", user: u}}]', '허용되지 않는'),
    ('processes: [{id: a, kind: ssh, command: x, ssh: {host: h, user: u, password_env: "hunter2 value"}}]', 'password_env'),
    ('processes: [{id: a, kind: local, command: x, detect: "(x"}]', 'detect'),
    ('processes: [{id: a, kind: local, command: x, domain: 999}]', 'domain'),
    ('processes: [{id: a, kind: local, command: x, cwd: "~/ws"}]', '절대 경로'),
    ('processes: [{id: a, kind: local, command: x, cwd: "ws/rel"}]', '절대 경로'),
    ('processes: [{id: a, kind: local, command: x, cwd: 5}]', 'cwd'),
])
def test_config_rejects_bad_processes(tmp_path, body, msg):
    path = write_cfg(tmp_path, body)
    with pytest.raises(ValueError) as e:
        load_config(path)
    assert msg in str(e.value)


def test_default_config_processes():
    cfg = load_config()
    ids = [p.id for p in cfg.processes]
    assert ids[0] == 'zone_manager' and ids[-1:] == ['bridge']   # 브릿지는 bringup 뒤 (QoS 때문)
    by = {p.id: p for p in cfg.processes}
    assert by['bridge'].command == 'ros2 launch launch/bridges.launch.xml'   # pinky1, pinky2 브릿지를 launch 하나로
    assert by['bridge'].cwd.startswith('/') and not by['bridge'].cwd.startswith('/home/pinky') and '{repo}' not in by['bridge'].cwd   # PC 의 저장소 경로
    b1, m1 = by['bringup_pinky1'], by['map_pinky1']
    assert b1.command == 'ros2 launch pinky_bringup bringup_robot.launch.xml' and b1.confirm_stop and b1.stop_on_exit   # 서버 종료 시 로봇 쪽도 정지
    assert m1.command == 'ros2 launch pinky_navigation bringup_launch.xml map:=my_pinky_map10.yaml'
    assert (b1.ssh_host, b1.ssh_user, b1.domain) == ('192.168.45.20', 'pinky', 20) and b1.cwd == m1.cwd
    assert b1.source[-1] == '/home/pinky/pinky_slam_nav/install/setup.bash' and b1.source[0].startswith('/opt/ros/') and b1.cwd == '/home/pinky'
    pt = by['patrol_pinky1']
    assert pt.command == 'ros2 run my_pinky_package pinky_patrol_node_pinky1_v2' and pt.cwd == '/home/pinky/pinky_slam_nav'
    assert pt.source[-1] == './install/setup.bash' and pt.domain == 20 and pt.confirm_stop and pt.stop_on_exit
    ids = [p.id for p in cfg.processes]
    assert ids.index('bringup_pinky1') < ids.index('map_pinky1') < ids.index('patrol_pinky1')   # 전체 시작 순서
    b2, m2 = by['bringup_pinky2'], by['map_pinky2']
    assert b2.command == b1.command and m2.command == m1.command and b2.domain == 22 and b2.ssh_host == '192.168.45.22'
    assert dict(b2.env)['RMW_IMPLEMENTATION'] == 'rmw_cyclonedds_cpp' and ids.index('bringup_pinky2') < ids.index('map_pinky2') < ids.index('patrol_pinky2')
    assert by['zone_manager'].stop_on_exit


def test_group_is_read_from_config(tmp_path):
    from backend.config import _load_processes
    f = tmp_path / 'p.yaml'
    f.write_text("""processes:
  - {id: a, label: A, kind: local, command: "echo a", group: "묶음 1"}
  - {id: b, kind: local, command: "echo b"}
""", encoding='utf-8')
    assert [s.group for s in _load_processes(f, ())] == ['묶음 1', '']


def test_env_is_exported_before_domain_and_quoted():
    from backend.procs import build_script
    spec = ProcSpec('a', 'a', 'ssh', 'ros2 run x y', 20, (), '/home/pinky', env=(('RMW_IMPLEMENTATION', 'rmw_cyclonedds_cpp'), ('X', 'a b; c')))
    script = build_script(spec)
    assert script == "cd /home/pinky && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export X='a b; c' && export ROS_DOMAIN_ID=20 && exec ros2 run x y"


def test_default_config_pinky1_robot_env():
    by = {p.id: p for p in load_config().processes}
    for pid in ('bringup_pinky1', 'map_pinky1', 'patrol_pinky1'):
        assert dict(by[pid].env)['RMW_IMPLEMENTATION'] == 'rmw_cyclonedds_cpp' and 'cyclonedds_robot.xml' in dict(by[pid].env)['CYCLONEDDS_URI']


@pytest.mark.parametrize('body,msg', [
    ('processes: [{id: a, kind: local, command: x, env: {ROS_DOMAIN_ID: "1"}}]', 'ROS_DOMAIN_ID'),
    ('processes: [{id: a, kind: local, command: x, env: {bad-name: "1"}}]', 'env'),
    ('processes: [{id: a, kind: local, command: x, env: [1]}]', 'env'),
])
def test_config_rejects_bad_env(tmp_path, body, msg):
    with pytest.raises(ValueError) as e:
        load_config(write_cfg(tmp_path, body))
    assert msg in str(e.value)


def test_sweep_kills_children_left_after_leader_exits():
    """ros2 launch(리더)가 먼저 끝나도 같은 그룹에 남은 자식(domain_bridge)을 정리한다 (서버 종료 때 브릿지가 남던 문제)."""
    import asyncio
    import os
    from backend.procs import ProcessManager, SystemRunner

    async def scenario():
        runner = SystemRunner()
        p = await runner.spawn(['bash', '-c', 'sleep 300 >/dev/null 2>&1 & echo $!; exit 0'])
        child = int((await p.stdout.readline()).decode().strip())
        await p.wait()
        assert runner.group_alive(p.pid)            # 리더는 끝났지만 자식이 남아 있다
        pm = ProcessManager([local()], runner)
        await pm._sweep_group(p.pid)
        assert not runner.group_alive(p.pid)
        try:
            os.kill(child, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        return alive

    assert asyncio.run(scenario()) is False
