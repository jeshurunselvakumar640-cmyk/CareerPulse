let mockOpportunities = [];
let savedInterestedJobs = [];
let waitlistedJobs = [];
let currentSwipeIndex = 0;
let currentEmailPayload = null;
let currentTechNews = [];

// Global auth state — prevents rendering before auth is resolved
const authState = {
    loading: true,
    authenticated: false,
    user: null
};

const USER_AVATAR_SVG = `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40' viewBox='0 0 24 24' fill='none' stroke='%23818cf8' stroke-width='1.5'><path d='M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2'/><circle cx='12' cy='7' r='4'/></svg>`;

const APP_VIEW_HASHES = ["dashboard", "profile", "interested", "waitlist", "news"];

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

            // Navigate based on URL hash or profile completeness
            const hash = window.location.hash.replace("#", "");
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
        }
    } catch (e) {
        console.error("Failed to sync state with Supabase:", e);
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
    // Personalized news is per-user: never let one account see another's feed.
    resetPersonalizedNews();
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

function renderInterestedList() {
    const container = document.getElementById("interestedJobsContainer");
    if (!container) return;
    if (savedInterestedJobs.length === 0) {
        container.innerHTML = `<p class="text-xs text-slate-500 italic">No interested jobs saved yet. Swipe right on opportunities to save them here.</p>`;
        return;
    }
    container.innerHTML = savedInterestedJobs.map(job => `
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