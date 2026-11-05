import os
import datetime
import httpx
from dotenv import load_dotenv

load_dotenv()


class SevenRoomsClient:
    def __init__(self):
        self.client_id = os.environ.get("CLIENT_ID")
        self.client_secret = os.environ.get("CLIENT_SECRET")
        self.base_url = "https://api.sevenrooms.com/2_4"
        self._token = None
        self._token_expiry = None
        # Persistent async client — one TCP connection reused across all requests,
        # no per-call handshake overhead.
        self._http = httpx.AsyncClient(timeout=15.0)

    async def _get_auth_token(self) -> str:
        """Returns the cached token or re-authenticates if expired."""
        now = datetime.datetime.now()
        if self._token and self._token_expiry and now < self._token_expiry:
            return self._token

        res = await self._http.post(
            f"{self.base_url}/auth",
            data={"client_id": self.client_id, "client_secret": self.client_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        res.raise_for_status()

        data = res.json().get("data", {})
        self._token = data.get("token")

        expiry_str = data.get("token_expiration_datetime")
        if expiry_str:
            try:
                self._token_expiry = datetime.datetime.fromisoformat(expiry_str)
            except ValueError:
                self._token_expiry = now + datetime.timedelta(hours=23)
        else:
            self._token_expiry = now + datetime.timedelta(hours=23)

        return self._token

    async def check_availability(
        self, venue_id: str, date: str, party_size: int,
        start_time: str, end_time: str, reservation_id: str = None
    ) -> list:
        """Checks for available booking times at a venue."""
        token = await self._get_auth_token()
        params = {
            "date": date,
            "party_size": party_size,
            "start_time": start_time,
            "end_time": end_time,
        }
        if reservation_id:
            params["reservation_id"] = reservation_id

        res = await self._http.get(
            f"{self.base_url}/venues/{venue_id}/availability",
            params=params,
            headers={"Authorization": token},
        )
        res.raise_for_status()

        available_slots = []
        for shift in res.json().get("data", {}).get("availability", []):
            for t in shift.get("times", []):
                if t.get("type") == "book":
                    available_slots.append({
                        "time": t.get("time"),
                        "access_persistent_id": t.get("access_persistent_id"),
                    })
        return available_slots

    async def create_reservation(
        self, venue_id: str, date: str, time: str, party_size: int,
        first_name: str, last_name: str, phone: str,
        email: str = None, notes: str = None
    ) -> dict:
        """Creates a new reservation at the specified venue."""
        token = await self._get_auth_token()
        payload = {
            "date": date,
            "time": time,
            "party_size": party_size,
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "source": "VoiceAgent",
        }
        if email:
            payload["email"] = email
        if notes:
            payload["notes"] = notes

        res = await self._http.put(
            f"{self.base_url}/venues/{venue_id}/book",
            data=payload,
            headers={"Authorization": token, "Content-Type": "application/x-www-form-urlencoded"},
        )
        res.raise_for_status()
        return res.json().get("data", {})

    async def modify_reservation(
        self, venue_id: str, reservation_id: str,
        date: str, time: str, party_size: int
    ) -> dict:
        """Modifies reservation date/time/party_size."""
        token = await self._get_auth_token()
        res = await self._http.put(
            f"{self.base_url}/venues/{venue_id}/book",
            data={"reservation_id": reservation_id, "date": date, "time": time, "party_size": party_size},
            headers={"Authorization": token, "Content-Type": "application/x-www-form-urlencoded"},
        )
        res.raise_for_status()
        return res.json().get("data", {})

    async def cancel_reservation(self, reservation_id: str) -> dict:
        """Cancels an existing reservation."""
        token = await self._get_auth_token()
        res = await self._http.post(
            f"{self.base_url}/reservations/{reservation_id}/cancel",
            headers={"Authorization": token, "Content-Type": "application/x-www-form-urlencoded"},
        )
        res.raise_for_status()
        return res.json().get("data", {})

    async def search_clients(self, phone: str = None, name: str = None) -> list:
        """Searches client profiles by phone number or name."""
        token = await self._get_auth_token()
        params = {}
        if phone:
            params["phone"] = phone
        if name:
            params["name"] = name
        res = await self._http.get(
            f"{self.base_url}/clients",
            params=params,
            headers={"Authorization": token},
        )
        res.raise_for_status()
        return res.json().get("data", {}).get("results", [])

    async def search_reservations(
        self, phone: str = None, name: str = None, status: str = None
    ) -> list:
        """Searches reservations by guest phone, guest name, or status."""
        token = await self._get_auth_token()
        params = {}
        if phone:
            params["phone"] = phone
        if name:
            params["name"] = name
        if status:
            params["status"] = status
        res = await self._http.get(
            f"{self.base_url}/reservations",
            params=params,
            headers={"Authorization": token},
        )
        res.raise_for_status()
        return res.json().get("data", {}).get("results", [])

    async def get_reservation(self, reservation_id: str) -> dict:
        """Retrieves a single reservation by ID or reference code."""
        token = await self._get_auth_token()
        res = await self._http.get(
            f"{self.base_url}/reservations/{reservation_id}",
            headers={"Authorization": token},
        )
        res.raise_for_status()
        return res.json().get("data", {})

    async def create_waitlist_entry(
        self, venue_id: str, date: str, party_size: int,
        first_name: str, last_name: str, phone: str,
        email: str = None, notes: str = None
    ) -> dict:
        """Adds a guest to the SevenRooms waitlist for a specific date/shift."""
        token = await self._get_auth_token()
        
        # 1. Fetch availability for that date to grab the active shift_persistent_id
        res_avail = await self._http.get(
            f"{self.base_url}/venues/{venue_id}/availability",
            params={
                "date": date,
                "party_size": party_size,
                "start_time": "12:00",
                "end_time": "22:00"
            },
            headers={"Authorization": token}
        )
        res_avail.raise_for_status()
        
        avail_data = res_avail.json().get("data", {}).get("availability", [])
        if not avail_data:
            raise ValueError("No shifts found for the requested date.")
            
        # Extract the shift_persistent_id from the first shift
        shift_persistent_id = avail_data[0].get("shift_persistent_id")
        if not shift_persistent_id:
            raise ValueError("Could not resolve shift_persistent_id for the date.")
            
        # 2. Call POST /waitlist to create the entry
        payload = {
            "venue_id": venue_id,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone,
            "phone_number_locale": "LU",
            "party_size": party_size,
            "shift_persistent_id": shift_persistent_id,
            "source": "VoiceAgent",
        }
        if email:
            payload["email"] = email
        if notes:
            payload["notes"] = notes
            
        res_wl = await self._http.post(
            f"{self.base_url}/waitlist",
            data=payload,
            headers={"Authorization": token, "Content-Type": "application/x-www-form-urlencoded"}
        )
        res_wl.raise_for_status()
        return res_wl.json().get("data", {})

