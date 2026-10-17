"""Offline, non-mutating diagnostics against actual host contracts."""
import argparse
import json
import os
from pathlib import Path

import pytest
import yaml

from assistant import doctor


def save(config):
    home = Path(os.environ['HERMES_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text(yaml.safe_dump(config))
    return home


def configured():
    return {'plugins': {'enabled': ['photon-personal-assistant'], 'entries': {
        'photon-personal-assistant': {'granted_capabilities': ['gateway.platform_actions']}}},
        'platforms': {'photon': {'enabled': True}}}


def checks():
    return {row['id']: row for row in doctor.run_checks()}


@pytest.mark.parametrize('version,status', [('0.22.2', 'ok'), ('0.22.1', 'fail'), ('unknown', 'fail')])
def test_version(monkeypatch, version, status):
    monkeypatch.setattr(doctor, 'running_hermes_version', lambda: version)
    assert checks()['hermes_version']['status'] == status


@pytest.mark.parametrize('mode,status', [('in_process', 'ok'), ('host', 'fail')])
def test_isolation(mode, status):
    save({'plugins': {'isolation': mode}})
    assert checks()['isolation']['status'] == status


@pytest.mark.parametrize('disabled,status', [([], 'ok'), (['photon-platform'], 'fail'), (['platforms/photon'], 'fail')])
def test_photon_plugin(disabled, status):
    cfg = configured()
    cfg['plugins']['disabled'] = disabled
    save(cfg)
    assert checks()['photon_plugin']['status'] == status


@pytest.mark.parametrize('present,status', [(True, 'ok'), (False, 'fail')])
def test_photon_presence(monkeypatch, present, status):
    save(configured())
    monkeypatch.delenv('PHOTON_PROJECT_SECRET')
    if present:
        monkeypatch.setenv('PHOTON_PROJECT_SECRET', 'synthetic-private-value')
    result = checks()
    assert result['photon_config']['status'] == status
    assert 'synthetic-private-value' not in json.dumps(result)


def test_photon_disabled_and_extra_credentials(monkeypatch):
    cfg = configured()
    cfg['platforms']['photon'] = {'enabled': False, 'extra': {'project_id': 'synthetic', 'project_secret': 'synthetic'}}
    for key in ('PHOTON_PROJECT_ID', 'PHOTON_PROJECT_SECRET'):
        monkeypatch.delenv(key)
    save(cfg)
    assert checks()['photon_config']['status'] == 'fail'
    cfg['platforms']['photon']['enabled'] = True
    save(cfg)
    assert checks()['photon_config']['status'] == 'ok'


@pytest.mark.parametrize('enabled,status', [(True, 'ok'), (False, 'fail')])
def test_assistant_enabled(enabled, status):
    cfg = configured()
    if not enabled:
        cfg['plugins']['disabled'] = ['photon-personal-assistant']
    save(cfg)
    assert checks()['assistant_plugin']['status'] == status


@pytest.mark.parametrize('grant,status', [(True, 'ok'), (False, 'fail')])
def test_capability(grant, status):
    cfg = configured()
    if not grant:
        cfg['plugins']['entries'] = {}
    save(cfg)
    assert checks()['capability']['status'] == status


@pytest.mark.parametrize('deferred,status', [(True, 'warn'), (False, 'ok')])
def test_visibility_auto_detects_host_fix(monkeypatch, deferred, status):
    import tools.tool_search
    calls = []
    def probe(name, defer_tools=None):
        calls.append(name)
        return deferred
    monkeypatch.setattr(tools.tool_search, 'is_deferrable_tool_name', probe)
    assert checks()['reaction_visibility']['status'] == status
    assert calls == ['photon_react']


@pytest.mark.parametrize('configured_override,support,status', [(False, False, 'ok'), (True, False, 'warn'), (True, True, 'ok')])
def test_reasoning(monkeypatch, configured_override, support, status):
    from gateway.config import ChannelOverride
    cfg = configured()
    if configured_override:
        cfg['platforms']['photon']['channel_overrides'] = {'synthetic-chat': {'reasoning_effort': 'low'}}
    if support:
        monkeypatch.setattr(ChannelOverride, '__dataclass_fields__', {**ChannelOverride.__dataclass_fields__, 'reasoning_effort': object()})
    save(cfg)
    assert checks()['reasoning']['status'] == status


@pytest.mark.parametrize('owner,status', [('auto', 'ok'), ('disabled', 'warn'), ('invalid', 'fail')])
def test_owner(owner, status):
    save({'photon_followthrough': {'owner': owner}})
    row = checks()['owner']
    assert row['status'] == status
    if owner == 'auto':
        assert 'native' in row['why']


@pytest.mark.parametrize('legacy', ['scripts/messages_followthrough.py', 'skills/other/messaging-personal-assistant/SKILL.md'])
def test_legacy_detection(legacy):
    home = save({})
    path = home / legacy
    path.parent.mkdir(parents=True)
    path.write_text('synthetic')
    assert 'legacy' in checks()['owner']['why']


@pytest.mark.parametrize('value,status', [(600, 'ok'), (0, 'ok'), (-1, 'fail'), (True, 'fail')])
def test_bubbles(value, status):
    save({'plugins': {'entries': {'photon-personal-assistant': {'settings': {'bubble_max_chars': value}}}}})
    assert checks()['bubbles']['status'] == status


@pytest.mark.parametrize('enabled,token,status', [(False, False, 'ok'), (True, False, 'warn'), (True, True, 'ok')])
def test_calendar(enabled, token, status):
    cfg = configured()
    cfg['plugins']['entries']['photon-personal-assistant']['settings'] = {'calendar': {
        'enabled': enabled, 'approval_granted': True, 'provider': 'google_workspace',
        'calendar_id': 'primary', 'approved_scopes': [{'chat_id': 'any;-;+15555550000', 'user_id': 'synthetic'}]}}
    home = save(cfg)
    if token:
        (home / 'google_token.json').write_text('DO NOT READ')
    row = checks()['calendar']
    assert row['status'] == status
    assert 'DO NOT READ' not in json.dumps(row)


def test_json_exit_and_readonly(capsys, monkeypatch):
    home = save(configured())
    monkeypatch.setattr(doctor, 'running_hermes_version', lambda: '0.22.2')
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    assert doctor.main(argparse.Namespace(json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] is True
    assert len(result['checks']) >= 10
    after = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    assert after == before
    save({})
    assert doctor.main(argparse.Namespace(json=False)) == 1
    assert '✗' in capsys.readouterr().out


def test_nonexistent_home_is_not_created(monkeypatch, tmp_path):
    home = tmp_path / 'absent'
    monkeypatch.setenv('HERMES_HOME', str(home))
    doctor.run_checks()
    assert not home.exists()


def test_invalid_config_does_not_leak(capsys):
    home = save({})
    (home / 'config.yaml').write_text('private-value: [')
    assert doctor.main(argparse.Namespace(json=True)) == 1
    output = capsys.readouterr().out
    assert 'private-value' not in output
    assert json.loads(output)['checks'][0]['id'] == 'config'
