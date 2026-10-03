let mockOpportunities = [];
let savedInterestedJobs = [];
let waitlistedJobs = [];
let currentSwipeIndex = 0;
let currentEmailPayload = null;
let currentTechNews = [];

// Application communication / company replies state.
// Every value here is per-user and is reset on logout so one account can never
// see another account's applications or replies.
const applicationsState = {
    loaded: false,
    loading: false,
    integrations: null,
    integrationError: null,
    applications: [],
    byOpportunity: {},
    unread: 0,
    lastSyncAt: null,
    syncNotice: null
};

let currentConversationId = null;

// Global auth state — prevents rendering before auth is resolved
const authState = {
    loading: true,
    authenticated: false,
    user: null
};

const USER_AVATAR_SVG = `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40' viewBox='0 0 24 24' fill='none' stroke='%23818cf8' stroke-width='1.5'><path d='M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2'/><circle cx='12' cy='7' r='4'/></svg>`;

const APP_VIEW_HASHES = ["dashboard", "profile", "interested", "waitlist", "applications", "news", "resume"];

// Personalized tech news state.
// Kept separate from currentTechNews so opening the tab never re-triggers an
// API call, and so a duplicate in-flight request cannot be issued.
const newsState = {
    loaded: false,
    loading: false,
    inFlight: false,
    articles: [],
    signature: null,
    error: null
};

function escapeHtml(text) {
    if (!text) return "";
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
    initBrandLogo();

    // Hide both containers initially while auth resolves
    const unauthEl = document.getElementById("unauthContainer");
    const authEl = document.getElementById("authContainer");
    if (unauthEl) unauthEl.classList.add("hidden");
    if (authEl) authEl.classList.add("hidden");

    checkBackendHealth();

    // Sequential auth init: authenticate first, then load dependent data
    initializeAuth();

    const logoutBtn = document.getElementById("profileLogoutBtn");
    if (logoutBtn) {
        logoutBtn.addEventListener("click", handleLogout);
    }
});

/**
 * Swaps the "CP" initials tile for the CareerPulse logo image once it loads.
 * A missing asset simply leaves the initials visible.
 */
function initBrandLogo() {
    document.querySelectorAll(".cp-app-logo").forEach(tile => {
        const img = tile.querySelector("[data-app-logo-img]");
        if (!img) return;
        if (img.complete && img.naturalWidth > 0) {
            tile.classList.add("has-logo");
        }
        img.addEventListener("load", () => tile.classList.add("has-logo"));
    });
}

/* ==========================================================================
   RESUME BUILDER
   ==========================================================================
   Mirrors the newsState pattern: one client-side document cache, loaded the
   first time the view opens. Ownership is never handled here — the server
   derives the user from the session cookie, so the browser has no way to name
   another account. Every write targets a single section, so saving one part of
   the resume can never clobber another.
   ========================================================================== */

/* Sections backed by a simple list of values (shared by 5 sections). */
const RESUME_ITEM_SECTIONS = ["skills", "hobbies", "awards", "activities", "languages"];

/* Sections backed by repeatable records with their own form. */
const RESUME_ENTRY_SECTIONS = ["education", "experience", "references", "projects", "publications"];

const resumeState = {
    loaded: false,
    loading: false,
    inFlight: false,
    error: null,
    profile: {},
    education: [],
    experience: [],
    references: [],
    projects: [],
    publications: [],
    skills: [],
    hobbies: [],
    awards: [],
    activities: [],
    languages: [],
    totalExperienceDisplay: "0 years 0 months",
    /* Display name of the uploaded photo, shown beside the preview. */
    profilePictureName: "",
    /* CareerPulse profile picture, used only as a fallback preview. */
    careerPulseAvatar: "",
    /* Which A4 template the preview renders. Presentation only: it is never
       persisted and never alters resume data. */
    template: "template-1",
    /* section -> record id being edited ("" = creating a new one) */
    editing: {},
    /* pending, unsaved values for the simple item lists */
    pendingItems: {}
};

const RESUME_EMPLOYMENT_TYPES = ["Internship", "Part Time Job", "Full Time Job"];

/* Field definitions for the repeatable entry forms, so all five sections share
   one renderer instead of five hand-written forms. */
const RESUME_ENTRY_SCHEMAS = {
    education: {
        title: "Education",
        ongoing: { name: "currently_doing", label: "Currently Doing" },
        fields: [
            { name: "course_degree", label: "Course / Degree *", type: "text", required: true },
            { name: "school_university", label: "School / University *", type: "text", required: true },
            { name: "grade_score", label: "Grade / Score", type: "text" },
            { name: "start_date", label: "Start Date *", type: "date", required: true },
            { name: "end_date", label: "End Date *", type: "date" }
        ]
    },
    experience: {
        title: "Experience",
        ongoing: { name: "currently_work_here", label: "I currently work here" },
        fields: [
            { name: "company_name", label: "Company Name *", type: "text", required: true },
            { name: "job_title", label: "Job Title *", type: "text", required: true },
            {
                name: "employment_type", label: "Employment Type *", type: "select", required: true,
                options: RESUME_EMPLOYMENT_TYPES
            },
            { name: "start_date", label: "Start Date *", type: "date", required: true },
            { name: "end_date", label: "End Date *", type: "date" },
            { name: "details", label: "Details", type: "textarea" }
        ]
    },
    references: {
        title: "Reference",
        fields: [
            { name: "referee_name", label: "Referee's Name *", type: "text", required: true },
            { name: "job_title", label: "Job Title", type: "text" },
            { name: "company_name", label: "Company Name", type: "text" },
            { name: "email", label: "Email", type: "email" },
            { name: "phone", label: "Phone", type: "tel" }
        ]
    },
    projects: {
        title: "Project",
        fields: [
            { name: "title", label: "Title *", type: "text", required: true },
            { name: "link", label: "Link", type: "url" },
            { name: "details", label: "Details", type: "textarea" }
        ]
    },
    publications: {
        title: "Publication",
        fields: [
            { name: "title", label: "Title *", type: "text", required: true },
            { name: "link", label: "Link", type: "url" },
            { name: "details", label: "Details", type: "textarea" }
        ]
    }
};

/* ---------- small helpers ---------- */

function resumeSafeId(value) {
    return String(value == null ? "" : value).replace(/[^0-9a-zA-Z-]/g, "");
}

function resumeSetValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value == null ? "" : value;
}

function resumeGetValue(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : "";
}

/* ---------- DD/MM/YYYY <-> ISO (YYYY-MM-DD) ----------
   The Resume Builder shows dates as DD/MM/YYYY while the API and the database
   keep ISO YYYY-MM-DD. These helpers are pure string arithmetic: no Date
   object is constructed, so there is no timezone shift and no off-by-one day. */

function resumeIsoToDisplay(iso) {
    const text = String(iso == null ? "" : iso).trim();
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!match) return "";
    return `${match[3]}/${match[2]}/${match[1]}`;
}

function resumeDisplayToIso(display) {
    const text = String(display == null ? "" : display).trim();
    if (!text) return "";
    const match = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (!match) return null;

    const day = parseInt(match[1], 10);
    const month = parseInt(match[2], 10);
    const year = parseInt(match[3], 10);

    if (month < 1 || month > 12) return null;
    if (day < 1 || day > 31) return null;

    // Days per month, with a leap-year check that avoids Date entirely.
    const lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
    const maxDay = month === 2 && leap ? 29 : lengths[month - 1];
    if (day > maxDay) return null;

    const pad = n => String(n).padStart(2, "0");
    return `${year}-${pad(month)}-${pad(day)}`;
}

/* "2024-03" or "2024-03-15" -> "Mar 2024" */
function resumeFormatMonth(value) {
    if (!value) return "";
    const parts = String(value).split("-");
    if (parts.length < 2) return "";
    const monthIndex = parseInt(parts[1], 10) - 1;
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const label = months[monthIndex] || "";
    return label ? `${label} ${parts[0]}` : parts[0];
}

function resumeInputClass() {
    return "w-full px-4 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-xs text-white focus:outline-none focus:border-indigo-500";
}

function resumeSectionTitle(section) {
    return { education: "Education", experience: "Experience", references: "Reference", projects: "Project", publications: "Publication" }[section] || "Entry";
}

/* Button busy state, following the existing setSyncBusy convention. */
function resumeSetBusy(btnId, busy, idleLabel) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.disabled = busy;
    btn.style.opacity = busy ? "0.6" : "1";
    if (busy) {
        if (!btn.dataset.idleLabel) btn.dataset.idleLabel = idleLabel || btn.textContent.trim();
        btn.textContent = "Saving…";
    } else if (btn.dataset.idleLabel) {
        btn.textContent = idleLabel || btn.dataset.idleLabel;
    }
}

/* Shared fetch wrapper. Returns parsed JSON or throws with the server detail. */
async function resumeRequest(url, options = {}) {
    const authHeaders = await getAuthHeader();
    const res = await fetch(url, {
        ...options,
        headers: { "Content-Type": "application/json", ...authHeaders, ...(options.headers || {}) },
        credentials: "same-origin"
    });

    if (res.status === 401) {
        handleExpiredToken();
        throw new Error("Session expired");
    }

    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }

    if (!res.ok) {
        throw new Error(data.detail || `Request failed (${res.status})`);
    }
    return data;
}

/* ---------- loading ---------- */

async function loadResumeView(force = false) {
    if (resumeState.inFlight) return;
    if (resumeState.loaded && !force) return;
    if (resumeState.loading) return;

    resumeState.loading = true;
    resumeState.error = null;

    const loadingEl = document.getElementById("resumeLoadingState");
    const errorEl = document.getElementById("resumeErrorState");
    const errorText = document.getElementById("resumeErrorText");
    const contentEl = document.getElementById("resumeContent");

    if (contentEl) contentEl.classList.add("hidden");
    if (loadingEl) loadingEl.classList.remove("hidden");
    if (errorEl) errorEl.classList.add("hidden");

    try {
        const data = await resumeRequest("/api/resume");
        const resume = data.resume || {};

        resumeState.profile = resume;
        RESUME_ENTRY_SECTIONS.forEach(section => {
            resumeState[section] = Array.isArray(resume[section]) ? resume[section] : [];
        });
        RESUME_ITEM_SECTIONS.forEach(section => {
            resumeState[section] = Array.isArray(resume[section]) ? resume[section] : [];
            // Seed the unsaved working copy from what is stored.
            resumeState.pendingItems[section] = resumeState[section].slice();
        });
        resumeState.totalExperienceDisplay = resume.total_experience_display || "0 years 0 months";
        resumeState.loaded = true;

        applyResumeToForm(resume);
        renderResumeAll();

        if (contentEl) contentEl.classList.remove("hidden");
    } catch (err) {
        resumeState.error = err.message || "Could not load your resume.";
        if (errorText) errorText.textContent = resumeState.error;
        if (errorEl) errorEl.classList.remove("hidden");
        if (loadingEl) loadingEl.classList.add("hidden");
        showToast(resumeState.error);
    } finally {
        resumeState.loading = false;
        if (loadingEl) loadingEl.classList.add("hidden");
    }
}

function applyResumeToForm(resume) {
    resumeSetValue("resumeName", resume.name);
    resumeSetValue("resumeEmail", resume.email);
    resumeSetValue("resumeDob", resumeIsoToDisplay(resume.date_of_birth));
    resumeSetValue("resumeGender", resume.gender);
    resumeSetValue("resumeLinkedin", resume.linkedin_url);
    resumeSetValue("resumeGithub", resume.github_url);
    resumeSetValue("resumeWebsite", resume.website_url);
    resumeSetValue("resumeAddress", resume.address);
    resumeSetValue("resumePincode", resume.pincode);
    resumeSetValue("resumeCity", resume.city);
    resumeSetValue("resumeState", resume.state);
    resumeSetValue("resumeCountry", resume.country);
    resumeSetValue("resumeHeadline", resume.headline);
    resumeSetValue("resumeSummary", resume.summary);
    resumeSetValue("resumeAdditionalInfo", resume.additional_information);

    // Reveal the "Add Additional Details" panel when any of its fields has a value.
    const hasAdditional = ["resumeDob", "resumeGender", "resumeLinkedin", "resumeGithub",
        "resumeWebsite", "resumeAddress", "resumePincode", "resumeCity", "resumeState",
        "resumeCountry"].some(id => resumeGetValue(id));
    if (hasAdditional) toggleResumeAdditional(true);

    renderResumeImage("profile-picture", resume.profile_picture_display, resume.profile_picture_url);
    renderResumeImage("signature", resume.signature_display, resume.signature_url);

    const totalEl = document.getElementById("resumeTotalExperience");
    if (totalEl) totalEl.textContent = resume.total_experience_display || "0 years 0 months";
}

/* Recover the display name of a saved photo from its storage path.
   Only the final segment is used, and only when it actually looks like a
   filename, so the user's UUID and the folder layout are never surfaced. */
function resumePhotoNameFromPath(storedPath) {
    const text = String(storedPath == null ? "" : storedPath).trim();
    if (!text) return "";
    const parts = text.split("/").filter(Boolean);
    if (!parts.length) return "";
    const file = parts[parts.length - 1];
    if (file.length > 120) return "";
    if (!/\.[A-Za-z0-9]{1,8}$/.test(file)) return "";
    return file;
}

/* ---------- A4 resume preview ---------- */

function rvDate(value) {
    return resumeIsoToDisplay(value) || "";
}

function rvPeriod(start, end, ongoingLabel) {
    const from = rvDate(start);
    const to = end ? rvDate(end) : (ongoingLabel || "Present");
    if (!from && !to) return "";
    if (from && to) return `${from} - ${to}`;
    return from || to;
}

function rvChips(values) {
    const list = (values || []).filter(v => String(v || "").trim());
    if (!list.length) return "";
    return `<div class="rv-chips">${list.map(v => `<span class="rv-chip">${escapeHtml(v)}</span>`).join("")}</div>`;
}

/* ---------- resume templates ----------

   All three templates read ONE normalized model built by
   resumePreviewModel(). A template only decides layout structure, header
   treatment and visual style, so switching templates can never add, alter or
   drop resume data. Presentation lives in resumeState.template and is never
   persisted. */

const RESUME_TEMPLATES = [
    {
        id: "template-1",
        label: "Template 1",
        tagline: "Warm Terracotta Header",
        description: "Modern professional design featuring an angled terracotta banner, circular photo, and balanced two-column layout.",
        preview: "template-1.png"
    },
    {
        id: "template-2",
        label: "Template 2",
        tagline: "Sage Sidebar & Blush Accent",
        description: "Editorial two-tone layout with a full-height sage sidebar, soft blush header, and chevron section markers.",
        preview: "template-2.png"
    },
    {
        id: "template-3",
        label: "Template 3",
        tagline: "Royal Blue Geometric Curve",
        description: "Vibrant modern layout with royal blue curved banner, geometric pattern motifs, and circular icon badges.",
        preview: "template-3.png"
    }
];

const RESUME_TEMPLATE_IDS = RESUME_TEMPLATES.map(function (t) { return t.id; });
const RESUME_DEFAULT_TEMPLATE = "template-1";

const RESUME_SECTION_TITLES = {
    summary: "Summary",
    experience: "Experience",
    education: "Education",
    skills: "Skills",
    projects: "Projects",
    publications: "Publications",
    awards: "Awards",
    activities: "Activities",
    languages: "Languages",
    references: "References",
    hobbies: "Hobbies",
    additional: "Additional Information"
};

const RESUME_ICONS = {
    phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="rv-svg-icon"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>',
    mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="rv-svg-icon"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>',
    pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="rv-svg-icon"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>',
    globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="rv-svg-icon"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>',
    calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="rv-svg-icon"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>',
    linkedin: '<svg viewBox="0 0 24 24" fill="currentColor" class="rv-svg-icon"><path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z"/></svg>',
    github: '<svg viewBox="0 0 24 24" fill="currentColor" class="rv-svg-icon"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/></svg>'
};

function resumeTemplateById(id) {
    return RESUME_TEMPLATES.find(function (t) { return t.id === id; })
        || RESUME_TEMPLATES.find(function (t) { return t.id === RESUME_DEFAULT_TEMPLATE; });
}

/* Links stay real anchors so the exported PDF keeps them clickable. Only
   http/https/mailto survive, so a stored value cannot inject a javascript: URL. */
function rvSafeHref(url) {
    const raw = String(url || "").trim();
    if (!raw) return "";
    const candidate = /^[a-z][a-z0-9+.-]*:/i.test(raw) ? raw : "https://" + raw;
    if (!/^(https?:|mailto:)/i.test(candidate)) return "";
    return escapeHtml(candidate);
}

function rvLinkHtml(link, className) {
    const href = rvSafeHref(link);
    if (!href) return "";
    return '<div class="' + (className || "rv-link") + '"><a href="' + href + '" target="_blank" rel="noopener">' + escapeHtml(String(link).trim()) + '</a></div>';
}

function rvBulletListHtml(values, listClass) {
    const list = (values || []).filter(v => String(v || "").trim());
    if (!list.length) return "";
    return '<ul class="' + listClass + '">' + list.map(v => '<li>' + escapeHtml(v) + '</li>').join("") + '</ul>';
}

