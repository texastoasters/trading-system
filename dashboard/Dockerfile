# dashboard/Dockerfile
# Multi-stage build for the Phoenix LiveView trading dashboard.
#
# Stage 1 (builder): compiles deps, builds assets, creates the Mix release.
# Stage 2 (runtime): minimal image that just runs the release binary.
#
# Build is triggered automatically by `docker compose up --build`.

# ── Stage 1: Builder ────────────────────────────────────────────────────────
FROM elixir:1.18-otp-27-alpine AS builder

WORKDIR /app

ENV MIX_ENV=prod

# Build tools
RUN apk add --no-cache build-base git nodejs npm

# Hex + rebar
RUN mix local.hex --force && mix local.rebar --force

# Fetch deps first (separate layer — only rebuilds when mix.exs/mix.lock change)
# mix.lock is generated on first `mix deps.get` and committed for reproducible builds.
# If mix.lock doesn't exist yet, `mix deps.get` creates it.
COPY mix.exs ./
COPY mix.lock* ./
RUN mix deps.get --only prod
RUN mix deps.compile

# Build assets (Tailwind + esbuild)
COPY assets assets
COPY priv priv
RUN mix assets.deploy

# Compile app and create release
COPY lib lib
COPY config config
RUN mix compile
RUN mix release

# ── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS runtime

# Runtime deps only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    libncurses6 \
    libstdc++6 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the compiled release from the builder stage
COPY --from=builder /app/_build/prod/rel/dashboard ./

ENV PHX_SERVER=true
ENV MIX_ENV=prod

EXPOSE 4000

# Bin entrypoint — reads PHX_HOST, SECRET_KEY_BASE, DATABASE_URL, REDIS_URL from env
CMD ["bin/dashboard", "start"]
