"""Ephemeral Queue view (paginated list + per-track operations). SPEC §5.4."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from ..player import MusicPlayer
from ..utils import embeds as embed_helpers
from ..utils.checks import can_control_player
from ..utils.format import format_duration, truncate

if TYPE_CHECKING:
    from .panel import ControlPanel

logger = logging.getLogger(__name__)

_PAGE_SIZE = 10


class QueueView(discord.ui.View):
    """Ephemeral queue view with paging + per-track ops. Times out after 60s."""

    def __init__(self, panel: ControlPanel, player: MusicPlayer, page: int = 1) -> None:
        super().__init__(timeout=60)
        self.panel = panel
        self.player = player
        self.page = page
        self._selected_index: int | None = None  # global queue index, 0-based
        self._build()

    @property
    def total_pages(self) -> int:
        total = len(self.player.queue)
        return max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    def _page_slice(self) -> tuple[int, int]:
        total = len(self.player.queue)
        start = max(0, (self.page - 1) * _PAGE_SIZE)
        end = min(total, start + _PAGE_SIZE)
        return start, end

    def build_embed(self) -> discord.Embed:
        total = len(self.player.queue)
        if total == 0:
            return discord.Embed(
                title="📜 Queue", description="The queue is empty.", color=0x5865F2
            )
        start, end = self._page_slice()
        lines = []
        for i in range(start, end):
            t = self.player.queue.peek(i)
            marker = "▶" if i == self._selected_index else " "
            lines.append(
                f"{marker} `{i + 1:>3}.` **{truncate(t.title, 60)}** "
                f"— `{format_duration(t.length)}`"
            )
        return discord.Embed(
            title=f"📜 Queue (page {self.page}/{self.total_pages}, {total} tracks)",
            description="\n".join(lines),
            color=0x5865F2,
        )

    def _check(self, interaction: discord.Interaction) -> tuple[bool, str | None]:
        return can_control_player(interaction.user, self.player.channel)

    def _rebuild(self) -> None:
        self.clear_items()
        self._build()

    def _build(self) -> None:
        start, end = self._page_slice()
        # Row 0: select menu
        if end > start:
            options = []
            for i in range(start, end):
                t = self.player.queue.peek(i)
                options.append(
                    discord.SelectOption(
                        label=truncate(t.title, 90),
                        description=f"#{i + 1} · {format_duration(t.length)}",
                        value=str(i),
                        default=(i == self._selected_index),
                    )
                )
            select: discord.ui.Select[QueueView] = discord.ui.Select(
                placeholder="Pick a track on this page…",
                options=options,
                min_values=1,
                max_values=1,
                row=0,
            )

            async def _picked(interaction: discord.Interaction) -> None:
                self._selected_index = int(select.values[0])
                self._rebuild()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            select.callback = _picked  # type: ignore[method-assign]
            self.add_item(select)

        # Row 1: per-track ops (apply to self._selected_index)
        idx = self._selected_index
        total = len(self.player.queue)
        on_top = idx == 0
        on_bottom = idx is not None and idx >= total - 1

        self.add_item(_OpButton(self, op="up", label="⬆", disabled=idx is None or on_top, row=1))
        self.add_item(
            _OpButton(self, op="down", label="⬇", disabled=idx is None or on_bottom, row=1)
        )
        self.add_item(
            _OpButton(self, op="top", label="🔝 Top", disabled=idx is None or on_top, row=1)
        )
        self.add_item(_OpButton(self, op="remove", label="🗑 Remove", disabled=idx is None, row=1))
        self.add_item(_OpButton(self, op="jump", label="▶ Jump", disabled=idx is None, row=1))

        # Row 2: paging / global ops
        self.add_item(_PageButton(self, delta=-1, label="◀ Prev", disabled=self.page <= 1, row=2))
        self.add_item(
            _PageButton(
                self, delta=1, label="Next ▶", disabled=self.page >= self.total_pages, row=2
            )
        )
        self.add_item(_OpButton(self, op="shuffle", label="🔀 Shuffle", disabled=total == 0, row=2))
        self.add_item(_OpButton(self, op="clear", label="🧹 Clear", disabled=total == 0, row=2))
        self.add_item(_OpButton(self, op="close", label="✖ Close", row=2))


class _PageButton(discord.ui.Button[QueueView]):
    def __init__(self, view: QueueView, delta: int, label: str, disabled: bool, row: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label,
            custom_id=f"mb:queue_page_{delta}",
            disabled=disabled,
            row=row,
        )
        self._owner = view
        self.delta = delta

    async def callback(self, interaction: discord.Interaction) -> None:
        allowed, reason = self._owner._check(interaction)  # noqa: SLF001
        if not allowed:
            await interaction.response.send_message(
                embed=embed_helpers.error(reason or "Not allowed"), ephemeral=True
            )
            return
        self._owner.page = max(1, min(self._owner.page + self.delta, self._owner.total_pages))
        # Selection may now be off-page; clear it.
        self._owner._selected_index = None  # noqa: SLF001
        self._owner._rebuild()  # noqa: SLF001
        await interaction.response.edit_message(embed=self._owner.build_embed(), view=self._owner)


class _OpButton(discord.ui.Button[QueueView]):
    def __init__(
        self,
        view: QueueView,
        op: str,
        label: str,
        disabled: bool = False,
        row: int = 1,
    ) -> None:
        style = (
            discord.ButtonStyle.danger
            if op in ("remove", "clear")
            else discord.ButtonStyle.secondary
        )
        super().__init__(
            style=style,
            label=label,
            custom_id=f"mb:queue_op_{op}",
            disabled=disabled,
            row=row,
        )
        self._owner = view
        self.op = op

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self._owner
        allowed, reason = view._check(interaction)  # noqa: SLF001
        if not allowed:
            await interaction.response.send_message(
                embed=embed_helpers.error(reason or "Not allowed"), ephemeral=True
            )
            return
        player = view.player
        idx = view._selected_index  # noqa: SLF001
        op = self.op

        if op == "close":
            await interaction.response.edit_message(embed=embed_helpers.info("Closed."), view=None)
            view.stop()
            return

        if op == "shuffle":
            player.queue.shuffle()
        elif op == "clear":
            player.queue.clear()
            view._selected_index = None  # noqa: SLF001
            view.panel.flash_footer("🧹 Queue cleared")
        elif idx is not None and 0 <= idx < len(player.queue):
            track = player.queue.peek(idx)
            if op == "up" and idx > 0:
                del player.queue[idx]
                player.queue.put_at(idx - 1, track)
                view._selected_index = idx - 1  # noqa: SLF001
            elif op == "down" and idx < len(player.queue) - 1:
                del player.queue[idx]
                player.queue.put_at(idx + 1, track)
                view._selected_index = idx + 1  # noqa: SLF001
            elif op == "top":
                del player.queue[idx]
                player.queue.put_at(0, track)
                view._selected_index = 0  # noqa: SLF001
            elif op == "remove":
                del player.queue[idx]
                view._selected_index = None  # noqa: SLF001
            elif op == "jump":
                # Drop everything before idx and skip current.
                for _ in range(idx):
                    player.queue.get()
                await player.skip(force=True)
                view._selected_index = None  # noqa: SLF001

        # Rebuild + refresh views.
        if view.page > view.total_pages:
            view.page = view.total_pages
        view._rebuild()  # noqa: SLF001
        await interaction.response.edit_message(embed=view.build_embed(), view=view)
        await view.panel.refresh()