/* The single normalized view model every template renders from. */
function resumePreviewModel() {
    const p = resumeState.profile || {};
    const photo = String(p.profile_picture_display || resumeState.careerPulseAvatar || "").trim();
    const signature = String(p.signature_display || "").trim();

    const contact = [p.email, p.phone, p.city, p.state, p.country,
        p.linkedin_url, p.github_url, p.website_url]
        .map(v => String(v || "").trim()).filter(Boolean);

    const items = {
        education: (resumeState.education || []).map(e => ({
            degree: e.course_degree || "",
            school: e.school_university || "",
            grade: e.grade_score || "",
            start_date: e.start_date || "",
            end_date: e.end_date || "",
            currently_doing: !!e.currently_doing,
            title: [e.course_degree, e.school_university]
                .filter(x => String(x || "").trim()).join(" - "),
            meta: rvPeriod(e.start_date, e.end_date, e.currently_doing ? "Present" : ""),
            body: e.grade_score ? "Grade: " + e.grade_score : ""
        })),
        experience: (resumeState.experience || []).map(e => ({
            company: e.company_name || "",
            job_title: e.job_title || "",
            employment_type: e.employment_type || "",
            start_date: e.start_date || "",
            end_date: e.end_date || "",
            currently_work_here: !!e.currently_work_here,
            title: [e.job_title, e.company_name]
                .filter(x => String(x || "").trim()).join(" at "),
            meta: rvPeriod(e.start_date, e.end_date, e.currently_work_here ? "Present" : ""),
            body: e.details || "",
            tag: String(e.employment_type || "").trim() === "Internship" ? "Internship" : ""
        })),
        projects: (resumeState.projects || []).map(x => ({
            title: x.title || "", meta: "", body: x.details || "", link: x.link || ""
        })),
        publications: (resumeState.publications || []).map(x => ({
            title: x.title || "", meta: "", body: x.details || "", link: x.link || ""
        })),
        references: (resumeState.references || []).map(r => ({
            name: r.referee_name || "",
            job_title: r.job_title || "",
            company: r.company_name || "",
            email: r.email || "",
            phone: r.phone || "",
            title: r.referee_name || "",
            meta: "",
            body: [r.job_title, r.company_name, r.email, r.phone].filter(Boolean).join(" | ")
        }))
    };

    return {
        profile: p,
        photo: photo,
        signature: signature,
        contact: contact,
        items: items,
        lists: {
            skills: (resumeState.skills || []),
            awards: (resumeState.awards || []),
            activities: (resumeState.activities || []),
            languages: (resumeState.languages || []),
            hobbies: (resumeState.hobbies || [])
        },
        summary: String(p.summary || "").trim(),
        additional: String(p.additional_information || "").trim()
    };
}

/* ---------- Template 1 (Warm Terracotta Header + Two Columns) ---------- */

function renderTemplate1(model) {
    const p = model.profile || {};
    const name = escapeHtml(p.name || "");
    const headline = escapeHtml(p.headline || "");
    const photo = model.photo
        ? '<div class="t1-photo-wrap"><img class="t1-photo" src="' + escapeHtml(model.photo) + '" alt=""></div>'
        : '<div class="t1-photo-wrap"><div class="t1-photo-empty"></div></div>';

    const header = '<header class="t1-header-container">' +
        '<div class="t1-header">' +
            '<div class="t1-header-text">' +
                '<h1 class="t1-name">' + name + '</h1>' +
                (headline ? '<div class="t1-headline">' + headline + '</div>' : "") +
            '</div>' +
            photo +
        '</div>' +
    '</header>';

    const contactRows = [];
    if (p.phone) contactRows.push('<div class="t1-contact-item"><span class="t1-contact-label">Phone:</span> ' + escapeHtml(p.phone) + '</div>');
    if (p.email) contactRows.push('<div class="t1-contact-item"><span class="t1-contact-label">Email:</span> ' + escapeHtml(p.email) + '</div>');
    const loc = [p.address, p.city, p.state, p.pincode, p.country].filter(Boolean).join(", ");
    if (loc) contactRows.push('<div class="t1-contact-item"><span class="t1-contact-label">Location:</span> ' + escapeHtml(loc) + '</div>');
    if (p.website_url) contactRows.push('<div class="t1-contact-item"><span class="t1-contact-label">Website:</span> <a href="' + rvSafeHref(p.website_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.website_url) + '</a></div>');
    if (p.date_of_birth) contactRows.push('<div class="t1-contact-item"><span class="t1-contact-label">Birthdate:</span> ' + escapeHtml(rvDate(p.date_of_birth) || p.date_of_birth) + '</div>');
    if (p.linkedin_url) contactRows.push('<div class="t1-contact-item"><span class="t1-contact-label">LinkedIn:</span> <a href="' + rvSafeHref(p.linkedin_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.linkedin_url) + '</a></div>');
    if (p.github_url) contactRows.push('<div class="t1-contact-item"><span class="t1-contact-label">GitHub:</span> <a href="' + rvSafeHref(p.github_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.github_url) + '</a></div>');

    let leftHtml = "";
    if (contactRows.length) {
        leftHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Contact Details</h2><div class="t1-contact-list">' + contactRows.join("") + '</div></section>';
    }
    if (model.summary) {
        leftHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Summary</h2><div class="t1-text">' + escapeHtml(model.summary) + '</div></section>';
    }
    const skillsHtml = rvBulletListHtml(model.lists.skills, "t1-list");
    if (skillsHtml) {
        leftHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Skills</h2>' + skillsHtml + '</section>';
    }
    const langHtml = rvBulletListHtml(model.lists.languages, "t1-list");
    if (langHtml) {
        leftHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Languages:</h2>' + langHtml + '</section>';
    }
    const actList = (model.lists.activities || []).filter(v => String(v || "").trim());
    if (actList.length) {
        leftHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Activities</h2><div class="t1-text">' + escapeHtml(actList.join("\n\n")) + '</div></section>';
    }
    const awardList = (model.lists.awards || []).filter(v => String(v || "").trim());
    if (awardList.length) {
        leftHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Award</h2><div class="t1-text">' + escapeHtml(awardList.join("\n\n")) + '</div></section>';
    }

    let rightHtml = "";
    const exps = model.items.experience || [];
    if (exps.length) {
        const itemsHtml = exps.map(e => '<div class="t1-item">' +
            '<div class="t1-item-title">' + escapeHtml(e.company || e.title) + '</div>' +
            (e.meta ? '<div class="t1-item-meta">' + escapeHtml(e.meta) + '</div>' : "") +
            (e.job_title ? '<div class="t1-item-role">' + escapeHtml(e.job_title) + '</div>' : "") +
            (e.body ? '<div class="t1-item-body">' + escapeHtml(e.body) + '</div>' : "") +
        '</div>').join("");
        rightHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Experience</h2>' + itemsHtml + '</section>';
    }

    const edus = model.items.education || [];
    if (edus.length) {
        const itemsHtml = edus.map(e => {
            const head = [e.meta, e.school || e.title].filter(Boolean).join(" - ");
            const sub = [e.degree, e.grade ? (String(e.grade).toUpperCase().includes("CGPA") || String(e.grade).toLowerCase().includes("grade") ? e.grade : "CGPA " + e.grade) : ""].filter(Boolean).join(" - ");
            return '<div class="t1-item">' +
                '<div class="t1-item-title">' + escapeHtml(head) + '</div>' +
                (sub ? '<div class="t1-item-role">' + escapeHtml(sub) + '</div>' : "") +
                (e.body && !sub ? '<div class="t1-item-body">' + escapeHtml(e.body) + '</div>' : "") +
            '</div>';
        }).join("");
        rightHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Education</h2>' + itemsHtml + '</section>';
    }

    const projs = model.items.projects || [];
    if (projs.length) {
        const itemsHtml = projs.map(x => '<div class="t1-item">' +
            '<div class="t1-item-title">' + escapeHtml(x.title) + '</div>' +
            (x.link ? rvLinkHtml(x.link, "t1-link") : "") +
            (x.body ? '<div class="t1-item-body">' + escapeHtml(x.body) + '</div>' : "") +
        '</div>').join("");
        rightHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Projects</h2>' + itemsHtml + '</section>';
    }

    const refs = model.items.references || [];
    if (refs.length) {
        const itemsHtml = refs.map(r => '<div class="t1-item">' +
            '<div class="t1-item-title">' + escapeHtml(r.name || r.title) + '</div>' +
            (r.email ? '<div class="t1-item-meta">' + escapeHtml(r.email) + '</div>' : "") +
            (r.phone ? '<div class="t1-item-meta">' + escapeHtml(r.phone) + '</div>' : "") +
            (!r.email && !r.phone && r.body ? '<div class="t1-item-body">' + escapeHtml(r.body) + '</div>' : "") +
        '</div>').join("");
        rightHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Reference</h2>' + itemsHtml + '</section>';
    }

    if (model.additional) {
        rightHtml += '<section class="t1-sec"><h2 class="t1-sec-title">Additional Information</h2><div class="t1-item-body">' + escapeHtml(model.additional) + '</div></section>';
    }

    const sig = model.signature
        ? '<div class="t1-sig"><img src="' + escapeHtml(model.signature) + '" alt="Signature"><div class="t1-sig-line">Authorized Signature</div></div>'
        : "";

    return header +
        '<div class="t1-body">' +
            '<div class="t1-col-left">' + leftHtml + '</div>' +
            '<div class="t1-col-right">' + rightHtml + sig + '</div>' +
        '</div>';
}

/* ---------- Template 2 (Sage Sidebar + Blush Header + Chevrons) ---------- */

function renderTemplate2(model) {
    const p = model.profile || {};
    const name = escapeHtml(p.name || "");
    const headline = escapeHtml(p.headline || "");
    const photo = model.photo
        ? '<div class="t2-photo-wrap"><img class="t2-photo" src="' + escapeHtml(model.photo) + '" alt=""></div>'
        : '<div class="t2-photo-wrap"><div class="t2-photo-empty"></div></div>';

    const contactRows = [];
    if (p.phone) contactRows.push('<div class="t2-contact-row"><span class="t2-contact-icon">' + RESUME_ICONS.phone + '</span><span>' + escapeHtml(p.phone) + '</span></div>');
    if (p.email) contactRows.push('<div class="t2-contact-row"><span class="t2-contact-icon">' + RESUME_ICONS.mail + '</span><span>' + escapeHtml(p.email) + '</span></div>');
    const loc = [p.address, p.city, p.state, p.pincode, p.country].filter(Boolean).join(", ");
    if (loc) contactRows.push('<div class="t2-contact-row"><span class="t2-contact-icon">' + RESUME_ICONS.pin + '</span><span>' + escapeHtml(loc) + '</span></div>');
    if (p.website_url) contactRows.push('<div class="t2-contact-row"><span class="t2-contact-icon">' + RESUME_ICONS.globe + '</span><a href="' + rvSafeHref(p.website_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.website_url) + '</a></div>');
    if (p.date_of_birth) contactRows.push('<div class="t2-contact-row"><span class="t2-contact-icon">' + RESUME_ICONS.calendar + '</span><span>' + escapeHtml(rvDate(p.date_of_birth) || p.date_of_birth) + '</span></div>');
    if (p.linkedin_url) contactRows.push('<div class="t2-contact-row"><span class="t2-contact-icon">' + RESUME_ICONS.linkedin + '</span><a href="' + rvSafeHref(p.linkedin_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.linkedin_url) + '</a></div>');
    if (p.github_url) contactRows.push('<div class="t2-contact-row"><span class="t2-contact-icon">' + RESUME_ICONS.github + '</span><a href="' + rvSafeHref(p.github_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.github_url) + '</a></div>');

    let sideHtml = photo;
    if (contactRows.length) {
        sideHtml += '<section class="t2-side-sec"><h2 class="t2-side-title">Contact Details</h2><div>' + contactRows.join("") + '</div></section>';
    }
    if (model.summary) {
        sideHtml += '<section class="t2-side-sec"><h2 class="t2-side-title">Summary</h2><div class="t2-side-text">' + escapeHtml(model.summary) + '</div></section>';
    }
    const skillsHtml = rvBulletListHtml(model.lists.skills, "t2-side-list");
    if (skillsHtml) {
        sideHtml += '<section class="t2-side-sec"><h2 class="t2-side-title">Skills</h2>' + skillsHtml + '</section>';
    }
    const langHtml = rvBulletListHtml(model.lists.languages, "t2-side-list");
    if (langHtml) {
        sideHtml += '<section class="t2-side-sec"><h2 class="t2-side-title">Languages</h2>' + langHtml + '</section>';
    }
    const actList = (model.lists.activities || []).filter(v => String(v || "").trim());
    if (actList.length) {
        sideHtml += '<section class="t2-side-sec"><h2 class="t2-side-title">Activities</h2><div class="t2-side-text">' + escapeHtml(actList.join(", ")) + '</div></section>';
    }
    const hobList = (model.lists.hobbies || []).filter(v => String(v || "").trim());
    if (hobList.length) {
        sideHtml += '<section class="t2-side-sec"><h2 class="t2-side-title">Hobbies</h2><div class="t2-side-text">' + escapeHtml(hobList.join(", ")) + '</div></section>';
    }

    const header = '<header class="t2-header">' +
        '<h1 class="t2-name">' + name + '</h1>' +
        (headline ? '<div class="t2-headline">' + headline + '</div>' : "") +
    '</header>';

    function t2Section(title, inner) {
        if (!inner) return "";
        return '<section class="t2-sec">' +
            '<div class="t2-sec-head"><span class="t2-sec-chevron">&gt;</span><h2 class="t2-sec-title">' + escapeHtml(title) + '</h2></div>' +
            inner +
        '</section>';
    }

    let mainBody = "";
    const edus = model.items.education || [];
    if (edus.length) {
        const items = edus.map(e => '<div class="t2-item">' +
            '<div class="t2-item-title">' + escapeHtml(e.school || e.title) + '</div>' +
            (e.degree || e.grade ? '<div class="t2-item-sub">' + escapeHtml([e.degree, e.grade].filter(Boolean).join(" - ")) + '</div>' : "") +
            (e.meta ? '<div class="t2-item-meta">' + escapeHtml(e.meta) + '</div>' : "") +
            (e.body && !e.grade ? '<div class="t2-item-body">' + escapeHtml(e.body) + '</div>' : "") +
        '</div>').join("");
        mainBody += t2Section("Education", items);
    }

    const exps = model.items.experience || [];
    if (exps.length) {
        const items = exps.map(e => '<div class="t2-item">' +
            '<div class="t2-item-head">' +
                '<span class="t2-item-title">' + escapeHtml(e.company || e.title) + '</span>' +
                (e.meta ? '<span class="t2-item-meta">' + escapeHtml(e.meta) + '</span>' : "") +
            '</div>' +
            (e.job_title ? '<div class="t2-item-sub">' + escapeHtml(e.job_title) + (e.tag ? ' - ' + escapeHtml(e.tag.toLowerCase()) : "") + '</div>' : "") +
            (e.body ? '<div class="t2-item-body">' + escapeHtml(e.body) + '</div>' : "") +
        '</div>').join("");
        mainBody += t2Section("Experience", items);
    }

    const projs = model.items.projects || [];
    if (projs.length) {
        const items = projs.map(x => '<div class="t2-item">' +
            '<div class="t2-item-title">' + escapeHtml(x.title) + '</div>' +
            (x.link ? rvLinkHtml(x.link, "t2-link") : "") +
            (x.body ? '<div class="t2-item-body">' + escapeHtml(x.body) + '</div>' : "") +
        '</div>').join("");
        mainBody += t2Section("Projects", items);
    }

    const pubs = model.items.publications || [];
    if (pubs.length) {
        const items = pubs.map(x => '<div class="t2-item">' +
            '<div class="t2-item-title">' + escapeHtml(x.title) + '</div>' +
            (x.link ? rvLinkHtml(x.link, "t2-link") : "") +
            (x.body ? '<div class="t2-item-body">' + escapeHtml(x.body) + '</div>' : "") +
        '</div>').join("");
        mainBody += t2Section("Publications", items);
    }

    const refs = model.items.references || [];
    if (refs.length) {
        const items = refs.map(r => '<div class="t2-item">' +
            '<div class="t2-item-title">' + escapeHtml(r.name || r.title) + '</div>' +
            (r.email ? '<div class="t2-item-meta">' + escapeHtml(r.email) + '</div>' : "") +
            (r.phone ? '<div class="t2-item-meta">' + escapeHtml(r.phone) + '</div>' : "") +
            (!r.email && !r.phone && r.body ? '<div class="t2-item-body">' + escapeHtml(r.body) + '</div>' : "") +
        '</div>').join("");
        mainBody += t2Section("Reference", items);
    }

    const awardList2 = (model.lists.awards || []).filter(v => String(v || "").trim());
    if (awardList2.length) {
        mainBody += t2Section("Awards", '<div class="t2-item-body">' + escapeHtml(awardList2.join("\n\n")) + '</div>');
    }

    if (model.additional) {
        mainBody += t2Section("Additional Information", '<div class="t2-item-body">' + escapeHtml(model.additional) + '</div>');
    }

    const sig = model.signature
        ? '<div class="t2-sig"><img src="' + escapeHtml(model.signature) + '" alt="Signature"><div class="t2-sig-line">Authorized Signature</div></div>'
        : "";

    return '<aside class="t2-sidebar">' + sideHtml + '</aside>' +
        '<main class="t2-main">' +
            header +
            '<div class="t2-body">' + mainBody + sig + '</div>' +
        '</main>';
}

/* ---------- Template 3 (Royal Blue Geometric Curve + Two Columns) ---------- */

