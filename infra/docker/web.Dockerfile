# MyTrakin web image.
# Build from the repository root: docker build -f infra/docker/web.Dockerfile .
# NEXT_PUBLIC_* values are baked at build time on purpose: they are public by
# definition (see docs/environment.md). Never pass a secret as a build arg.
FROM node:22-alpine AS deps
WORKDIR /srv/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /srv/web
COPY --from=deps /srv/web/node_modules ./node_modules
COPY apps/web/ ./
ARG NEXT_PUBLIC_API_BASE_URL=https://api.mytrakin.app
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_APP_NAME=MyTrakin
ARG NEXT_PUBLIC_ENABLE_AI=true
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY \
    NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \
    NEXT_PUBLIC_ENABLE_AI=$NEXT_PUBLIC_ENABLE_AI
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /srv/web
ENV NODE_ENV=production
COPY --from=builder /srv/web/.next/standalone ./
COPY --from=builder /srv/web/.next/static ./.next/static
COPY --from=builder /srv/web/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
