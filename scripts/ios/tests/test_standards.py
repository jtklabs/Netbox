"""Evaluating the five shipped standards against a dirty and a clean switch."""

from conftest import (
    LOCAL_USERS,
    NO_HTTP,
    NO_HTTPS,
    NO_TYPE_7,
    PASSWORD_ENCRYPTION,
)
from iosconfig import RESULT_COMPLIANT, RESULT_NON_COMPLIANT, evaluate, parse_config


def check(standard, config):
    return evaluate(standard, parse_config(config))


class TestAbsentStandards:
    def test_http_server_is_a_violation(self, dirty_config):
        result = check(NO_HTTP, dirty_config)
        assert result.result == RESULT_NON_COMPLIANT
        assert [v.line.stripped for v in result.violations] == ['ip http server']

    def test_negated_form_is_not_a_violation(self, clean_config):
        # `no ip http server` must not match `^ip http server$`. Getting this
        # wrong reports every compliant switch as broken, which is how a
        # compliance report loses its audience.
        assert check(NO_HTTP, clean_config).result == RESULT_COMPLIANT

    def test_http_and_https_are_separate_findings(self, dirty_config):
        assert check(NO_HTTP, dirty_config).result == RESULT_NON_COMPLIANT
        assert check(NO_HTTPS, dirty_config).result == RESULT_NON_COMPLIANT
        # ...and the HTTP pattern must not swallow the secure-server line.
        http = check(NO_HTTP, dirty_config)
        assert all('secure' not in v.line.stripped for v in http.violations)


class TestType7:
    def test_finds_every_flavour(self, dirty_config):
        result = check(NO_TYPE_7, dirty_config)
        assert result.result == RESULT_NON_COMPLIANT
        found = {v.line.stripped for v in result.violations}
        assert 'enable password 7 070C285F4D06' in found
        assert 'username admin privilege 15 password 7 0822455D0A16' in found
        assert 'password 7 121A0C041104' in found            # inside line vty 0 4
        assert 'key-string 7 060506324F41584B56' in found    # inside a key chain

    def test_reports_where_each_one_lives(self, dirty_config):
        result = check(NO_TYPE_7, dirty_config)
        contexts = {v.line.stripped: v.line.context for v in result.violations}
        assert contexts['password 7 121A0C041104'] == 'line vty 0 4'
        assert contexts['enable password 7 070C285F4D06'] == ''

    def test_type_9_secrets_are_not_flagged(self, clean_config):
        assert check(NO_TYPE_7, clean_config).result == RESULT_COMPLIANT

    def test_findings_carry_no_hashes(self, dirty_config):
        result = check(NO_TYPE_7, dirty_config)
        blob = repr(result.findings()) + result.observed_text
        for secret in ('070C285F4D06', '0822455D0A16', '121A0C041104',
                       '060506324F41584B56'):
            assert secret not in blob


class TestPresentStandard:
    def test_missing_service_is_reported(self, dirty_config):
        result = check(PASSWORD_ENCRYPTION, dirty_config)
        assert result.result == RESULT_NON_COMPLIANT
        assert result.missing == ['service password-encryption']

    def test_present_service_passes(self, clean_config):
        assert check(PASSWORD_ENCRYPTION, clean_config).result == RESULT_COMPLIANT


class TestExactSet:
    def test_missing_and_extra_are_both_drift(self, dirty_config):
        result = check(LOCAL_USERS, dirty_config)
        assert result.result == RESULT_NON_COMPLIANT
        assert result.missing == ['netops']
        assert {e.key for e in result.extra} == {'admin', 'olduser', 'monitor'}

    def test_privilege_is_captured_for_the_lockout_guard(self, dirty_config):
        result = check(LOCAL_USERS, dirty_config)
        privileges = {e.key: e.groups.get('privilege') for e in result.observed}
        assert privileges == {'admin': '15', 'olduser': '15', 'monitor': '1'}

    def test_exact_match_passes(self, clean_config):
        assert check(LOCAL_USERS, clean_config).result == RESULT_COMPLIANT

    def test_observed_text_is_redacted(self, dirty_config):
        text = check(LOCAL_USERS, dirty_config).observed_text
        assert 'username olduser privilege 15 secret 9 <redacted>' in text
        assert '$14$' not in text

    def test_governed_capture_shows_the_block_a_line_lives_in(self, dirty_config):
        capture = check(NO_TYPE_7, dirty_config).governed_capture()
        assert 'line vty 0 4' in capture
        assert ' password 7 <redacted>' in capture
        assert '121A0C041104' not in capture
