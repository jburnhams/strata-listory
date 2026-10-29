import json
from app import settings


def test_doctor_reports_the_environment(cli):
    r = cli('doctor', check=False)
    assert 'app 0.1.0' in r.stdout and 'fastapi' in r.stdout and r.code == 0


def test_catalogue_from_the_command_line(cli):
    b = json.loads(cli('add', 'Dune', '--author', 'Frank Herbert', '--json').stdout)
    assert b['id'] == 1
    cli('edit', 1, '--status', 'listening')
    assert json.loads(cli('list', '--status', 'listening', '--json').stdout)[0]['title'] == 'Dune'
    assert 'removed #1' in cli('remove', 1).stdout
    assert cli('remove', 1, check=False).code != 0


def test_settings_are_viewed_and_updated_from_the_command_line(cli, tmp_path, monkeypatch):
    monkeypatch.delenv('APP_DATA', raising=False)
    elsewhere = tmp_path / 'elsewhere'
    assert str(elsewhere) in cli('set', 'data_dir', elsewhere).stdout and settings.data_dir() == str(elsewhere.resolve())
    assert 'data_dir' in cli('set', 'data_dir', 'default').stdout
    assert cli('set', 'nope', 'x', check=False).code != 0


def test_the_command_line_runs_as_a_real_process(cli_subprocess):
    assert 'app 0.1.0' in cli_subprocess('doctor').stdout
    assert cli_subprocess('add', 'Subprocess').stdout.strip().startswith('#1')
