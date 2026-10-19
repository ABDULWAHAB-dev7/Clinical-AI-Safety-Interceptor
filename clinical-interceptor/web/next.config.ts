import type { NextConfig } from "next";

// The Content-Security-Policy is set per request in src/proxy.ts, because it carries a nonce.
const nextConfig: NextConfig = {
  poweredByHeader: false,
  turbopack: {
    root: import.meta.dirname,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
