"""GUI 에서 켜고 끄는 프로세스 관리: 도메인 브릿지, zone_manager (관제 PC) 와 로봇의 bringup, map (SSH).

설계 원칙
  - 설정(config/processes.yaml)에 적힌 프로세스만 실행한다. 브라우저가 보낸 문자열은 id 로만 쓰고 명령으로는 절대 쓰지 않는다.
  - 비밀번호는 코드·설정·로그에 두지 않는다. SSH 는 키 로그인(BatchMode)이 기본이고, 설정이 환경 변수 '이름' 을 가리키면 sshpass -e 로 쓴다.
  - 정지는 SIGINT -> SIGTERM -> SIGKILL 순서로, 프로세스 그룹 전체에 보낸다 (ros2 launch 는 자식을 여럿 띄운다).
  - 시작 전에 같은 프로세스가 이미 도는지 확인한다 (터미널에서 직접 켠 것과 겹치지 않도록). 그런 프로세스는 GUI 가 정지시킬 수 없다.
  - rclpy 에 의존하지 않는다. 프로세스를 만드는 일(runner)과 시계는 주입받는다 (이벤트 루프 스레드에서만 호출한다).
"""
import asyncio
import collections
import logging
import os
import re
import shlex
import shutil
import signal
import time

log = logging.getLogger('backend.procs')

LOG_LINES = 200       # 프로세스당 보관하는 로그 줄 수
MAX_LINE = 500        # 한 줄 최대 길이
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]')

STOPPED, STARTING, RUNNING, STOPPING, FAILED, EXITED = 'stopped', 'starting', 'running', 'stopping', 'failed', 'exited'
ACTIVE = (STARTING, RUNNING, STOPPING)


class ProcError(Exception):
    """사용자에게 그대로 보여줄 수 있는 거절 사유. code: 'unknown' | 'unset' | 'busy' | 'external' | 'confirm' | 'fail'."""

    def __init__(self, message, code='fail'):
        super().__init__(message)
        self.code = code


# ---- 명령 조립 (순수 함수: 테스트에서 직접 확인한다) ----
def build_script(spec):
    """cd -> source 들 -> env -> ROS_DOMAIN_ID -> exec 명령. exec 로 셸을 명령으로 바꿔서 시그널이 명령에 직접 간다.

    cd 가 맨 앞이라 source ./install/setup.bash 와 map:=x.yaml 같은 상대 경로가 cwd 기준으로 풀린다.
    SSH 의 비대화형 셸은 ~/.bashrc 를 읽지 않으므로, 로봇의 ROS 환경은 source 에 명시해야 한다.
    """
    parts = [f'cd {shlex.quote(spec.cwd)}'] if spec.cwd else []
    parts += [f'source {shlex.quote(f)}' for f in spec.source]
    parts += [f'export {k}={shlex.quote(v)}' for k, v in spec.env]   # SSH 는 ~/.bashrc 를 안 읽으므로 RMW/DDS 설정을 여기서 준다
    if spec.domain is not None:
        parts.append(f'export ROS_DOMAIN_ID={int(spec.domain)}')
    parts.append(f'exec {spec.command}')
    return ' && '.join(parts)


def self_excluding(pattern):
    """pgrep -f 가 자기 자신(원격 셸의 명령줄에 패턴이 들어 있다)을 찾지 않도록 첫 글자를 [x] 로 감싼다."""
    return f'[{pattern[0]}]{pattern[1:]}' if pattern else pattern


def ssh_argv(spec, remote, tty=False, environ=None):
    """(argv, env 추가분). 키 로그인이 기본(BatchMode=yes). password_env 가 설정돼 있고 그 환경 변수가 있으면 sshpass -e 를 쓴다."""
    environ = os.environ if environ is None else environ
    pw = environ.get(spec.ssh_password_env) if spec.ssh_password_env else None
    argv = ['ssh']
    if tty:
        argv.append('-tt')  # pty 를 잡으면 stdin 으로 Ctrl-C(\x03) 를 보내 원격 프로세스에 SIGINT 를 줄 수 있다
    argv += ['-o', 'StrictHostKeyChecking=yes',   # 처음 보는 호스트에서 물어보며 멈추지 않게 (먼저 터미널에서 한 번 접속해 등록한다)
             '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3']
    if pw is None:
        argv += ['-o', 'BatchMode=yes']
    if spec.ssh_key:
        argv += ['-i', spec.ssh_key, '-o', 'IdentitiesOnly=yes']
    argv += ['-p', str(spec.ssh_port), f'{spec.ssh_user}@{spec.ssh_host}', remote]
    if pw is None:
        return argv, {}
    return ['sshpass', '-e'] + argv, {'SSHPASS': pw}


