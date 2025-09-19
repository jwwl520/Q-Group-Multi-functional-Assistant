import asyncio
import random
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.filter.permission import PermissionType
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from .core.invitation_manager import InvitationManager
from astrbot.core.utils.session_waiter import session_waiter, SessionController
from astrbot.core.message.message_event_result import MessageChain
from astrbot import logger


@register(
    "astrbot_plugin_integrated",
    "YourName",
    "一个集成了邀请统计和分群广播的综合插件",
    "v1.0.0"
)
class IntegratedPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.enabled_groups = self.config.get("enabled_groups", [])
        self.broadcast_enabled_groups = self.config.get("broadcast_enabled_groups", [])
        self.invitation_manager = InvitationManager(self.config)
        self.broadcast_message = None

    # region Feature Toggle
    def is_feature_enabled(self, group_id: str) -> bool:
        """检查指定群组是否启用了邀请统计功能"""
        return group_id in self.enabled_groups

    def is_broadcast_enabled(self, group_id: str) -> bool:
        """检查指定群组是否启用了广播功能"""
        return group_id in self.broadcast_enabled_groups

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("功能开启")
    async def enable_feature(self, event: AiocqhttpMessageEvent):
        """为当前群组开启邀请统计功能"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此指令")
            return

        if self.is_feature_enabled(group_id):
            yield event.plain_result("本群邀请统计功能已开启，无需重复操作")
            return

        self.enabled_groups.append(group_id)
        self.config.save_config()
        yield event.plain_result("本群已成功开启邀请统计功能")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("功能关闭")
    async def disable_feature(self, event: AiocqhttpMessageEvent):
        """为当前群组关闭邀请统计功能"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此指令")
            return

        if not self.is_feature_enabled(group_id):
            yield event.plain_result("本群邀请统计功能未开启")
            return

        self.enabled_groups.remove(group_id)
        self.config.save_config()
        yield event.plain_result("本群已成功关闭邀请统计功能")
    # endregion

    # region Invitation Statistics
    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def event_monitoring(self, event: AiocqhttpMessageEvent):
        """监听群成员增加事件"""
        raw_message = getattr(event.message_obj, "raw_message", None)
        if (
            not isinstance(raw_message, dict)
            or raw_message.get("post_type") != "notice"
            or raw_message.get("notice_type") != "group_increase"
        ):
            return

        group_id = event.get_group_id()
        if not self.is_feature_enabled(group_id):
            return

        if raw_message.get("sub_type") == "invite":
            inviter_id = str(raw_message.get("operator_id"))
            invitee_id = str(raw_message.get("user_id"))
            self.invitation_manager.add_invite(group_id, inviter_id, invitee_id)

    @filter.command("查询邀请")
    async def query_invites(self, event: AiocqhttpMessageEvent):
        """查询邀请信息. 用法: /查询邀请 [@用户]"""
        group_id = event.get_group_id()
        if not self.is_feature_enabled(group_id):
            yield event.plain_result("本群未开启邀请统计功能")
            return

        ats = event.get_ats()
        if ats:
            # 查询指定用户的邀请
            user_id = ats[0]
            invited_list = self.invitation_manager.get_invites_by_user(group_id, user_id)
            count = len(invited_list)
            try:
                user_info = await event.bot.get_group_member_info(group_id=int(group_id), user_id=int(user_id))
                user_name = user_info.get("card") or user_info.get("nickname", user_id)
            except Exception:
                user_name = f"未知用户"

            reply = f"用户 {user_name} ({user_id}) 邀请了 {count} 人。\n"
            if count > 0:
                invited_members_info = []
                for member_id in invited_list:
                    try:
                        member_info = await event.bot.get_group_member_info(group_id=int(group_id), user_id=int(member_id))
                        member_name = member_info.get("card") or member_info.get("nickname", member_id)
                        invited_members_info.append(f"{member_name} ({member_id})")
                    except Exception:
                        invited_members_info.append(f"未知或已退群用户 ({member_id})")
                reply += "邀请列表:\n" + "\n".join(invited_members_info)
        else:
            # 查询全群的邀请
            group_invites = self.invitation_manager.get_invites_by_group(group_id)
            if not group_invites:
                yield event.plain_result("本群暂无邀请记录")
                return
            
            reply = "本群邀请排行榜:\n"
            sorted_inviters = sorted(group_invites.items(), key=lambda item: len(item[1]), reverse=True)
            
            for i, (inviter_id, invited_list) in enumerate(sorted_inviters[:10]): # Display top 10
                count = len(invited_list)
                try:
                    user_info = await event.bot.get_group_member_info(group_id=int(group_id), user_id=int(inviter_id))
                    user_name = user_info.get("card") or user_info.get("nickname", inviter_id)
                except Exception:
                    user_name = f"未知或已退群用户"
                reply += f"{i+1}. {user_name} ({inviter_id}): {count} 人\n"

        yield event.plain_result(reply)
    # endregion

    # region Broadcast
    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("开启广播")
    async def enable_broadcast(self, event: AiocqhttpMessageEvent):
        """为当前群组开启广播功能"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此指令")
            return

        if self.is_broadcast_enabled(group_id):
            yield event.plain_result("本群广播功能已开启，无需重复操作")
            return

        self.broadcast_enabled_groups.append(group_id)
        self.config.set("broadcast_enabled_groups", self.broadcast_enabled_groups)
        self.config.save_config()
        yield event.plain_result("本群已成功开启广播功能")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("关闭广播")
    async def disable_broadcast(self, event: AiocqhttpMessageEvent):
        """为当前群组关闭广播功能"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此指令")
            return

        if not self.is_broadcast_enabled(group_id):
            yield event.plain_result("本群广播功能未开启")
            return

        self.broadcast_enabled_groups.remove(group_id)
        self.config.set("broadcast_enabled_groups", self.broadcast_enabled_groups)
        self.config.save_config()
        yield event.plain_result("本群已成功关闭广播功能")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("广播列表")
    async def broadcast_list(self, event: AiocqhttpMessageEvent):
        """查看所有群聊的广播开启状态"""
        all_groups = await event.bot.get_group_list()
        all_groups.sort(key=lambda x: x["group_id"])
        
        enabled_list = []
        disabled_list = []

        for group in all_groups:
            group_id = str(group["group_id"])
            group_name = group["group_name"]
            info = f"{group_name} ({group_id})"
            if self.is_broadcast_enabled(group_id):
                enabled_list.append(info)
            else:
                disabled_list.append(info)
        
        reply = "【广播功能开启的群聊】\n"
        reply += "\n".join(enabled_list) if enabled_list else "无"
        reply += "\n\n【广播功能关闭的群聊】\n"
        reply += "\n".join(disabled_list) if disabled_list else "无"
        
        yield event.plain_result(reply)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("广播")
    async def broadcast(self, event: AiocqhttpMessageEvent):
        """向所有启用广播的群聊广播消息"""
        group_id = event.get_group_id()
        sender_id = event.get_sender_id()
        yield event.plain_result("请在30秒内发送要广播的消息，输入"取消广播"可取消。")

        @session_waiter(timeout=30)
        async def broadcast_waiter(controller: SessionController, event: AiocqhttpMessageEvent):
            if group_id != event.get_group_id() or sender_id != event.get_sender_id():
                return

            if event.message_str == "取消广播":
                await event.send(event.make_result().message("广播已取消"))
                controller.stop()
                return

            self.broadcast_message = await event._parse_onebot_json(
                MessageChain(chain=event.message_obj.message)
            )
            
            able_gids = self.broadcast_enabled_groups
            if not able_gids:
                await event.send(event.make_result().message("没有可广播的群聊"))
                controller.stop()
                return

            await event.send(event.make_result().message(f"准备向 {len(able_gids)} 个群广播，请发送"确认广播""))

            @session_waiter(timeout=30)
            async def confirm_waiter(confirm_controller: SessionController, confirm_event: AiocqhttpMessageEvent):
                if group_id != confirm_event.get_group_id() or sender_id != confirm_event.get_sender_id():
                    return
                
                if confirm_event.message_str == "确认广播":
                    await confirm_event.send(confirm_event.make_result().message("正在广播中..."))
                    success_count = 0
                    failure_count = 0
                    for gid in able_gids:
                        await asyncio.sleep(random.uniform(1, 3))
                        try:
                            await event.bot.send_group_msg(group_id=int(gid), message=self.broadcast_message)
                            success_count += 1
                        except Exception as e:
                            failure_count += 1
                            logger.error(f"向群组 {gid} 发送广播失败: {e}")
                    
                    await confirm_event.send(confirm_event.make_result().message(f"广播完成\n成功: {success_count} | 失败: {failure_count}"))
                    self.broadcast_message = None
                    confirm_controller.stop()
                else:
                    await confirm_event.send(confirm_event.make_result().message("广播已取消"))
                    confirm_controller.stop()

            try:
                await confirm_waiter(event)
            except TimeoutError:
                await event.send(event.make_result().message("确认超时，广播已取消"))
            finally:
                controller.stop()

        try:
            await broadcast_waiter(event)
        except TimeoutError:
            yield event.plain_result("等待超时，广播已取消")
        finally:
            event.stop_event()
    # endregion

    async def initialize(self):
        """插件初始化"""
        # 从配置中加载数据
        self.enabled_groups = self.config.get("enabled_groups", [])
        self.broadcast_enabled_groups = self.config.get("broadcast_enabled_groups", [])
        self.invitation_manager = InvitationManager(self.config)
        logger.info("Integrated Plugin Loaded.")

    async def terminate(self):
        """插件卸载/停用"""
        self.config.save_config()
        logger.info("Integrated Plugin Terminated and config saved.")