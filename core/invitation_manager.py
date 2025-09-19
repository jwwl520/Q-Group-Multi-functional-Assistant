import json
from collections import defaultdict
from astrbot.core.config.astrbot_config import AstrBotConfig

class InvitationManager:
    def __init__(self, config: AstrBotConfig):
        self.config = config
        self.invitation_data = defaultdict(lambda: defaultdict(list), self.config.get("invitation_data", {}))

    def save_data(self):
        """保存邀请数据到配置"""
        self.config.set("invitation_data", self.invitation_data)
        self.config.save_config()

    def add_invite(self, group_id: str, inviter_id: str, invitee_id: str):
        """记录邀请关系"""
        self.invitation_data[group_id][inviter_id].append(invitee_id)
        self.save_data()

    def get_invites_by_group(self, group_id: str):
        """获取群聊的所有邀请记录"""
        return self.invitation_data.get(group_id, {})

    def get_invites_by_user(self, group_id: str, user_id: str):
        """获取用户在指定群聊的邀请记录"""
        group_invites = self.get_invites_by_group(group_id)
        return group_invites.get(user_id, [])
