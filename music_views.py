"""Discord UI: queue/playback control buttons."""
import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from music_cog import music_cog

log = logging.getLogger(__name__)

PAUSE_EMOJI = "\u23f8\ufe0f"
PLAY_EMOJI = "\u25b6\ufe0f"
REORDER_EMOJI = "\U0001f500"


def build_playlist_options(entries: list) -> list:
    """SelectOptions from (name, description) pairs."""
    return [
        discord.SelectOption(label=name[:100], value=name, description=desc[:100])
        for name, desc in entries
    ]


class PlaylistPickView(discord.ui.View):
    """Ephemeral dropdown of saved playlists; only the opener can use it."""

    def __init__(self, cog: "music_cog", user_id: int, entries: list):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        select = discord.ui.Select(
            placeholder="Pick a playlist to play...",
            options=build_playlist_options(entries),
            min_values=1,
            max_values=1,
        )

        async def on_select(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "That menu isn't yours, Teme.", ephemeral=True
                )
                return
            name = select.values[0]
            await interaction.response.defer()
            try:
                await interaction.edit_original_response(
                    content=f"Loading **{name}**...", view=None
                )
            except Exception as e:
                log.warning("playlist picker edit failed: %s", e)
            self.stop()
            await self.cog.play_saved_playlist(interaction, name)

        select.callback = on_select
        self.add_item(select)


