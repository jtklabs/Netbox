import copy

import pytest

from netops.clearpass_cluster import discover, validate


def devices():
    return [{'id': i, 'primary_ip4': {'address': f'192.0.2.{i}/24'},
             'platform': {'slug': 'aruba-clearpass'}, 'status': {'value': 'active'},
             'tags': [{'slug': f'clearpass-{role}'}]}
            for i, role in ((1, 'publisher'), (2, 'subscriber'))]


class Client:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def get(self, path, params):
        self.calls.append((path, params))
        return [d for d in self.rows if params['tag'] in {t['slug'] for t in d['tags']}]


def test_discovery_is_global_and_role_aware():
    client = Client(devices())
    assert discover(client) == [
        {'device_id': 1, 'address': '192.0.2.1', 'role': 'publisher'},
        {'device_id': 2, 'address': '192.0.2.2', 'role': 'subscriber'}]
    assert client.calls == [('dcim/devices/', {'tag': 'clearpass-publisher'}),
                            ('dcim/devices/', {'tag': 'clearpass-subscriber'})]


@pytest.mark.parametrize('change', ['dual', 'two_publishers', 'no_publisher', 'inactive', 'no_ip',
                                    'ipv6', 'wrong_platform', 'duplicate_ip'])
def test_invalid_inventory_fails_closed(change):
    rows = devices()
    if change == 'dual':
        rows[1]['tags'] += rows[0]['tags']
    elif change == 'two_publishers':
        rows[1]['tags'] = rows[0]['tags']
    elif change == 'no_publisher':
        rows[0]['tags'] = rows[1]['tags']
    elif change == 'inactive':
        rows[1]['status'] = {'value': 'offline'}
    elif change == 'no_ip':
        rows[1]['primary_ip4'] = None
    elif change == 'ipv6':
        rows[1]['primary_ip4']['address'] = '2001:db8::1/64'
    elif change == 'wrong_platform':
        rows[1]['platform']['slug'] = 'cisco-ios'
    else:
        rows[1]['primary_ip4'] = rows[0]['primary_ip4']
    with pytest.raises(ValueError):
        discover(Client(rows))


def test_unfiltered_api_response_is_rejected():
    class Unfiltered(Client):
        def get(self, *args):
            return self.rows
    with pytest.raises(ValueError, match='filter'):
        discover(Unfiltered(devices()))


def test_snapshot_rejects_duplicate_identity():
    members = discover(Client(devices()))
    members.append(copy.deepcopy(members[1]))
    with pytest.raises(ValueError, match='unique'):
        validate(members)
