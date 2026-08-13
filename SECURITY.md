# Security Policy

## Reporting a vulnerability

Please do not publish credentials, private media, or a working exploit in a public issue. Open a minimal GitHub Security Advisory for this repository, or contact the repository owner privately through the GitHub profile.

Include the affected version, reproduction steps, expected impact, and any suggested mitigation. Remove API keys, customer assets, signed URLs, and personal information from reports.

## Deployment guidance

- Keep `backend/.env` local and rotate any credential that has been exposed.
- Restrict upload types and sizes, and scan public deployments for malicious files.
- Put the API behind authentication before exposing it beyond localhost.
- Use tenant-isolated object storage and short-lived signed URLs.
- Validate provider webhook signatures and make billing operations idempotent.
- Treat generated product claims and depictions of identifiable people as requiring human approval.
- Do not use the bundled personal-studio JSON store for untrusted multi-user workloads.
