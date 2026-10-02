import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone — a self-contained server with only the node_modules
  // it actually uses, which is what the Dockerfile copies. Without this the
  // image has to carry the full dependency tree.
  output: "standalone",
};

export default nextConfig;
