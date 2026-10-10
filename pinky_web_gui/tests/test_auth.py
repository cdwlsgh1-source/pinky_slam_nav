"""인증(역할, 세션, 로그인 제한) 단위 테스트."""
from backend.auth import MAX_FAILS, OPERATOR, SESSION_SEC, VIEWER, Auth


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(op='op-pass', vw='vw-pass', clock=None):
    return Auth(op, vw, now=clock or Clock())


def test_disabled_without_passwords():
    a = Auth(None, None)
    assert not a.enabled and not a.procs_allowed
    assert a.role_of(None) == OPERATOR  # 인증이 꺼져 있으면 기존처럼 모두 사용 가능


def test_from_env():
    a = Auth.from_env({'PINKY_OPERATOR_PASSWORD': 'x'})
    assert a.enabled and a.procs_allowed
    b = Auth.from_env({'PINKY_VIEWER_PASSWORD': 'y'})
    assert b.enabled and not b.procs_allowed  # viewer 만 있으면 프로세스 제어는 켜지지 않는다
    assert not Auth.from_env({'PINKY_OPERATOR_PASSWORD': ''}).enabled


def test_roles():
    a = make()
    tok, role = a.login('op-pass')
    assert role == OPERATOR and a.role_of(tok) == OPERATOR
    tok2, role2 = a.login('vw-pass')
    assert role2 == VIEWER and a.role_of(tok2) == VIEWER
    assert Auth.allows(OPERATOR, VIEWER) and Auth.allows(OPERATOR, OPERATOR)
    assert Auth.allows(VIEWER, VIEWER) and not Auth.allows(VIEWER, OPERATOR)
    assert not Auth.allows(None, VIEWER)


def test_wrong_password_and_unknown_token():
    a = make()
    assert a.login('nope') == (None, 'invalid')
    assert a.login(None) == (None, 'invalid')
    assert a.login(123) == (None, 'invalid')
    assert a.login('') == (None, 'invalid')
    assert a.role_of('forged') is None and a.role_of(None) is None


def test_same_password_prefers_operator():
    a = Auth('same', 'same')
    assert a.login('same')[1] == OPERATOR


def test_session_expiry_and_logout():
    c = Clock()
    a = make(clock=c)
    tok, _ = a.login('op-pass')
    c.t += SESSION_SEC - 1
    assert a.role_of(tok) == OPERATOR
    c.t += 2
    assert a.role_of(tok) is None
    tok, _ = a.login('op-pass')
    a.logout(tok)
    assert a.role_of(tok) is None


def test_lockout_and_recovery():
    c = Clock()
    a = make(clock=c)
    for _ in range(MAX_FAILS):
        assert a.login('bad', 'ip1')[1] == 'invalid'
    assert a.login('op-pass', 'ip1') == (None, 'locked')   # 맞는 비밀번호도 잠시 막는다
    assert a.login('op-pass', 'ip2')[1] == OPERATOR          # 다른 주소는 영향 없다
    c.t += 61
    assert a.login('op-pass', 'ip1')[1] == OPERATOR


def test_successful_login_clears_failures():
    a = make()
    for _ in range(MAX_FAILS - 1):
        a.login('bad', 'ip')
    assert a.login('op-pass', 'ip')[1] == OPERATOR
    for _ in range(MAX_FAILS - 1):
        assert a.login('bad', 'ip')[1] == 'invalid'
