# syntax=docker/dockerfile:1
# Frontend image (Next.js). Build context = repo root (pnpm workspace lives there).
FROM node:20-slim

RUN corepack enable

WORKDIR /repo

# Workspace manifests + app source, then a full workspace install so pnpm links
# frontend/node_modules correctly (install-before-copy with --filter leaves the
# next binary unlinked).
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend ./frontend
RUN pnpm install --frozen-lockfile

# NEXT_PUBLIC_* is baked at build time; the browser runs on the host, so it must
# reach the backend via the published host port (default http://localhost:8000),
# not the internal service name.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN pnpm --filter @huading/frontend build

WORKDIR /repo/frontend
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
EXPOSE 3000
CMD ["pnpm", "start"]
