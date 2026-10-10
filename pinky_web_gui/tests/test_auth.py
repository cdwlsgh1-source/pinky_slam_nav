"""인증(고정 계정, 역할, 세션, 로그인 제한) 단위 테스트."""
from backend.auth import DEFAULT_ACCOUNTS, MAX_FAILS, MANAGER, OPERATOR, SESSION_SEC, Auth


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(clock=None):
    return Auth(now=clock or Clock())


def test_fixed_accounts_are_in_the_program():
    assert DEFAULT_ACCOUNTS == {'mngr': ('mngr', MANAGER), 'oper': ('oper', OPERATOR)}
    a = make()
    assert a.enabled and a.procs_allowed


def test_disabled_has_no_accounts_and_everyone_is_manager():
    a = Auth.disabled()
    assert not a.enabled and not a.procs_allowed
    assert a.role_of(None) == MANAGER
    assert a.login('mngr', 'mngr') == (None, 'invalid')


def test_login_needs_matching_id_and_password():
    a = make()
    tok, role = a.login('mngr', 'mngr')
    assert role == MANAGER and a.role_of(tok) == MANAGER and a.user_of(tok) == 'mngr'
    tok2, role2 = a.login('oper', 'oper')
    assert role2 == OPERATOR and a.role_of(tok2) == OPERATOR and a.user_of(tok2) == 'oper'
    assert a.login('mngr', 'oper') == (None, 'invalid')      # 엇갈린 조합
    assert a.login('oper', 'mngr') == (None, 'invalid')
    assert a.login('admin', 'mngr') == (None, 'invalid')        # 없는 ID
    assert a.login('', '') == (None, 'invalid')


def test_roles():
    assert Auth.allows(MANAGER, OPERATOR) and Auth.allows(MANAGER, MANAGER)
    assert Auth.allows(OPERATOR, OPERATOR) and not Auth.allows(OPERATOR, MANAGER)
    assert not Auth.allows(None, OPERATOR)


def test_bad_types_and_unknown_token():
    a = make()
    assert a.login(None, None) == (None, 'invalid')
    assert a.login('mngr', 123) == (None, 'invalid')
    assert a.login(['mngr'], 'mngr') == (None, 'invalid')
    assert a.role_of('forged') is None and a.role_of(None) is None and a.user_of('forged') is None


def test_session_expiry_and_logout():
    c = Clock()
    a = make(c)
    tok, _ = a.login('mngr', 'mngr')
    c.t += SESSION_SEC - 1
    assert a.role_of(tok) == MANAGER
    c.t += 2
    assert a.role_of(tok) is None
    tok, _ = a.login('mngr', 'mngr')
    a.logout(tok)
    assert a.role_of(tok) is None


def test_lockout_and_recovery():
    c = Clock()
    a = make(c)
    for _ in range(MAX_FAILS):
        assert a.login('mngr', 'bad', 'ip1')[1] == 'invalid'
    assert a.login('mngr', 'mngr', 'ip1') == (None, 'locked')   # 맞는 정보도 잠시 막는다
    assert a.login('mngr', 'mngr', 'ip2')[1] == MANAGER          # 다른 주소는 영향 없다
    c.t += 61
    assert a.login('mngr', 'mngr', 'ip1')[1] == MANAGER


def test_successful_login_clears_failures():
    a = make()
    for _ in range(MAX_FAILS - 1):
        a.login('mngr', 'bad', 'ip')
    assert a.login('mngr', 'mngr', 'ip')[1] == MANAGER
    for _ in range(MAX_FAILS - 1):
        assert a.login('mngr', 'bad', 'ip')[1] == 'invalid'
