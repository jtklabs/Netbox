"""Exercise release enforcement against real commit trees, without dependencies."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('poller_versions', Path(__file__).parents[1] / 'check-poller-versions.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class VersionGuardTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        previous = os.getcwd()
        os.chdir(self.temporary.name)
        self.addCleanup(os.chdir, previous)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.write('nornir-netops/netops/__init__.py', '')
        self.write('nornir-netops/pyproject.toml', '[project]\nversion = "0.1.0"\n')
        self.write('snmp-inventory/snmpinv/__init__.py', '__version__ = "1.5.0"\n')
        self.write('snmp-inventory/snmpinv/snmp.py', '# old implementation\n')
        self.commit()
        self.git('tag', 'base')

    def git(self, *args):
        return subprocess.check_output(['git', *args], stderr=subprocess.STDOUT, text=True)

    def write(self, path, text):
        path = Path('scripts') / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture')

    def test_docs_and_tests_do_not_require_release(self):
        self.write('nornir-netops/README.md', 'Documentation')
        self.write('snmp-inventory/tests/test_scan.py', '# test')
        self.commit()
        self.assertEqual(guard.check('base', 'HEAD'), [])

    def test_runtime_change_without_bump_fails(self):
        self.write('snmp-inventory/snmpinv/snmp.py', '# changed implementation')
        self.commit()
        self.assertIn('increment', guard.check('base', 'HEAD')[0])

    def test_templates_and_dependencies_require_release(self):
        self.write('nornir-netops/templates/f5_tmsh/ntp.j2', 'new template')
        self.write('snmp-inventory/requirements.txt', 'new dependency')
        self.commit()
        self.assertTrue(any('nornir-netops: increment' in e for e in guard.check('base', 'HEAD')))
        self.assertTrue(any('snmp-inventory: increment' in e for e in guard.check('base', 'HEAD')))

    def test_deletion_requires_release(self):
        Path('scripts/snmp-inventory/snmpinv/snmp.py').unlink()
        self.commit()
        self.assertIn('increment', guard.check('base', 'HEAD')[0])

    def test_numeric_increment_and_legacy_migration_pass(self):
        self.write('nornir-netops/netops/__init__.py', '__version__ = "0.2.0"')
        self.write('nornir-netops/pyproject.toml', '[project]\ndynamic = ["version"]\n'
                   '[tool.setuptools.dynamic]\nversion = {attr = "netops.__version__"}\n')
        self.write('snmp-inventory/snmpinv/__init__.py', '__version__ = "1.10.0"')
        self.commit()
        self.assertEqual(guard.check('base', 'HEAD'), [])

    def test_same_version_as_newer_main_is_rejected(self):
        self.write('snmp-inventory/snmpinv/__init__.py', '__version__ = "1.5.1"')
        self.commit()
        self.git('tag', 'newer-main')
        self.write('snmp-inventory/snmpinv/snmp.py', '# another change')
        self.commit()
        self.assertEqual(guard.check('base', 'HEAD'), [])
        self.assertIn('increment', guard.check('newer-main', 'HEAD')[0])

    def test_version_cannot_decrease(self):
        self.write('snmp-inventory/snmpinv/__init__.py', '__version__ = "1.4.9"')
        self.commit()
        self.assertIn('increment', guard.check('base', 'HEAD')[0])

    def test_nonnumeric_version_fails(self):
        self.write('snmp-inventory/snmpinv/__init__.py', '__version__ = "latest"')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'numeric'):
            guard.check('base', 'HEAD')

    def test_package_cannot_diverge_from_runtime(self):
        self.write('nornir-netops/netops/__init__.py', '__version__ = "0.2.0"')
        self.commit()
        self.assertIn('package version', guard.check('base', 'HEAD')[0])


if __name__ == '__main__':
    unittest.main()
