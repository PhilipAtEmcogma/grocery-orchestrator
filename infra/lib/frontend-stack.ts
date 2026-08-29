/**
 * SCAFFOLD — FrontendStack (later): host the static chat UI.
 *
 * STATUS: stub. The UI does not exist yet. Implement against
 * infra/docs/09-FRONTEND.md (researched best practice) and
 * infra/docs/03-STACK-SPECS.md → FrontendStack.
 *
 * Contains, once implemented:
 *   - private S3 bucket (BLOCK_ALL public access; not a website endpoint)
 *   - CloudFront distribution with Origin Access Control (OAC, not legacy OAI),
 *     REDIRECT_TO_HTTPS, defaultRootObject index.html
 *   - SPA fallback: 403/404 → /index.html (200) for client-side routing
 *     (React/Vite or static HTML). Next.js static export would instead need a
 *     CloudFront Function URL-rewrite — see 09 §4.
 *   - a Response Headers Policy (HSTS, nosniff, frame-ancestors, CSP scoped to
 *     self + the API origin)
 *   - BucketDeployment with the `distribution` prop (auto cache invalidation)
 *
 * The CloudFront domain becomes the API's CORS_ORIGIN (two-pass deploy, 06 §3d).
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';

export interface FrontendStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
}

export class FrontendStack extends cdk.Stack {
  // The distribution domain — feed into ServiceStack's CORS_ORIGIN once built.
  public distributionDomainName?: string;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);
    // const { cfg } = props;

    // TODO: private S3 site bucket (BLOCK_ALL, S3_MANAGED encryption).
    // TODO: CloudFront Distribution with S3BucketOrigin.withOriginAccessControl(bucket).
    // TODO: SPA error responses 403/404 → /index.html (200).
    // TODO: ResponseHeadersPolicy (security headers + CSP connect-src = API origin).
    // TODO: BucketDeployment(sources=[frontend/dist], distribution=...) for invalidation.
    // TODO: expose this.distributionDomainName as a CfnOutput.

    cdk.Annotations.of(this).addInfo(
      'FrontendStack is a SCAFFOLD stub — no UI exists yet. See infra/docs/09-FRONTEND.md.',
    );
  }
}
