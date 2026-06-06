# Branching Strategy

## Branches

- `main`: protected release branch. Direct pushes are disabled once repository protection is configured.
- `develop`: integration branch for tested work before release promotion.
- `feature/*`: feature development branches.
- `fix/*`: defect repair branches.

## Pull Requests

- PRs should target `develop` by default.
- Release PRs promote `develop` to `main`.
- Every PR should include scope, validation, and linked issue context when available.
- CI must pass before merge.

## Protection Checklist

Configure `main` in GitHub branch protection:

- Require pull request before merging.
- Require status checks to pass.
- Require conversation resolution.
- Disallow force pushes.
- Disallow deletions.
