import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Backend is on a separate port (8000) — proxy REST API in dev.
  // WebSocket connections to /api/v1/ws are opened directly by the browser
  // (the page constructs ws:// URLs from window.location), so no rewrite is
  // needed for ws:// — Next.js rewrites only support http(s) destinations.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
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
