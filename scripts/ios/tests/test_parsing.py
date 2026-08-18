"""Parsing and redaction — the two things everything else stands on."""

from iosconfig import parse_config, redact, redact_config


def test_indented_lines_carry_their_block(dirty_config):
    lines = parse_config(dirty_config)
    by_text = {line.stripped: line for line in lines}

    assert by_text['password 7 121A0C041104'].parent == 'line vty 0 4'
    assert by_text['switchport mode trunk'].parent == 'interface GigabitEthernet1/0/1'
    assert by_text['ip http server'].parent == ''


def test_nested_blocks_pop_correctly(dirty_config):
    lines = parse_config(dirty_config)
    key_string = next(l for l in lines if l.stripped.startswith('key-string'))
    assert key_string.parent == 'key 1'
    # And the block above it is still tracked, two levels up.
    key = next(l for l in lines if l.stripped == 'key 1')
    assert key.parent == 'key chain ospf-keys'


def test_comments_and_blanks_are_dropped(dirty_config):
    lines = parse_config(dirty_config)
    assert not any(line.stripped.startswith('!') for line in lines)
    assert not any(line.stripped == '' for line in lines)


def test_line_numbers_point_at_the_original_file(dirty_config):
    lines = parse_config(dirty_config)
    raw = dirty_config.splitlines()
    for line in lines:
        assert raw[line.number - 1].strip() == line.stripped


class TestRedaction:
    def test_type_7_password_keeps_its_shape(self):
        assert redact('enable password 7 070C285F4D06') == 'enable password 7 <redacted>'

    def test_username_line_keeps_the_username(self):
        out = redact('username olduser privilege 15 secret 9 $14$Xn8k$Qm2xLpR7vY0oZa')
        assert out == 'username olduser privilege 15 secret 9 <redacted>'
        assert 'olduser' in out          # the finding still names the account
        assert 'Qm2xLpR7' not in out

    def test_untyped_password(self):
        assert redact(' password cleartextpw') == ' password <redacted>'

    def test_key_string_in_a_key_chain(self):
        assert redact('  key-string 7 060506324F41584B56') == '  key-string 7 <redacted>'

    def test_a_bare_key_id_is_not_a_secret(self):
        # `key 1` inside a key chain is an identifier, not a credential.
        assert redact(' key 1') == ' key 1'

    def test_snmp_community(self):
        assert redact('snmp-server community s3cr3tw0rd RW') == \
            'snmp-server community <redacted> RW'

    def test_redaction_is_idempotent(self):
        once = redact('username x secret 9 $14$abc')
        assert redact(once) == once

    def test_no_hash_survives_a_whole_config(self, dirty_config):
        out = redact_config(dirty_config)
        for secret in ('070C285F4D06', '0822455D0A16', '121A0C041104',
                       '060506324F41584B56', 'Qm2xLpR7vY0oZa', 's3cr3tw0rd'):
            assert secret not in out, '%s survived redaction' % secret

    def test_indentation_is_preserved(self):
        assert redact(' password 7 ABCD1234').startswith(' password')