def start_argv(spec, environ=None):
    script = build_script(spec)
    if spec.kind == 'local':
        return ['bash', '-c', script], {}
    return ssh_argv(spec, 'bash -c ' + shlex.quote(script), tty=True, environ=environ)


def detect_argv(spec, environ=None):
    pat = self_excluding(spec.detect)
    if spec.kind == 'local':
        return ['pgrep', '-f', '--', pat], {}
    return ssh_argv(spec, 'pgrep -f -- ' + shlex.quote(pat), environ=environ)


def remote_kill_argv(spec, sig='INT', environ=None):
    pat = self_excluding(spec.stop_pattern or spec.detect)
    return ssh_argv(spec, f'pkill -{sig} -f -- ' + shlex.quote(pat), environ=environ)


# ---- 실제 프로세스 실행기 ----
class SystemRunner:
    async def spawn(self, argv, env=None, tag=None):
        e = dict(os.environ)
        e.update(env or {})
        return await asyncio.create_subprocess_exec(
            *argv, env=e, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True)

    async def capture(self, argv, env=None, timeout=10.0, tag=None):
        """끝날 때까지 실행해 (종료 코드, 출력) 을 돌려준다. 시간 초과면 (None, '')."""
        e = dict(os.environ)
        e.update(env or {})
        p = await asyncio.create_subprocess_exec(
            *argv, env=e, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            self.signal_group(p.pid, signal.SIGKILL)
            await p.wait()
            return None, ''
        return p.returncode, out.decode(errors='replace')

    def signal_group(self, pid, sig):
        # start_new_session=True 라 프로세스 그룹 id 는 pid 와 같다. getpgid(pid) 는 리더가 끝나면 실패하므로 쓰지 않는다
        # (리더인 ros2 launch 가 먼저 끝나도 남은 자식에게 신호를 보낼 수 있어야 한다)
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def group_alive(self, pid):
        """리더가 끝난 뒤에도 그룹에 남은 프로세스(launch 가 먼저 죽고 남은 자식)가 있는지."""
        try:
            os.killpg(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


class _Entry:
    def __init__(self, spec):
        self.spec = spec
        self.state = STOPPED
        self.message = ''
        self.pid = None
        self.started = None
        self.exit_code = None
        self.log = collections.deque(maxlen=LOG_LINES)
        self.log_n = 0           # 지금까지 받은 줄 수 (화면이 로그를 다시 받을지 판단)
        self.proc = None
        self.stop_requested = False
        self.tasks = []


class ProcessManager:
    def __init__(self, specs, runner=None, health=None, on_change=None, now=time.monotonic, environ=None, which=shutil.which, log_sink=None):
        self._sink = log_sink   # 로그 화면용: (프로세스 id, 한 줄) 을 받는다
        self._specs = {s.id: s for s in specs}
        self._order = [s.id for s in specs]
        self._e = {s.id: _Entry(s) for s in specs}
        self._runner = runner or SystemRunner()
        self._health = health or (lambda robot: None)
        self._on_change = on_change or (lambda: None)
        self._now = now
        self._environ = os.environ if environ is None else environ
        self._which = which
        self._seq = None
        self._seq_task = None
        self._secrets = [self._environ[s.ssh_password_env] for s in specs
                         if s.ssh_password_env and self._environ.get(s.ssh_password_env)]

    # ---- 상태 ----
    def _changed(self):
        try:
            self._on_change()
        except Exception:
            log.exception('상태 변경 알림 실패(무시)')

    def spec(self, pid):
        if pid not in self._specs:
            raise ProcError(f'알 수 없는 프로세스입니다: {pid}', 'unknown')
        return self._specs[pid]

    def status(self):
        now = self._now()
        procs = []
        for pid in self._order:
            e = self._e[pid]
            s = e.spec
            procs.append({
                'id': pid, 'label': s.label, 'group': s.group, 'kind': s.kind, 'robot': s.robot,
                'host': s.ssh_host if s.kind == 'ssh' else None,
                'configured': bool(s.command), 'state': e.state, 'message': e.message, 'pid': e.pid,
                'uptime_sec': round(now - e.started, 1) if e.state == RUNNING and e.started is not None else None,
                'exit_code': e.exit_code, 'confirm_stop': s.confirm_stop,
                'health': self._health(s.robot) if s.health and s.robot else None,
                'log_n': e.log_n,
            })
        return {'procs': procs, 'sequence': dict(self._seq) if self._seq else None}

    def log_lines(self, pid, n=LOG_LINES):
        self.spec(pid)
        return list(self._e[pid].log)[-n:]

    def _add_log(self, e, text):
        for sec in self._secrets:
            text = text.replace(sec, '***')
        text = ANSI.sub('', text).rstrip()
        if text:
            e.log.append(text[:MAX_LINE])
            e.log_n += 1
            if self._sink:
                self._sink(e.spec.id, text)

    # ---- 시작 ----
    async def _detect(self, spec):
        """이미 도는 같은 프로세스의 pid 목록. 확인할 수 없으면 ProcError (모르는 채로 시작하지 않는다)."""
        if not spec.detect:
            return []
        argv, env = detect_argv(spec, self._environ)
        if argv[0] == 'sshpass' and not self._which('sshpass'):
            raise ProcError('sshpass 가 설치돼 있지 않습니다 (password_env 대신 SSH 키 로그인을 쓰거나 sshpass 를 설치하세요)')
        try:
            rc, out = await self._runner.capture(argv, env, 15.0, tag=spec.id)
        except FileNotFoundError as e:
            raise ProcError(f'실행 파일을 찾을 수 없습니다: {e.filename}') from None
        if rc == 0:
            return [x for x in out.split() if x.isdigit()] or ['?']
        if rc == 1:
            return []
        if rc is None:
            raise ProcError('중복 실행 확인이 시간 초과됐습니다 (SSH 접속 불가?)')
        detail = self._clean(out).splitlines()[-1:] or ['']
        raise ProcError(f'중복 실행을 확인하지 못했습니다 (종료 코드 {rc}): {detail[0]}')

    def _clean(self, text):
        for sec in self._secrets:
            text = text.replace(sec, '***')
        return ANSI.sub('', text).strip()

    async def start(self, pid):
        spec = self.spec(pid)
        e = self._e[pid]
        if not spec.command:
            raise ProcError('명령이 설정되지 않았습니다 (config/processes.yaml 의 command)', 'unset')
        if e.state in ACTIVE:
            raise ProcError(f'이미 {e.state} 상태입니다', 'busy')
        prev = (e.state, e.message, e.exit_code)
        e.state, e.message, e.exit_code = STARTING, '중복 실행 확인 중', None
        self._changed()
        try:
            found = await self._detect(spec)
            if found:
                raise ProcError(f'이미 실행 중인 같은 프로세스가 있습니다 (pid {", ".join(found)}). 터미널에서 직접 켠 것이면 그쪽에서 끄세요', 'external')
            argv, env = start_argv(spec, self._environ)
            if argv[0] == 'sshpass' and not self._which('sshpass'):
                raise ProcError('sshpass 가 설치돼 있지 않습니다 (password_env 대신 SSH 키 로그인을 쓰거나 sshpass 를 설치하세요)')
            try:
                proc = await self._runner.spawn(argv, env, tag=pid)
            except (FileNotFoundError, PermissionError, OSError) as ex:
                raise ProcError(f'프로세스를 시작하지 못했습니다: {ex}') from None
        except ProcError as ex:
            e.state, e.message, e.exit_code = (STOPPED if ex.code == 'external' else FAILED), str(ex), None
            self._changed()
            raise
        except BaseException:
            e.state, e.message, e.exit_code = prev
            self._changed()
            raise
        e.proc, e.pid, e.started, e.stop_requested = proc, proc.pid, self._now(), False
        e.log.clear()
        e.log_n = 0
        e.message = ''
        self._add_log(e, f'$ {spec.kind}: {spec.command}')
        reader = asyncio.create_task(self._read(e, proc))
        e.tasks = [reader, asyncio.create_task(self._watch(e, proc, reader)), asyncio.create_task(self._settle(e, proc))]
        log.info('%s 시작 (pid %s)', pid, proc.pid)
        self._changed()

    async def _read(self, e, proc):
        buf = ''
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk.decode(errors='replace').replace('\r\n', '\n').replace('\r', '\n')
                *lines, buf = buf.split('\n')
                for ln in lines:
                    self._add_log(e, ln)
                if lines:
                    self._changed()
        except Exception:
            log.exception('%s 출력 읽기 실패', e.spec.id)
        self._add_log(e, buf)

    async def _settle(self, e, proc):
        await asyncio.sleep(e.spec.settle_sec)
        if e.proc is proc and e.state == STARTING and proc.returncode is None:
            e.state = RUNNING
            self._changed()

    async def _watch(self, e, proc, reader):
        rc = await proc.wait()
        try:
            await asyncio.wait_for(asyncio.shield(reader), 1.0)  # 남은 출력을 마저 읽는다 (종료 사유가 마지막 줄에 있다)
        except (asyncio.TimeoutError, Exception):
            pass
        if e.proc is not proc:
            return
        e.exit_code = rc
        last = e.log[-1] if e.log else ''
        was = e.state
        if e.stop_requested:
            e.state, e.message = STOPPED, ''
        elif was == STARTING:
            e.state, e.message = FAILED, f'시작 직후 종료됨 (코드 {rc})' + (f': {last}' if last else '')
        elif rc == 0:
            e.state, e.message = EXITED, '프로세스가 종료됐습니다 (코드 0)'
        else:
            e.state, e.message = FAILED, f'예기치 않게 종료됨 (코드 {rc})' + (f': {last}' if last else '')
        (log.info if e.state == STOPPED else log.warning)('%s 종료: %s (코드 %s)', e.spec.id, e.state, rc)
        self._changed()

    # ---- 정지 ----
    async def _sweep_group(self, pgid):
        """리더가 끝난 뒤 그룹에 남은 프로세스를 정리한다: 잠시 기다림 -> SIGTERM -> SIGKILL."""
        alive = getattr(self._runner, 'group_alive', None)
        if alive is None:
            return
        for s, wait in ((None, 2.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 2.0)):
            if s is not None:
                self._runner.signal_group(pgid, s)
            end = self._now() + wait
            while alive(pgid) and self._now() < end:
                await asyncio.sleep(0.1)
            if not alive(pgid):
                return
        log.warning('프로세스 그룹 %s 에 종료되지 않은 프로세스가 남았다', pgid)

    async def _wait_exit(self, proc, sec):
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), sec)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self, pid, confirm=False):
        spec = self.spec(pid)
        e = self._e[pid]
        if spec.confirm_stop and not confirm:
            raise ProcError('로봇에서 실행 중인 프로세스입니다. 확인 후 다시 요청하세요', 'confirm')
        if e.state == STOPPING:
            raise ProcError('이미 정지하는 중입니다', 'busy')
        if e.state not in (STARTING, RUNNING) or e.proc is None:
            raise ProcError('실행 중이 아닙니다', 'busy')
        proc = e.proc
        e.state, e.message, e.stop_requested = STOPPING, '정지하는 중', True
        self._changed()
        t = spec.stop_timeout_sec
        sig = self._runner.signal_group
        gone = False
        if spec.kind == 'local':
            for s, wait in ((signal.SIGINT, t), (signal.SIGTERM, t), (signal.SIGKILL, 3.0)):
                sig(proc.pid, s)
                if await self._wait_exit(proc, wait):
                    gone = True
                    break
        else:
            try:  # pty 에 Ctrl-C 를 보낸다 -> 원격 프로세스 그룹에 SIGINT
                proc.stdin.write(b'\x03')
                await proc.stdin.drain()
            except Exception:
                pass
            gone = await self._wait_exit(proc, t)
            if not gone:  # Ctrl-C 가 안 먹으면 로봇에서 직접 SIGINT, 그래도 안 되면 SSH 를 끊는다 (pty 가 닫히며 SIGHUP)
                try:
                    argv, env = remote_kill_argv(spec, 'INT', self._environ)
                    await self._runner.capture(argv, env, 10.0, tag=pid)
                except Exception as ex:
                    log.warning('%s 원격 pkill 실패: %s', pid, ex)
                gone = await self._wait_exit(proc, t)
            for s, wait in ((signal.SIGTERM, t), (signal.SIGKILL, 3.0)):
                if gone:
                    break
                sig(proc.pid, s)
                gone = await self._wait_exit(proc, wait)
        if gone and spec.kind == 'local':
            await self._sweep_group(proc.pid)   # ros2 launch 가 먼저 끝나도 자식(domain_bridge 등)이 남지 않게 한다
        if not gone:
            e.state, e.message = FAILED, '프로세스가 종료되지 않습니다 (SIGKILL 후에도 살아 있음)'
            self._changed()
            raise ProcError(e.message)
        await asyncio.gather(*e.tasks, return_exceptions=True)
        if spec.kind == 'ssh' and spec.detect:  # SSH 만 닫혔고 로봇에서는 아직 도는지 확인한다
            try:
                left = await self._detect(spec)
            except ProcError:
                left = []
            if left:
                e.state = FAILED
                e.message = f'SSH 는 종료했지만 로봇에서 아직 실행 중입니다 (pid {", ".join(left)}). 로봇에서 직접 확인하세요'
                self._changed()
                raise ProcError(e.message)
        e.state, e.message = STOPPED, ''
        self._changed()

    # ---- 전체 시작 / 정지 (순서 있는 시퀀스) ----
    def sequence_running(self):
        return bool(self._seq_task and not self._seq_task.done())

    def _begin(self, kind):
        if self.sequence_running():
            raise ProcError('다른 전체 시작/정지가 진행 중입니다', 'busy')
        self._seq = {'kind': kind, 'state': 'running', 'step': None, 'message': '', 'results': []}
        self._changed()

    def _finish(self, state, message):
        self._seq.update(state=state, step=None, message=message)
        self._changed()

    def start_all(self):
        self._begin('start_all')
        self._seq_task = asyncio.create_task(self._run_start_all())

    async def _run_start_all(self):
        seq = self._seq
        try:
            for pid in self._order:
                e = self._e[pid]
                if not e.spec.command:
                    seq['results'].append({'id': pid, 'result': 'unset'})
                    continue
                if e.state == RUNNING:
                    seq['results'].append({'id': pid, 'result': 'already'})
                    continue
                seq['step'] = pid
                self._changed()
                try:
                    await self.start(pid)
                except ProcError as ex:
                    if ex.code == 'external':  # 터미널에서 이미 켜져 있다 -> 다음 단계로 (우리가 정지할 수는 없다)
                        seq['results'].append({'id': pid, 'result': 'external'})
                        continue
                    seq['results'].append({'id': pid, 'result': 'failed', 'message': str(ex)})
                    return self._finish('failed', f'{e.spec.label}: {ex}')
                while e.state == STARTING:
                    await asyncio.sleep(0.05)
                if e.state != RUNNING:
                    seq['results'].append({'id': pid, 'result': 'failed', 'message': e.message})
                    return self._finish('failed', f'{e.spec.label}: {e.message or e.state}')
                seq['results'].append({'id': pid, 'result': 'started'})
            self._finish('done', '')
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            log.exception('전체 시작 실패')
            self._finish('failed', f'내부 오류: {ex}')

    def stop_all(self, confirm=False):
        need = [pid for pid in self._order if self._specs[pid].confirm_stop and self._e[pid].state in (STARTING, RUNNING)]
        if need and not confirm:
            raise ProcError('로봇에서 실행 중인 프로세스가 포함돼 있습니다. 확인 후 다시 요청하세요', 'confirm')
        self._begin('stop_all')
        self._seq_task = asyncio.create_task(self._run_stop_all())

    async def _run_stop_all(self):
        seq = self._seq
        failed = []
        try:
            for pid in reversed(self._order):
                e = self._e[pid]
                if e.state not in (STARTING, RUNNING):
                    continue
                seq['step'] = pid
                self._changed()
                try:
                    await self.stop(pid, confirm=True)
                    seq['results'].append({'id': pid, 'result': 'stopped'})
                except ProcError as ex:
                    seq['results'].append({'id': pid, 'result': 'failed', 'message': str(ex)})
                    failed.append(f'{e.spec.label}: {ex}')
            self._finish('failed' if failed else 'done', ' / '.join(failed))
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            log.exception('전체 정지 실패')
            self._finish('failed', f'내부 오류: {ex}')

    # ---- 서버 종료 ----
    async def shutdown(self):
        """서버 종료: stop_on_exit(기본 True) 인 프로세스를 모두 정지한다. 로봇 쪽도 포함이다 (남겨 두면 다음 실행이 '이미 실행 중' 으로 잠긴다).
        stop_on_exit: false 로 둔 프로세스만 남긴다."""
        if self._seq_task and not self._seq_task.done():
            self._seq_task.cancel()
            await asyncio.gather(self._seq_task, return_exceptions=True)
        todo = [pid for pid in self._order if self._specs[pid].stop_on_exit and self._e[pid].state in (STARTING, RUNNING)]
        await asyncio.gather(*(self.stop(pid, confirm=True) for pid in todo), return_exceptions=True)
        for e in self._e.values():
            for t in e.tasks:
                t.cancel()
        await asyncio.gather(*(t for e in self._e.values() for t in e.tasks), return_exceptions=True)
