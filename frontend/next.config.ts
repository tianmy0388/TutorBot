import type { NextConfig } from "next";

const BACKEND_PORT = process.env.BACKEND_PORT || process.env.TUTOR_PORT || "8000";
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ["127.0.0.1"],
  // Backend is on a separate port — proxy REST API in development.
  // WebSocket clients connect directly to the backend port because the
  // development rewrite does not reliably forward upgrade requests.
  //
  // **2026-07-09 fix (38a445a1 trace):** the FastAPI app now mounts
  // ``StaticFiles`` at ``/static/manim`` so the rendered MP4 actually
  // resolves. Without this rule the proxy chain would terminate at
  // Next.js' own static handler and 404. Each route that the
  // backend serves under ``/static/...`` needs its own rewrite —
  // ``/api/*`` covers JSON/REST but binary files served outside
  // that prefix are a separate concern.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` },
      {
        source: "/static/manim/:path*",
        destination: `${BACKEND_ORIGIN}/static/manim/:path*`,
      },
    ];
  },
  experimental: {
    // Enable React 19 server actions if needed
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
};

export default nextConfig;
