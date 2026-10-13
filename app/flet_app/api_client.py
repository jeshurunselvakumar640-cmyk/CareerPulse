"""
CareerPulse API Client.
Handles all HTTP communication between the Flet Python frontend and the FastAPI backend.
"""
import os
import logging
from typing import Dict, Any, List, Optional
import httpx

from app.flet_app.config import API_BASE_URL

logger = logging.getLogger("careerpulse.flet_api")


class CareerPulseApiClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or API_BASE_URL).rstrip("/")
        self.token: Optional[str] = None
        self._timeout = httpx.Timeout(45.0, connect=10.0)

    def set_token(self, token: Optional[str]) -> None:
        self.token = token

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def get_health(self) -> Dict[str, Any]:
        """Check API backend status."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(f"{self.base_url}/api/health")
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.warning("Health check failed: %s", e)
        return {"status": "offline", "service": "Backend Unreachable"}

    async def get_profile(self) -> Dict[str, Any]:
        """Fetch current user profile and session status."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(f"{self.base_url}/api/user/profile", headers=self._get_headers())
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Fetch profile failed: %s", e)
        return {"authenticated": False, "profile_complete": False, "profile": {}}

    async def update_profile(self, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update user profile in Supabase."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/user/profile/update",
                    headers=self._get_headers(),
                    json=profile_data
                )
                if res.status_code == 200:
                    return res.json()
                return {"status": "error", "detail": res.text}
        except Exception as e:
            logger.error("Update profile failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def get_insights_and_opportunities(self, count: int = 25) -> Dict[str, Any]:
        """Fetch AI opportunity matches and insights for the user profile."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(
                    f"{self.base_url}/api/user/profile/insights?count={count}",
                    headers=self._get_headers()
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Fetch insights failed: %s", e)
        return {"status": "error", "insights": {}, "opportunities": [], "daily_news": []}

    async def get_saved_opportunities(self) -> Dict[str, Any]:
        """Fetch saved and waitlisted opportunities from Supabase."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(
                    f"{self.base_url}/api/user/opportunities/saved",
                    headers=self._get_headers()
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Fetch saved opportunities failed: %s", e)
        return {"status": "success", "saved": [], "waitlisted": []}

    async def save_opportunity(self, opportunity: Dict[str, Any], status: str = "interested") -> Dict[str, Any]:
        """Save opportunity to user's saved list in Supabase."""
        try:
            payload = {**opportunity, "status": status}
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/user/opportunities/save",
                    headers=self._get_headers(),
                    json=payload
                )
                return {"status": "success" if res.status_code == 200 else "error"}
        except Exception as e:
            logger.error("Save opportunity failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def remove_saved_opportunity(self, company: str, title: str) -> Dict[str, Any]:
        """Remove opportunity from user's saved list in Supabase."""
        try:
            payload = {"company": company, "title": title}
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/user/opportunities/remove",
                    headers=self._get_headers(),
                    json=payload
                )
                return {"status": "success" if res.status_code == 200 else "error"}
        except Exception as e:
            logger.error("Remove opportunity failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def get_personalized_news(self, limit: int = 10, refresh: bool = False) -> Dict[str, Any]:
        """Fetch personalized tech news verified by Tavily and Gemini."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(
                    f"{self.base_url}/api/user/news/personalized?limit={limit}&refresh={str(refresh).lower()}",
                    headers=self._get_headers()
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Fetch personalized news failed: %s", e)
        return {"status": "success", "articles": []}

    async def lookup_company_contact(self, company: str, title: str) -> Dict[str, Any]:
        """Lookup recruiter/company contact using Tavily."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/opportunity/contact-lookup",
                    headers=self._get_headers(),
                    json={"company": company, "title": title}
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Contact lookup failed: %s", e)
        return {"status": "error", "contact": {}}

    async def generate_email_draft(self, draft_req: Dict[str, Any]) -> Dict[str, Any]:
        """Generate AI cold outreach email using Gemini."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/email/generate",
                    headers=self._get_headers(),
                    json=draft_req
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Email generation failed: %s", e)
        return {"status": "error", "subject": "", "body": "Failed to generate email draft."}

    async def send_email(self, send_req: Dict[str, Any]) -> Dict[str, Any]:
        """Send application outreach email via SMTP."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/email/send",
                    headers=self._get_headers(),
                    json=send_req
                )
                if res.status_code == 200:
                    return res.json()
                return {"status": "error", "detail": res.text}
        except Exception as e:
            logger.error("Send email failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def trigger_daily_digest(self, force: bool = True) -> Dict[str, Any]:
        """Trigger instant delivery of the 24-hour career digest email."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/digest/send",
                    headers=self._get_headers(),
                    json={"force": force}
                )
                if res.status_code == 200:
                    return res.json()
                return {"status": "error", "detail": res.text}
        except Exception as e:
            logger.error("Trigger digest failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def get_applications(self) -> Dict[str, Any]:
        """Fetch user applications and tracking status."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(
                    f"{self.base_url}/api/applications",
                    headers=self._get_headers()
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Fetch applications failed: %s", e)
        return {"status": "success", "applications": []}

    async def get_mailbox_status(self) -> Dict[str, Any]:
        """Get connected Gmail mailbox sync status."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(
                    f"{self.base_url}/api/email/integration/status",
                    headers=self._get_headers()
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Fetch mailbox status failed: %s", e)
        return {"connected": False}

    async def sync_mailbox_replies(self, force: bool = True) -> Dict[str, Any]:
        """Synchronize incoming employer replies from connected Gmail."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/email/sync",
                    headers=self._get_headers(),
                    json={"force": force}
                )
                if res.status_code == 200:
                    return res.json()
                return {"status": "error", "detail": res.text}
        except Exception as e:
            logger.error("Sync replies failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def get_resume(self) -> Dict[str, Any]:
        """Fetch resume details from Supabase resume builder."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.get(
                    f"{self.base_url}/api/resume",
                    headers=self._get_headers()
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error("Fetch resume failed: %s", e)
        return {"status": "success", "profile": None}

    async def save_resume_personal_details(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """Save resume personal details."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/resume/personal-details",
                    headers=self._get_headers(),
                    json=details
                )
                return res.json() if res.status_code == 200 else {"status": "error"}
        except Exception as e:
            logger.error("Save resume personal details failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def save_resume_education(self, edu: Dict[str, Any]) -> Dict[str, Any]:
        """Add or update education entry in resume."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/resume/education",
                    headers=self._get_headers(),
                    json=edu
                )
                return res.json() if res.status_code == 200 else {"status": "error"}
        except Exception as e:
            logger.error("Save resume education failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def save_resume_experience(self, exp: Dict[str, Any]) -> Dict[str, Any]:
        """Add or update experience entry in resume."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(
                    f"{self.base_url}/api/resume/experience",
                    headers=self._get_headers(),
                    json=exp
                )
                return res.json() if res.status_code == 200 else {"status": "error"}
        except Exception as e:
            logger.error("Save resume experience failed: %s", e)
            return {"status": "error", "detail": str(e)}

    async def delete_resume_child(self, section: str, item_id: str) -> Dict[str, Any]:
        """Delete education, experience, or reference item."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.delete(
                    f"{self.base_url}/api/resume/child/{section}/{item_id}",
                    headers=self._get_headers()
                )
                return res.json() if res.status_code == 200 else {"status": "error"}
        except Exception as e:
            logger.error("Delete resume item failed: %s", e)
            return {"status": "error", "detail": str(e)}


api_client = CareerPulseApiClient()
