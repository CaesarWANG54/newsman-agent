import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig = {
  distDir: process.env.NEWSMAN_BUILD_DIR || ".next",
  outputFileTracingRoot: __dirname,
  reactStrictMode: true
};

export default nextConfig;
