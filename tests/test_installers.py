"""Exercise generated commands and dispatch without installing an application."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('installer', ['install.sh', 'install-linux.sh'])
@pytest.mark.parametrize('platform', ['Darwin', 'Linux'])
def test_fire_lifecycle(tmp_path, installer, platform):
    source = (ROOT / 'scripts' / installer).read_text()
    helpers = source[source.index('install_fire() {'):source.index('do_uninstall() {')]
    mock_bin = tmp_path / 'mock'
    mock_bin.mkdir()
    for name, body in {
        'uname': f'echo {platform}',
        'open': 'printf "%s" "$1" > "$HOME/opened"',
        'xdg-open': 'printf "%s" "$1" > "$HOME/opened"',
    }.items():
        p = mock_bin / name
        p.write_text('#!/bin/sh\n' + body + '\n')
        p.chmod(0o755)
    profile = tmp_path / '.profile'
    profile.write_text('export EXISTING_SETTING=yes\n')
    command_dir = tmp_path / 'commands with spaces'
    env = dict(os.environ, HOME=str(tmp_path), ZDOTDIR=str(tmp_path / 'zsh config'),
               DOUYIN_BIN_DIR=str(command_dir), PATH=f'{mock_bin}:/usr/bin:/bin')
    script = helpers + '''
set -eu
panel_url() { printf 'http://127.0.0.1:9876/'; }
ok() { :; }
install_fire
install_fire
source "$HOME/.profile"
fire
'''
    subprocess.run(['bash', '-c', script], env=env, check=True)
    assert (tmp_path / 'opened').read_text() == 'http://127.0.0.1:9876/'
    assert profile.read_text().count('# douyin-fire command') == 1
    assert 'export EXISTING_SETTING=yes' in profile.read_text()
    assert not (tmp_path / '.bash_profile').exists()
    subprocess.run(['bash', '-c', helpers + '\nremove_fire'], env=env, check=True)
    assert not (command_dir / 'fire').exists()
    assert profile.read_text() == 'export EXISTING_SETTING=yes\n'


def test_linux_dispatch_local_and_piped(tmp_path):
    mock_bin = tmp_path / 'mock'
    mock_bin.mkdir()
    uname = mock_bin / 'uname'
    uname.write_text('#!/bin/sh\necho Linux\n')
    uname.chmod(0o755)
    wrapper = tmp_path / 'install.sh'
    wrapper.write_text((ROOT / 'scripts/install.sh').read_text())
    target = tmp_path / 'install-linux.sh'
    stub = '#!/bin/bash\nprintf "%s\\n" "$@"\n'
    target.write_text(stub)
    env = dict(os.environ, PATH=f'{mock_bin}:/usr/bin:/bin')
    result = subprocess.run(['bash', str(wrapper), '--port', '9876', '--no-open'],
                            env=env, text=True, capture_output=True, check=True)
    assert result.stdout.splitlines() == ['--port', '9876', '--no-open']
    target.unlink()
    curl = mock_bin / 'curl'
    curl.write_text('''#!/bin/bash
set -eu
[[ "$*" == *https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install-linux.sh* ]]
while [ "$1" != '-o' ]; do shift; done
cp "$HOME/linux-stub" "$2"
''')
    curl.chmod(0o755)
    (tmp_path / 'linux-stub').write_text(stub)
    env['HOME'] = str(tmp_path)
    result = subprocess.run(['bash', '-s', '--', '--port', '9876'],
                            input=wrapper.read_text(), cwd=tmp_path, env=env,
                            text=True, capture_output=True, check=True)
    assert result.stdout.splitlines() == ['--port', '9876']
