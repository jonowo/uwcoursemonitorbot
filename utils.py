from typing import Any

from aiogram.utils.markdown import hbold


def section_to_str(section: dict[str, Any]) -> str:
    res = f"{section['section_name']} {section['enrolled']}/{section['capacity']}"
    if section["meeting_weekdays"]:
        res += f" {section['meeting_weekdays']} {section['start_time']:%H:%M}-{section['end_time']:%H:%M}"
    return res


def course_to_str(key: str, sections: list[dict[str, Any]]) -> str:
    return f"{hbold(key)}\n" + "\n".join(map(section_to_str, sections))
