/*
 * CareerPulse service worker
 * ---------------------------------------------------------------------------
 * Bump CACHE_VERSION to ship a new build. On activate the worker deletes only
 * its own previous versions (identified by CACHE_PREFIX), so clients are never
 * left stuck on an old shell and caches belonging to anything else are left
 * untouched.
 *
 * PRIVACY CONTRACT
 * This worker must never store anything that is personalised, authenticated or
 * tied to a session. That means: no API responses, no Supabase auth traffic, no
 * LinkedIn/Google/Gmail OAuth traffic, no cookies and no access tokens.
 *
 * Enforcement happens in three independent layers:
 *   1. Only same-origin GET requests are ever considered (Supabase and the
 *      OAuth providers are cross-origin, so they are never touched at all).
 *   2. A deny-list short-circuits /api/*, /auth/*, /oauth/* and callback paths
 *      before any caching logic runs. These requests are not intercepted, so
 *      the browser handles them exactly as it does today.
 *   3. A response is only ever written to the cache if it is a 200 "basic"
 *      same-origin response for a known-static file type.
 */

const CACHE_VERSION = "v4";
const CACHE_NAME = `careerpulse-static-${CACHE_VERSION}`;
const CACHE_PREFIX = "careerpulse-static-";

/*
 * The application shell. Both documents plus the static assets they reference,
 * so the app can open while offline. None of these contain user data: the
 * dashboard document is an identical empty shell that is populated at runtime
 * from /api/*, which is never cached.
 */
const PRECACHE_URLS = [
    "/",
    "/app",
    "/manifest.webmanifest",
    "/static/landing.js",
    "/static/app.js",
    "/static/styles/landing.css",
    "/static/styles/dashboard.css",
    "/static/assets/logo.png",
    "/static/assets/icon-192.png",
    "/static/assets/icon-512.png",
    "/static/assets/apple-touch-icon.png"
];

/*
 * Deny-list. A request whose path starts with any of these is passed straight
 * through to the network and is never read from or written to the cache.
 * This covers every backend route (/api/*) including the LinkedIn, Google and
 * Gmail OAuth entry points and callbacks, and the sign-in / sign-out routes.
 */
const NEVER_CACHE_PREFIXES = [
    "/api/",
    "/auth/",
    "/oauth/",
    "/.auth/",
    "/login",
    "/logout",
    "/callback"
];

/*
 * The only file types this worker is willing to store. Anything else that is
 * same-origin is simply not intercepted.
 */
const STATIC_ASSET_PATTERN = /\.(?:css|js|mjs|png|svg|webp|ico|woff2?|ttf|otf)$/i;

self.addEventListener("install", (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE_NAME);

        // Cached one by one rather than with cache.addAll: a single failing
        // request would otherwise reject the whole install and leave the
        // worker permanently unregistered.
        await Promise.all(PRECACHE_URLS.map(async (url) => {
            try {
                const response = await fetch(url, {
                    cache: "reload",
                    credentials: "same-origin"
                });
                if (isCacheable(response)) {
                    await cache.put(url, response);
                }
            } catch (error) {
                // Offline or asset unavailable at install time. Skip it; the
                // runtime handler will pick it up on the next successful visit.
            }
        }));

        await self.skipWaiting();
    })());
});

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(
            names
                .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
                .map((name) => caches.delete(name))
        );
        await self.clients.claim();
    })());
});

self.addEventListener("message", (event) => {
    // Lets a future in-app "update available" prompt activate a waiting worker.
    if (event.data && event.data.type === "SKIP_WAITING") {
        self.skipWaiting();
    }
});

self.addEventListener("fetch", (event) => {
    const request = event.request;

    // Never touch anything that is not a plain GET.
    if (request.method !== "GET") return;

    let url;
    try {
        url = new URL(request.url);
    } catch (error) {
        return;
    }

    // Supabase and the OAuth providers are cross-origin. Leaving these
    // un-intercepted keeps sessions, tokens and provider responses entirely
    // under browser control.
    if (url.origin !== self.location.origin) return;

    // Deny-list: /api/*, /auth/*, /oauth/* and OAuth callbacks. Not
    // intercepted at all, so existing behaviour is unchanged.
    if (isNeverCached(url.pathname)) return;

    // HTML documents: network-first so users always get the current build,
    // falling back to the cached shell when offline.
    if (request.mode === "navigate") {
        event.respondWith(networkFirstDocument(request, url));
        return;
    }

    // Versioned static assets: cache-first.
    if (STATIC_ASSET_PATTERN.test(url.pathname)) {
        event.respondWith(cacheFirst(request));
    }
});

/**
 * True when a path must never be read from or written to the cache.
 */
function isNeverCached(pathname) {
    const path = pathname.toLowerCase();
    return NEVER_CACHE_PREFIXES.some((prefix) => path.startsWith(prefix));
}

/**
 * A response is only storable if it is a successful, same-origin "basic"
 * response. Opaque, error and cross-origin responses are rejected, so no
 * third-party or error payload can enter the cache.
 */
function isCacheable(response) {
    return Boolean(response) && response.status === 200 && response.type === "basic";
}

/**
 * Network-first for documents, with the precached shell as the offline
 * fallback. The cached document is the same static shell served to every
 * visitor, so nothing personalised is exposed offline.
 */
async function networkFirstDocument(request, url) {
    const cache = await caches.open(CACHE_NAME);

    try {
        const response = await fetch(request);
        if (isCacheable(response)) {
            cache.put(request, response.clone()).catch(() => {});
        }
        return response;
    } catch (error) {
        const cached =
            await cache.match(request) ||
            (url.pathname.startsWith("/app")
                ? await cache.match("/app")
                : await cache.match("/"));

        if (cached) return cached;
        return offlineResponse();
    }
}

/**
 * Cache-first for versioned static assets. Static files are immutable between
 * deploys, and the versioned cache name is what forces a refetch.
 */
async function cacheFirst(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);

    if (cached) return cached;

    try {
        const response = await fetch(request);
        if (isCacheable(response)) {
            cache.put(request, response.clone()).catch(() => {});
        }
        return response;
    } catch (error) {
        // Fail safely: no fabricated content, just an honest offline signal.
        return new Response("", {
            status: 504,
            statusText: "Offline"
        });
    }
}

/**
 * Last-resort response when a navigation has neither network nor cache.
 * It states that the app is offline and does not invent any data.
 */
function offlineResponse() {
    return new Response(
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
        "<title>CareerPulse - Offline</title></head>" +
        "<body style=\"font-family:system-ui,sans-serif;background:#090d16;color:#e2e8f0;" +
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;" +
        "text-align:center;padding:24px\">" +
        "<div><h1 style=\"font-size:1.25rem;margin:0 0 8px\">You are offline</h1>" +
        "<p style=\"font-size:0.875rem;color:#94a3b8;margin:0\">" +
        "CareerPulse needs a connection to load your dashboard. " +
        "Please reconnect and try again.</p></div></body></html>",
        {
            status: 503,
            statusText: "Service Unavailable",
            headers: { "Content-Type": "text/html; charset=utf-8" }
        }
    );
}
