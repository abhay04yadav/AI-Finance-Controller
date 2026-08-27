/**
 * Next.js config. Guide §3.3, §8.
 *
 * Deliberately almost empty. No Tailwind, no CSS-in-JS, no chart library, no
 * icon package: the design in `docs/AI Finance Controller wireframes` is exact
 * hex values and exact pixel sizes, and a utility framework can only express
 * those as arbitrary values — `text-[#191713] text-[13.5px]` is Tailwind in
 * name only and strictly harder to read than the CSS it compiles to. The whole
 * dependency list is next, react and react-dom.
 *
 * The API runs separately on :8000. Rewrites proxy /api through the dev server
 * so the browser sees one origin and no CORS preflight on every action.
 */
const API = process.env.AFC_API_URL || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
export default {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
