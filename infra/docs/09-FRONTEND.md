# 09 — Frontend Hosting (S3 + CloudFront)

> **Status: Design documentation for the HOSTING, which does not exist.** The
> chat UI itself does — a Vite/React client in `frontend/`, merged 2026-08-31,
> which answers the framework question this doc left open. What is still
> unbuilt is `FrontendStack`: the bucket, the distribution and the deploy. Read
> §4 with React/Vite as settled rather than as a choice. It expands the `FrontendStack` sketch
> in [03-STACK-SPECS](03-STACK-SPECS.md) and resolves
> [08-OPEN-DECISIONS §7](08-OPEN-DECISIONS.md).

## 1. What the frontend has to be

The UI is a **single-page chat interface**. The user types a message, it calls
`POST /chat` (the contract in [`FRONTEND-INTEGRATION.md`](../../FRONTEND-INTEGRATION.md)),
and renders the streamed/returned `ChatResponse` — price comparisons and meal
plans with their citations. That's it. There is:

- **one page** (no marketing site, no blog, no multi-route content to index);
- **no SEO requirement** (a shopper tool, not public content);
- **no server-side rendering need** (all dynamism is the chat API call);
- a hard **$0 hosting** target.

Those four facts drive every choice below.

## 2. Framework decision (researched)

All three candidates below produce **static assets** that host identically on
S3 + CloudFront at $0. The difference is build tooling and fit.

| Option | What it is | Fit for a one-page chat UI | Verdict |
|--------|-----------|----------------------------|---------|
| **Plain static HTML/CSS/JS** | hand-written `index.html` + a JS file | Perfect — it *is* one page; zero build; matches the project's original architecture diagram | ✅ Great for a workshop demo |
| **React SPA (Vite)** | component model, `vite build` → static `dist/` | Perfect — one bundle, client-side only, great DX, easy chat-state handling | ✅ **Recommended** if the team wants structure |
| **Next.js static export** (`output: 'export'`) | React framework, exports static HTML | **Overkill** — its value is SSR/SSG/routing/SEO, *none* of which a single anonymous chat page needs; static export also adds routing caveats (see §4) | ⚠️ Allowed, but the most tooling for the least benefit |

### Why Next.js is the weakest fit here — the reasoning

Next.js earns its keep when you have **multiple pages to pre-render for SEO** or
need **server-side rendering**. A single anonymous chat page has neither. Its
static export then behaves as a *multi-file SSG* site, which needs **CloudFront
URL-rewriting** rather than the simpler SPA fallback (see §4) — i.e. it *adds* a
CloudFront moving part to solve a problem (SEO-clean multi-page routing) you
don't have. The boilerplate brief named Next.js, but the boilerplate described a
different app; for *this* one, the lighter tools are the better engineering call.

### Recommendation

**A React (Vite) SPA**, or **plain static HTML/JS** if the team prefers the
absolute minimum. Both are single-bundle SPAs, both host with the simple pattern
in §3–§4, both are $0. Pick React/Vite if you want component structure and a
tidy way to manage chat state; pick plain HTML if you want to be able to read
the entire frontend in one sitting. **Leave Next.js for a future marketing
site**, not the chat tool.

## 3. The hosting pattern (S3 + CloudFront + OAC)

This is the current best-practice shape (2024–2026), and it's what
`FrontendStack` implements:

- **Private S3 bucket** — `blockPublicAccess: BLOCK_ALL`. The bucket is **not** a
  website endpoint and **not** public.
- **CloudFront with Origin Access Control (OAC)** — the modern replacement for
  the legacy Origin Access Identity (OAI). CloudFront is the *only* thing that
  can read the bucket; users can't reach S3 directly.
- **HTTPS enforced** — `viewerProtocolPolicy: REDIRECT_TO_HTTPS`. TLS terminates
  at CloudFront (free default certificate on the `*.cloudfront.net` domain; a
  custom domain would use ACM).
