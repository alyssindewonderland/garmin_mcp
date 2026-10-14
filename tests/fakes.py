"""A fake garminconnect.Garmin with canned payloads shaped like real responses."""

from __future__ import annotations

from typing import Any, ClassVar


class FakeClient:
    def __init__(self) -> None:
        self.dumped_to: list[str] = []
        self.blob = '{"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"}'

    def dumps(self) -> str:
        return self.blob

    def dump(self, path: str) -> None:
        self.dumped_to.append(path)


class FakeGarmin:
    instances: ClassVar[list[FakeGarmin]] = []

    def __init__(self, email=None, password=None, is_cn=False, **_: Any):
        self.email, self.password = email, password
        self.client = FakeClient()
        self.calls: list[tuple] = []
        self.login_tokenstore: str | None = None
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        self.login_tokenstore = tokenstore
        return None, None

    def get_full_name(self):
        return "Test Runner"

    def get_unit_system(self):
        return "metric"

    def _rec(self, name, *args):
        self.calls.append((name, *args))

    def get_user_summary(self, cdate):
        self._rec("get_user_summary", cdate)
        return {
            "calendarDate": cdate,
            "totalSteps": 12345,
            "totalDistanceMeters": 9876,
            "restingHeartRate": 48,
            "averageStressLevel": 27,
            "bodyBatteryHighestValue": 88,
            "bodyBatteryLowestValue": 21,
            "moderateIntensityMinutes": 30,
            "vigorousIntensityMinutes": 15,
            "sleepingSeconds": 27000,
            "someUnusedField": None,
        }

    def get_sleep_data(self, cdate):
        self._rec("get_sleep_data", cdate)
        return {
            "dailySleepDTO": {
                "calendarDate": cdate,
                "sleepTimeSeconds": 26400,
                "deepSleepSeconds": 5400,
                "lightSleepSeconds": 13800,
                "remSleepSeconds": 6000,
                "awakeSleepSeconds": 1200,
                "sleepScores": {
                    "overall": {"value": 82, "qualifierKey": "GOOD"},
                    "remPercentage": {"value": 23, "qualifierKey": "EXCELLENT"},
                },
            },
            "avgOvernightHrv": 61.0,
            "hrvStatus": "BALANCED",
            "restingHeartRate": 47,
        }

    def get_hrv_data(self, cdate):
        self._rec("get_hrv_data", cdate)
        return {
            "hrvSummary": {
                "calendarDate": cdate,
                "weeklyAvg": 60,
                "lastNightAvg": 61,
                "status": "BALANCED",
                "baseline": {"balancedLow": 52, "balancedUpper": 70},
            }
        }

    def get_body_battery(self, start, end=None):
        self._rec("get_body_battery", start, end)
        return [
            {"date": start, "charged": 60, "drained": 55,
             "bodyBatteryValuesArray": [[1, 30], [2, 90], [3, 40]]}
        ]

    def get_activities_by_date(self, start, end=None, activitytype=None, sortorder=None):
        self._rec("get_activities_by_date", start, end, activitytype)
        return [
            {
                "activityId": 111,
                "activityName": "Morning Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": f"{end} 07:00:00",
                "distance": 10000.0,
                "duration": 3000.0,
                "averageSpeed": 3.333,
                "averageHR": 150,
            },
            {
                "activityId": 110,
                "activityName": "Commute",
                "activityType": {"typeKey": "cycling"},
                "distance": 8000.0,
                "duration": 1200.0,
                "averageSpeed": 6.667,
            },
        ]

    def upload_workout(self, payload):
        self._rec("upload_workout", payload)
        return {"workoutId": 999, "workoutName": payload["workoutName"],
                "sportType": payload["sportType"]}

    def schedule_workout(self, workout_id, date_str):
        self._rec("schedule_workout", workout_id, date_str)
        return {"workoutScheduleId": 555}