class ReorderView(discord.ui.View):
    """Ephemeral picker for moving pending songs up, down, or to the front."""

    def __init__(self, cog: "music_cog", guild_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.selected = 1
        self._rebuild_select()

    def _rebuild_select(self):
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        queue = self.cog.get_queue(self.guild_id)
        if self.selected > len(queue.songs):
            self.selected = max(1, len(queue.songs))
        options = [
            discord.SelectOption(
                label=f"{i}. {song.title}"[:100],
                value=str(i),
                description=(song.requester_name or "unknown")[:100],
                default=(i == self.selected),
            )
            for i, song in enumerate(queue.songs[:25], start=1)
        ]
        if not options:
            return

        select = discord.ui.Select(
            placeholder="Pick a song to move...",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

        async def on_select(interaction: discord.Interaction):
            if err := self.cog.same_vc_error(interaction):
                await interaction.response.send_message(err, ephemeral=True)
                return
            self.selected = int(select.values[0])
            self._rebuild_select()
            await interaction.response.edit_message(view=self)

        select.callback = on_select
        self.add_item(select)

    async def _move(self, interaction: discord.Interaction, action: str):
        if err := self.cog.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return

        queue = self.cog.get_queue(self.guild_id)
        if not queue.songs:
            await interaction.response.edit_message(
                content="Nothing to reorder.", embed=None, view=None
            )
            return
        if action == "up":
            ok = queue.move_up(self.selected)
            if ok and self.selected > 1:
                self.selected -= 1
        elif action == "down":
            ok = queue.move_down(self.selected)
            if ok:
                self.selected += 1
        else:
            ok = queue.move_to_front(self.selected)
            if ok:
                self.selected = 1

        self._rebuild_select()
        if not ok:
            await interaction.response.edit_message(
                content="Can't move that, Teme.",
                embed=self.cog.build_queue_embed(queue),
                view=self,
            )
            return

        self.cog.schedule_preload(self.guild_id)
        self.cog.persist_queue(self.guild_id)
        await interaction.response.edit_message(
            content=None, embed=self.cog.build_queue_embed(queue), view=self
        )

    @discord.ui.button(label="Move up", style=discord.ButtonStyle.secondary, row=1)
    async def move_up_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._move(interaction, "up")

    @discord.ui.button(label="Move down", style=discord.ButtonStyle.secondary, row=1)
    async def move_down_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._move(interaction, "down")

    @discord.ui.button(label="To front", style=discord.ButtonStyle.primary, row=1)
    async def to_front_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._move(interaction, "front")


class QueueControlView(discord.ui.View):
    """Playback controls for the now-playing and queue messages.

    Buttons listen for 180 seconds, then Discord stops dispatching them.
    Run /queue or /nowplaying again (or start another song) for a fresh row.
    """

    def __init__(
        self,
        cog: "music_cog",
        guild_id: int,
        *,
        show_queue_button: bool = False,
    ):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        if not show_queue_button:
            self.remove_item(self.queue_btn)
        self._sync_pause_label()
        self._sync_autoplay_label()

    def _sync_pause_label(self):
        guild = self.cog.client.get_guild(self.guild_id)
        voice = guild.voice_client if guild else None
        paused = bool(voice and voice.is_paused())
        self.pause_btn.emoji = PLAY_EMOJI if paused else PAUSE_EMOJI

    def _sync_autoplay_label(self):
        on = bool(self.cog.is_autoplay(self.guild_id))
        self.autoplay_btn.label = "Autoplay: On" if on else "Autoplay: Off"
        self.autoplay_btn.style = (
            discord.ButtonStyle.success if on else discord.ButtonStyle.secondary
        )

    async def _refuse(self, interaction: discord.Interaction) -> bool:
        if err := self.cog.same_vc_error(interaction):
            await interaction.response.send_message(err, ephemeral=True)
            return True
        return False

    @discord.ui.button(emoji=PAUSE_EMOJI, style=discord.ButtonStyle.secondary, row=0)
    async def pause_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        voice = interaction.guild.voice_client
        if voice and voice.is_playing():
            voice.pause()
            self.cog.get_queue(self.guild_id).clock.pause()
            text = "Paused, Baka Yaro"
        elif voice and voice.is_paused():
            voice.resume()
            self.cog.get_queue(self.guild_id).clock.resume()
            text = "Resuming..."
        else:
            await interaction.response.send_message(
                "No audio is playing, Teme.", ephemeral=True
            )
            return
        self._sync_pause_label()
        await interaction.response.send_message(text, ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception as e:
            log.warning("pause button refresh failed: %s", e)

    @discord.ui.button(label="Next song", style=discord.ButtonStyle.primary, row=0)
    async def skip_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        voice = interaction.guild.voice_client
        queue = self.cog.get_queue(self.guild_id)
        if not voice or not voice.is_playing():
            await interaction.response.send_message(
                "Nothing is playing..", ephemeral=True
            )
            return
        if not queue:
            await interaction.response.send_message(
                "No songs in queue", ephemeral=True
            )
            return
        queue.skipping = True
        await interaction.response.send_message("Skipping..", ephemeral=True)
        voice.stop()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        voice = interaction.guild.voice_client
        if voice and (voice.is_paused() or voice.is_playing()):
            queue = self.cog.get_queue(self.guild_id)
            queue.stopping = True
            self.cog.clear_queue_state(self.guild_id)
            voice.stop()
            await voice.disconnect()
            await self.cog.client.change_presence(
                status=discord.Status.do_not_disturb
            )
            await interaction.response.send_message("Yare Yare... I'll be back")
            return
        await interaction.response.send_message(
            "Nothing to stop, Teme.", ephemeral=True
        )

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, row=0)
    async def queue_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        queue = self.cog.get_queue(self.guild_id)
        if not queue.current and not queue.songs:
            await interaction.response.send_message(
                "Queue is empty, Teme.", ephemeral=True
            )
            return
        embed = self.cog.build_queue_embed(queue)
        view = QueueControlView(self.cog, self.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    @discord.ui.button(label="Autoplay: Off", style=discord.ButtonStyle.secondary, row=0)
    async def autoplay_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        on = self.cog.toggle_autoplay(self.guild_id)
        self._sync_autoplay_label()
        text = "Autoplay ON. Yare Yare..." if on else "Autoplay OFF. Owari da."
        await interaction.response.send_message(text, ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception as e:
            log.warning("autoplay button refresh failed: %s", e)

    @discord.ui.button(
        label="Change song order",
        emoji=REORDER_EMOJI,
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def reorder_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if await self._refuse(interaction):
            return
        queue = self.cog.get_queue(self.guild_id)
        if not queue.songs:
            await interaction.response.send_message(
                "Nothing to reorder.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=self.cog.build_queue_embed(queue),
            view=ReorderView(self.cog, self.guild_id),
            ephemeral=True,
        )
