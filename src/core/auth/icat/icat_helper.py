# -*- coding: utf-8 -*-

"""
    :author: Ming Tang
    :copyright: © 2020 CSNS
    :license: GPL V3, see LICENSE for more details.
"""
# pylint: disable=invalid-name,broad-except,consider-using-f-string
import logging
from . import icat
import json
from typing import Optional, List, Any

# ============================================================================
# IcatHelper - ICAT 基本操作封装
# ============================================================================

class IcatHelper:
    def __init__(self, icatUri: str, cert: Any = None):
        """
        初始化 ICAT 助手
        
        参数:
            icatUri: ICAT 服务地址
            cert: SSL 证书，True=验证，False=禁用验证，或者证书路径
        """
        self._icatUri = icatUri
        # 注意：这里 cert 参数改为可配置，原代码是 True
        self._icatService = icat.ICAT(icatUri, cert)

    def getSessionById(self, icatSessionId):
        return icat.Session(self._icatService, str(icatSessionId))

    def loginIcat(self, username, password, plugin="ldap"):
        credentials = []
        credentials.append({"username": username})
        credentials.append({"password": password})
        _icatSession = self._icatService.login(plugin, {"credentials": credentials})
        credentials.clear()
        return _icatSession

    def logoutIcat(self, icatSession):
        try:
            icatSession.logout()
        except Exception as error:
            logging.getLogger("flask.info").error(f"user logout Icat fail:{error}")

    def isLoggedIn(self, username):
        return self._icatService.isLoggedIn(username)

    def getIcatUserByName(self, icatSession, userName):
        """根据用户名 查询 ICAT 用户"""
        _tmp = icatSession.search(
            "SELECT u From User u where u.name=" + "'" + userName + "'"
        )
        if len(_tmp):
            return _tmp[0]["User"]

        return None

    def getIcatUserById(self, icatSession, userId):
        """根据用户 ID 查询 ICAT 用户"""
        if userId is None:
            return None

        _tmp = icatSession.search("SELECT u From User u where u.id=%i" % (int(userId)))
        if len(_tmp):
            return _tmp[0]["User"]

        return None

    def getInstrumentsForFacility(self, icatSession, facilityName='CSNS'):
        return icatSession.search(f"SELECT i FROM Instrument i WHERE i.facility.name='{facilityName}'")

    def getInstrumentsForUser(self, icatSession, facilityName='CSNS'):
        """查询当前用户可访问的谱仪"""
        _username = icatSession.getUserName()
        return icatSession.search(f"SELECT i FROM Instrument i JOIN i.investigationInstruments ii JOIN ii.investigation inv JOIN inv.investigationUsers iu JOIN iu.user u where u.name='{_username}'")

    def getProposalsCountForInstrument(self, icatSession, beamlineName, proposalName):
        """统计某个谱仪下的课题数量"""
        return icatSession.search("SELECT COUNT(i.id) From Investigation i JOIN i.investigationInstruments ii JOIN ii.instrument inst where inst.name='%s' AND i.name LIKE '%s'" % (beamlineName, "%" + proposalName + "%"))
        
    def getProposalsForInstrument(self, icatSession, beamlineName, proposalName, offset=0, num=50):
        """查询某个谱仪下的课题列表"""
        return icatSession.search("SELECT i From Investigation i JOIN i.investigationInstruments ii JOIN ii.instrument inst where inst.name='%s' AND i.name LIKE '%s' LIMIT %i, %i" % (beamlineName, "%" + proposalName + "%", offset, num))

    def getOwnProposalsCount(self, icatSession, proposalName):
        """统计当前用户拥有的课题数量"""
        _username = icatSession.getUserName()
        return icatSession.search("SELECT  COUNT(i.id)  From Investigation i JOIN i.investigationUsers iu JOIN iu.user u where u.name=" + "'" + _username + "'")

    def getOwnProposals(self, icatSession, proposalName, offset=0, num=50):
        """查询当前用户拥有的课题列表"""
        _username = icatSession.getUserName()
        return icatSession.search("SELECT i From Investigation i JOIN i.investigationUsers iu JOIN iu.user u where u.name='%s' LIMIT %i, %i" % (_username, offset, num))

    def getInstrumentsForProposal(self, icatSession, proposalId):
        """查询课题关联的谱仪"""
        return icatSession.search("SELECT i From Instrument i JOIN i.investigationInstruments ii JOIN ii.investigation inv where inv.id='%s'" % (proposalId))

    def getFacilityForProposal(self, icatSession, proposalId):
        return icatSession.search("SELECT f From Facility f JOIN f.investigations i where i.id='%s'" % (proposalId))

    def getDatasetsCountForProposal(self, icatSession, proposalId, name):
        return icatSession.search("SELECT COUNT(ds) FROM Dataset ds JOIN ds.investigation i WHERE i.id = '%s' AND ds.name LIKE '%s'" % (proposalId, "__csns__%" + name + "%"))

    def getDatasetsForProposal(self, icatSession, proposalId, name, offset=0, num=50):
        """统计课题下的 dataset（run）列表"""
        return icatSession.search("SELECT ds FROM Dataset ds JOIN ds.investigation i WHERE i.id = '%s' AND ds.name LIKE '%s' LIMIT %i, %i" % (proposalId, "__csns__%" + name + "%", offset, num))

    def getProposalById(self, icatSession, proposalId):
        """根据 ID 查询课题详情（包含关联数据）"""
        return icatSession.get("Investigation i INCLUDE i.facility, i.datasets, i.investigationUsers iu, iu.user, i.investigationInstruments ii, ii.instrument", proposalId)

    def getOwnInstruments(self, icatSession, offset=0, num=50):
        """查询当前用户可访问的谱仪列表（通过参与的课题关联）"""
        _username = icatSession.getUserName()
        return icatSession.search("SELECT i From Instrument i join i.investigationInstruments ii join ii.investigation iv JOIN iv.investigationUsers iu join iu.user u where u.name='%s' LIMIT %i, %i" % (_username, offset, num))

    def getScientistInstruments(self, icatSession, offset=0, num=50):
        """查询当前用户作为谱仪科学家注册的谱仪列表"""
        _username = icatSession.getUserName()
        return icatSession.search(
            "SELECT i FROM Instrument i JOIN i.instrumentScientists isc JOIN isc.user u "
            "WHERE u.name='%s' LIMIT %i, %i" % (_username, offset, num)
        )

    def getAllInstruments(self, icatSession):
        return icatSession.search("SELECT i From Instrument i")

    def getProposalByRunName(self, icatSession, run_number: str):
        """
        根据 run 号反查所属课题（Investigation）。

        CSNS Dataset 命名规则：__csns__<run_number>_<description>
        run_number 传入格式支持：'21905' 或 'RUN0021905'（自动去掉 RUN 前缀和前导零）

        返回第一条匹配的 Investigation dict，未找到返回 None。
        """
        # 标准化：去掉 RUN 前缀，保留数字部分（不补零，用 LIKE 模糊匹配）
        normalized = run_number.strip().upper()
        if normalized.startswith('RUN'):
            normalized = normalized[3:]
        # 去掉前导零，避免 __csns__0021905 和 __csns__21905 都能命中
        normalized = normalized.lstrip('0') or '0'

        results = icatSession.search(
            "SELECT i FROM Investigation i JOIN i.datasets d "
            "WHERE d.name LIKE '__csns__%%%s%%'" % normalized
        )
        return results[0] if results else None


