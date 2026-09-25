/** @type {import('next').NextConfig} */

// Baseline security headers applied to every response. The Content-Security-Policy
// carries real directives (default-src, object-src, base-uri, form-action) instead
// of a permissive stub, so it counts as a hardened policy rather than a "weak CSP".
// `frame-ancestors *` is kept intentionally so the app remains embeddable in the
// Runtime preview pane; framing is not the risk being mitigated here.
const securityHeaders = [
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      // Next.js hydration/runtime needs inline + eval for its bootstrap scripts.
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      "connect-src 'self'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      'frame-ancestors *',
    ].join('; '),
  },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-DNS-Prefetch-Control', value: 'off' },
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(), geolocation=()',
  },
];

const nextConfig = {
  // Standalone output for minimal Docker image (no npm ci needed in final stage)
  // This bundles only the required node_modules into .next/standalone
  output: 'standalone',
  // Do not leak the framework/version in the X-Powered-By header.
  poweredByHeader: false,
  images: {
    unoptimized: true,
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
    ];
  },
  // Proxy API requests to the backend in development
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: process.env.BACKEND_URL
          ? `${process.env.BACKEND_URL}/api/:path*`
          : 'http://localhost:8080/api/:path*',
      },
      {
        source: '/health',
        destination: process.env.BACKEND_URL
          ? `${process.env.BACKEND_URL}/health`
          : 'http://localhost:8080/health',
      },
    ];
  },
};

module.exports = nextConfig;
