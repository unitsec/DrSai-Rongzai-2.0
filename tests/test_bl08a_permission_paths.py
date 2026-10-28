import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.agent.agent import RongZaiAgent
from src.core.auth.icat.icat_helper import IcatHelper
from src.core.auth.icat_permission_provider import IcatPermissionProvider
from src.core.auth.permission_provider import ProposalInfo, UserPermissions


@pytest.fixture
def permissions(tmp_path, monkeypatch):
    root = tmp_path / 'BL08A'
    (root / 'P08TEST' / '2026' / 'RUN0000177').mkdir(parents=True)
    monkeypatch.setenv('BL08A_RAW_DATA_ROOT', str(root))
    monkeypatch.setenv('SERVICE_MODE', 'PROD')
    login = Mock(return_value=Mock())
    logout = Mock()
    monkeypatch.setattr(IcatHelper, 'loginIcat', login)
    monkeypatch.setattr(IcatHelper, 'logoutIcat', logout)
    monkeypatch.setattr(IcatHelper, 'getProposalByRunName', Mock(return_value={
        'Investigation': {'name': 'P08TEST'},
    }))
    provider = Mock(return_value=UserPermissions(
        'alice', ['BL08A'], [ProposalInfo('42', 'P08TEST', beamline='BL08A')], 'icat',
    ))
    monkeypatch.setattr(IcatPermissionProvider, 'get_user_permissions', provider)
    agent = SimpleNamespace(
        _user_id='alice', _thread_id='test', _extra_work_dirs=[],
        _user_cookies={'sso-token': 'token', 'rz-auth-provider': 'icat', 'rz-role': 'user'},
    )
    return root, agent, provider, login, logout


def check(agent, beamline='BL08A'):
    return asyncio.run(RongZaiAgent.check_user_permission_for_file(
        agent, 'alice', beamline, 'RUN0000177',
    ))


@pytest.mark.parametrize('role', ['user', 'scientist'])
def test_original_icat_flow_returns_raw_data_after_authorization(permissions, role):
    root, agent, provider, login, logout = permissions
    agent._user_cookies['rz-role'] = role
    result = check(agent)
    assert result['status'] == 'success'
    assert result['data_root'] == str(root / 'P08TEST' / '2026')
    assert result['raw_data_path'] == str(root / 'P08TEST' / '2026' / 'RUN0000177')
    assert 'reduction_folder_path' not in result
    assert result['raw_data_path'] in agent._extra_work_dirs
    provider.assert_called_once_with('alice', 'token', role=role)
    login.assert_called_once_with('alice', 'token')
    logout.assert_called_once_with(login.return_value)


def test_denied_icat_permission_does_not_inspect_mount(permissions, monkeypatch):
    _, agent, provider, _, _ = permissions
    provider.return_value.proposals = []
    monkeypatch.setattr('src.core.agent.agent.Path.iterdir', Mock(side_effect=AssertionError('unauthorized')))
    result = check(agent)
    assert result['status'] == 'failed'
    assert '无权访问' in result['error']
    assert not agent._extra_work_dirs


@pytest.mark.parametrize('layout', ['missing-proposal', 'missing-run', 'two-years'])
def test_unavailable_or_ambiguous_raw_path_does_not_grant_access(permissions, layout):
    root, agent, _, _, _ = permissions
    folder = root / 'P08TEST' / '2026' / 'RUN0000177'
    if layout == 'missing-proposal':
        (root / 'P08TEST').rename(root / 'OTHER')
    elif layout == 'missing-run':
        folder.rmdir()
    else:
        (root / 'P08TEST' / '2025' / 'RUN0000177').mkdir(parents=True)
    result = check(agent)
    assert result['status'] == 'failed'
    assert '有权限' in result['error']
    assert not agent._extra_work_dirs


def test_domas_bl08a_does_not_switch_to_icat(permissions, monkeypatch):
    _, agent, _, login, _ = permissions
    agent._user_cookies.update({'rz-auth-provider': 'domas', 'rz-role': 'scientist'})
    domas = Mock()
    domas.get_proposal_by_runid.return_value = {'proposalId': 'DOMAS-PROPOSAL'}
    monkeypatch.setattr('src.core.agent.agent.DomasPermissionProvider', Mock(return_value=domas))
    monkeypatch.setattr('src.core.agent.agent.os.path.exists', lambda _: False)
    result = check(agent)
    domas.get_proposal_by_runid.assert_called_once_with('token', 'alice', 'RUN0000177', 'BL08A')
    login.assert_not_called()
    assert result['status'] == 'failed'
    assert 'DOMAS-PROPOSAL' in result['error']
    assert 'reduction' in result['error']


def test_other_icat_beamlines_keep_reduction_logic(permissions, monkeypatch):
    _, agent, _, _, _ = permissions
    monkeypatch.setattr('src.core.agent.agent.os.path.exists', lambda _: False)
    result = check(agent, 'BL16')
    assert result['status'] == 'failed'
    assert 'reduction' in result['error']
