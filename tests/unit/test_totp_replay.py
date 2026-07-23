import pyotp

from idraa.services.totp import verify_totp, verify_totp_step


def test_step_returned_replay_rejected_next_accepted():
    secret = pyotp.random_base32()
    t = 1_700_000_000
    step = t // 30
    code = pyotp.TOTP(secret).at(t)
    assert verify_totp_step(secret, code, for_time=t) == step
    assert verify_totp_step(secret, code, after_step=step, for_time=t) is None  # replay
    later = t + 30
    assert (
        verify_totp_step(secret, pyotp.TOTP(secret).at(later), after_step=step, for_time=later)
        == step + 1
    )


def test_wrong_code_none_and_verify_totp_delegates():
    secret = pyotp.random_base32()
    assert verify_totp_step(secret, "000000", for_time=1_700_000_000) is None
    # Brief-Step-1 note: the original verbatim test compared a code frozen at
    # for_time=1_700_000_000 against verify_totp() with no for_time override
    # (defaults to real wall-clock time) — that pairing can never match, so
    # it was rewritten to generate the code for "now" via .now(), preserving
    # the actual intent ("unified path still accepts a currently-valid code").
    assert verify_totp(secret, pyotp.TOTP(secret).now()) is True  # unified path still accepts


def test_non_ascii_code_returns_none_not_typeerror():
    import pyotp

    from idraa.services.totp import verify_totp, verify_totp_step

    secret = pyotp.random_base32()
    t = 1_700_000_000
    assert verify_totp_step(secret, "café12", for_time=t) is None  # accented
    assert verify_totp_step(secret, "１２３４５６", for_time=t) is None  # full-width digits
    assert verify_totp(secret, "café12") is False  # delegate path also graceful
