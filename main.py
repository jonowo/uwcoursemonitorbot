import asyncio
import functools
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Optional

import orjson
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import Message
from aiogram.utils.markdown import hbold, hcode
from aiohttp import ClientResponseError
from dotenv import load_dotenv

from client import UWAPIClient
from utils import course_to_str

# data.json format: course code -> list of schedule dicts

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_PATH = "./data.json"
data_lock = asyncio.Lock()

load_dotenv()

USER_ID = int(os.environ["USER_ID"])
bot = Bot(os.environ["BOT_TOKEN"], parse_mode=ParseMode.HTML)
dp = Dispatcher()
client = UWAPIClient(os.environ["UW_API_KEY"])


def owner_only(handler: Callable) -> Callable:
    @functools.wraps(handler)
    async def wrapper(message: Message, *args, **kwargs):
        if message.from_user.id != USER_ID:
            return
        return await handler(message, *args, **kwargs)

    return wrapper


def read_data() -> dict[str, Any]:
    with open(DATA_PATH, "rb") as f:
        data = orjson.loads(f.read())
        # Why doesn't orjson deserialize datetimes? workaround
        for sections in data.values():
            for section in sections:
                if section["start_time"] is not None:
                    section["start_time"] = datetime.strptime(section["start_time"], "%H:%M:%S").time()
                if section["end_time"] is not None:
                    section["end_time"] = datetime.strptime(section["end_time"], "%H:%M:%S").time()
        return data


def write_data(data: dict[str, Any]) -> None:
    with open(DATA_PATH, "wb") as f:
        f.write(orjson.dumps(data))


@dp.message(CommandStart())
@owner_only
async def command_start_handler(message: Message) -> None:
    await message.answer(f"Hi there!")


@dp.message(Command("help"))
@owner_only
async def command_help_handler(message: Message) -> None:
    await message.answer(
        f"{hcode("/add (term) (course code)")} - Add a course to the list, e.g., {hcode("/add W23 MATH 237")}\n"
        f"{hcode("/remove (term) [course code]")} - Remove a course from the list\n"
        f"{hcode("/list")} - List all courses in the list\n"
        f"{hcode("/clear")} - Clear the list"
    )


@dp.message(Command("list"))
@owner_only
async def command_list_handler(message: Message) -> None:
    async with data_lock:
        data = read_data()

    if len(data) == 0:
        await message.answer("Course list is empty!")
        return

    await message.answer("\n\n".join(course_to_str(key, section) for key, section in data.items()))


@dp.message(Command("clear"))
@owner_only
async def command_clear_handler(message: Message) -> None:
    async with data_lock:
        write_data({})
    await message.answer("List cleared!")


async def get_term(term_name: Optional[str]) -> dict:
    if term_name is not None:
        term = [t for t in await client.get_terms() if t["name"] == term_name.upper()][0]
    else:
        term = await client.get_default_term()
    return term


@dp.message(Command("add"))
@owner_only
async def command_add_handler(message: Message, command: CommandObject) -> None:
    if match := re.match(r"(?:([FWSfws]\d\d)\s+)?([A-Za-z]+)\s*(\d+[A-Za-z]?)$", command.args):
        course_code = match[2].upper() + " " + match[3].upper()
        term = await get_term(match[1])
        key = f"{term['name']} {course_code}"
    else:
        await message.answer("Usage example: /add W23 MATH 237")
        return

    async with data_lock:
        data = read_data()
        if key in data:
            await message.answer(f"{key} is already in list!")
            return

    try:
        course_schedules = await client.get_class_schedules(term['code'], course_code)
    except ClientResponseError as e:
        if e.status == 404:
            await message.answer(f"{key} has no schedules.")
            return
        else:
            raise

    # Add to file
    async with data_lock:
        data = read_data()
        data[key] = course_schedules
        write_data(data)

    await message.answer(f"{key} added to list!")


@dp.message(Command("remove"))
@owner_only
async def command_remove_handler(message: Message, command: CommandObject) -> None:
    if match := re.match(r"(?:([FWSfws]\d\d)\s+)?([A-Za-z]+)\s*(\d+[A-Za-z]?)$", command.args):
        course_code = match[2].upper() + " " + match[3].upper()
        term = await get_term(match[1])
        key = f"{term['name']} {course_code}"
    else:
        await message.answer("Usage example: /remove W23 MATH 237")
        return

    async with data_lock:
        data = read_data()
        if key in data:
            del data[key]
            write_data(data)
            await message.answer(f"Removed {key} from list.")
        else:
            await message.answer(f"{key} is not in list!")


async def notify_course_schedules_diff(key: str, old_schedules, new_schedules):
    old_msg = course_to_str(key, old_schedules).splitlines()
    new_msg = course_to_str(key, new_schedules).splitlines()

    final_msg = []
    if len(old_msg) == len(new_msg):
        # Bold the lines that have changed
        for old_line, new_line in zip(old_msg, new_msg):
            if old_line == new_line:
                final_msg.append(new_line)
            else:
                final_msg.append(hbold(new_line))
    else:
        final_msg = new_msg

    await bot.send_message(USER_ID, "Course info changed:\n\n" + "\n".join(final_msg))


async def bg_loop() -> None:
    try:
        logger.info("Starting background loop...")
        while True:
            logger.info("Checking course schedules for updates...")

            async with data_lock:
                keys = read_data().keys()

            for key in keys:
                term_name, _, course_code = key.partition(" ")
                term = await client.get_term_with_name(term_name)
                new_course_schedules = await client.get_class_schedules(term["code"], course_code)

                async with data_lock:
                    data = read_data()
                    if key not in data:  # because it might have been removed
                        continue

                    old_course_schedules = data[key]
                    data[key] = new_course_schedules
                    write_data(data)

                    if old_course_schedules != new_course_schedules:
                        await notify_course_schedules_diff(key, old_course_schedules, new_course_schedules)

                await asyncio.sleep(10)

            await asyncio.sleep(120)
    except asyncio.CancelledError:
        logger.info("Ending background loop...")
        raise


async def main() -> None:
    if os.path.isfile(DATA_PATH):
        logger.info(f"Loading existing {DATA_PATH}")
    else:
        logger.info(f"Creating new {DATA_PATH}")
        write_data({})

    await client.init()

    task = asyncio.create_task(bg_loop())
    await dp.start_polling(bot)
    task.cancel()

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