# ============================================================================
# User - 用户对象
# ============================================================================

class User:
    """用户对象，用于 Flask-Login 等认证框架"""
    
    def __init__(
        self,
        id=None,
        name=None,
        icatSessionId=None,
        isAuthenticated=False,
        isAnonymous=True,
        isActive=False,
    ):
        self.id = id
        self.name = name
        self.icatSessionId = icatSessionId
        self.isAuthenticated = isAuthenticated
        self.isAnonymous = isAnonymous
        self.isActive = isActive
        self.token = None

    def is_authenticated(self):
        return self.isAuthenticated

    def is_active(self):
        return self.isActive

    def is_anonymous(self):
        return self.isAnonymous

    def get_id(self):
        return self.id

    def __str__(self):
        return str(self.__dict__)

    def toJson(self):
        return json.dumps(self, default=lambda o: o.__dict__)


def try_login(icatHelper: IcatHelper, username: str = "", 
              password: str = "", plugin: str = "ldap") -> User:
    """
    快速登录辅助函数
    
    参数:
        icatHelper: IcatHelper 实例
        username: 用户名
        password: 密码
        plugin: 认证插件，默认 ldap
        
    返回:
        User 对象（已认证）
    """
    _icatSession = icatHelper.loginIcat(username, password, plugin)
    _username = _icatSession.getUserName()
    _icatUser = icatHelper.getIcatUserByName(_icatSession, _username)
    _userId = _icatUser["id"]
    
    _user = User()
    _user.name = _username
    _user.id = _userId
    _user.icatSessionId = _icatSession.sessionId
    _user.isAuthenticated = True
    _user.isAnonymous = False
    _user.isActive = True
    return _user


# ============================================================================
# PermissionChecker - 权限检查工具类
# ============================================================================

