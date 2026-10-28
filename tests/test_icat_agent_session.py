import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.agent.agent import RongZaiAgent
from src.core.auth.icat.icat import ICAT, IcatException
from src.core.auth.icat.icat_helper import IcatHelper
from src.core.auth.icat_permission_provider import IcatPermissionProvider
from src.core.auth.permission_provider import ProposalInfo, UserPermissions


@pytest.fixture
def local_agent(monkeypatch):
    monkeypatch.setenv('SERVICE_MODE', 'PROD')
    monkeypatch.setenv('ICAT_URI', 'https://icat.test')
    session = Mock()
    session.getUserName.return_value = 'ldap/alice'
    monkeypatch.setattr(IcatHelper, 'getSessionById', Mock(return_value=session))
    monkeypatch.setattr(IcatHelper, 'loginIcat', Mock(side_effect=AssertionError('must not login')))
    proposal = ProposalInfo('123', 'P16TEST', beamline='BL16')
    monkeypatch.setattr(IcatPermissionProvider, '_fetch_beamlines', Mock(return_value=['BL16']))
    monkeypatch.setattr(IcatPermissionProvider, '_fetch_proposals_with_runs', Mock(return_value=[proposal]))
    monkeypatch.setattr(IcatPermissionProvider, '_fetch_scientist_beamlines', Mock(return_value=['BL16']))
    monkeypatch.setattr(IcatPermissionProvider, '_fetch_proposals_for_scientist', Mock(return_value=[proposal]))
    monkeypatch.setattr(
        IcatHelper, 'getProposalByRunName',
        Mock(return_value={'Investigation': {'id': 123, 'name': 'P16TEST'}}),
    )
    agent = SimpleNamespace(
        _user_id='rz-local-test', _thread_id='test', _extra_work_dirs=[],
        _user_cookies={'rz-icat-session': 'secret-session', 'rz-auth-provider': 'icat', 'rz-role': 'user'},
    )
    monkeypatch.setattr('src.core.agent.agent.os.path.exists', lambda _: True)
    monkeypatch.setattr('src.core.agent.agent.os.path.isdir', lambda _: True)
    monkeypatch.setattr('src.core.agent.agent.os.listdir', lambda _: ['RUN0023568_reduced'])
    mkdir = Mock()
    monkeypatch.setattr('src.core.agent.agent.os.makedirs', mkdir)
    return agent, session, mkdir


def check(agent, beamline='BL16', run='RUN0023568'):
    return asyncio.run(RongZaiAgent.check_user_permission_for_file(agent, 'wrong-model-user', beamline, run))


@pytest.mark.parametrize('role', ['user', 'scientist'])
def test_local_session_returns_paths_without_password_or_model_identity(local_agent, role):
    agent, session, _ = local_agent
    agent._user_cookies['rz-role'] = role
    for _ in range(2):
        result = check(agent, 'bl16')
        assert result['status'] == 'success'
        assert result['proposal'] == 'P16TEST'
        assert result['reduction_folder_path'] == '/csns_data/BL16/P16TEST/reduction'
        assert result['run_folders_path'] == ['RUN0023568_reduced']
        assert result['analysis_folder_path'] == '/csns_data/BL16/P16TEST/analysis'
        assert result['info_folder_path'] == '/csns_data/BL16/BL16_info'
        assert 'ldap/alice' in result['description']
        assert 'secret-session' not in str(result)
    session.logout.assert_not_called()
    assert '/csns_data/BL16/P16TEST/reduction' in agent._extra_work_dirs


@pytest.mark.parametrize('role', ['user', 'scientist'])
def test_wrong_beamline_denied_even_with_scientist_cookie(local_agent, role):
    agent, session, mkdir = local_agent
    agent._user_cookies['rz-role'] = role
    result = check(agent, 'BL09')
    assert result['status'] == 'failed'
    assert 'reduction_folder_path' not in result
    assert not agent._extra_work_dirs
    mkdir.assert_not_called()
    session.logout.assert_not_called()


def test_other_proposal_denied(local_agent, monkeypatch):
    agent, _, _ = local_agent
    monkeypatch.setattr(IcatHelper, 'getProposalByRunName', Mock(return_value={
        'Investigation': {'name': 'OTHER'},
    }))
    assert check(agent)['status'] == 'failed'
    assert not agent._extra_work_dirs


