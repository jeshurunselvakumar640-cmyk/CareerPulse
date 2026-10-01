# CareerPulse 🚀

### Your Personal Career Opportunity Agent

CareerPulse is an AI-powered career assistant that automatically discovers relevant jobs and internships, analyzes opportunities using AI, matches them with a user's skills and preferences, and delivers personalized career opportunities and major technology news through automated email digests.

## ✨ Features

* 🔎 Automated job and internship discovery
* 🤖 AI-powered opportunity extraction and analysis
* 🎯 Personalized skill-based matching
* 🏆 Gold / Silver / Bronze opportunity classification
* 📰 Major technology news digest
* 📧 Automated 24-hour email briefings
* 💼 LinkedIn authentication and profile integration
* 🔖 Save and track opportunities
* 📅 Deadline tracking
* 🔗 Verified application links
* 🛡️ Job and news validation
* 🧠 Skill-gap identification

## 🛠️ Technology Stack

| Component      | Technology                     |
| -------------- | ------------------------------ |
| Frontend       | HTML, Tailwind CSS, JavaScript |
| Backend        | Python, FastAPI                |
| Database       | Supabase / PostgreSQL          |
| AI             | Google Gemini API              |
| Web Search     | Tavily Search API              |
| Authentication | LinkedIn OAuth                 |
| Email          | Gmail SMTP + MIME              |

## 🔄 How It Works

```text
User Profile
     ↓
CareerPulse
     ↓
Tavily Web Search
     ↓
Source Validation
     ↓
Gemini AI Analysis
     ↓
Job & News Verification
     ↓
Skill Matching
     ↓
Supabase
     ↓
Dashboard + 24-Hour Email Digest
```

## 🎯 Project Objective

CareerPulse aims to reduce the time students spend manually searching multiple websites for internships, jobs, and technology opportunities.

Instead of repeatedly searching different platforms, users provide their skills, interests, preferred roles, and locations. CareerPulse then discovers, analyzes, filters, and organizes relevant opportunities in one place.

## 📰 24-Hour Technology Digest

CareerPulse can automatically send a personalized email digest every 24 hours containing:

* Major technology news
* Relevant internships and jobs
* Matched and missing skills
* Upcoming deadlines
* Verified application links

The system prioritizes important technology developments and avoids filling the digest with trivial or duplicate stories.

## 🎯 Opportunity Matching

CareerPulse compares job requirements with the user's profile and identifies:

* Matching skills
* Missing skills
* Role relevance
* Experience compatibility
* Location compatibility

Opportunities can then be categorized into:

* 🥇 Gold
* 🥈 Silver
* 🥉 Bronze

## 🔐 Environment Variables

Create a `.env` file containing your API credentials:

```env
SUPABASE_URL=
SUPABASE_KEY=

GEMINI_API_KEY=
TAVILY_API_KEY=

LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
RECIPIENT_EMAIL=
```

**Never commit your `.env` file or expose API keys, passwords, or OAuth credentials.**

## 🚀 Running Locally

Clone the repository:

```bash
git clone <your-repository-url>
cd CareerPulse
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the FastAPI server:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the application:

```text
http://127.0.0.1:8000/
```

| Route     | Description                                        |
| --------- | -------------------------------------------------- |
| `/`       | Public landing page (no authentication required)    |
| `/app`    | Authenticated dashboard app (LinkedIn sign-in)      |

API documentation:

```text
http://127.0.0.1:8000/docs
```

## 📌 Project Status

CareerPulse is an actively developed project demonstrating the integration of:

**Artificial Intelligence + Web Search + Automation + Database Management + Personalized Career Recommendations**

## 👥 Team

This project was developed as a group project by:

* **Jeshurun Selvakumar**
* **Ojas Joshi**
* **Kshitij Jadhav**

## 📄 License

This project is licensed under the **MIT License**.

See the [LICENSE](LICENSE) file for the complete license terms.
