import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Backend is on a separate port (8000) — proxy API + WebSocket in dev.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
      { source: "/ws", destination: "ws://localhost:8000/api/v1/ws" },
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
