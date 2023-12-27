from datetime import datetime, timedelta
from typing import Any, Optional

from aiocache import cached
from aiohttp import ClientSession


class UWAPIClient:
    def __init__(self, api_key: str):
        self.client: Optional[ClientSession] = None
        self._api_key = api_key
        self._base_url = "https://openapi.data.uwaterloo.ca/v3"

    @staticmethod
    def _parse_term(data: dict[str, Any]):
        return {
            "code": data["termCode"],
            "name": data["name"][0] + data["name"][-2:],
            "start_date": datetime.fromisoformat(data["termBeginDate"]),
            "end_date": datetime.fromisoformat(data["termEndDate"])
        }

    @cached(ttl=24 * 60 * 60)
    async def get_terms(self):
        # Returns term ordered chronologically
        resp = await self.client.get(self._base_url + "/terms")
        data = await resp.json()
        data = [self._parse_term(term) for term in data]
        data.sort(key=lambda t: t["start_date"])
        return data

    @cached(ttl=5 * 60)
    async def get_current_term(self):
        resp = await self.client.get(self._base_url + "/terms/current")
        data = await resp.json()
        return self._parse_term(data)

    @cached(ttl=5 * 60)
    async def get_next_term(self):
        terms = await self.get_terms()
        cur_term = await self.get_current_term()
        return terms[terms.index(cur_term) + 1]

    async def get_term_with_name(self, name: str):
        return [t for t in await self.get_terms() if t["name"] == name.upper()][0]

    async def get_default_term(self):
        current_term = await self.get_current_term()
        next_term = await self.get_next_term()
        if datetime.now() > current_term["start_date"] + timedelta(days=60):  # 2 months after term start
            return next_term
        else:
            return current_term

    @staticmethod
    def _parse_class_schedule(data: dict[str, Any]):
        schedule_data = data["scheduleData"][0]
        return {
            "section_name": f"{data['courseComponent']} {data['classSection']:>03}",
            "enrolled": data['enrolledStudents'],
            "capacity": data['maxEnrollmentCapacity'],
            "meeting_weekdays": schedule_data['classMeetingDayPatternCode'],
            "start_time": (datetime.fromisoformat(schedule_data['classMeetingStartTime']).time()
                           if schedule_data['classMeetingStartTime'] else None),
            "end_time": (datetime.fromisoformat(schedule_data['classMeetingEndTime']).time()
                         if schedule_data['classMeetingEndTime'] else None)
        }

    async def get_class_schedules(self, term_id: int, course_code: str):
        subject, catalog_number = course_code.split()
        resp = await self.client.get(self._base_url + f"/classschedules/{term_id}/{subject}/{catalog_number}")
        data = await resp.json()
        return sorted([self._parse_class_schedule(cs) for cs in data], key=lambda s: s["section_name"])

    async def init(self):
        self.client = ClientSession(headers={"X-API-KEY": self._api_key}, raise_for_status=True)

    async def close(self):
        await self.client.close()
