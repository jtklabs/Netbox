"""Optional Docker integration: private index, build isolation, no saved secret.

Run: NETBOX_TEST_DOCKER=1 python -m unittest discover -s testing/python-index -v
Uses a temporary local package index and synthetic packages; no public PyPI.
"""
import functools
import http.server
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('NETBOX_TEST_DOCKER') == '1', 'requires local Docker')
class BuildIndexTest(unittest.TestCase):
    def test_loader_without_runtime_env_files(self):
        with tempfile.TemporaryDirectory(prefix='python-index-loader-') as directory:
            root = Path(directory)
            # AMI builds need no runtime compose files or database credentials.
            (root / '.env').write_text(
                'COMPOSE_FILE=missing-runtime-compose.yml\n'
                'INDEX_HOST=packages.invalid\n'
                'PYTHON_INDEX_URL=https://${INDEX_HOST}/simple/\n'
                'UNRELATED_SECRET=not-for-the-build\n')
            env = dict(os.environ)
            for key in ('PYTHON_INDEX_URL', 'COMPOSE_FILE', 'COMPOSE_ENV_FILES'):
                env.pop(key, None)
            command = ['bash', '-c', '''set -euo pipefail
source "$1"
load_python_build_index
test "$PYTHON_INDEX_URL" = "$EXPECTED_INDEX"
test -z "${UNRELATED_SECRET+x}"
''', 'loader-test', str(ROOT / 'scripts/load-build-index.sh')]
            for override in (None, 'https://override.invalid/simple/', ''):
                with self.subTest(override=override):
                    case_env = dict(env, EXPECTED_INDEX=override or 'https://packages.invalid/simple/')
                    if override is not None:
                        case_env['PYTHON_INDEX_URL'] = override
                    result = subprocess.run(command, cwd=root, env=case_env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1 if override == '' else 0, result.stderr)
                    self.assertNotIn('https://', result.stdout + result.stderr)
                    self.assertNotIn('not-for-the-build', result.stdout + result.stderr)
            (root / '.env').unlink()
            env.update(PYTHON_INDEX_URL='https://bake.invalid/simple/', EXPECTED_INDEX='https://bake.invalid/simple/')
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_env_index_is_used_for_packages_and_build_dependencies(self):
        for bake in ('false', 'true'):
            with self.subTest(COMPOSE_BAKE=bake):
                self.build_with_index(bake)

    def build_with_index(self, bake):
        with tempfile.TemporaryDirectory(prefix='python-index-test-') as directory:
            root = Path(directory)
            context, registry = root / 'context', root / 'registry'
            context.mkdir()
            registry.mkdir()
            requests = []

            class Handler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    requests.append(self.path)
                    super().do_GET()

            server = http.server.ThreadingHTTPServer(('0.0.0.0', 0), functools.partial(Handler, directory=str(registry)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            image = 'netbox-python-index-test:' + uuid.uuid4().hex[:12]
            try:
                self.make_registry(registry)
                (context / 'local-plugin').mkdir()
                (context / 'local-plugin/pyproject.toml').write_text(
                    '[build-system]\nrequires=["index-probe==1.0.0"]\nbuild-backend="index_probe"\n')
                shutil.copy2(ROOT / 'scripts/install-build-packages.sh', context / 'install.sh')
                (context / 'scripts').mkdir()
                for name in ('compose-build.sh', 'load-build-index.sh'):
                    shutil.copy2(ROOT / 'scripts' / name, context / 'scripts' / name)
                (context / 'Dockerfile').write_text('''FROM netboxcommunity/netbox:v4.6.7-5.0.2
COPY install.sh /install.sh
COPY local-plugin /local-plugin
RUN --mount=type=secret,id=python_index_url,required=true sh /install.sh index-probe==1.0.0
RUN --mount=type=secret,id=python_index_url,required=true sh /install.sh /local-plugin
RUN test ! -s /run/secrets/python_index_url && test -z "${UV_DEFAULT_INDEX+x}" && /opt/netbox/venv/bin/python -c 'import local_plugin; assert local_plugin.VALUE == 42'
''')
                (context / 'compose.yml').write_text(f'''services:
  test:
    image: {image}
    build:
      context: .
      extra_hosts:
        - "host.docker.internal:host-gateway"
      secrets:
        - python_index_url
secrets:
  python_index_url:
    environment: PYTHON_INDEX_URL
''')
                secret = 'test-password-$literal-' + uuid.uuid4().hex
                index = f'http://test:{secret}@host.docker.internal:{server.server_port}/simple/'
                env_file = root / 'persistent.env'
                env_file.write_text("PYTHON_INDEX_URL='" + index + "'\n")
                (context / '.env').symlink_to(env_file)
                env = dict(os.environ)
                env.pop('PYTHON_INDEX_URL', None)
                env.pop('COMPOSE_FILE', None)
                env.pop('COMPOSE_ENV_FILES', None)
                env['COMPOSE_BAKE'] = bake
                command = ['bash', str(context / 'scripts/compose-build.sh'), '--no-cache']
                if bake == 'false':
                    command[2:2] = ['--env-file', str(env_file)]
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                output = result.stdout + result.stderr
                self.assertNotIn(secret, output)
                self.assertEqual(result.returncode, 0, output.replace(secret, '<redacted>'))
                self.assertGreaterEqual(requests.count('/simple/index-probe/'), 2, requests)
                for cmd in (['docker', 'image', 'inspect', image],
                            ['docker', 'history', '--no-trunc', image]):
                    metadata = subprocess.check_output(cmd, text=True)
                    self.assertNotIn(secret, metadata)
                    self.assertNotIn(index, metadata)
                requests.clear()
                for setting in ('PYTHON_INDEX_URL=\n', ''):
                    (context / '.env').write_text(setting)
                    result = subprocess.run(command, env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0, 'missing index unexpectedly built')
                    self.assertEqual(requests, [], 'missing index attempted a package request')
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                subprocess.run(['docker', 'image', 'rm', image], capture_output=True)

    def make_registry(self, root):
        directory = root / 'simple/index-probe'
        directory.mkdir(parents=True)
        filename = 'index_probe-1.0.0-py3-none-any.whl'
        backend = '''import os, zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    filename = "local_plugin-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(os.path.join(wheel_directory, filename), "w") as wheel:
        wheel.writestr("local_plugin.py", "VALUE = 42\\n")
        wheel.writestr("local_plugin-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\\nName: local-plugin\\nVersion: 1.0.0\\n")
        wheel.writestr("local_plugin-1.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
        wheel.writestr("local_plugin-1.0.0.dist-info/RECORD", "")
    return filename
'''
        with zipfile.ZipFile(directory / filename, 'w') as wheel:
            wheel.writestr('index_probe.py', backend)
            wheel.writestr('index_probe-1.0.0.dist-info/METADATA',
                           'Metadata-Version: 2.1\nName: index-probe\nVersion: 1.0.0\n')
            wheel.writestr('index_probe-1.0.0.dist-info/WHEEL',
                           'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            wheel.writestr('index_probe-1.0.0.dist-info/RECORD', '')
        (directory / 'index.html').write_text(f'<a href="{filename}">{filename}</a>')
