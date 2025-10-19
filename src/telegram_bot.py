import asyncio
import logging
from typing import Callable, Awaitable
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from .config import settings

class TelegramControl:
    def __init__(self) -> None:
        self._logger = logging.getLogger("TelegramControl")
        self._app: Application | None = None
        self._on_start: Callable[[], Awaitable[str]] | None = None
        self._on_stop: Callable[[], Awaitable[str]] | None = None
        self._on_status: Callable[[], Awaitable[str]] | None = None
        self._on_setrisk: Callable[[float], Awaitable[str]] | None = None
        self._on_addsym: Callable[[str], Awaitable[str]] | None = None
        self._on_remsym: Callable[[str], Awaitable[str]] | None = None

    def set_handlers(
        self,
        on_start: Callable[[], Awaitable[str]],
        on_stop: Callable[[], Awaitable[str]],
        on_status: Callable[[], Awaitable[str]],
        on_setrisk: Callable[[float], Awaitable[str]] | None = None,
        on_addsym: Callable[[str], Awaitable[str]] | None = None,
        on_remsym: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_status = on_status
        self._on_setrisk = on_setrisk
        self._on_addsym = on_addsym
        self._on_remsym = on_remsym

    async def _auth(self, update: Update) -> bool:
        if not settings.telegram_allowed_user_ids:
            return True
        user = update.effective_user
        return bool(user and user.id in settings.telegram_allowed_user_ids)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        if self._on_start:
            msg = await self._on_start()
            await update.message.reply_text(msg)

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        if self._on_stop:
            msg = await self._on_stop()
            await update.message.reply_text(msg)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        if self._on_status:
            msg = await self._on_status()
            await update.message.reply_text(msg)

    async def _cmd_setrisk(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        if not self._on_setrisk:
            return
        try:
            pct = float(context.args[0])
        except Exception:
            await update.message.reply_text("Usage: /setrisk <percent>")
            return
        msg = await self._on_setrisk(pct)
        await update.message.reply_text(msg)

    async def _cmd_addsym(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        if not self._on_addsym:
            return
        if not context.args:
            await update.message.reply_text("Usage: /addsym <SYMBOL>")
            return
        sym = context.args[0]
        msg = await self._on_addsym(sym)
        await update.message.reply_text(msg)

    async def _cmd_remsym(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._auth(update):
            return
        if not self._on_remsym:
            return
        if not context.args:
            await update.message.reply_text("Usage: /remsym <SYMBOL>")
            return
        sym = context.args[0]
        msg = await self._on_remsym(sym)
        await update.message.reply_text(msg)

    async def run(self) -> None:
        if not settings.telegram_bot_token:
            return
        self._app = Application.builder().token(settings.telegram_bot_token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("stop", self._cmd_stop))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("setrisk", self._cmd_setrisk))
        self._app.add_handler(CommandHandler("addsym", self._cmd_addsym))
        self._app.add_handler(CommandHandler("remsym", self._cmd_remsym))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