function renderTemplate3(model) {
    const p = model.profile || {};
    const name = escapeHtml(p.name || "");
    const headline = escapeHtml(p.headline || "");
    const photo = model.photo
        ? '<div class="t3-photo-wrap"><img class="t3-photo" src="' + escapeHtml(model.photo) + '" alt=""></div>'
        : '<div class="t3-photo-wrap"><div class="t3-photo-empty"></div></div>';

    const header = '<header class="t3-header">' +
        '<div class="t3-header-pattern"></div>' +
        photo +
        '<div class="t3-header-text">' +
            '<h1 class="t3-name">' + name + '</h1>' +
            (headline ? '<div class="t3-headline">' + headline + '</div>' : "") +
        '</div>' +
    '</header>';

    let leftHtml = "";
    const edus = model.items.education || [];
    if (edus.length) {
        const items = edus.map(e => '<div class="t3-item">' +
            '<div class="t3-item-title">' + escapeHtml(e.school || e.title) + '</div>' +
            (e.meta ? '<div class="t3-item-meta">' + escapeHtml(e.meta) + '</div>' : "") +
            (e.degree || e.grade ? '<div class="t3-item-sub">' + escapeHtml([e.degree, e.grade ? (String(e.grade).toUpperCase().includes("CGPA") ? e.grade : "CGPA " + e.grade) : ""].filter(Boolean).join(" - ")) + '</div>' : "") +
            (e.body && !e.grade ? '<div class="t3-item-body">' + escapeHtml(e.body) + '</div>' : "") +
        '</div>').join("");
        leftHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Education</h2>' + items + '</section>';
    }

    const exps = model.items.experience || [];
    if (exps.length) {
        const items = exps.map(e => '<div class="t3-item">' +
            '<div class="t3-item-title">' + escapeHtml(e.company || e.title) + '</div>' +
            (e.meta ? '<div class="t3-item-meta">' + escapeHtml(e.meta) + '</div>' : "") +
            (e.job_title ? '<div class="t3-item-sub">' + escapeHtml(e.job_title) + '</div>' : "") +
            (e.body ? '<div class="t3-item-body">' + escapeHtml(e.body) + '</div>' : "") +
        '</div>').join("");
        leftHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Experience</h2>' + items + '</section>';
    }

    const projs = model.items.projects || [];
    if (projs.length) {
        const items = projs.map(x => '<div class="t3-item">' +
            '<div class="t3-item-title">' + escapeHtml(x.title) + '</div>' +
            (x.link ? rvLinkHtml(x.link, "t3-link") : "") +
            (x.body ? '<div class="t3-item-body">' + escapeHtml(x.body) + '</div>' : "") +
        '</div>').join("");
        leftHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Projects</h2>' + items + '</section>';
    }

    const refs = model.items.references || [];
    if (refs.length) {
        const items = refs.map(r => '<div class="t3-item">' +
            '<div class="t3-item-title">' + escapeHtml(r.name || r.title) + '</div>' +
            (r.email ? '<div class="t3-item-meta">' + escapeHtml(r.email) + '</div>' : "") +
            (r.phone ? '<div class="t3-item-meta">' + escapeHtml(r.phone) + '</div>' : "") +
            (!r.email && !r.phone && r.body ? '<div class="t3-item-body">' + escapeHtml(r.body) + '</div>' : "") +
        '</div>').join("");
        leftHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Reference</h2>' + items + '</section>';
    }

    const pubs = model.items.publications || [];
    if (pubs.length) {
        const items = pubs.map(x => '<div class="t3-item">' +
            '<div class="t3-item-title">' + escapeHtml(x.title) + '</div>' +
            (x.link ? rvLinkHtml(x.link, "t3-link") : "") +
            (x.body ? '<div class="t3-item-body">' + escapeHtml(x.body) + '</div>' : "") +
        '</div>').join("");
        leftHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Publications</h2>' + items + '</section>';
    }

    const contactRows = [];
    if (p.phone) contactRows.push('<div class="t3-contact-row"><span class="t3-icon-badge">' + RESUME_ICONS.phone + '</span><span>' + escapeHtml(p.phone) + '</span></div>');
    if (p.email) contactRows.push('<div class="t3-contact-row"><span class="t3-icon-badge">' + RESUME_ICONS.mail + '</span><span>' + escapeHtml(p.email) + '</span></div>');
    const loc = [p.address, p.city, p.state, p.pincode, p.country].filter(Boolean).join(", ");
    if (loc) contactRows.push('<div class="t3-contact-row"><span class="t3-icon-badge">' + RESUME_ICONS.pin + '</span><span>' + escapeHtml(loc) + '</span></div>');
    if (p.website_url) contactRows.push('<div class="t3-contact-row"><span class="t3-icon-badge">' + RESUME_ICONS.globe + '</span><a href="' + rvSafeHref(p.website_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.website_url) + '</a></div>');
    if (p.date_of_birth) contactRows.push('<div class="t3-contact-row"><span class="t3-icon-badge">' + RESUME_ICONS.calendar + '</span><span>' + escapeHtml(rvDate(p.date_of_birth) || p.date_of_birth) + '</span></div>');
    if (p.linkedin_url) contactRows.push('<div class="t3-contact-row"><span class="t3-icon-badge">' + RESUME_ICONS.linkedin + '</span><a href="' + rvSafeHref(p.linkedin_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.linkedin_url) + '</a></div>');
    if (p.github_url) contactRows.push('<div class="t3-contact-row"><span class="t3-icon-badge">' + RESUME_ICONS.github + '</span><a href="' + rvSafeHref(p.github_url) + '" target="_blank" rel="noopener">' + escapeHtml(p.github_url) + '</a></div>');

    let rightHtml = "";
    if (contactRows.length) {
        rightHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Contact Details</h2><div>' + contactRows.join("") + '</div></section>';
    }
    if (model.summary) {
        rightHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Summary</h2><div class="t3-text">' + escapeHtml(model.summary) + '</div></section>';
    }
    const skillsHtml = rvBulletListHtml(model.lists.skills, "t3-list");
    if (skillsHtml) {
        rightHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Skills</h2>' + skillsHtml + '</section>';
    }
    const langHtml = rvBulletListHtml(model.lists.languages, "t3-list");
    if (langHtml) {
        rightHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Languages</h2>' + langHtml + '</section>';
    }
    const actList = (model.lists.activities || []).filter(v => String(v || "").trim());
    if (actList.length) {
        rightHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Activities</h2><div class="t3-text">' + escapeHtml(actList.join("\n\n")) + '</div></section>';
    }
    const awardList3 = (model.lists.awards || []).filter(v => String(v || "").trim());
    if (awardList3.length) {
        rightHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Awards</h2><div class="t3-text">' + escapeHtml(awardList3.join("\n\n")) + '</div></section>';
    }
    if (model.additional) {
        rightHtml += '<section class="t3-sec"><h2 class="t3-sec-title">Additional Information</h2><div class="t3-text">' + escapeHtml(model.additional) + '</div></section>';
    }

    const sig = model.signature
        ? '<div class="t3-sig"><img src="' + escapeHtml(model.signature) + '" alt="Signature"><div class="t3-sig-line">Authorized Signature</div></div>'
        : "";

    return header +
        '<div class="t3-body">' +
            '<div class="t3-col-left">' + leftHtml + '</div>' +
            '<div class="t3-col-right">' + rightHtml + sig + '</div>' +
        '</div>';
}

/* Template dispatcher: one model in, one A4 sheet out. */
function renderTemplateBody(def, model) {
    if (def && def.id === "template-2") {
        return renderTemplate2(model);
    }
    if (def && def.id === "template-3") {
        return renderTemplate3(model);
    }
    return renderTemplate1(model);
}

function renderResumeTemplatePicker() {
    const host = document.getElementById("resumeTemplatePicker");
    if (!host) return;
    const current = resumeTemplateById(resumeState.template).id;
    host.innerHTML = RESUME_TEMPLATES.map(function (t) {
        const selected = t.id === current;
        return `<button type="button" class="tpl-card" data-template="${escapeHtml(t.id)}"
            aria-pressed="${selected ? "true" : "false"}"
            title="${escapeHtml(t.description)}"
            onclick="selectResumeTemplate('${escapeHtml(t.id)}')">
            <span class="tpl-thumb" data-template="${escapeHtml(t.id)}">
                <img src="/static/assets/templates/${escapeHtml(t.preview)}" alt="${escapeHtml(t.label)}" class="tpl-thumb-img">
            </span>
            <span class="tpl-card-body">
                <span class="tpl-card-label">${escapeHtml(t.label)}</span>
                <span class="tpl-card-tag">${escapeHtml(t.tagline)}</span>
            </span>
        </button>`;
    }).join("");
}

/* Presentation-only switch. Reads resumeState, writes no resume field, and
   calls no API, so the user's resume is untouched. */
function selectResumeTemplate(id) {
    if (RESUME_TEMPLATE_IDS.indexOf(id) === -1) return;
    resumeState.template = id;
    renderResumeTemplatePicker();
    renderResumePreview();
}

function renderResumePreview() {
    const body = document.getElementById("resumePreviewBody");
    if (!body) return;

    const def = resumeTemplateById(resumeState.template);
    body.dataset.template = def.id;
    body.innerHTML = renderTemplateBody(def, resumePreviewModel());
}

function closeResumePreview() {
    const modal = document.getElementById("resumePreviewModal");
    if (modal) modal.classList.add("hidden");
}

function printResumePreview() {
    window.print();
}

/* Build Resume: persist anything pending, reload, then show the A4 preview. */
async function buildResume() {
    const btn = document.getElementById("resumeBuildBtn");
    if (btn) resumeSetBusy("resumeBuildBtn", true, "Build Resume");
    try {
        // Flush any open editable row (education/experience/references/etc).
        // Only a section that is actively being edited needs a flush.
        const editing = Object.keys(resumeState.editing || {})
            .filter(k => resumeState.editing[k]);
        if (editing.length) await saveResumeEntry(editing[0]);

        // Persist any list sections the user edited but did not save.
        const dirty = Object.keys(resumeState.pendingItems || {})
            .filter(k => JSON.stringify(resumeState.pendingItems[k])
                !== JSON.stringify(resumeState[k] || []));
        for (const section of dirty) await saveResumeItemList(section);

        await loadResumeView(true);
        renderResumeTemplatePicker();
        renderResumePreview();
        const modal = document.getElementById("resumePreviewModal");
        if (modal) {
            modal.classList.remove("hidden");
            // Let the browser paint before the user interacts with the sheet.
            await new Promise(resolve => requestAnimationFrame(resolve));
        }
    } catch (err) {
        console.error("[CareerPulse] buildResume failed:", err);
        showToast(err.message || "Could not build the resume preview.");
    } finally {
        if (btn) resumeSetBusy("resumeBuildBtn", false, "Build Resume");
    }
}

function renderResumeImage(kind, displayUrl, storedPath) {
    if (kind === "signature") {
        const img = document.getElementById("resumeSignaturePreview");
        const empty = document.getElementById("resumeSignatureEmpty");
        const clearBtn = document.getElementById("resumeSignatureClearBtn");
        if (displayUrl) {
            img.src = displayUrl;
            img.classList.remove("hidden");
            if (empty) empty.classList.add("hidden");
            if (clearBtn) clearBtn.classList.remove("hidden");
        } else {
            img.classList.add("hidden");
            img.removeAttribute("src");
            if (empty) empty.classList.remove("hidden");
            if (clearBtn) clearBtn.classList.add("hidden");
        }
        return;
    }

    const img = document.getElementById("resumeProfilePic");
    const clearBtn = document.getElementById("resumeProfilePicClearBtn");
    const changeBtn = document.getElementById("resumeProfilePicChangeBtn");
    const uploadBtn = document.getElementById("resumeProfilePicBtn");
    const nameEl = document.getElementById("resumeProfilePicName");

    // An <img> with no src renders an empty box, so only trust a signed URL
    // that actually carries a value.
    const usable = String(displayUrl || "").trim();
    const stored = String(storedPath || "").trim();
    const statusEl = document.getElementById("resumeProfilePicStatus");
    // A photo is "owned by the resume" as soon as a stored path exists, even if
    // its signed URL has not arrived yet. State 2 must not fall back to the
    // CareerPulse avatar, or the user's own photo appears to vanish.
    const hasResumePhoto = !!(usable || stored);

    if (hasResumePhoto) {
        // Change / Remove are driven by resume state, never by input.files.
        if (clearBtn) clearBtn.classList.remove("hidden");
        if (changeBtn) changeBtn.classList.remove("hidden");
        if (uploadBtn) uploadBtn.classList.add("hidden");
        if (nameEl) {
            const label = resumeState.profilePictureName
                || resumePhotoNameFromPath(stored)
                || "";
            nameEl.textContent = label ? label : "Photo uploaded";
            nameEl.title = label;
            nameEl.classList.remove("hidden");
        }

        if (usable) {
            // Signed URLs carry a bearer token, so log the shape of the URL
            // rather than the URL itself.
            img.onload = function () {
                if (statusEl) statusEl.classList.add("hidden");
                img.classList.remove("hidden");
            };
            img.onerror = function (event) {
                // Once only: a dead signed URL must not loop.
                img.onerror = null;
                img.classList.add("hidden");
                console.warn("[CareerPulse] resume photo failed to load",
                    { status: img.naturalWidth + "x" + img.naturalHeight, event: event && event.type });
                if (statusEl) {
                    statusEl.textContent = "Photo could not be displayed. Use Change Photo to re-upload.";
                    statusEl.classList.remove("hidden");
                }
            };
            img.src = usable;
            img.classList.remove("hidden");
        } else {
            // State 2: stored photo, signed URL not ready. Keep the row and the
            // file name visible and say so, rather than showing the avatar.
            img.removeAttribute("src");
            img.onerror = null;
            img.classList.add("hidden");
            if (statusEl) {
                statusEl.textContent = "Photo is loading…";
                statusEl.classList.remove("hidden");
            }
        }
    } else {
        // State 3: no resume photo at all -> CareerPulse avatar or empty state.
        const fallback = resumeState.careerPulseAvatar || "";
        if (fallback) {
            img.src = fallback;
            img.classList.remove("hidden");
        } else {
            img.removeAttribute("src");
            img.onerror = null;
            img.classList.add("hidden");
        }
        if (clearBtn) clearBtn.classList.add("hidden");
        if (changeBtn) changeBtn.classList.add("hidden");
        if (uploadBtn) uploadBtn.classList.remove("hidden");
        if (nameEl) {
            nameEl.textContent = "";
            nameEl.classList.add("hidden");
        }
        if (statusEl) statusEl.classList.add("hidden");
    }
}

/* "Upload Picture" / "Change Photo" both open the one existing file picker.
   The input itself is hidden and carries the onchange that runs the upload. */
function resumeStartPhotoChange() {
    const input = document.getElementById("resumeProfilePicInput");
    if (input) input.click();
}

/* ---------- rendering ---------- */

function renderResumeAll() {
    RESUME_ENTRY_SECTIONS.forEach(renderResumeEntries);
    RESUME_ITEM_SECTIONS.forEach(renderResumeItemList);
    const totalEl = document.getElementById("resumeTotalExperience");
    if (totalEl) totalEl.textContent = resumeState.totalExperienceDisplay;
}

function toggleResumeSection(bodyId) {
    const el = document.getElementById(bodyId);
    if (el) el.classList.toggle("hidden");
}

function toggleResumeAdditional(forceOpen) {
    const fields = document.getElementById("resumeAdditionalFields");
    const label = document.getElementById("resumeAdditionalToggleLabel");
    if (!fields) return;
    const open = forceOpen === true ? true : (forceOpen === false ? false : fields.classList.contains("hidden"));
    fields.classList.toggle("hidden", !open);
    if (label) label.textContent = open ? "Hide Additional Details" : "Add Additional Details";
}

/* ---------- simple item lists (skills, hobbies, awards, activities, languages) ---------- */

function addResumeItem(section) {
    const inputId = `resume${section.charAt(0).toUpperCase() + section.slice(1)}Input`;
    const value = resumeGetValue(inputId);
    if (!value) {
        showToast("Enter a value first.");
        return;
    }
    const list = resumeState.pendingItems[section] || (resumeState.pendingItems[section] = []);
    if (list.some(item => String(item).toLowerCase() === value.toLowerCase())) {
        showToast(`"${value}" is already in the list.`);
        return;
    }
    list.push(value);
    resumeSetValue(inputId, "");
    renderResumeItemList(section);
}

function removeResumeItem(section, index) {
    const list = resumeState.pendingItems[section];
    if (!Array.isArray(list)) return;
    list.splice(index, 1);
    renderResumeItemList(section);
}

function renderResumeItemList(section) {
    const container = document.getElementById(`resume${section.charAt(0).toUpperCase() + section.slice(1)}List`);
    if (!container) return;
    const list = resumeState.pendingItems[section] || [];

    if (!list.length) {
        container.innerHTML = `<p class="text-[10px] text-slate-500 italic w-full">Nothing added yet.</p>`;
        return;
    }

    container.innerHTML = list.map((item, index) => `
        <span class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-[11px] text-slate-200">
            <span>${escapeHtml(item)}</span>
            <button type="button" onclick="removeResumeItem('${section}', ${index})" title="Remove"
                class="text-slate-400 hover:text-rose-300 transition font-bold">&times;</button>
        </span>
    `).join("");
}

