import asyncio
import logging
from .telegram_bot import TelegramControl
from .runner import BotRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

async def main() -> None:
    runner = BotRunner()
    tele = TelegramControl()

    tele.set_handlers(
        runner.start,
        runner.stop,
        runner.status,
        runner.set_risk,
        runner.add_symbol,
        runner.remove_symbol,
    )

    await asyncio.gather(tele.run())

if __name__ == "__main__":
    asyncio.run(main())