def test_expired_session_is_explicit_and_never_leaks_credential(local_agent):
    agent, session, _ = local_agent
    session.getUserName.side_effect = IcatException('SESSION', 'secret-session')
    result = check(agent)
    assert result['status'] == 'failed'
    assert '重新' in result['error']
    assert 'secret-session' not in str(result)
    assert not agent._extra_work_dirs


def test_transport_exception_is_redacted(local_agent):
    agent, session, _ = local_agent
    session.getUserName.side_effect = RuntimeError('https://icat.test/session/secret-session')
    result = check(agent)
    assert result['status'] == 'failed'
    assert 'secret-session' not in str(result)


def test_missing_cookie_never_attempts_empty_password_login(local_agent):
    agent, _, _ = local_agent
    agent._user_cookies.pop('rz-icat-session')
    result = check(agent)
    assert result['status'] == 'failed'
    assert '重新' in result['error']
    IcatHelper.loginIcat.assert_not_called()


def test_missing_data_directory_is_not_authentication_failure(local_agent, monkeypatch):
    agent, _, _ = local_agent
    monkeypatch.setattr('src.core.agent.agent.os.path.exists', lambda _: False)
    result = check(agent)
    assert result['status'] == 'failed'
    assert '不存在 reduction' in result['error']
    assert not agent._extra_work_dirs


def test_missing_run_directory_does_not_grant_access(local_agent, monkeypatch):
    agent, _, _ = local_agent
    monkeypatch.setattr('src.core.agent.agent.os.listdir', lambda _: ['RUN0000001_reduced'])
    result = check(agent)
    assert result['status'] == 'failed'
    assert '不存在包含 run' in result['error']
    assert not agent._extra_work_dirs


@pytest.mark.parametrize('proposal', ['../other', '..', 'path\\other'])
def test_invalid_proposal_directory_is_rejected(local_agent, monkeypatch, proposal):
    agent, _, _ = local_agent
    monkeypatch.setattr(IcatHelper, 'getProposalByRunName', Mock(return_value={
        'Investigation': {'name': proposal},
    }))
    monkeypatch.setattr(IcatPermissionProvider, '_fetch_proposals_with_runs', Mock(return_value=[
        ProposalInfo('123', proposal, beamline='BL16'),
    ]))
    result = check(agent)
    assert result['status'] == 'failed'
    assert not agent._extra_work_dirs


def test_missing_icat_configuration(local_agent, monkeypatch):
    agent, _, _ = local_agent
    monkeypatch.delenv('ICAT_URI')
    assert '未配置' in check(agent)['error']


@pytest.mark.parametrize('role', ['user', 'scientist'])
def test_domas_flow_is_unchanged(local_agent, monkeypatch, role):
    agent, _, _ = local_agent
    agent._user_cookies.update({'rz-auth-provider': 'domas', 'sso-token': 'sso-token', 'rz-role': role})
    provider = Mock()
    provider.get_proposal_by_runid.return_value = {'proposalId': 'P16TEST'}
    provider.get_user_permissions.return_value = UserPermissions(
        'sso-user', ['BL16'], [ProposalInfo('P16TEST', 'P16TEST')], 'domas',
    )
    provider._fetch_runs_for_proposal.return_value = [
        SimpleNamespace(to_dict=lambda: {'run_number': 'RUN0023568'}),
    ]
    monkeypatch.setattr('src.core.agent.agent.DomasPermissionProvider', Mock(return_value=provider))
    assert check(agent)['status'] == 'success'
    IcatHelper.getSessionById.assert_not_called()


def test_owned_session_is_still_closed(local_agent):
    provider = IcatPermissionProvider('https://icat.test')
    session = Mock()
    session.getUserName.return_value = 'alice'
    provider._helper = Mock()
    provider._helper.loginIcat.return_value = session
    provider.get_user_permissions('alice', 'password')
    provider._helper.logoutIcat.assert_called_once_with(session)


def test_icat_logout_targets_session_endpoint(monkeypatch):
    delete = Mock(return_value=Mock(status_code=204))
    monkeypatch.setattr('src.core.auth.icat.icat.requests.delete', delete)
    ICAT('https://icat.test', True)._logout('test-session')
    delete.assert_called_once_with(
        'https://icat.test/icat/session/test-session', params=None, verify=True, timeout=5,
    )
