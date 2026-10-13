"""
Application state model for CareerPulse Flet GUI.
"""
from typing import Dict, Any, List, Optional

class AppState:
    def __init__(self):
        self.token: Optional[str] = None
        self.authenticated: bool = False
        self.user_profile: Dict[str, Any] = {
            "name": "",
            "email": "",
            "headline": "",
            "degree": "",
            "college": "",
            "year": "",
            "location": "",
            "experience": "",
            "experience_level": "Entry-Level",
            "skills": [],
            "interests": [],
            "preferred_roles": [],
            "preferred_domains": [],
            "github": "",
            "linkedin": "",
            "projects": [],
        }
        self.current_view: str = "dashboard"
        self.opportunities: List[Dict[str, Any]] = []
        self.insights: Dict[str, Any] = {
            "insight_text": "AI opportunity agent is analyzing live postings...",
            "best_role": "Software Engineer",
            "top_skill": "Python",
            "growing_skill": "Cloud / API Development",
            "recommended": "Full Stack Engineering"
        }
        self.saved_opportunities: List[Dict[str, Any]] = []
        self.waitlisted_opportunities: List[Dict[str, Any]] = []
        self.news_articles: List[Dict[str, Any]] = []
        self.applications: List[Dict[str, Any]] = []
        self.mailbox_status: Dict[str, Any] = {"connected": False}
        self.resume_data: Dict[str, Any] = {}
        self.is_loading: bool = False
        self.status_message: str = ""

state = AppState()