- **`defaultRootObject: 'index.html'`.**
- **A CloudFront Response Headers Policy** for security headers — HSTS,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`/frame-ancestors,
  Referrer-Policy, and a Content-Security-Policy scoped to *self* plus the API
  origin. CDK exposes this as `ResponseHeadersPolicy` (there's a managed
  `SECURITY_HEADERS` policy to start from).
- **Cache invalidation on deploy** — CDK's `BucketDeployment` with the
  `distribution` prop auto-invalidates the CloudFront cache each deploy, so a new
  build is visible immediately instead of after the TTL.

## 4. Client-side routing: the one CloudFront subtlety

This is the detail most people get wrong, and it depends on the framework choice.

- **For a SPA (React/Vite or plain HTML) — use CloudFront custom error
  responses.** A client-side router (or a single page) means *every* path must
  return `index.html` so the JS can boot. Map CloudFront **403 and 404 → return
  `/index.html` with HTTP 200**. Rule of thumb from the research: *"1 file = SPA
  → use error responses."* For a literal single page this is trivial but still
  the correct config so a deep link or refresh doesn't 403.
- **For Next.js static export (SSG, multi-file) — do NOT use the error-response
  fallback.** It would make every URL serve the same HTML, which *"breaks
  discoverability — all pages look the same to search engines."* Instead use a
  **CloudFront Function** to rewrite `/about` → `/about/index.html` *before* S3.
  Rule of thumb: *"Multiple files = SSG → use URL rewrite."*

This asymmetry is the concrete reason the lighter SPA options are simpler here:
they use the one-line error-response fallback; Next.js export pulls in a
CloudFront Function you'd otherwise not need.

## 5. Wiring to the API (the CORS loop)

The frontend calls `POST /chat` on API Gateway. Two coupling points:

1. **`CORS_ORIGIN`.** The API's allowed origin (production mode: never `*`) is
   the **CloudFront domain** of this stack. Because that's a frontend→service
   reference, resolve it by deploying the frontend first and passing its domain
   into the service stack's `CORS_ORIGIN` (the two-pass deploy in
   [06-DEPLOYMENT-GUIDE §3d](06-DEPLOYMENT-GUIDE.md)), or by fixing a custom
   domain up front.
2. **The API base URL** is injected into the frontend build (a `VITE_API_URL`
   env at build time, or a small `config.json` fetched at runtime). Do **not**
   hardcode it in source.

Keep the CSP's `connect-src` limited to the API Gateway origin so the page can
only talk to your backend.

## 6. Cost

**$0 at workshop scale.** CloudFront's **always-free** tier is 1 TB egress +
10M requests/month; S3 storage for a small bundle is cents (free-tier eligible
in year one). No server, no build service required (the build runs in CI /
locally). See [07-COST-AND-SCALING](07-COST-AND-SCALING.md).

## 7. Deferred (market-stage)

- **AWS WAF** on the distribution — before any public/authenticated surface
  (`security.md`). Costs ~$5/mo + rules; noted, not built.
- **Cognito Hosted UI / login** — when the pilot stops being anonymous.
- **Custom domain + ACM certificate** — cosmetic for the pilot.

## 8. References

- Deploying a React + Vite SPA to a private S3 bucket with CloudFront and OAC — https://dev.to/one-beyond/deploying-a-react-vite-spa-to-a-private-s3-bucket-with-cloudfront-and-oac-mhh
- CloudFront routing: SPAs vs Static Site Generation (error responses vs URL rewrite) — https://geekcafe.com/blog/2025/07/cloudfront-routing-spa-vs-static
- Choosing between CloudFront+S3, Amplify, and ECS for frontends — https://dev.to/muhammad_ahmad_khan/from-static-to-runtime-choosing-between-cloudfront-s3-amplify-and-ecs-for-frontends-on-aws-15i5
- Securing CloudFront with a Response Headers Policy (CDK) — https://knowledge.businesscompassllc.com/secure-your-cloudfront-distribution-response-headers-policy-in-aws-cdk/
- Content Security Policies for CloudFront single-page apps — https://levischuck.com/blog/2024-02-content-security-policies-cloudfront-aws-single-page-apps
- AWS CDK `ResponseHeadersPolicy` API — https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_cloudfront.ResponseHeadersPolicy.html