async function saveResumeItemList(section) {
    const btnId = `resume${section.charAt(0).toUpperCase() + section.slice(1)}SaveBtn`;
    const values = resumeState.pendingItems[section] || [];
    resumeSetBusy(btnId, true);
    try {
        const data = await resumeRequest(`/api/resume/items/${section}`, {
            method: "POST",
            body: JSON.stringify({ values })
        });
        resumeState[section] = data.values || values;
        resumeState.pendingItems[section] = resumeState[section].slice();
        renderResumeItemList(section);
        showToast(data.message || `${resumeSectionTitle(section)} saved`);
    } catch (err) {
        showToast(err.message || "Could not save.");
    } finally {
        resumeSetBusy(btnId, false);
    }
}

/* ---------- repeatable entry sections ---------- */

function openResumeEntryForm(section, id) {
    const schema = RESUME_ENTRY_SCHEMAS[section];
    const form = document.getElementById(`resume${section.charAt(0).toUpperCase() + section.slice(1)}Form`);
    if (!schema || !form) return;

    resumeState.editing[section] = id ? resumeSafeId(id) : "";
    const record = id ? (resumeState[section] || []).find(row => row.id === id) : null;

    form.innerHTML = `
        <h4 class="text-sm font-bold text-white">${record ? `Edit ${escapeHtml(schema.title)}` : `Add ${escapeHtml(schema.title)}`}</h4>
        ${schema.fields.map(field => resumeRenderField(section, field, record)).join("")}
        ${schema.ongoing ? `
        <label class="flex items-center gap-2 text-xs font-semibold text-slate-300">
            <input type="checkbox" id="resumeOngoing_${section}" data-resume-ongoing
                onchange="toggleResumeOngoing('${section}', this.checked)"
                class="w-4 h-4 rounded border-slate-700 bg-slate-950 accent-indigo-600"
                ${record && record[schema.ongoing.name] ? "checked" : ""}>
            ${escapeHtml(schema.ongoing.label)}
        </label>` : ""}
        <div class="flex flex-wrap gap-2">
            <button type="button" id="resumeEntrySave_${section}"
                onclick="saveResumeEntry('${section}')"
                class="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition shadow-lg shadow-indigo-600/20">Save</button>
            <button type="button" onclick="closeResumeEntryForm('${section}')"
                class="px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition">Cancel</button>
        </div>
    `;

    form.classList.remove("hidden");
    if (schema.ongoing) {
        const box = document.getElementById(`resumeOngoing_${section}`);
        if (box) toggleResumeOngoing(section, box.checked);
    }
    form.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function resumeRenderField(section, field, record) {
    const value = record ? (record[field.name] || "") : "";
    const required = field.required ? "required" : "";
    const id = `resumeField_${section}_${field.name}`;

    let control;
    if (field.type === "textarea") {
        control = `<textarea id="${id}" data-resume-field="${field.name}" rows="3" ${required}
            class="${resumeInputClass()} leading-relaxed">${escapeHtml(value)}</textarea>`;
    } else if (field.type === "select") {
        control = `<select id="${id}" data-resume-field="${field.name}" ${required} class="${resumeInputClass()}">
            <option value="">Select employment type</option>
            ${field.options.map(option => `<option value="${escapeHtml(option)}" ${value === option ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
        </select>`;
    } else if (field.type === "date") {
        // A text input with a DD/MM/YYYY mask, so the browser's locale-dependent
        // MM/DD/YYYY picker never appears. Stored values stay ISO.
        control = `<input type="text" inputmode="numeric" autocomplete="off" id="${id}"
            data-resume-field="${field.name}" data-resume-date="1" placeholder="DD/MM/YYYY"
            maxlength="10" value="${escapeHtml(resumeIsoToDisplay(value))}"
            class="${resumeInputClass()}">`;
    } else {
        control = `<input type="${field.type}" id="${id}" data-resume-field="${field.name}" ${required}
            value="${escapeHtml(value)}" class="${resumeInputClass()}">`;
    }

    return `
        <div data-resume-wrap="${field.name}" class="space-y-1.5">
            <label class="text-xs font-semibold text-slate-300 block">${escapeHtml(field.label)}</label>
            ${control}
        </div>`;
}

function toggleResumeOngoing(section, checked) {
    const form = document.getElementById(`resume${section.charAt(0).toUpperCase() + section.slice(1)}Form`);
    if (!form) return;
    const endWrap = form.querySelector('[data-resume-wrap="end_date"]');
    if (endWrap) endWrap.classList.toggle("hidden", !!checked);
}

function closeResumeEntryForm(section) {
    const form = document.getElementById(`resume${section.charAt(0).toUpperCase() + section.slice(1)}Form`);
    if (form) {
        form.classList.add("hidden");
        form.innerHTML = "";
    }
    resumeState.editing[section] = "";
}

async function saveResumeEntry(section) {
    const schema = RESUME_ENTRY_SCHEMAS[section];
    if (!schema) return;

    const form = document.getElementById(`resume${section.charAt(0).toUpperCase() + section.slice(1)}Form`);
    const payload = { id: resumeState.editing[section] || "" };

    let invalidDate = false;
    schema.fields.forEach(field => {
        const el = form ? form.querySelector(`[data-resume-field="${field.name}"]`) : null;
        let value = el ? el.value.trim() : "";
        if (field.type === "checkbox") value = !!el.checked;
        if (field.type === "date") {
            // DD/MM/YYYY in the UI, ISO on the wire.
            const iso = resumeDisplayToIso(value);
            if (iso === null) {
                showToast(`${field.label.replace(/\s*\*$/, "")} must be a real date in DD/MM/YYYY format.`);
                invalidDate = true;
                return;
            }
            value = iso;
        }
        payload[field.name] = value;
    });
    if (invalidDate) return;

    if (schema.ongoing) {
        const box = document.getElementById(`resumeOngoing_${section}`);
        payload[schema.ongoing.name] = box ? !!box.checked : false;
        // An ongoing entry has no end date; the server drops it regardless.
        if (payload[schema.ongoing.name]) payload.end_date = "";
    }

    resumeSetBusy(`resumeEntrySave_${section}`, true);
    try {
        const endpoint = (section === "education" || section === "experience" || section === "references")
            ? `/api/resume/${section}`
            : `/api/resume/link-item/${section}`;
        const data = await resumeRequest(endpoint, { method: "POST", body: JSON.stringify(payload) });

        closeResumeEntryForm(section);
        await loadResumeView(true);

        if (section === "experience" && data.total_experience_display) {
            resumeState.totalExperienceDisplay = data.total_experience_display;
            const totalEl = document.getElementById("resumeTotalExperience");
            if (totalEl) totalEl.textContent = data.total_experience_display;
        }
        showToast(data.message || "Saved");
    } catch (err) {
        showToast(err.message || "Could not save.");
    } finally {
        resumeSetBusy(`resumeEntrySave_${section}`, false);
    }
}

async function deleteResumeEntry(section, id) {
    if (!confirm("Delete this entry? This cannot be undone.")) return;
    try {
        const data = await resumeRequest(`/api/resume/child/${section}/${resumeSafeId(id)}`, { method: "DELETE" });
        await loadResumeView(true);
        if (section === "experience" && data.total_experience_display) {
            resumeState.totalExperienceDisplay = data.total_experience_display;
            const totalEl = document.getElementById("resumeTotalExperience");
            if (totalEl) totalEl.textContent = data.total_experience_display;
        }
        showToast(data.message || "Entry deleted");
    } catch (err) {
        showToast(err.message || "Could not delete.");
    }
}

function renderResumeEntries(section) {
    const container = document.getElementById(`resume${section.charAt(0).toUpperCase() + section.slice(1)}List`);
    if (!container) return;
    const rows = resumeState[section] || [];

    if (!rows.length) {
        container.innerHTML = `<p class="text-xs text-slate-500 italic">No ${escapeHtml(section)} added yet.</p>`;
        return;
    }

    container.innerHTML = rows.map((row, index) => {
        const safeId = resumeSafeId(row.id);
        let heading = "";
        let sub = "";

        if (section === "education") {
            heading = row.course_degree || "Course";
            sub = [row.school_university, row.grade_score].filter(Boolean).join(" · ");
        } else if (section === "experience") {
            heading = row.job_title || "Role";
            sub = [row.company_name, row.employment_type].filter(Boolean).join(" · ");
        } else if (section === "references") {
            heading = row.referee_name || "Referee";
            sub = [row.job_title, row.company_name, row.email, row.phone].filter(Boolean).join(" · ");
        } else {
            heading = row.title || "Title";
            sub = row.link || "";
        }

        const ongoingFlag = section === "education" ? "currently_doing" : "currently_work_here";
        const from = resumeFormatMonth(row.start_date);
        const to = row[ongoingFlag] ? "Present" : resumeFormatMonth(row.end_date);
        const period = [from, to].filter(Boolean).join(" — ");
        const details = (row.details || "").trim();

        return `
        <div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-2">
            <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                    <p class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">${escapeHtml(resumeSectionTitle(section))} ${index + 1}</p>
                    <h5 class="text-sm font-bold text-white">${escapeHtml(heading)}</h5>
                    ${sub ? `<p class="text-xs text-indigo-400">${escapeHtml(sub)}</p>` : ""}
                    ${period ? `<p class="text-[11px] text-slate-400">${escapeHtml(period)}</p>` : ""}
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <button type="button" onclick="openResumeEntryForm('${section}', '${safeId}')"
                        class="px-3.5 py-1.5 rounded-lg bg-slate-800 text-slate-300 text-xs font-bold hover:bg-slate-700 transition">Edit</button>
                    <button type="button" onclick="deleteResumeEntry('${section}', '${safeId}')"
                        class="px-3.5 py-1.5 rounded-lg bg-rose-600/20 text-rose-300 border border-rose-500/30 text-xs font-bold hover:bg-rose-600 hover:text-white transition">Delete</button>
                </div>
            </div>
            ${details ? `<p class="text-[11px] text-slate-400 leading-relaxed">${escapeHtml(details)}</p>` : ""}
        </div>`;
    }).join("");
}

/* ---------- fixed sections ---------- */

async function saveResumePersonalDetails() {
    // The DOB field shows DD/MM/YYYY; the API stores ISO YYYY-MM-DD.
    const dobIso = resumeDisplayToIso(resumeGetValue("resumeDob"));
    if (dobIso === null) {
        showToast("Date of Birth must be a real date in DD/MM/YYYY format.");
        return;
    }

    const payload = {
        profile_picture_url: resumeState.profile.profile_picture_url || "",
        name: resumeGetValue("resumeName"),
        email: resumeGetValue("resumeEmail"),
        date_of_birth: dobIso,
        gender: resumeGetValue("resumeGender"),
        linkedin_url: resumeGetValue("resumeLinkedin"),
        github_url: resumeGetValue("resumeGithub"),
        website_url: resumeGetValue("resumeWebsite"),
        address: resumeGetValue("resumeAddress"),
        pincode: resumeGetValue("resumePincode"),
        city: resumeGetValue("resumeCity"),
        state: resumeGetValue("resumeState"),
        country: resumeGetValue("resumeCountry")
    };

    resumeSetBusy("resumePersonalSaveBtn", true, "Save Personal Details");
    try {
        const data = await resumeRequest("/api/resume/personal-details", {
            method: "POST",
            body: JSON.stringify(payload)
        });
        // Keep the stored path so a later save does not wipe the uploaded image.
        resumeState.profile = { ...resumeState.profile, ...payload };
        showToast(data.message || "Personal details saved");
    } catch (err) {
        showToast(err.message || "Could not save personal details.");
    } finally {
        resumeSetBusy("resumePersonalSaveBtn", false, "Save Personal Details");
    }
}

async function saveResumeHeadline() {
    resumeSetBusy("resumeHeadlineSaveBtn", true, "Save Headline");
    try {
        const data = await resumeRequest("/api/resume/headline", {
            method: "POST",
            body: JSON.stringify({ headline: resumeGetValue("resumeHeadline") })
        });
        resumeState.profile.headline = resumeGetValue("resumeHeadline");
        showToast(data.message || "Headline saved");
    } catch (err) {
        showToast(err.message || "Could not save headline.");
    } finally {
        resumeSetBusy("resumeHeadlineSaveBtn", false, "Save Headline");
    }
}

async function saveResumeTextField(field) {
    const map = {
        summary: { input: "resumeSummary", btn: "resumeSummarySaveBtn", label: "Save Summary" },
        additional_information: { input: "resumeAdditionalInfo", btn: "resumeAdditionalInfoSaveBtn", label: "Save Additional Information" }
    };
    const conf = map[field];
    if (!conf) return;

    resumeSetBusy(conf.btn, true, conf.label);
    try {
        const data = await resumeRequest(`/api/resume/text/${field}`, {
            method: "POST",
            body: JSON.stringify({ value: resumeGetValue(conf.input) })
        });
        resumeState.profile[field] = resumeGetValue(conf.input);
        showToast(data.message || "Saved");
    } catch (err) {
        showToast(err.message || "Could not save.");
    } finally {
        resumeSetBusy(conf.btn, false, conf.label);
    }
}

/* ---------- image upload ---------- */

async function uploadResumeImage(kind) {
    const inputId = kind === "signature" ? "resumeSignatureInput" : "resumeProfilePicInput";
    const input = document.getElementById(inputId);
    if (!input || !input.files || !input.files[0]) {
        showToast("Choose an image first.");
        return;
    }

    const file = input.files[0];
    if (file.size > 2 * 1024 * 1024) {
        showToast("Image must be 2MB or smaller.");
        input.value = "";
        return;
    }
    if (!/^image\/(jpeg|png)$/i.test(file.type)) {
        showToast("Only JPG and PNG images are accepted.");
        input.value = "";
        return;
    }

    // Send busy state to whichever control is actually on screen: the Upload
    // button is hidden once a photo exists, and Change Photo replaces it.
    const changeBtnEl = document.getElementById("resumeProfilePicChangeBtn");
    const busyOnChange = !!(changeBtnEl && !changeBtnEl.classList.contains("hidden"));
    const btnId = kind === "signature"
        ? "resumeSignatureBtn"
        : (busyOnChange ? "resumeProfilePicChangeBtn" : "resumeProfilePicBtn");
    const busyLabel = kind === "signature"
        ? "Upload Signature"
        : (busyOnChange ? "Change Photo" : "Upload Picture");

    resumeSetBusy(btnId, true, busyLabel);
    try {
        const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error("Could not read the image."));
            reader.readAsDataURL(file);
        });

        const data = await resumeRequest("/api/resume/asset", {
            method: "POST",
            body: JSON.stringify({ kind, data_url: dataUrl })
        });

        // Store the path (round-trippable), render the signed URL. The
        // *_display values are kept in step as well, because the A4 preview
        // reads them and would otherwise keep showing the previous image.
        if (kind === "signature") {
            resumeState.profile.signature_url = data.path;
            resumeState.profile.signature_display = data.display_url || null;
            renderResumeImage("signature", data.display_url, data.path);
        } else {
            resumeState.profile.profile_picture_url = data.path;
            resumeState.profile.profile_picture_display = data.display_url || null;
            // Keep the name so the uploaded file stays identifiable after a
            // reload, when the stored path alone is all we have.
            resumeState.profilePictureName = file.name;
            resumeState.careerPulseAvatar = "";
            renderResumeImage("profile-picture", data.display_url, data.path);
        }
        showToast(data.message || "Image uploaded");
    } catch (err) {
        showToast(err.message || "Upload failed.");
    } finally {
        // Clearing the input only resets the picker; the visible state is owned
        // by resumeState, so this cannot make the photo disappear.
        input.value = "";
        resumeSetBusy(btnId, false, busyLabel);
    }
}

async function clearResumeImage(kind) {
    if (!confirm("Remove this image?")) return;
    try {
        await resumeRequest("/api/resume/asset/clear", {
            method: "POST",
            body: JSON.stringify({ kind })
        });
        if (kind === "signature") {
            resumeState.profile.signature_url = null;
            // Clear the signed URL too, otherwise the A4 preview keeps
            // rendering the signature that was just removed.
            resumeState.profile.signature_display = null;
            renderResumeImage("signature", null, null);
        } else {
            resumeState.profile.profile_picture_url = null;
            resumeState.profile.profile_picture_display = null;
            resumeState.profilePictureName = "";
            // Clear the file input so the same file can be re-selected later.
            const input = document.getElementById("resumeProfilePicInput");
            if (input) input.value = "";
            // Reveals the CareerPulse profile picture as the fallback.
            renderResumeImage("profile-picture", null, null);
        }
        showToast("Image removed");
    } catch (err) {
        showToast(err.message || "Could not remove image.");
    }
}

/* Cleared on logout so one user's resume is never rendered for the next. */
function resetResumeState() {
    resumeState.loaded = false;
    resumeState.loading = false;
    resumeState.inFlight = false;
    resumeState.error = null;
    resumeState.profile = {};
    RESUME_ENTRY_SECTIONS.forEach(section => { resumeState[section] = []; });
    RESUME_ITEM_SECTIONS.forEach(section => {
        resumeState[section] = [];
        resumeState.pendingItems[section] = [];
    });
    resumeState.totalExperienceDisplay = "0 years 0 months";
    resumeState.profilePictureName = "";
    resumeState.careerPulseAvatar = "";
    resumeState.editing = {};
}

// Session Token Retrieval & Refresh Helper
async function getAuthHeader() {
    if (window.supabaseClient) {
        const { data: { session }, error } = await window.supabaseClient.auth.getSession();
        if (error || !session) {
            return {};
        }

        const now = Math.floor(Date.now() / 1000);
        if (session.expires_at - now < 60) {
            const { data: refreshedSession } = await window.supabaseClient.auth.refreshSession();
            if (refreshedSession?.session) {
                return { "Authorization": `Bearer ${refreshedSession.session.access_token}` };
            }
        }
        return { "Authorization": `Bearer ${session.access_token}` };
    }
    return {};
}

function handleExpiredToken() {
    showToast("Session expired. Redirecting to login...");
    setTimeout(() => {
        handleLogout();
    }, 1200);
}

async function checkBackendHealth() {
    try {
        const res = await fetch("/api/health");
        if (res.ok) {
            const dot = document.getElementById("statusDot");
            const txt = document.getElementById("statusText");
            if (dot) dot.className = "w-2.5 h-2.5 rounded-full bg-emerald-400";
            if (txt) txt.textContent = "Backend Active";
        }
    } catch (e) {
        console.warn("Backend health check unreachable");
    }
}

/**
 * Sequential auth initialization:
 * 1. Fetch /api/user/profile (uses httponly cookie automatically)
 * 2. If authenticated → show UI, navigate to correct view
 * 3. If not authenticated → show login button
 */
/**
 * Splits the location hash into the view name and its parameters.
 * The mailbox OAuth redirect uses "#applications&mailbox=connected".
 */
function parseHashLocation() {
    const raw = (window.location.hash || "").replace(/^#/, "");
    const [view, ...rest] = raw.split("&");
    const params = {};
    rest.forEach(pair => {
        const [key, value] = pair.split("=");
        if (key) params[key] = value === undefined ? "" : decodeURIComponent(value);
    });
    return { view: (view || "").trim(), params };
}

async function initializeAuth() {
    try {
        const res = await fetch("/api/user/profile", {
            credentials: "same-origin"
        });

        if (!res.ok) {
            redirectUnauthenticatedToLanding();
            return;
        }

        const data = await res.json();
        authState.loading = false;

        if (data.authenticated && data.profile) {
            authState.authenticated = true;
            authState.user = data.profile;
            showAuthenticatedUI(data.profile);

            // After profile loads, also fetch saved jobs
            fetchSupabaseSavedJobs();

            // Mailbox check on open. The server throttles this to a sensible
            // interval, so this is not a tight poll loop.
            bootstrapReplies();

            // Navigate based on URL hash or profile completeness
            const location_ = parseHashLocation();
            const hash = location_.view;
            if (location_.params.mailbox) {
                handleMailboxRedirectResult(location_.params.mailbox);
            }
            if (hash === "profile" && !data.profile_complete) {
                navigateTo("profile");
            } else if (hash && APP_VIEW_HASHES.includes(hash)) {
                navigateTo(hash);
            } else {
                navigateTo("dashboard");
            }
        } else {
            authState.authenticated = false;
            // The dashboard app is an authenticated surface only.
            redirectUnauthenticatedToLanding();
        }
    } catch (e) {
        console.error("Auth initialization error:", e);
        authState.loading = false;
        redirectUnauthenticatedToLanding();
    }
}

/**
 * Sends any unauthenticated visitor of /app back to the public landing page.
 * Uses replace() so browser Back never re-exposes the authenticated shell.
 */
function redirectUnauthenticatedToLanding() {
    if (window.location.pathname === "/") return;
    window.location.replace("/");
}

async function fetchUserProfile() {
    // Reload profile data and update UI without full re-auth
    try {
        const res = await fetch("/api/user/profile", {
            credentials: "same-origin"
        });

        if (res.status === 401) {
            handleExpiredToken();
            return;
        }

        if (res.ok) {
            const data = await res.json();
            if (data.authenticated && data.profile) {
                authState.user = data.profile;
                showAuthenticatedUI(data.profile);
                return;
            }
        }
    } catch (e) {
        console.error("Profile reload error:", e);
    }
}

function showUnauthenticatedUI() {
    const unauthEl = document.getElementById("unauthContainer");
    const authEl = document.getElementById("authContainer");
    if (unauthEl) unauthEl.classList.remove("hidden");
    if (authEl) authEl.classList.add("hidden");
}

async function saveProfileChanges(event) {
    event.preventDefault();

    const projectsInput = document.getElementById("profProjects").value;
    const skillsInput = document.getElementById("profSkills").value;
    const interestsInput = document.getElementById("profInterests")?.value || "";
    const rolesInput = document.getElementById("profPreferredRoles")?.value || "";
    const domainsInput = document.getElementById("profPreferredDomains")?.value || "";
    const experienceInput = document.getElementById("profExperienceLevel")?.value || "";

    const splitList = (v) => v ? v.split(",").map(s => s.trim()).filter(Boolean) : [];

    const payload = {
        headline: document.getElementById("profHeadline").value.trim(),
        degree: document.getElementById("profDegree").value.trim(),
        college: document.getElementById("profCollege").value.trim(),
        year: document.getElementById("profYear").value.trim(),
        location: document.getElementById("profLocation").value.trim(),
        github: document.getElementById("profGithub").value.trim(),
        linkedin: document.getElementById("profLinkedin").value.trim(),
        projects: splitList(projectsInput),
        skills: splitList(skillsInput),
        interests: splitList(interestsInput),
        preferred_roles: splitList(rolesInput),
        preferred_domains: splitList(domainsInput),
        experience_level: experienceInput
    };

    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/user/profile/update", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders
            },
            body: JSON.stringify(payload),
            credentials: "same-origin"
        });

        if (res.status === 401) {
            handleExpiredToken();
            return;
        }

        const data = await res.json();
        if (res.ok && data.status === "success") {
            showToast("Profile settings saved directly to Supabase!");
            // Reload profile and then redirect existing users to dashboard
            await fetchUserProfile();
            // A profile edit changes what the Tech News feed is personalized
            // for, so the cached feed must be discarded locally and refetched.
            resetPersonalizedNews();
            // After first-time profile save, navigate to dashboard
            setTimeout(() => navigateTo("dashboard"), 400);
        } else {
            showToast(data.detail || "Error saving profile settings.");
        }
    } catch (e) {
        console.error("Profile save error:", e);
        showToast("Error connecting to server.");
    }
}

async function fetchSupabaseSavedJobs() {
    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/user/opportunities/saved", {
            headers: { ...authHeaders },
            credentials: "same-origin"
        });

        if (res.ok) {
            const data = await res.json();
            savedInterestedJobs = data.saved || [];
            waitlistedJobs = data.waitlisted || [];
            updateStats();
            renderInterestedList();
            renderWaitlistedList();
        }
    } catch (e) {
        console.warn("Could not load saved opportunities from Supabase");
    }
}

async function persistOpportunityState(job, status) {
    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/user/opportunities/save", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders
            },
            body: JSON.stringify({ ...job, status: status }),
            credentials: "same-origin"
        });

        if (res.status === 401) {
            handleExpiredToken();
            return false;
        }

        // The save route answers HTTP 200 with {"status":"error"} when the
        // write fails, so res.ok on its own would report a false success and
        // the card would look saved even though nothing was persisted.
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status === "error") {
            console.error("Failed to save opportunity:",
                data.detail || ("HTTP " + res.status));
            showToast("Could not save this opportunity. Please try again.");
            return false;
        }
        return true;
    } catch (e) {
        console.error("Failed to sync state with Supabase:", e);
        showToast("Could not save this opportunity. Please try again.");
        return false;
    }
}

/**
 * Safely set an avatar image element.
 * - If url is a valid http/https URL, set src and attach onerror fallback.
 * - If url is empty/invalid, immediately show the fallback SVG.
 * - onerror fires at most once per element (flag guard prevents retry loops).
 */
function setAvatarImage(el, url) {
    if (!el) return;
    el._avatarErrorFired = false; // reset guard
    if (url && (url.startsWith("http://") || url.startsWith("https://"))) {
        el.src = url;
        el.onerror = function () {
            if (!this._avatarErrorFired) {
                this._avatarErrorFired = true;
                this.onerror = null; // prevent further retries
                this.src = USER_AVATAR_SVG;
            }
        };
    } else {
        el.src = USER_AVATAR_SVG;
        el.onerror = null;
    }
}

function showAuthenticatedUI(user) {
    const unauth = document.getElementById("unauthContainer");
    const auth = document.getElementById("authContainer");

    if (unauth) unauth.classList.add("hidden");
    if (auth) auth.classList.remove("hidden");

    const name = user.name || "Jeshurun Selvakumar";
    const email = user.email || "";
    const profilePicUrl = user.profile_picture || "";
    // Remember it so the Resume Builder can fall back to it when the user has
    // no resume photo. Stored only in state, never copied into the resume row.
    if (typeof resumeState !== "undefined") resumeState.careerPulseAvatar = profilePicUrl;
    const headline = user.headline || "Computer Engineering Student | SIES GST";
    const location = user.location || "Mumbai / Navi Mumbai";
    const skillsArr = user.skills || ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"];

    if (document.getElementById("sidebarName")) document.getElementById("sidebarName").textContent = name;
    setAvatarImage(document.getElementById("sidebarAvatar"), profilePicUrl);

    if (document.getElementById("headerName")) document.getElementById("headerName").textContent = name.split(" ")[0];
    setAvatarImage(document.getElementById("headerAvatar"), profilePicUrl);

    const hour = new Date().getHours();
    const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    if (document.getElementById("welcomeHeading")) document.getElementById("welcomeHeading").textContent = `${greeting}, ${name.split(" ")[0]} 👋`;
    if (document.getElementById("cardProfileName")) document.getElementById("cardProfileName").textContent = name;
    if (document.getElementById("cardProfileHeadline")) document.getElementById("cardProfileHeadline").textContent = headline;
    if (document.getElementById("cardProfileLocation")) document.getElementById("cardProfileLocation").textContent = location;
    setAvatarImage(document.getElementById("cardProfileImg"), profilePicUrl);

    if (document.getElementById("profilePageName")) document.getElementById("profilePageName").textContent = name;
    if (document.getElementById("profilePageEmail")) document.getElementById("profilePageEmail").textContent = email;
    setAvatarImage(document.getElementById("profilePageAvatar"), profilePicUrl);

    if (document.getElementById("profHeadline")) document.getElementById("profHeadline").value = headline;
    if (document.getElementById("profDegree")) document.getElementById("profDegree").value = user.degree || "B.E. Computer Engineering";
    if (document.getElementById("profCollege")) document.getElementById("profCollege").value = user.college || "SIES Graduate School of Technology";
    if (document.getElementById("profYear")) document.getElementById("profYear").value = user.year || "Final Year (Semester 7)";
    if (document.getElementById("profLocation")) document.getElementById("profLocation").value = location;
    if (document.getElementById("profGithub")) document.getElementById("profGithub").value = user.github || "";
    if (document.getElementById("profLinkedin")) document.getElementById("profLinkedin").value = user.linkedin || "";
    if (document.getElementById("profProjects")) document.getElementById("profProjects").value = Array.isArray(user.projects) ? user.projects.join(", ") : (user.projects || "");
    if (document.getElementById("profSkills")) document.getElementById("profSkills").value = Array.isArray(skillsArr) ? skillsArr.join(", ") : skillsArr;
    if (document.getElementById("profInterests")) document.getElementById("profInterests").value = Array.isArray(user.interests) ? user.interests.join(", ") : (user.interests || "");
    if (document.getElementById("profPreferredRoles")) document.getElementById("profPreferredRoles").value = Array.isArray(user.preferred_roles) ? user.preferred_roles.join(", ") : (user.preferred_roles || "");
    if (document.getElementById("profPreferredDomains")) document.getElementById("profPreferredDomains").value = Array.isArray(user.preferred_domains) ? user.preferred_domains.join(", ") : (user.preferred_domains || "");
    if (document.getElementById("profExperienceLevel")) document.getElementById("profExperienceLevel").value = user.experience_level || "";

    renderSkillsBadge(Array.isArray(skillsArr) ? skillsArr : ["Python", "FastAPI"]);
}

function clearUserState() {
    // Clear all user-specific state so previous user's data never leaks
    mockOpportunities = [];
    savedInterestedJobs = [];
    waitlistedJobs = [];
    currentSwipeIndex = 0;
    currentEmailPayload = null;
    currentTechNews = [];
    // Application communication is per-user: never let one account see
    // another account's sent emails or company replies.
    resetApplicationsState();
    // Personalized news is per-user: never let one account see another's feed.
    resetPersonalizedNews();
    // Resume data is per-user too: never leave one account's resume in the DOM.
    resetResumeState();
    authState.authenticated = false;
    authState.user = null;

    // Reset avatar images to fallback immediately
    ["sidebarAvatar", "headerAvatar", "cardProfileImg", "profilePageAvatar"].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.src = USER_AVATAR_SVG; el.onerror = null; }
    });

    // Clear job lists UI
    const intEl = document.getElementById("interestedJobsContainer");
    const wlEl = document.getElementById("waitlistedJobsContainer");
    if (intEl) intEl.innerHTML = "";
    if (wlEl) wlEl.innerHTML = "";
    updateStats();
}

/**
 * Clears every browser-side cache that could hold user-specific state
 * (saved jobs, discovered opportunities, stored auth artifacts).
 */
function clearClientCaches() {
    try {
        window.localStorage.clear();
    } catch (e) { /* storage may be unavailable */ }
    try {
        window.sessionStorage.clear();
    } catch (e) { /* storage may be unavailable */ }
    try {
        if (typeof caches !== "undefined" && caches.keys) {
            caches.keys().then(keys => keys.forEach(k => caches.delete(k))).catch(() => { });
        }
    } catch (e) { /* caches API unsupported */ }
}

async function handleLogout() {
    // 1. Best-effort in-app Supabase sign-out
    try {
        if (window.supabaseClient) {
            await window.supabaseClient.auth.signOut();
        }
    } catch (e) {
        console.error("Supabase sign-out error:", e);
    }

    // 2. Invalidate the server session and clear the auth cookie
    try {
        await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    } catch (e) {
        console.error("Logout error:", e);
    }

    // 3. Clear all frontend auth state and cached user data
    clearUserState();
    clearClientCaches();

    // 4. Replace the history entry so Back cannot return to the authenticated UI
    window.location.replace("/");
}

function navigateTo(view) {
    document.querySelectorAll(".page-view").forEach(el => el.classList.add("hidden"));
    const active = document.getElementById(`view-${view}`);
    if (active) active.classList.remove("hidden");

    document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("bg-indigo-600", "text-white"));
    const navBtn = document.getElementById(`nav-${view}`);
    if (navBtn) navBtn.classList.add("bg-indigo-600", "text-white");

    // Load personalized news the first time the tab is opened only.
    if (view === "news") loadPersonalizedNews();
    if (view === "applications") loadApplicationsView();
    if (view === "resume") loadResumeView();
}

/* ------------------------------------------------------------------
 * APPLICATION COMMUNICATION & COMPANY REPLIES
 *
 * Replies come from the user's own connected mailbox (read-only Gmail
 * grant). CareerPulse only ever shows messages it could confidently link to
 * an outreach email it sent, and it never replies to a company on its own.
 * ------------------------------------------------------------------ */

function resetApplicationsState() {
    applicationsState.loaded = false;
    applicationsState.loading = false;
    applicationsState.integration = null;
    applicationsState.integrationError = null;
    applicationsState.applications = [];
    applicationsState.byOpportunity = {};
    applicationsState.unread = 0;
    applicationsState.lastSyncAt = null;
    applicationsState.syncNotice = null;
    currentConversationId = null;

    ["applicationsContainer", "mailboxIntegrationCard", "repliesNotification"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = "";
    });
    const badge = document.getElementById("applicationsUnreadBadge");
    if (badge) {
        badge.classList.add("hidden");
        badge.textContent = "0";
    }
    const modal = document.getElementById("conversationModal");
    if (modal) modal.classList.add("hidden");
}

async function handleMailboxRedirectResult(result) {
    const messages = {
        connected: "Gmail connected. Company replies will appear here.",
        denied: "Gmail access was not granted. You can connect it any time.",
        state_mismatch: "That Gmail authorization request expired. Please try connecting again.",
        missing_code: "Gmail did not return an authorization code. Please try again.",
        not_configured: "Gmail integration is not configured on this server yet.",
        email_unresolved: "Google authorized Gmail, but your Gmail address could not be verified. Please reconnect to finish setup.",
        auth_error: "Gmail could not be authorized. Please try again.",
        provider_error: "Gmail could not be reached. Please try again shortly."
    };

    // The redirect hash is not proof of a working connection. Confirm against the
    // server before reporting success, so an unresolved identity is never shown
    // as "Gmail connected".
    if (result === "connected") {
        await loadMailboxStatus();
        const integration = applicationsState.integration;
        if (!integration || !integration.connected || !integration.email) {
            result = "email_unresolved";
        }
    }

    applicationsState.syncNotice = {
        tone: result === "connected" ? "success" : "warning",
        text: messages[result] || "The mailbox connection could not be completed."
    };
    renderReplyNotification();
}

function setSyncBusy(busy) {
    const btn = document.getElementById("btnSyncReplies");
    const label = document.getElementById("syncRepliesLabel");
    const icon = document.getElementById("syncRepliesIcon");
    if (btn) btn.disabled = busy;
    if (label) label.textContent = busy ? "Checking…" : "Sync Replies";
    if (icon) icon.style.opacity = busy ? "0.4" : "1";
}

function formatTimestamp(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (isNaN(parsed.getTime())) return "—";
    return parsed.toLocaleString([], {
        day: "numeric", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit"
    });
}

function applicationStatusTone(status) {
    const map = {
        "Reply Received": "indigo",
        "Interview Requested": "emerald",
        "Interview Scheduled": "emerald",
        "Additional Information Requested": "amber",
        "Application Received": "sky",
        "Rejected": "rose",
        "Email Sent": "slate"
    };
    return map[status] || "slate";
}

function updateUnreadBadge() {
    const badge = document.getElementById("applicationsUnreadBadge");
    if (!badge) return;
    const count = applicationsState.unread || 0;
    badge.textContent = count > 99 ? "99+" : String(count);
    if (count > 0) badge.classList.remove("hidden");
    else badge.classList.add("hidden");
}

function renderMailboxIntegration() {
    const card = document.getElementById("mailboxIntegrationCard");
    if (!card) return;

    const integration = applicationsState.integration;
    if (!integration) {
        card.innerHTML = applicationsState.integrationError
            ? `<p class="text-xs text-rose-300">${escapeHtml(applicationsState.integrationError)}</p>`
            : `<p class="text-xs text-slate-400">Checking your mailbox connection…</p>`;
        return;
    }

    const scopeNote = "Read-only access to receive replies. CareerPulse cannot send, delete, or modify mail through this connection.";

    if (!integration.configured) {
        card.innerHTML = `
            <div class="space-y-2">
                <h3 class="text-sm font-bold text-white">Email Integration</h3>
                <p class="text-xs text-slate-400">Gmail integration is not configured on this server yet, so replies
                    cannot be imported. Sending outreach emails is unaffected.</p>
                <p class="text-[11px] text-slate-500">An administrator needs to add the Google OAuth credentials.</p>
            </div>`;
        return;
    }

    if (!integration.connected) {
        const needsReconnect = integration.needs_reconnect;
        card.innerHTML = `
            <div class="space-y-3">
                <div class="space-y-1">
                    <h3 class="text-sm font-bold text-white">Email Integration</h3>
                    <p class="text-xs text-slate-400">${needsReconnect
                        ? "Your Gmail connection expired or was revoked. Reconnect to keep receiving company replies."
                        : "Connect your Gmail account to receive company replies inside CareerPulse."}</p>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <button onclick="connectGmailMailbox()"
                        class="px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition">${needsReconnect ? "Reconnect Gmail" : "Connect Gmail"}</button>
                </div>
                <p class="text-[11px] text-slate-500">${escapeHtml(scopeNote)} You will be asked to approve read-only
                    access on Google's consent screen. Your password is never shared with CareerPulse.</p>
            </div>`;
        return;
    }

    card.innerHTML = `
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div class="space-y-1">
                <h3 class="text-sm font-bold text-white flex items-center gap-2">
                    <span class="text-emerald-400">&#10003;</span> Gmail connected
                    ${integration.needs_reconnect ? '<span class="text-[10px] font-bold text-amber-400 uppercase">needs renewal</span>' : ""}
                </h3>
                <p class="text-xs text-slate-400">Email: <span class="text-slate-200">${escapeHtml(integration.email || "Not reported")}</span></p>
                <p class="text-[11px] text-slate-500">Last checked: ${escapeHtml(integration.last_sync_at ? formatTimestamp(integration.last_sync_at) : "Not yet synced")}</p>
            </div>
            <div class="flex flex-wrap items-center gap-2">
                <button onclick="syncReplies(true)"
                    class="px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold transition border border-slate-700">Sync Replies</button>
                <button onclick="disconnectMailbox()"
                    class="px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-rose-600 hover:text-white text-slate-300 text-xs font-bold transition">Disconnect</button>
            </div>
        </div>
        <p class="text-[11px] text-slate-500 pt-3 mt-3 border-t border-slate-800">${escapeHtml(scopeNote)}</p>`;
}

function renderReplyNotification() {
    const banner = document.getElementById("repliesNotification");
    if (!banner) return;

    const notice = applicationsState.syncNotice;
    if (!notice) {
        banner.classList.add("hidden");
        banner.innerHTML = "";
        return;
    }

    const newest = applicationsState.applications.find(a => a.unread_count > 0);
    const toneClasses = notice.tone === "success"
        ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-200"
        : "bg-amber-500/10 border-amber-500/30 text-amber-200";

    banner.className = `p-4 rounded-2xl border space-y-3 ${toneClasses}`;
    banner.innerHTML = `
        <p class="text-xs font-bold">${escapeHtml(notice.text)}</p>
        ${newest ? `
            <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div class="min-w-0">
                    <p class="text-xs font-bold text-white">${escapeHtml(newest.company)}</p>
                    <p class="text-[11px] text-slate-400">${escapeHtml(newest.title)}</p>
                    <p class="text-[11px] text-slate-300 mt-1 truncate">${escapeHtml(newest.last_reply_preview || "New company reply")}</p>
                </div>
                <button onclick="openConversation('${escapeHtml(newest.opportunity_id).replace(/'/g, "\\'")}')"
                    class="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition shrink-0">View Reply</button>
            </div>` : ""}`;
}

function renderApplicationsList() {
    const container = document.getElementById("applicationsContainer");
    if (!container) return;

    const notice = applicationsState.syncNotice;

    if (!applicationsState.applications.length) {
        const message = notice && notice.tone === "success"
            ? "No company replies yet. Applications appear here as soon as you send an outreach email."
            : "No outreach emails have been sent from CareerPulse yet. Applications will appear here once you send one.";
        container.innerHTML = `
            <div class="p-8 rounded-2xl bg-slate-900 border border-slate-800 text-center space-y-3">
                <p class="text-sm text-slate-300 font-semibold">${escapeHtml(message)}</p>
                <button onclick="navigateTo('interested')"
                    class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition">Go to Interested Jobs</button>
            </div>`;
        return;
    }

    container.innerHTML = applicationsState.applications.map(app => {
        const tone = applicationStatusTone(app.status);
        const isReplied = app.reply_count > 0;
        const unread = app.unread_count > 0;
        const preview = app.last_reply_preview || "No reply received yet.";
        const label = unread ? `${app.unread_count} new repl${app.unread_count === 1 ? "y" : "ies"}` : `${app.reply_count} repl${app.reply_count === 1 ? "y" : "ies"}`;

        return `
        <div class="p-4 rounded-2xl bg-slate-900 border ${unread ? "border-indigo-500/40" : "border-slate-800"} space-y-3 shadow-lg">
            <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                <div class="min-w-0">
                    <h4 class="text-sm font-bold text-white truncate">${escapeHtml(app.title)}</h4>
                    <p class="text-xs text-indigo-400 truncate">${escapeHtml(app.company)}${app.recipient_email ? ` • ${escapeHtml(app.recipient_email)}` : ""}</p>
                </div>
                <span class="self-start px-2.5 py-1 rounded-lg bg-${tone}-500/15 text-${tone}-300 border border-${tone}-500/30 text-[10px] font-bold uppercase tracking-wide shrink-0">${escapeHtml(app.status)}</span>
            </div>

            <div class="space-y-1 text-[11px] text-slate-400">
                ${app.last_sent_at ? `<p>&#10003; Email sent: ${escapeHtml(formatTimestamp(app.last_sent_at))}${app.sent_count > 1 ? ` (${app.sent_count} emails)` : ""}</p>` : ""}
                ${isReplied ? `<p>${unread ? "&#128172;" : "&#8226;"} ${label} &middot; ${escapeHtml(formatTimestamp(app.last_reply_at))}</p>` : (app.last_sent_at ? "<p>No reply received yet.</p>" : "<p>Email not sent</p>")}
            </div>

            ${isReplied ? `<p class="text-[11px] text-slate-300 italic border-l-2 border-slate-700 pl-2">${escapeHtml(preview)}</p>` : ""}

            <div class="flex flex-wrap gap-2">
                <button onclick="openConversation('${escapeHtml(app.opportunity_id).replace(/'/g, "\\'")}')"
                    class="px-3.5 py-1.5 rounded-lg ${unread ? "bg-indigo-600 hover:bg-indigo-500 text-white" : "bg-slate-800 hover:bg-slate-700 text-slate-200"} text-xs font-bold transition">View Conversation</button>
            </div>
        </div>`;
    }).join("");
}

async function loadMailboxStatus() {
    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/email/integration/status", {
            headers: { ...authHeaders },
            credentials: "same-origin"
        });
        if (res.status === 401) {
            // Session validity is decided by /api/user/profile, so a 401 on this
            // background probe must not sign the user out.
            applicationsState.integration = null;
            applicationsState.integrationError = null;
            return;
        }
        const data = await res.json();
        if (res.ok) {
            applicationsState.integration = data;
            applicationsState.unread = data.unread_replies || 0;
            updateUnreadBadge();
            renderMailboxIntegration();
            renderReplyNotification();
        } else {
            applicationsState.integrationError = data.detail || "Could not read the mailbox connection.";
            renderMailboxIntegration();
        }
    } catch (e) {
        console.warn("Mailbox status unavailable");
        applicationsState.integrationError = "Could not reach the server.";
        renderMailboxIntegration();
    }
}

async function loadApplications() {
    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/applications", {
            headers: { ...authHeaders },
            credentials: "same-origin"
        });
        if (res.status === 401) {
            handleExpiredToken();
            return;
        }
        const data = await res.json();
        if (res.ok && data.status === "success") {
            applicationsState.applications = data.applications || [];
            applicationsState.byOpportunity = {};
            applicationsState.applications.forEach(app => {
                applicationsState.byOpportunity[app.opportunity_id] = app;
            });
            applicationsState.unread = data.unread_replies || 0;
            applicationsState.loaded = true;
            updateUnreadBadge();
            renderApplicationsList();
            // The Interested view shows a communication strip per opportunity.
            renderInterestedList();
        }
    } catch (e) {
        console.warn("Could not load applications:", e);
    }
}

function loadApplicationsView() {
    if (applicationsState.loading) return;
    applicationsState.loading = true;
    renderApplicationsList();
    loadMailboxStatus();
    loadApplications().finally(() => {
        applicationsState.loading = false;
    });
}

async function bootstrapReplies() {
    await loadMailboxStatus();
    const integration = applicationsState.integration;
    if (integration && integration.configured && integration.connected && !integration.needs_reconnect) {
        await syncReplies(false);
    }
}

function connectGmailMailbox() {
    // Server-side OAuth redirect: the browser never sees a client secret or token.
    window.location.href = "/api/email/integration/connect";
}

async function disconnectMailbox() {
    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/email/integration/disconnect", {
            method: "POST",
            headers: { ...authHeaders },
            credentials: "same-origin"
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            applicationsState.integration = { configured: true, connected: false };
            applicationsState.unread = 0;
            updateUnreadBadge();
            renderMailboxIntegration();
            showToast("Gmail disconnected. No mailbox data is stored.");
        } else {
            showToast(data.detail || "Could not disconnect Gmail.");
        }
    } catch (e) {
        showToast("Could not reach the server.");
    }
}

async function syncReplies(force) {
    setSyncBusy(true);
    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/email/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders },
            body: JSON.stringify({ force: !!force }),
            credentials: "same-origin"
        });
        const data = await res.json().catch(() => ({}));

        if (res.status === 401) {
            // Throttled background sync; a stale session here must not force a
            // logout while /api/user/profile still reports a valid session.
            return;
        }
        if (res.ok && data.status === "success") {
            const tone = data.inserted > 0 ? "success" : "info";
            applicationsState.syncNotice = {
                tone,
                text: data.skipped
                    ? data.message
                    : (data.inserted > 0
                        ? `${data.inserted} new company repl${data.inserted === 1 ? "y" : "ies"} received.`
                        : data.message)
            };
            await loadMailboxStatus();
            await loadApplications();
            renderReplyNotification();
        } else {
            applicationsState.syncNotice = { tone: "warning", text: data.detail || "Replies could not be checked right now." };
            renderReplyNotification();
            renderMailboxIntegration();
            if (data.detail) showToast(data.detail);
        }
    } catch (e) {
        showToast("Could not reach the server.");
    } finally {
        setSyncBusy(false);
    }
}

function closeConversation() {
    const modal = document.getElementById("conversationModal");
    if (modal) modal.classList.add("hidden");
    currentConversationId = null;
}

async function openConversation(opportunityId) {
    const modal = document.getElementById("conversationModal");
    const messagesEl = document.getElementById("conversationMessages");
    if (!modal || !messagesEl) return;

    currentConversationId = opportunityId;
    modal.classList.remove("hidden");
    messagesEl.innerHTML = `<p class="text-xs text-slate-400">Loading conversation…</p>`;

    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch(`/api/opportunities/${encodeURIComponent(opportunityId)}/conversation`, {
            headers: { ...authHeaders },
            credentials: "same-origin"
        });
        if (res.status === 401) {
            handleExpiredToken();
            return;
        }
        const data = await res.json();
        if (!res.ok) {
            messagesEl.innerHTML = `<p class="text-xs text-rose-300">${escapeHtml(data.detail || "Conversation unavailable.")}</p>`;
            return;
        }
        renderConversation(data);
        markConversationRepliesRead(data.messages || []);
    } catch (e) {
        messagesEl.innerHTML = `<p class="text-xs text-rose-300">Could not reach the server.</p>`;
    }
}

function renderConversation(data) {
    const application = data.application || {};
    const titleEl = document.getElementById("conversationTitle");
    const subtitleEl = document.getElementById("conversationSubtitle");
    const stripEl = document.getElementById("conversationStatusStrip");
    const messagesEl = document.getElementById("conversationMessages");
    if (!messagesEl) return;

    if (titleEl) titleEl.textContent = application.title || "Conversation";
    if (subtitleEl) subtitleEl.textContent = `${application.company || ""} • ${data.opportunity_id}`;

    const tone = applicationStatusTone(application.status);
    if (stripEl) {
        stripEl.innerHTML = `
            <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 flex flex-wrap items-center gap-2">
                <span class="px-2.5 py-1 rounded-lg bg-${tone}-500/15 text-${tone}-300 border border-${tone}-500/30 text-[10px] font-bold uppercase tracking-wide">${escapeHtml(application.status || "")}</span>
                <span class="text-[11px] text-slate-400">${application.reply_count ? `${application.reply_count} reply message(s)` : "No replies yet"}</span>
            </div>`;
    }

    const messages = data.messages || [];
    if (!messages.length) {
        messagesEl.innerHTML = `<p class="text-xs text-slate-500 italic">No messages recorded for this application yet.</p>`;
        return;
    }

    messagesEl.innerHTML = messages.map(msg => {
        const inbound = msg.direction === "inbound";
        const unread = inbound && msg.is_read === false;
        const alignment = inbound ? "ml-auto max-w-[85%]" : "mr-auto max-w-[85%]";
        const bubble = inbound
            ? "bg-slate-800 border-slate-700"
            : "bg-indigo-600/15 border-indigo-500/30";
        const who = inbound
            ? (msg.from_name || msg.from_email || "Company")
            : "You";

        const meta = [];
        if (msg.classification && msg.classification !== "unknown") {
            meta.push(`<span class="px-2 py-0.5 rounded bg-slate-950 border border-slate-700 text-[10px] font-bold uppercase tracking-wide">${escapeHtml(String(msg.classification).replace(/_/g, " "))}</span>`);
        }
        if (msg.match_method) {
            meta.push(`<span class="text-[10px] text-slate-500" title="How this reply was linked to your application">${escapeHtml(String(msg.match_method).replace(/_/g, " "))}</span>`);
        }

        const attachments = (msg.attachment_names || []).length
            ? `<p class="text-[11px] text-slate-400 mt-2">&#128206; Attachment received: ${escapeHtml((msg.attachment_names || []).join(", "))}</p>`
            : "";

        const requested = (msg.requested_information || []).length
            ? `<div class="mt-2 p-2 rounded-lg bg-slate-950 border border-slate-700 text-[11px] text-slate-300">
                 <p class="font-bold text-slate-200">Information requested</p>
                 <ul class="list-disc pl-4 mt-1 space-y-0.5">${msg.requested_information.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
               </div>`
            : "";

        const body = (msg.body_text || "").split("\n")
            .map(line => escapeHtml(line))
            .join("<br>");

        return `
        <div class="${alignment}">
            <div class="rounded-2xl border ${bubble} p-4 space-y-2">
                <div class="flex items-center gap-2 flex-wrap">
                    <span class="text-xs font-bold text-white">${escapeHtml(who)}</span>
                    ${unread ? '<span class="px-1.5 py-0.5 rounded bg-indigo-500 text-white text-[9px] font-bold uppercase">new</span>' : ""}
                    ${meta.join(" ")}
                </div>
                <p class="text-[11px] text-slate-400">${escapeHtml(msg.subject || "(no subject)")} &middot; ${escapeHtml(formatTimestamp(msg.timestamp))}</p>
                ${msg.classification_summary ? `<p class="text-[11px] text-indigo-300 italic">${escapeHtml(msg.classification_summary)}</p>` : ""}
                <div class="text-xs text-slate-200 leading-relaxed">${body}</div>
                ${requested}
                ${attachments}
            </div>
        </div>`;
    }).join("");
}