class PermissionChecker:
    """ICAT 权限检查工具类"""
    
    def __init__(self):
        pass

    @classmethod
    def isSessionValid(cls, icatSession) -> bool:
        """检查 session 是否有效"""
        try:
            if icatSession.getRemainingMinutes() > 0:
                return True
            return False
        except icat.IcatException:
            return False

    @classmethod
    def isInstrumentScientist(cls, icatSession, beamlineName: str) -> bool:
        """检查当前用户是否为某谱仪的科学家"""
        try:
            _username = icatSession.getUserName()
            _scientists = icatSession.search(
                "SELECT u.name FROM User u "
                "JOIN u.instrumentScientists isc JOIN isc.instrument inst "
                "WHERE inst.name='%s'" % (str(beamlineName).upper())
            )
            return _username in _scientists
        except icat.IcatException:
            return False

    @classmethod
    def isProposalOwner(cls, icatSession, proposalId) -> bool:
        """检查当前用户是否为某课题的负责人"""
        try:
            _username = icatSession.getUserName()
            _proposals = icatSession.search(
                "SELECT i.id FROM Investigation i "
                "JOIN i.investigationUsers iu JOIN iu.user u "
                "WHERE u.name='%s'" % (str(_username))
            )
            return int(proposalId) in _proposals
        except icat.IcatException as ex:
            logging.getLogger("flask.info").error(f"IcatException:{ex}")
            return False

    @classmethod
    def isDatasetsOwner(cls, icatSession, proposalId, datasetIdList) -> bool:
        """检查当前用户是否为某数据集的所有者"""
        if not cls.isProposalOwner(icatSession, proposalId):
            return False
        try:
            _datasets = icatSession.search(
                "SELECT d.id FROM Dataset d JOIN d.investigation i WHERE i.id=%i"
                % (int(proposalId))
            )
            for _id in datasetIdList:
                if int(_id) not in _datasets:
                    return False
            return True
        except icat.IcatException as ex:
            logging.getLogger("flask.info").error(f"IcatException:{ex}")
            return False

    @classmethod
    def isPublicProposal(cls, icatSession, proposalId) -> bool:
        """检查课题是否为公开课题"""
        try:
            _pub_proposals = icatSession.search(
                "SELECT i FROM Investigation i WHERE i.releaseDate < CURRENT_TIMESTAMP"
            )
            return int(proposalId) in _pub_proposals
        except icat.IcatException as ex:
            logging.getLogger("flask.info").error(f"IcatException:{ex}")
            return False

    @classmethod
    def isPublicDatasets(cls, icatSession, proposalId, datasetIdList) -> bool:
        """检查数据集是否为公开数据集"""
        if not cls.isPublicProposal(icatSession, proposalId):
            return False
        try:
            _pub_datasets = icatSession.search(
                "SELECT d.id FROM Dataset d JOIN d.investigation i WHERE i.id=%i"
                % (int(proposalId))
            )
            for _id in datasetIdList:
                if int(_id) not in _pub_datasets:
                    return False
            return True
        except icat.IcatException as ex:
            logging.getLogger("flask.info").error(f"IcatException:{ex}")
            return False

    @classmethod
    def isAccessPermitted(cls, icatSession, proposalId, datasetIdList=None) -> bool:
        """
        综合检查：用户是否有权限访问指定的课题/数据集
        
        规则：
        - 如果是课题负责人 -> 有权限
        - 如果是公开课题/数据集 -> 有权限
        - 否则 -> 无权限
        """
        if datasetIdList is None:
            datasetIdList = []
            
        if cls.isProposalOwner(icatSession, proposalId):
            if len(datasetIdList):
                return cls.isDatasetsOwner(icatSession, proposalId, datasetIdList)
            return True

        if cls.isPublicProposal(icatSession, proposalId):
            if len(datasetIdList):
                return cls.isPublicDatasets(icatSession, proposalId, datasetIdList)
            return True

        return False

    @classmethod
    def isProposalBeamline(cls, icatSession, beamlineName: str, proposalId, datasetIdList) -> bool:
        """检查课题是否属于指定谱仪，且数据集有效"""
        proposalIsInstrument = icatSession.search(
            "SELECT i From Instrument i "
            "JOIN i.investigationInstruments ii JOIN ii.investigation inv "
            "where inv.id='%s'" % (proposalId)
        )
        if proposalIsInstrument and proposalIsInstrument[0]["Instrument"]["name"] != beamlineName:
            return False
        try:
            _datasets = icatSession.search(
                "SELECT d.id FROM Dataset d JOIN d.investigation i WHERE i.id=%i"
                % (int(proposalId))
            )
            for _id in datasetIdList:
                if int(_id) not in _datasets:
                    return False
            return True
        except icat.IcatException as ex:
            logging.getLogger("flask.info").error(f"IcatException:{ex}")
            return False

    @classmethod
    def isVerifyRun(cls, icatSession, beamlineName: str, proposalId, datasetIdList) -> bool:
        """
        验证 run 的访问权限（用于谱仪科学家验证）
        
        规则：
        - 如果是谱仪科学家且课题属于该谱仪 -> 有权限
        - 如果是数据集所有者 -> 有权限
        - 否则 -> 无权限
        """
        if cls.isInstrumentScientist(icatSession, beamlineName):
            return cls.isProposalBeamline(icatSession, beamlineName, proposalId, datasetIdList)
        return cls.isDatasetsOwner(icatSession, proposalId, datasetIdList)