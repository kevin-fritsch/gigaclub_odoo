import asyncio
import logging
import threading

import discord  # noqa: W7936

from odoo import _, api, http, registry
from odoo.http import request

_logger = logging.getLogger(__name__)


class MainController(http.Controller):
    class MyBot(discord.Client):
        def __init__(self, env):
            intents = discord.Intents.default()
            intents.members = True
            intents.guilds = True
            discord.Client.__init__(self, intents=intents)
            self.env = env

        async def on_ready(self):
            with registry(self.env.cr.dbname).cursor() as new_cr:
                self.env = api.Environment(new_cr, self.env.uid, self.env.context)
                company = self.env.user.company_id or self.env["res.company"].browse(1)
                for guild in self.guilds:
                    if guild.id == int(company.discord_server_id):
                        self.guild = guild
                        await self.create_and_update_not_created_channels(guild)
                        await self.create_and_update_not_created_categories(guild)
                        created_channels = self.env["gc.discord.channel"].search(
                            [("discord_channel_uuid", "!=", False)]
                        )
                        saved_channel_ids = created_channels.mapped(
                            lambda x: int(x.discord_channel_uuid)
                        )
                        created_categories = self.env["gc.discord.category"].search(
                            [("discord_channel_uuid", "!=", False)]
                        )
                        saved_category_ids = created_categories.mapped(
                            lambda x: int(x.discord_channel_uuid)
                        )
                        saved_channel_ids.extend(saved_category_ids)
                        channels_to_remove = [
                            channel
                            for channel in guild.channels
                            if channel.id not in saved_channel_ids
                        ]
                        for channel_to_remove in channels_to_remove:
                            try:
                                await channel_to_remove.delete()
                            except Exception:
                                _logger.exception(
                                    f"Error occured on remove the channel {channel_to_remove}:"
                                )
                        await self.create_and_update_not_created_roles(guild)
                        for member in guild.members:
                            if member.bot:
                                continue
                            user = self.env["gc.user"].search(
                                [("discord_uuid", "=", str(member.id))], limit=1
                            )
                            if not user:
                                user = self.env["gc.user"].create(
                                    {
                                        "discord_uuid": str(member.id),
                                        "name": member.name,
                                    }
                                )
                            dc_user_roles = member.roles
                            dc_existing_user_roles = []

                            # Add new roles to the user
                            for role_record in user.role_ids:
                                role_id = int(role_record.role_id)
                                dc_role = discord.utils.get(guild.roles, id=role_id)
                                if dc_role is None:
                                    continue
                                dc_existing_user_roles.append(dc_role)
                                if dc_role in dc_user_roles:
                                    continue
                                await member.add_roles(dc_role)

                            # Remove roles that were added by the bot
                            roles_to_remove = []
                            for role in dc_user_roles:
                                if (
                                    role not in dc_existing_user_roles
                                    and role.name != "@everyone"
                                ):
                                    roles_to_remove.append(role)
                            if len(roles_to_remove) > 0:
                                await member.remove_roles(*roles_to_remove)
                        break
                new_cr.commit()

        async def on_member_join(self, member):
            if not self.env["gc.user"].search_count(
                [("discord_uuid", "=", str(member.id))]
            ):
                self.env["gc.user"].create({"discord_uuid": str(member.id)})
                user = self.env["gc.user"].search([("discord_uuid", "=", member.id)])
                events = self.env["gc.discord.event"].search(
                    [("event_type", "=", "guild_join")]
                )
                for event in events:
                    start_actions = self.env["gc.discord.action"].search(
                        [("start_event_id", "=", event.id)]
                    )
                    for action in start_actions:
                        action_worker = self.env[
                            "gc.discord.action.worker"
                        ].create_worker(action, user)
                        current_event_worker = action_worker.current_event_worker_id
                        current_event_worker.start_next_event()
                current_event_worker = self.env["gc.discord.event.worker"].search(
                    [
                        ("event_id.event_type", "=", "guild_join"),
                        ("action_worker_id.user_id", "=", user.id),
                        ("current", "=", True),
                        ("done", "=", False),
                    ],
                    limit=1,
                )
                if current_event_worker:
                    current_event_worker.start_next_event()

        async def create_and_update_not_created_channels(self, guild):  # noqa: C901
            # TODO: fix complexity...
            not_created_channels = self.env["gc.discord.channel"].search(
                [("discord_channel_uuid", "=", False)]
            )
            for channel_record in not_created_channels:
                category = False
                channel = False
                if (
                    channel_record.category_id
                    and not channel_record.category_id.discord_channel_uuid
                ):
                    try:
                        category = await guild.create_category(
                            name=channel_record.category_id.name
                        )
                        channel_record.category_id.discord_channel_uuid = str(
                            category.id
                        )
                    except discord.Forbidden:
                        _logger.exception("Error editing category:")
                        continue
                    except discord.HTTPException:
                        _logger.exception("Error editing category:")
                        continue
                elif channel_record.category_id:
                    category = guild.get_channel(
                        int(channel_record.category_id.discord_channel_uuid)
                    )
                try:
                    if channel_record.type == "text":
                        channel = await guild.create_text_channel(
                            name=channel_record.name,
                            category=category,
                        )
                    elif channel_record.type == "voice":
                        channel = await guild.create_voice_channel(
                            name=channel_record.name,
                            category=category,
                        )
                    elif channel_record.type == "stage":
                        channel = await guild.create_stage_channel(
                            name=channel_record.name,
                            category=category,
                        )
                    elif channel_record.type == "announcement":
                        channel = await guild.create_text_channel(
                            name=channel_record.name,
                            category=category,
                            type=discord.ChannelType.news,
                        )
                    channel_record.discord_channel_uuid = str(channel.id)
                except discord.Forbidden:
                    _logger.exception("Error editing channel:")
                    continue
                except discord.HTTPException:
                    _logger.exception("Error editing channel:")
                    continue
            created_channels = self.env["gc.discord.channel"].search(
                [("discord_channel_uuid", "!=", False)]
            )
            for channel_record in created_channels:
                try:
                    channel = guild.get_channel(
                        int(channel_record.discord_channel_uuid)
                    )
                    await channel.edit(name=channel_record.name)
                    if channel_record.category_id.discord_channel_uuid:
                        category = guild.get_channel(
                            int(channel_record.category_id.discord_channel_uuid)
                        )
                        await channel.edit(category=category)
                except discord.Forbidden:
                    _logger.exception("Error editing channel:")
                    continue
                except discord.HTTPException:
                    _logger.exception("Error editing channel:")
                    continue

        async def create_and_update_not_created_categories(self, guild):
            not_created_categories = self.env["gc.discord.category"].search(
                [("discord_channel_uuid", "=", False)]
            )
            for category_record in not_created_categories:
                category = await guild.create_category(name=category_record.name)
                category_record.discord_channel_uuid = str(category.id)
            created_categories = self.env["gc.discord.category"].search(
                [("discord_channel_uuid", "!=", False)]
            )
            for category_record in created_categories:
                category = self.get_channel(int(category_record.discord_channel_uuid))
                await category.edit(name=category_record.name)

        async def create_and_update_not_created_roles(self, guild):
            not_created_roles = self.env["gc.discord.role"].search(
                [("role_id", "=", False)]
            )
            for role_record in not_created_roles:
                permission_profile = role_record.permission_profile_id
                permissions = discord.Permissions(
                    administrator=permission_profile.administrator,
                    create_instant_invite=permission_profile.create_instant_invite,
                    kick_members=permission_profile.kick_members,
                    ban_members=permission_profile.ban_members,
                    manage_channels=permission_profile.manage_channels,
                    manage_guild=permission_profile.manage_guild,
                    add_reactions=permission_profile.add_reactions,
                    view_audit_log=permission_profile.view_audit_log,
                    priority_speaker=permission_profile.priority_speaker,
                    stream=permission_profile.stream,
                    read_messages=permission_profile.read_messages,
                    send_messages=permission_profile.send_messages,
                    send_tts_messages=permission_profile.send_tts_messages,
                    manage_messages=permission_profile.manage_messages,
                    embed_links=permission_profile.embed_links,
                    attach_files=permission_profile.attach_files,
                    read_message_history=permission_profile.read_message_history,
                    mention_everyone=permission_profile.mention_everyone,
                    external_emojis=permission_profile.external_emojis,
                    view_guild_insights=permission_profile.view_guild_insights,
                    connect=permission_profile.connect,
                    speak=permission_profile.speak,
                    mute_members=permission_profile.mute_members,
                    deafen_members=permission_profile.deafen_members,
                    move_members=permission_profile.move_members,
                    use_voice_activation=permission_profile.use_voice_activation,
                    change_nickname=permission_profile.change_nickname,
                    manage_nicknames=permission_profile.manage_nicknames,
                    manage_roles=permission_profile.manage_roles,
                    manage_webhooks=permission_profile.manage_webhooks,
                    manage_emojis=permission_profile.manage_emojis,
                    request_to_speak=permission_profile.request_to_speak,
                )
                color = discord.Color.from_str(role_record.color)
                role = discord.utils.get(guild.roles, name=role_record.name)
                try:
                    if role:
                        role_record.role_id = role.id
                        await role.edit(
                            name=role_record.name,
                            hoist=role_record.hoist,
                            mentionable=role_record.mentionable,
                            permissions=permissions,
                            color=color,
                        )
                        continue
                    role = await guild.create_role(
                        name=role_record.name,
                        hoist=role_record.hoist,
                        mentionable=role_record.mentionable,
                        permissions=permissions,
                        color=color,
                    )
                    role_record.role_id = role.id
                except Exception:
                    _logger.exception(f"Error occurred at editing role {role}:")
            created_roles = self.env["gc.discord.role"].search(
                [("role_id", "!=", False)]
            )
            for role_record in created_roles:
                permission_profile = role_record.permission_profile_id
                permissions = discord.Permissions(
                    administrator=permission_profile.administrator,
                    create_instant_invite=permission_profile.create_instant_invite,
                    kick_members=permission_profile.kick_members,
                    ban_members=permission_profile.ban_members,
                    manage_channels=permission_profile.manage_channels,
                    manage_guild=permission_profile.manage_guild,
                    add_reactions=permission_profile.add_reactions,
                    view_audit_log=permission_profile.view_audit_log,
                    priority_speaker=permission_profile.priority_speaker,
                    stream=permission_profile.stream,
                    read_messages=permission_profile.read_messages,
                    send_messages=permission_profile.send_messages,
                    send_tts_messages=permission_profile.send_tts_messages,
                    manage_messages=permission_profile.manage_messages,
                    embed_links=permission_profile.embed_links,
                    attach_files=permission_profile.attach_files,
                    read_message_history=permission_profile.read_message_history,
                    mention_everyone=permission_profile.mention_everyone,
                    external_emojis=permission_profile.external_emojis,
                    view_guild_insights=permission_profile.view_guild_insights,
                    connect=permission_profile.connect,
                    speak=permission_profile.speak,
                    mute_members=permission_profile.mute_members,
                    deafen_members=permission_profile.deafen_members,
                    move_members=permission_profile.move_members,
                    use_voice_activation=permission_profile.use_voice_activation,
                    change_nickname=permission_profile.change_nickname,
                    manage_nicknames=permission_profile.manage_nicknames,
                    manage_roles=permission_profile.manage_roles,
                    manage_webhooks=permission_profile.manage_webhooks,
                    manage_emojis=permission_profile.manage_emojis,
                    request_to_speak=permission_profile.request_to_speak,
                )
                color = discord.Color.from_str(role_record.color)
                role = discord.utils.get(guild.roles, id=role_record.role_id)
                if role:
                    await role.edit(
                        name=role_record.name,
                        hoist=role_record.hoist,
                        mentionable=role_record.mentionable,
                        position=role_record.position,
                        permissions=permissions,
                        color=color,
                    )
            role_ids = created_roles.mapped(lambda x: int(x.role_id))
            roles_to_remove = [role for role in guild.roles if role.id not in role_ids]
            for role_to_remove in roles_to_remove:
                try:
                    await role_to_remove.delete()
                except Exception:
                    _logger.exception(
                        f"Error occured on remove of role {role_to_remove}:"
                    )

        async def on_message(self, message):
            if message.author == self.user:
                return
            with registry(self.env.cr.dbname).cursor() as new_cr:
                new_env = api.Environment(new_cr, self.env.uid, self.env.context)
                if type(message.channel) == discord.DMChannel:
                    user = new_env["gc.user"].search(
                        [("discord_uuid", "=", message.author.id)]
                    )
                    events = new_env["gc.discord.event"].search(
                        [("event_type", "=", "get_private_message")]
                    )
                    for event in events:
                        start_actions = new_env["gc.discord.action"].search(
                            [("start_event_id", "=", event.id)]
                        )
                        for action in start_actions:
                            action_worker = new_env[
                                "gc.discord.action.worker"
                            ].create_worker(action, user)
                            current_event_worker = action_worker.current_event_worker_id
                            if current_event_worker:
                                current_event_worker.start_next_event()
                    current_event_worker = new_env["gc.discord.event.worker"].search(
                        [
                            ("event_id.event_type", "=", "get_private_message"),
                            ("action_worker_id.user_id", "=", user.id),
                            ("current", "=", True),
                            ("done", "=", False),
                        ],
                        limit=1,
                    )
                    if current_event_worker:
                        current_event_worker.start_next_event()

        async def send_message_request(self, user_id, message):
            user = self.get_user(int(user_id))
            if user:
                await user.send(message)

        async def add_roles(self, user_id, role_id):
            user = self.get_user(int(user_id))
            if user and self.guild:
                member = self.guild.get_member(int(user_id))
                role = discord.utils.get(self.guild.roles, id=int(role_id))
                await member.add_roles(role)

    async def bot_async_start(self, discord_bot_token):
        await self.client.start(discord_bot_token)

    def bot_loop_start(self, loop):
        loop.run_forever()

    @http.route(["/discordbot/start"], type="http", methods=["GET"], csrf=False)
    def start_discord_bot(self):
        company_id = request.env.user.company_id or request.env["res.company"].browse(1)
        if (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("gigaclub.discord_bot_token")
            and company_id.discord_server_status
            and company_id.discord_server_status == "stopped"
        ):
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = self.MyBot(env=request.env)
            self.loop.create_task(
                self.bot_async_start(
                    request.env["ir.config_parameter"]
                    .sudo()
                    .get_param("gigaclub.discord_bot_token")
                )
            )
            bot_thread = threading.Thread(target=self.bot_loop_start, args=(self.loop,))
            bot_thread.start()
            company_id.discord_server_status = "started"
        else:
            raise Exception(
                _("Bot is Started or Discord Bot Token or Discord Server ID not set!")
            )
        return "<script>window.close()</script>"

    @http.route(["/discordbot/stop"], type="http", methods=["GET"], csrf=False)
    def stop_discord_bot(self):
        company_id = request.env.user.company_id or request.env["res.company"].browse(1)
        if (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("gigaclub.discord_bot_token")
            and company_id.discord_server_status
            and company_id.discord_server_status == "started"
        ):
            try:
                asyncio.run(self.client.close())
                del self.client
            except Exception as e:
                _logger.error(_("Error occured on Discord Bot stop: %s") % e)
            company_id.discord_server_status = "stopped"
        else:
            raise Exception(
                _("Bot is Stopped or Discord Bot Token or Discord Server ID not set!")
            )
        return "<script>window.close()</script>"

    @http.route(["/discordbot/reload"], type="http", methods=["GET"], csrf=False)
    def reload_discord_bot(self):
        self.stop_discord_bot()
        self.start_discord_bot()
        return "<script>window.close()</script>"

    @http.route(
        ["/discordbot/event/<int:event_worker_id>"],
        type="http",
        methods=["POST"],
        csrf=False,
    )
    def discord_bot_event(self, event_worker_id):
        event_worker = request.env["gc.discord.event.worker"].browse(event_worker_id)
        user_id = event_worker.action_worker_id.user_id.discord_uuid
        if user_id:
            if event_worker.event_id.event_type == "send_private_message":
                message_content = event_worker.event_id.message_content
                asyncio.run_coroutine_threadsafe(
                    self.client.send_message_request(user_id, message_content),
                    self.loop,
                )
            elif event_worker.event_id.event_type == "set_role":
                for role in event_worker.event_id.role_ids:
                    asyncio.run_coroutine_threadsafe(
                        self.client.add_roles(user_id, role.role_id), self.loop
                    )
        event_worker.start_next_event()
        return "<script>window.close()</script>"
