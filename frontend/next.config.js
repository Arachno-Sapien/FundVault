// src/lib/api.js falls back to http://localhost:8000 and prefixes every
// endpoint with "/", so a deploy without the variable (or with a trailing
// slash) builds fine and then fails every request in the browser.
const apiBase = process.env.NEXT_PUBLIC_API_BASE;
if (process.env.VERCEL && !apiBase) {
  throw new Error(
    "NEXT_PUBLIC_API_BASE is not set. Set it in the Vercel project's environment variables " +
      "to the backend origin (e.g. https://api.example.com) and redeploy."
  );
}
if (apiBase && apiBase.endsWith("/")) {
  throw new Error(`NEXT_PUBLIC_API_BASE must not end with a slash (got "${apiBase}").`);
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  turbopack: {
    root: __dirname
  }
};

module.exports = nextConfig;