async function markConversationRepliesRead(messages) {
    const unread = messages.filter(m => m.direction === "inbound" && m.is_read === false);
    if (!unread.length) return;

    try {
        const authHeaders = await getAuthHeader();
        for (const msg of unread) {
            const res = await fetch(`/api/email/replies/${encodeURIComponent(msg.id)}/read`, {
                method: "POST",
                headers: { ...authHeaders },
                credentials: "same-origin"
            });
            if (res.ok) {
                const data = await res.json().catch(() => ({}));
                applicationsState.unread = data.unread_count || 0;
                msg.is_read = true;
            }
        }
        updateUnreadBadge();
        loadApplications();
    } catch (e) {
        // Read-state persistence is best effort; the reply is already shown.
    }
}

async function draftResponseAndCompose(opportunityId) {
    if (!opportunityId) return;
    showToast("Drafting a response for your review…");

    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/email/draft-response", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders },
            body: JSON.stringify({ opportunity_id: opportunityId }),
            credentials: "same-origin"
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(data.detail || "A response draft could not be created.");
            return;
        }

        const conversationRes = await fetch(`/api/opportunities/${encodeURIComponent(opportunityId)}/conversation`, {
            headers: { ...authHeaders },
            credentials: "same-origin"
        });
        const conversationData = await conversationRes.json().catch(() => ({}));
        const messages = conversationData.messages || [];
        const latestInbound = [...messages].reverse().find(m => m.direction === "inbound");
        const application = conversationData.application || {};

        const composer = document.getElementById("emailComposerModal");
        if (composer) composer.classList.remove("hidden");
        document.getElementById("composerJobTitle").textContent = application.title || "Application";
        document.getElementById("composerCompany").textContent = application.company || "";
        document.getElementById("composerToEmail").value = latestInbound?.from_email || application.recipient_email || "";
        document.getElementById("composerSubject").value = data.subject || `Re: ${latestInbound?.subject || application.subject || ""}`;
        document.getElementById("composerBody").value = data.body || "";

        const notice = document.getElementById("composerNotice");
        if (notice) {
            notice.textContent = data.notice || "Review this draft and edit it before you send. Nothing has been sent yet.";
            notice.classList.remove("hidden");
        }

        currentEmailPayload = {
            title: application.title || "Application",
            company: application.company || "",
            recipient_email: document.getElementById("composerToEmail").value,
            recipient_name: latestInbound?.from_name || "",
            opportunity_id: opportunityId
        };

        closeConversation();
    } catch (e) {
        showToast("Could not reach the server.");
    }
}


