import json
from google import genai
from app.config import settings

def generate_outreach_email(candidate_name: str, job_title: str, company: str, highlights: list) -> dict:
    if not settings.gemini_api_key:
        return {
            "subject": f"Application for {job_title} - {candidate_name}",
            "body": f"Dear Hiring Manager,\n\nI am writing to express my strong interest in the {job_title} role at {company}."
        }

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = f"""
    Draft a concise, high-converting cold email for a job application.
    Applicant Name: {candidate_name}
    Position: {job_title} at {company}
    Key Strengths: {', '.join(highlights)}

    Return strict JSON with keys:
    - "subject": string
    - "body": string
    """
    try:
        response = client.models.generate_content(
            model=settings.primary_model or "gemini-3.5-flash-lite",
            contents=prompt
        )
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(cleaned_text)
    except Exception as e:
        print(f"Email drafting error: {e}")
        return {
            "subject": f"Interest in {job_title} position",
            "body": f"Dear Hiring Team at {company},\n\nI would love to discuss how my background aligns with the {job_title} role."
        }