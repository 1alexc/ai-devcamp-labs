# Cloud Run image for the Social Spark frontend.
#
# The frontend talks to the ADK backend server-side only (app/lib/backend.ts),
# so the browser never needs a Google token. On Cloud Run the token comes from
# the attached service account, which needs roles/aiplatform.user — gcs-setup.sh
# Step 4 creates one. Build from the repo root:
#   docker build -f frontend/Dockerfile -t social-spark-frontend frontend
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined at build time, not read at runtime. Left unset so
# the gallery uses this app's own same-origin proxy routes; the Google sign-in
# client ID is optional (the app falls back to "Continue as guest").
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=""
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=$NEXT_PUBLIC_GOOGLE_CLIENT_ID
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# Cloud Run injects PORT; Next's standalone server reads it.
ENV PORT=8080
EXPOSE 8080
RUN useradd -m nextjs
COPY --from=builder --chown=nextjs:nextjs /app/public ./public
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static
USER nextjs
CMD ["node", "server.js"]