/* ------------------------------------------------------------------
 * PERSONALIZED TECHNOLOGY NEWS
 * ------------------------------------------------------------------ */

function resetPersonalizedNews() {
    newsState.loaded = false;
    newsState.loading = false;
    newsState.inFlight = false;
    newsState.articles = [];
    newsState.signature = null;
    newsState.error = null;
    newsState.loadedSignature = null;
}

function setNewsRefreshBusy(busy) {
    const btn = document.getElementById("btnRefreshNews");
    const label = document.getElementById("refreshNewsLabel");
    const icon = document.getElementById("refreshNewsIcon");
    if (btn) btn.disabled = busy;
    if (label) label.textContent = busy ? "Refreshing…" : "Refresh News";
    if (icon) icon.style.opacity = busy ? "0.4" : "1";
}

function renderNewsLoading() {
    const c = document.getElementById("techNewsContainer");
    if (!c) return;
    c.innerHTML = `
        <div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 flex items-center gap-3">
            <svg class="w-4 h-4 animate-spin text-indigo-400" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
            <p class="text-xs text-slate-400">Finding verified technology news for your profile…</p>
        </div>`;
}

function renderNewsEmpty(message, showUpdateProfile) {
    const c = document.getElementById("techNewsContainer");
    if (!c) return;
    c.innerHTML = `
        <div class="p-8 rounded-2xl bg-slate-900 border border-slate-800 text-center space-y-4">
            <p class="text-sm text-slate-300 font-semibold">${escapeHtml(message)}</p>
            <div class="flex flex-col sm:flex-row gap-2 justify-center">
                <button onclick="refreshPersonalizedNews()"
                    class="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition">Refresh News</button>
                ${showUpdateProfile ? `<button onclick="navigateTo('profile')"
                    class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold transition border border-slate-700">Update Profile</button>` : ""}
            </div>
        </div>`;
}

function renderNewsError(detail) {
    const c = document.getElementById("techNewsContainer");
    if (!c) return;
    c.innerHTML = `
        <div class="p-6 rounded-2xl bg-rose-500/10 border border-rose-500/30 space-y-3">
            <p class="text-sm text-rose-200 font-semibold">Could not load technology news</p>
            <p class="text-xs text-rose-200/80">${escapeHtml(detail || "Please try again in a moment.")}</p>
            <button onclick="refreshPersonalizedNews()"
                class="px-4 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold transition">Retry</button>
        </div>`;
}

/** Builds a single news card. All external text is escaped; URLs are validated. */
function buildNewsCard(article, index) {
    const url = (article.url && /^https?:\/\//i.test(article.url)) ? article.url : "";
    const tags = [
        ...(article.matched_interests || []),
        ...(article.matched_skills || [])
    ].filter(Boolean);

    const card = document.createElement("article");
    card.className = "p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3 transition hover:border-indigo-600/50";

    // Category + index
    const head = document.createElement("div");
    head.className = "flex items-center gap-3";
    const num = document.createElement("span");
    num.className = "text-[10px] font-extrabold text-indigo-400 tracking-widest";
    num.textContent = String(index + 1).padStart(2, "0");
    const cat = document.createElement("span");
    cat.className = "px-2 py-0.5 rounded-md bg-indigo-500/15 text-indigo-300 text-[10px] font-bold";
    cat.textContent = article.category || "Technology";
    head.appendChild(num);
    head.appendChild(cat);
    card.appendChild(head);

    // Headline
    const title = document.createElement("h3");
    title.className = "text-base font-bold text-white leading-snug";
    title.textContent = article.title || "";
    card.appendChild(title);

    // Source + published
    const meta = document.createElement("p");
    meta.className = "text-[11px] text-slate-400 flex flex-wrap items-center gap-x-2 gap-y-1";
    const src = document.createElement("span");
    src.className = "font-semibold text-slate-300";
    src.textContent = article.source_name || article.source_domain || "Source";
    meta.appendChild(src);
    if (article.published_at) {
        const sep = document.createElement("span");
        sep.textContent = "·";
        const when = document.createElement("span");
        const d = new Date(article.published_at);
        when.textContent = Number.isNaN(d.getTime())
            ? article.published_at
            : d.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
        meta.appendChild(sep);
        meta.appendChild(when);
    }
    card.appendChild(meta);

    // Summary
    if (article.summary) {
        const sum = document.createElement("p");
        sum.className = "text-xs text-slate-300 leading-relaxed";
        sum.textContent = article.summary;
        card.appendChild(sum);
    }

    // Why this matters to you
    if (article.why_it_matters) {
        const why = document.createElement("div");
        why.className = "p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/20";
        const label = document.createElement("p");
        label.className = "text-[10px] font-bold text-indigo-300 uppercase tracking-wide mb-1";
        label.textContent = "Why this matters to you";
        const body = document.createElement("p");
        body.className = "text-xs text-indigo-100/90 leading-relaxed";
        body.textContent = article.why_it_matters;
        why.appendChild(label);
        why.appendChild(body);
        card.appendChild(why);
    }

    // Matched interests / skills
    if (tags.length) {
        const tagRow = document.createElement("div");
        tagRow.className = "flex flex-wrap gap-1.5";
        tags.forEach(t => {
            const chip = document.createElement("span");
            chip.className = "px-2 py-0.5 rounded-md bg-slate-800 text-slate-300 text-[10px] font-semibold border border-slate-700";
            chip.textContent = t;
            tagRow.appendChild(chip);
        });
        card.appendChild(tagRow);
    }

    // Read more
    if (url) {
        const btn = document.createElement("a");
        btn.href = url;
        btn.target = "_blank";
        btn.rel = "noopener noreferrer";
        btn.className = "inline-flex items-center gap-1.5 self-start px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold transition border border-slate-700";
        btn.textContent = "Read Article →";
        card.appendChild(btn);
    }

    return card;
}

function renderPersonalizedNews(articles) {
    const c = document.getElementById("techNewsContainer");
    if (!c) return;

    if (!articles || !articles.length) {
        const emptyProfile = !newsState.hasProfileSignal;
        renderNewsEmpty(
            emptyProfile
                ? "Add skills and interests to your profile so CareerPulse can personalize your technology news."
                : "No recent technology news matching your interests was found.",
            emptyProfile
        );
        return;
    }

    c.innerHTML = "";
    articles.forEach((article, i) => c.appendChild(buildNewsCard(article, i)));

    // Show what the feed was personalized for
    const bar = document.getElementById("newsPersonalizationBar");
    const p = newsState.personalizedFor;
    if (bar && p) {
        const bits = [];
        if (p.interests?.length) bits.push(`interests: ${p.interests.join(", ")}`);
        if (p.skills?.length) bits.push(`skills: ${p.skills.slice(0, 6).join(", ")}`);
        if (p.roles?.length) bits.push(`roles: ${p.roles.join(", ")}`);
        if (p.domains?.length) bits.push(`domains: ${p.domains.join(", ")}`);
        bar.textContent = bits.length
            ? `Personalized for your ${bits.join(" · ")}`
            : "Personalized for your profile";
        bar.classList.remove("hidden");
    }
}

async function loadPersonalizedNews(forceRefresh) {
    if (newsState.inFlight) return;
    if (newsState.loaded && !forceRefresh) return;

    newsState.inFlight = true;
    newsState.loading = true;
    renderNewsLoading();
    setNewsRefreshBusy(true);

    try {
        const authHeaders = await getAuthHeader();
        const url = `/api/user/news/personalized?limit=10${forceRefresh ? "&refresh=true" : ""}`;
        const res = await fetch(url, { headers: { ...authHeaders }, credentials: "same-origin" });

        if (res.status === 401) {
            handleExpiredToken();
            return;
        }

        const data = await res.json();

        if (!res.ok || data.status === "error") {
            newsState.error = data.detail || "News retrieval failed.";
            renderNewsError(newsState.error);
            newsState.loaded = false;
            return;
        }

        newsState.articles = data.articles || [];
        newsState.personalizedFor = data.personalized_for || null;
        newsState.hasProfileSignal = !!(
            data.personalized_for &&
            ((data.personalized_for.interests || []).length ||
                (data.personalized_for.skills || []).length ||
                (data.personalized_for.roles || []).length ||
                (data.personalized_for.domains || []).length)
        );
        newsState.error = null;
        newsState.loaded = true;
        renderPersonalizedNews(newsState.articles);
    } catch (e) {
        console.error("Personalized news error:", e);
        newsState.error = "Network error while loading news.";
        renderNewsError(newsState.error);
        newsState.loaded = false;
    } finally {
        newsState.inFlight = false;
        newsState.loading = false;
        setNewsRefreshBusy(false);
    }
}

/** Manual "Refresh News" button. */
function refreshPersonalizedNews() {
    loadPersonalizedNews(true);
}

function openFetchModal() { document.getElementById("fetchOptionsModal").classList.remove("hidden"); }
function closeFetchModal() { document.getElementById("fetchOptionsModal").classList.add("hidden"); }
function openTinderModal() { document.getElementById("tinderModal").classList.remove("hidden"); }
function closeTinderModal() { document.getElementById("tinderModal").classList.add("hidden"); }

function confirmFetchOpportunities() {
    const select = document.getElementById("fetchCountSelect");
    const count = select ? parseInt(select.value) : 25;
    closeFetchModal();
    fetchLiveOpportunities(count);
}

async function fetchLiveOpportunities(requestedCount = 50) {
    const loadingOverlay = document.getElementById("loadingOverlay");
    const stepTitle = document.getElementById("loadingStepTitle");
    const stepDesc = document.getElementById("loadingStepDesc");
    const percentEl = document.getElementById("loadingPercent");
    const progressBar = document.getElementById("loadingProgressBar");

    loadingOverlay.classList.remove("hidden");

    let currentProgress = 10;
    const updateProgress = (target, title, desc) => {
        currentProgress = target;
        if (percentEl) percentEl.textContent = `${currentProgress}%`;
        if (progressBar) progressBar.style.width = `${currentProgress}%`;
        if (stepTitle) stepTitle.textContent = title;
        if (stepDesc) stepDesc.textContent = desc;
    };

    updateProgress(30, "Querying Individual Openings...", "Filtering out listicles & searching exact job pages");

    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch(`/api/user/profile/insights?count=${requestedCount}`, {
            headers: { ...authHeaders },
            credentials: "same-origin"
        });

        if (res.status === 401) {
            loadingOverlay.classList.add("hidden");
            handleExpiredToken();
            return;
        }

        if (!res.ok) throw new Error("Fetch failed");

        const data = await res.json();
        updateProgress(100, "Processing Complete!", "Loading verified job cards");

        setTimeout(() => {
            if (data.status === "success") {
                const ins = data.insights;
                if (document.getElementById("aiInsightText")) document.getElementById("aiInsightText").textContent = ins.insight_text;

                currentTechNews = data.daily_news || [];
                renderTechNews(currentTechNews);

                mockOpportunities = (data.opportunities || []).map((item, idx) => {
                    const score = typeof item.match?.score === 'number' ? item.match.score : 60;
                    const tier = item.match?.tier || (score >= 60 ? 'Gold' : score >= 40 ? 'Silver' : 'Bronze');
                    const matched = item.skill_analysis?.matched || item.match?.matched_skills || ["Python"];
                    const missing = item.skill_analysis?.missing || item.match?.missing_skills || [];
                    const targetUrl = item.application_url || item.source_url || item.url || "https://linkedin.com/jobs";

                    return {
                        id: `live-job-${idx}`,
                        title: item.title || "Software Engineering Intern",
                        company: item.company || "Enterprise Tech",
                        location: Array.isArray(item.location) ? item.location.join(", ") : (item.location || "Mumbai / Remote"),
                        matchPercentage: score,
                        tier: tier,
                        skills: matched,
                        lackingSkills: missing,
                        summary: item.description_summary || item.description || "Direct verified web posting aligned with candidate profile.",
                        url: targetUrl,
                        status: "discovered"
                    };
                });

                currentSwipeIndex = 0;
                updateStats();
                loadingOverlay.classList.add("hidden");

                renderTinderCard();
                openTinderModal();
                if (mockOpportunities.length > 0) {
                    showToast(`Loaded ${mockOpportunities.length} verified individual openings!`);
                } else {
                    showToast("No verified individual openings found for this query.");
                }
            }
        }, 400);

    } catch (err) {
        console.error("Error fetching live opportunities:", err);
        loadingOverlay.classList.add("hidden");
        showToast("Error retrieving live openings. Please try again.");
    }
}

function renderTinderCard() {
    const container = document.getElementById("tinderCardContainer");
    const progressText = document.getElementById("swipeProgressText");
    if (!container) return;

    if (mockOpportunities.length === 0) {
        if (progressText) progressText.textContent = "0 Openings";
        container.innerHTML = `
            <div class="text-center p-8 bg-slate-900 border border-slate-800 rounded-2xl space-y-3 w-full">
                <p class="text-base font-bold text-white">No verified opportunities found for this search.</p>
                <p class="text-xs text-slate-400 leading-relaxed">CareerPulse strictly filters out listing pages, category indexes, aggregators, and unverified postings.</p>
                <button onclick="closeTinderModal()" class="px-4 py-2 rounded-xl text-xs font-bold bg-indigo-600 text-white mt-2">Close Window</button>
            </div>
        `;
        return;
    }

    if (currentSwipeIndex >= mockOpportunities.length) {
        if (progressText) progressText.textContent = "Completed";
        container.innerHTML = `
            <div class="text-center p-8 bg-slate-900 border border-slate-800 rounded-2xl space-y-3 w-full">
                <p class="text-base font-bold text-white">All caught up! 🎉</p>
                <p class="text-xs text-slate-400">You have swiped through all verified job opportunities.</p>
                <button onclick="closeTinderModal()" class="px-4 py-2 rounded-xl text-xs font-bold bg-indigo-600 text-white mt-2">Close Window</button>
            </div>
        `;
        return;
    }

    if (progressText) {
        progressText.textContent = `Job ${currentSwipeIndex + 1} of ${mockOpportunities.length}`;
    }

    const job = mockOpportunities[currentSwipeIndex];
    const scoreVal = typeof job.matchPercentage === 'number' ? job.matchPercentage : 60;

    let cardClass = "w-full p-6 rounded-2xl space-y-4 shadow-2xl bg-slate-900 border border-slate-800";
    let badgeMarkup = `<span class="px-2.5 py-1 rounded-full text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">${scoreVal}% MATCH</span>`;

    if (job.tier === 'Gold' || scoreVal >= 60) {
        cardClass = "w-full p-6 rounded-2xl space-y-4 shadow-2xl gold-card";
        badgeMarkup = `<span class="px-2.5 py-1 rounded-full text-[10px] font-bold gold-tier-badge">GOLD MATCH (${scoreVal}%)</span>`;
    } else if (job.tier === 'Silver' || scoreVal >= 40) {
        cardClass = "w-full p-6 rounded-2xl space-y-4 shadow-2xl silver-card";
        badgeMarkup = `<span class="px-2.5 py-1 rounded-full text-[10px] font-bold silver-tier-badge">SILVER MATCH (${scoreVal}%)</span>`;
    } else {
        cardClass = "w-full p-6 rounded-2xl space-y-4 shadow-2xl bronze-card";
        badgeMarkup = `<span class="px-2.5 py-1 rounded-full text-[10px] font-bold bronze-tier-badge">BRONZE MATCH (${scoreVal}%)</span>`;
    }

    let lackingArray = job.lackingSkills || [];
    if (typeof lackingArray === 'string') lackingArray = [lackingArray];
    const lackingBadges = lackingArray.map(s => `<span class="px-2.5 py-1 rounded-md text-[10px] font-semibold bg-rose-500/10 text-rose-300 border border-rose-500/20">${escapeHtml(s)}</span>`).join("");

    const companyInitial = escapeHtml((job.company || "E").charAt(0));

    container.innerHTML = `
        <div id="popupSwipeCard" class="${cardClass}">
            <div class="flex justify-between items-start gap-3">
                <div class="flex items-center gap-3 min-w-0">
                    <div class="w-10 h-10 rounded-xl bg-slate-950/80 p-2 border border-slate-800 shrink-0 flex items-center justify-center text-indigo-400 font-bold text-sm">
                        ${companyInitial}
                    </div>
                    <div class="min-w-0">
                        <h4 class="text-base font-bold text-white truncate">${escapeHtml(job.title)}</h4>
                        <p class="text-xs font-semibold text-indigo-400 truncate">${escapeHtml(job.company)} • ${escapeHtml(job.location)}</p>
                    </div>
                </div>
                ${badgeMarkup}
            </div>

            <p class="text-xs text-slate-300 leading-relaxed">${escapeHtml(job.summary)}</p>

            <div class="p-3 rounded-xl bg-slate-950/90 border border-slate-800 space-y-2">
                <span class="text-[10px] font-bold text-rose-400 uppercase tracking-wider block">Missing Skills</span>
                <div class="flex flex-wrap gap-1.5">
                    ${lackingBadges || '<span class="text-[10px] text-emerald-400 font-semibold">✓ All required skills matched!</span>'}
                </div>
            </div>

            <div class="pt-2 border-t border-slate-800/80 flex justify-between items-center text-xs">
                <span class="text-[10px] text-slate-400">✓ Verified Source</span>
                <a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer" class="text-indigo-400 hover:underline font-bold text-xs flex items-center gap-1">
                    <span>Apply / View Post</span>
                    <span>↗</span>
                </a>
            </div>
        </div>
    `;
}

function handlePopupSwipe(direction) {
    const card = document.getElementById("popupSwipeCard");
    if (!card || currentSwipeIndex >= mockOpportunities.length) return;

    const currentJob = mockOpportunities[currentSwipeIndex];

    if (direction === 'right') {
        card.classList.add("animate-swipe-right");
        if (!currentJob) return;
        currentJob.status = "interested";
        savedInterestedJobs.push(currentJob);
        persistOpportunityState(currentJob, "interested");
        showToast(`Saved ${currentJob.title} to Interested!`);
    } else if (direction === 'left') {
        card.classList.add("animate-swipe-left");
        currentJob.status = "rejected";
        showToast(`Dismissed ${currentJob.title}`);
    } else if (direction === 'down') {
        card.classList.add("animate-swipe-down");
        currentJob.status = "waitlisted";
        waitlistedJobs.push(currentJob);
        persistOpportunityState(currentJob, "waitlisted");
        showToast(`Added ${currentJob.title} to Waitlist`);
    }

    setTimeout(() => {
        currentSwipeIndex++;
        updateStats();
        renderInterestedList();
        renderWaitlistedList();
        renderTinderCard();
    }, 320);
}

async function fetchContactDetails(company, title, btnElement) {
    const parentContainer = btnElement.parentElement.parentElement;
    btnElement.disabled = true;
    btnElement.textContent = "Searching Contact...";

    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/opportunity/contact-lookup", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders
            },
            body: JSON.stringify({ company, title }),
            credentials: "same-origin"
        });

        if (res.ok) {
            const data = await res.json();
            const contact = data.contact;

            const existingBox = parentContainer.querySelector(".contact-details-box");
            if (existingBox) existingBox.remove();

            const box = document.createElement("div");
            box.className = "contact-details-box w-full col-span-2 mt-3";

            if (contact && contact.email) {
                const escapedEmail = escapeHtml(contact.email);
                const escapedName = escapeHtml(contact.name || "");

                box.innerHTML = `
                    <div class="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs space-y-2">
                        <div class="flex justify-between items-center">
                            <p class="font-bold text-emerald-400 text-xs">✓ Verified Recruiter Contact Found</p>
                            <span class="text-[10px] text-emerald-300 font-semibold bg-emerald-500/20 px-2 py-0.5 rounded-full">100% Verified</span>
                        </div>
                        <div class="space-y-0.5 text-slate-200">
                            <p><strong>Email:</strong> <a href="mailto:${escapedEmail}" class="text-indigo-400 underline font-semibold">${escapedEmail}</a></p>
                            ${contact.name ? `<p><strong>Name:</strong> ${escapedName}</p>` : ''}
                        </div>
                        <p class="text-[10px] text-slate-400">${escapeHtml(contact.note || 'Official recruitment search result')}</p>
                        <div class="pt-2 flex justify-end">
                            <button id="btnDraftEmail" class="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition shadow-lg shadow-indigo-600/30 flex items-center gap-1.5">
                                <span>Draft Outreach Email</span>
                                <span>✉</span>
                            </button>
                        </div>
                    </div>
                `;
                const draftBtn = box.querySelector("#btnDraftEmail");
                if (draftBtn) {
                    draftBtn.onclick = () => openEmailComposer(company, title, contact.email, contact.name || "");
                }
            } else {
                box.innerHTML = `
                    <div class="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs space-y-2">
                        <p class="font-bold text-amber-400">ℹ No Verified Direct Recruiter Email Found</p>
                        <p class="text-slate-300">${escapeHtml(contact ? contact.note : 'Official recruitment email is not publicly indexed. Please apply directly via the company career portal.')}</p>
                    </div>
                `;
            }

            parentContainer.appendChild(box);
            btnElement.textContent = "Refetch Contact";
            btnElement.disabled = false;
        } else {
            showToast("Failed to lookup contact details.");
            btnElement.textContent = "Fetch HR Contact";
            btnElement.disabled = false;
        }
    } catch (e) {
        console.error("Contact lookup error:", e);
        showToast("Error performing contact lookup.");
        btnElement.textContent = "Fetch HR Contact";
        btnElement.disabled = false;
    }
}

async function openEmailComposer(company, title, recipientEmail, recipientName = "") {
    const modal = document.getElementById("emailComposerModal");
    if (!modal) return;
    modal.classList.remove("hidden");

    const notice = document.getElementById("composerNotice");
    if (notice) notice.classList.add("hidden");

    document.getElementById("composerJobTitle").textContent = title;
    document.getElementById("composerCompany").textContent = company;
    document.getElementById("composerToEmail").value = recipientEmail;
    document.getElementById("composerSubject").value = `Application for ${title} - Jeshurun Selvakumar`;
    document.getElementById("composerBody").value = "Drafting personalized email using academic & candidate profile...";

    currentEmailPayload = {
        title,
        company,
        recipient_email: recipientEmail,
        recipient_name: recipientName,
        opportunity_id: `${company}_${title}`.toLowerCase().replace(/\s+/g, '_')
    };

    try {
        const authHeaders = await getAuthHeader();
        const draftRes = await fetch("/api/email/generate", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders
            },
            body: JSON.stringify({
                title,
                company,
                summary: "Software engineering opportunity matching candidate technical skills.",
                recipient_email: recipientEmail,
                recipient_name: recipientName
            })
        });
        const draftData = await draftRes.json();
        if (draftData.status === "success") {
            document.getElementById("composerSubject").value = draftData.subject;
            document.getElementById("composerBody").value = draftData.body;
        }
    } catch (e) {
        console.error("Email draft error:", e);
    }
}

function closeEmailComposer() {
    const modal = document.getElementById("emailComposerModal");
    if (modal) modal.classList.add("hidden");
}

async function submitOutreachEmail() {
    const toEmail = document.getElementById("composerToEmail").value.trim();
    const subject = document.getElementById("composerSubject").value.trim();
    const body = document.getElementById("composerBody").value.trim();
    const sendBtn = document.getElementById("sendEmailBtn");

    if (!toEmail || !toEmail.includes("@")) {
        showToast("Please provide a valid recipient email address.");
        return;
    }
    if (!subject || !body) {
        showToast("Subject and body cannot be empty.");
        return;
    }

    sendBtn.disabled = true;
    sendBtn.textContent = "Sending...";

    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/email/send", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders
            },
            body: JSON.stringify({
                to_email: toEmail,
                subject: subject,
                body: body,
                opportunity_id: currentEmailPayload.opportunity_id,
                company: currentEmailPayload.company,
                title: currentEmailPayload.title
            })
        });

        if (res.status === 401) {
            sendBtn.disabled = false;
            sendBtn.textContent = "Send Email";
            handleExpiredToken();
            return;
        }

        const data = await res.json();
        if (res.ok && data.status === "success") {
            sendBtn.textContent = "✓ Email Sent";
            showToast("Email sent successfully!");
            // The application now exists in the Applications list with a
            // tracked sent-email record that replies can be matched against.
            loadApplications();
            setTimeout(() => {
                closeEmailComposer();
                sendBtn.disabled = false;
                sendBtn.textContent = "Send Email";
            }, 1500);
        } else {
            throw new Error(data.detail || "Failed to send email");
        }
    } catch (err) {
        console.error("Email send error:", err);
        showToast(err.message || "Unable to send email. Please check recipient and retry.");
        sendBtn.disabled = false;
        sendBtn.textContent = "Try Again";
    }
}

function opportunityKeyFor(company, title) {
    return `${company}_${title}`.toLowerCase().replace(/\s+/g, '_');
}

/**
 * Compact application-communication state for an opportunity card.
 * Reads the Applications data already loaded for the current user; renders
 * nothing extra when the user has not sent email to that company.
 */
function renderCommunicationStrip(company, title) {
    const key = opportunityKeyFor(company, title);
    let application = applicationsState.byOpportunity[key];
    if (!application) {
        // Opportunity ids are normalized on both sides; match defensively.
        application = applicationsState.applications.find(app =>
            String(app.opportunity_id || "").toLowerCase().replace(/\s+/g, '_') === key
        );
    }
    if (!application) return "";

    const tone = applicationStatusTone(application.status);
    const replied = application.reply_count > 0;
    const unread = application.unread_count > 0;

    if (replied) {
        return `
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pt-3 border-t border-slate-800">
            <div class="min-w-0">
                <p class="text-[11px] font-bold text-${tone}-300">${unread ? "&#128172;" : "&#10003;"} ${escapeHtml(application.status)}${unread ? ` &middot; ${application.unread_count} new` : ""}</p>
                <p class="text-[11px] text-slate-400 truncate">${escapeHtml(application.last_reply_preview || "Company replied")}</p>
            </div>
            <button onclick="openConversation('${escapeHtml(key).replace(/'/g, "\\'")}')"
                class="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition shrink-0">View Conversation</button>
        </div>`;
    }

    return `
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pt-3 border-t border-slate-800">
        <p class="text-[11px] text-slate-400">&#10003; Application email sent &middot; no reply yet</p>
        <button onclick="openConversation('${escapeHtml(key).replace(/'/g, "\\'")}')"
            class="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition shrink-0">View Conversation</button>
    </div>`;
}

function renderInterestedList() {
    const container = document.getElementById("interestedJobsContainer");
    if (!container) return;
    if (savedInterestedJobs.length === 0) {
        container.innerHTML = `<p class="text-xs text-slate-500 italic">No interested jobs saved yet. Swipe right on opportunities to save them here.</p>`;
        return;
    }
    container.innerHTML = savedInterestedJobs
        .filter(job => job && typeof job === "object")
        .map(job => `
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800 flex flex-col gap-3">
            <div class="flex justify-between items-center">
                <div>
                    <h5 class="text-sm font-bold text-white">${escapeHtml(job.title)}</h5>
                    <p class="text-xs text-indigo-400">${escapeHtml(job.company)} • ${escapeHtml(job.location)}</p>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="fetchContactDetails('${escapeHtml(job.company).replace(/'/g, "\\'")}', '${escapeHtml(job.title).replace(/'/g, "\\'")}', this)" class="px-3.5 py-1.5 rounded-lg bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 text-xs font-bold hover:bg-emerald-600 hover:text-white transition flex items-center gap-1.5">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                        <span>Fetch HR Contact</span>
                    </button>
                    <a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer" class="px-3.5 py-1.5 rounded-lg bg-slate-800 text-slate-300 text-xs font-bold hover:bg-slate-700 transition">Apply ↗</a>
                </div>
            </div>
            ${renderCommunicationStrip(job.company, job.title)}
        </div>
    `).join("");
}

function renderWaitlistedList() {
    const container = document.getElementById("waitlistedJobsContainer");
    if (!container) return;
    if (waitlistedJobs.length === 0) {
        container.innerHTML = `<p class="text-xs text-slate-500 italic">No waitlisted opportunities right now.</p>`;
        return;
    }
    container.innerHTML = waitlistedJobs.map(job => `
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800 flex justify-between items-center">
            <div>
                <h5 class="text-sm font-bold text-white">${escapeHtml(job.title)}</h5>
                <p class="text-xs text-amber-400">${escapeHtml(job.company)} • ${escapeHtml(job.location)}</p>
            </div>
            <a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer" class="px-3 py-1.5 rounded-lg bg-amber-600/20 text-amber-300 border border-amber-500/30 text-xs font-bold hover:bg-amber-600 hover:text-white transition">View ↗</a>
        </div>
    `).join("");
}

function renderTechNews(newsList) {
    const container = document.getElementById("techNewsContainer");
    if (!container) return;
    if (!newsList || newsList.length === 0) {
        container.innerHTML = `<div class="p-6 rounded-2xl bg-slate-900 border border-slate-800 text-center text-xs text-slate-400">No verified major technology developments recorded from the last 24 hours. Run "Fetch Opportunities" to refresh news discovery.</div>`;
        return;
    }
    container.innerHTML = newsList.map((item) => `
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3 shadow-lg">
            <div class="flex justify-between items-start gap-4">
                <div>
                    <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">${escapeHtml(item.source_name || 'Verified Tech Source')} • ${escapeHtml(item.published_at || '')}</span>
                    <h4 class="text-sm font-bold text-white mt-0.5">${escapeHtml(item.title)}</h4>
                </div>
                <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer" class="px-3 py-1.5 rounded-lg bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 text-xs font-semibold hover:bg-indigo-600 hover:text-white transition shrink-0">Read Source ↗</a>
            </div>
            <p class="text-xs text-slate-300 leading-relaxed">${escapeHtml(item.summary)}</p>
            ${item.why_it_matters ? `
            <div class="p-3 rounded-xl bg-slate-950/80 border border-slate-800/80">
                <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-wider block mb-0.5">Why it matters:</span>
                <p class="text-xs text-slate-300">${escapeHtml(item.why_it_matters)}</p>
            </div>` : ''}
        </div>
    `).join("");
}

async function triggerManualDigestEmail() {
    const btn = document.getElementById("btnSendDigestNow");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Checking & Sending...";
    }
    try {
        const authHeaders = await getAuthHeader();
        const res = await fetch("/api/digest/send", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...authHeaders
            },
            body: JSON.stringify({ force: false }),
            credentials: "same-origin"
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast("Daily digest email sent successfully!");
        } else if (data.status === "skipped") {
            showToast("Digest already sent in the last 24 hours (Idempotency Active).");
        } else {
            showToast(data.message || "Failed to send digest.");
        }
    } catch (e) {
        showToast("Error triggering daily digest.");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Send Daily Digest Email";
        }
    }
}

function updateStats() {
    if (document.getElementById("statFound")) document.getElementById("statFound").textContent = mockOpportunities.length;
    if (document.getElementById("statMatches")) document.getElementById("statMatches").textContent = mockOpportunities.filter(j => j.tier === 'Gold').length;
    if (document.getElementById("statWaitlist")) document.getElementById("statWaitlist").textContent = waitlistedJobs.length;
    if (document.getElementById("statInterested")) document.getElementById("statInterested").textContent = savedInterestedJobs.length;
}

function renderSkillsBadge(skillsArray) {
    const container = document.getElementById("cardProfileSkills");
    if (!container) return;
    const skills = skillsArray || ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"];
    container.innerHTML = skills.map(s => `<span class="px-2 py-0.5 rounded-md text-[10px] font-semibold bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">${escapeHtml(s)}</span>`).join("");
}

function showToast(msg) {
    const container = document.getElementById("toastContainer");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "px-4 py-3 rounded-xl bg-slate-900 border border-slate-800 text-xs text-white shadow-2xl flex items-center gap-2";
    toast.innerHTML = `<span class="w-2 h-2 rounded-full bg-indigo-400"></span><span>${escapeHtml(msg)}</span>`;
    container.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

/* ---------- Progressive Web App ---------- */
/* Registered on window load so it never competes with authentication
   initialisation. Failure is non-fatal: the app works normally without it. */
if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js")
            .catch(error => console.error("PWA service worker registration failed:", error));
    });
}