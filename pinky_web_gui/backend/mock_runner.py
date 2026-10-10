"""가짜 프로세스 실행기: --mock 모드와 테스트에서 실제 ros2 / ssh 를 실행하지 않고 프로세스 관리 흐름만 확인한다.

가짜 프로세스는 1초마다 로그 한 줄을 내고, SIGINT/SIGTERM/SIGKILL 이나 stdin 의 Ctrl-C(\\x03) 에 반응해 종료한다.
옵션(tag = 프로세스 id):
  fail_start : 시작 직후 오류 로그를 내고 코드 1 로 종료
  ignore_sigint : SIGINT 와 Ctrl-C 를 무시 (SIGTERM 이 와야 종료) - 단계별 정지 시험용
  external   : 중복 확인(pgrep)이 '이미 실행 중' 을 돌려준다 (터미널에서 직접 켠 경우)
  die_after  : {tag: 초} 시작 후 이 시간이 지나면 예기치 않게 종료(코드 1)
  detect_fail: 중복 확인 자체가 실패 (SSH 접속 불가)
"""
import asyncio
import itertools
import signal

_pids = itertools.count(40000)


class _Stdin:
    def __init__(self, proc):
        self._p = proc

    def write(self, data):
        if b'\x03' in data and not self._p.ignore_int:
            self._p.finish(130)

    async def drain(self):
        return None

    def close(self):
        pass


class FakeProc:
    def __init__(self, tag, opts, interval=1.0):
        self.pid = next(_pids)
        self.tag = tag
        self.returncode = None
        self.ignore_int = tag in opts.get('ignore_sigint', ())
        self.stdout = asyncio.StreamReader()
        self.stdin = _Stdin(self)
        self._done = asyncio.Event()
        self._task = asyncio.create_task(self._run(opts, interval))

    async def _run(self, opts, interval):
        try:
            if self.tag in opts.get('fail_start', ()):
                await asyncio.sleep(0.2)
                self.stdout.feed_data(f'[{self.tag}] 오류: 초기화 실패 (mock)\n'.encode())
                self.finish(1)
                return
            self.stdout.feed_data(f'[{self.tag}] 시작 (mock)\n'.encode())
            die = opts.get('die_after', {}).get(self.tag)
            n = 0
            while self.returncode is None:
                await asyncio.sleep(interval)
                n += 1
                if self.returncode is not None:
                    break
                if die is not None and n * interval >= die:
                    self.stdout.feed_data(f'[{self.tag}] 치명적 오류 (mock)\n'.encode())
                    self.finish(1)
                    break
                self.stdout.feed_data(f'[{self.tag}] 동작 중 {n}\n'.encode())
        except asyncio.CancelledError:
            pass

    def finish(self, code):
        if self.returncode is None:
            self.returncode = code
            self.stdout.feed_eof()
            self._done.set()

    async def wait(self):
        await self._done.wait()
        return self.returncode


class MockRunner:
    def __init__(self, interval=1.0, **opts):
        self.opts = {k: (set(v) if isinstance(v, (list, tuple, set)) else v) for k, v in opts.items()}
        self.interval = interval
        self.procs = {}   # pid -> FakeProc
        self.calls = []   # (종류, argv, tag) 호출 기록 (테스트가 확인한다)
        self.signals = []  # (pid, sig)

    async def spawn(self, argv, env=None, tag=None):
        self.calls.append(('spawn', list(argv), tag))
        p = FakeProc(tag, self.opts, self.interval)
        self.procs[p.pid] = p
        return p

    async def capture(self, argv, env=None, timeout=10.0, tag=None):
        self.calls.append(('capture', list(argv), tag))
        if tag in self.opts.get('detect_fail', ()):
            return 255, 'ssh: connect to host mock port 22: No route to host'
        if tag in self.opts.get('external', ()) and 'pgrep' in ' '.join(argv):
            return 0, '12345\n'
        return 1, ''

    def signal_group(self, pid, sig):
        self.signals.append((pid, sig))
        p = self.procs.get(pid)
        if p is None:
            return
        if sig == signal.SIGINT and p.ignore_int:
            return
        p.finish(-int(sig))
