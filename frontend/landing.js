/* CareerPulse public landing page interactions */

document.addEventListener("DOMContentLoaded", () => {
    initMobileNav();
    initFaq();
    initReveal();
    resolveAuthState();
    showAuthNotice();
});

/* ---------- mobile navigation ---------- */

function initMobileNav() {
    const toggle = document.getElementById("navToggle");
    const menu = document.getElementById("mobileMenu");
    if (!toggle || !menu) return;

    toggle.addEventListener("click", () => {
        const open = menu.classList.toggle("open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    menu.querySelectorAll("a[href^='#']").forEach(link => {
        link.addEventListener("click", () => {
            menu.classList.remove("open");
            toggle.setAttribute("aria-expanded", "false");
        });
    });
}

/* ---------- faq accordion ---------- */

function initFaq() {
    document.querySelectorAll(".cp-faq-q").forEach(button => {
        button.addEventListener("click", () => {
            const item = button.closest(".cp-faq-item");
            const isOpen = item.classList.contains("open");

            document.querySelectorAll(".cp-faq-item.open").forEach(openItem => {
                openItem.classList.remove("open");
                const openButton = openItem.querySelector(".cp-faq-q");
                if (openButton) openButton.setAttribute("aria-expanded", "false");
            });

            if (!isOpen) {
                item.classList.add("open");
                button.setAttribute("aria-expanded", "true");
            }
        });
    });
}

/* ---------- scroll reveal ---------- */

function initReveal() {
    const targets = document.querySelectorAll(".cp-card, .cp-faq-item, .cp-hero-panel");
    if (!("IntersectionObserver" in window)) {
        targets.forEach(el => el.classList.add("visible"));
        return;
    }

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add("visible");
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });

    targets.forEach(el => {
        el.classList.add("cp-reveal");
        observer.observe(el);
    });
}

/* ---------- auth state ---------- */

const AUTH_LINKS = [
    { link: "navAuthLink", label: "navAuthLabel" },
    { link: "mobileAuthLink", label: "mobileAuthLabel" },
    { link: "heroAuthLink", label: "heroAuthLabel" },
    { link: "linkedinCtaLink", label: "linkedinCtaLabel" },
    { link: "finalAuthLink", label: "finalAuthLabel" }
];

async function resolveAuthState() {
    try {
        const res = await fetch("/api/user/profile", { credentials: "same-origin" });
        if (!res.ok) return;

        const data = await res.json();
        if (!data.authenticated) return;

        // Already signed in: point every CTA at the dashboard instead of re-authenticating.
        AUTH_LINKS.forEach(({ link, label }) => {
            const anchor = document.getElementById(link);
            const text = document.getElementById(label);
            if (anchor) anchor.setAttribute("href", "/app");
            if (text) text.textContent = "Open Dashboard";
        });
    } catch (e) {
        // Landing page stays fully usable without a session.
    }
}

/* ---------- auth error notice ---------- */

function showAuthNotice() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("error") !== "auth_failed") return;

    const banner = document.createElement("div");
    banner.className = "mx-auto max-w-[1180px] px-5 pt-5 md:px-8";
    banner.innerHTML =
        '<div class="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-xs font-semibold text-rose-200">' +
        "LinkedIn sign-in could not be completed. Please try connecting again." +
        "</div>";

    const nav = document.querySelector(".cp-nav");
    if (nav && nav.nextSibling) nav.parentNode.insertBefore(banner, nav.nextSibling);
}