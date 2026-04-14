# dashboard/Dockerfile
# Multi-stage build for the Phoenix LiveView trading dashboard.
#
# Stage 1 (builder): compiles deps, builds assets, creates the Mix release.
# Stage 2 (runtime): minimal image that just runs the release binary.
#
# Build is triggered automatically by `docker compose up --build`.

# ── Stage 1: Builder ────────────────────────────────────────────────────────
FROM elixir:1.18-otp-27 AS builder

WORKDIR /app

ENV MIX_ENV=prod

# Build tools (Debian-based image — same glibc as the ubuntu:24.04 runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Hex + rebar
RUN mix local.hex --force && mix local.rebar --force

# Fetch deps first (separate layer — only rebuilds when mix.exs/mix.lock change)
# mix.lock is generated on first `mix deps.get` and committed for reproducible builds.
# If mix.lock doesn't exist yet, `mix deps.get` creates it.
COPY mix.exs ./
COPY mix.lock* ./

# Copy compile-time config before deps.compile so config changes
# correctly bust the deps layer cache.
COPY config/config.exs config/prod.exs config/

RUN mix deps.get --only prod
RUN mix deps.compile

# Copy lib before assets so tailwind can scan .heex/.ex files for class names
COPY lib lib

# Build assets (Tailwind + esbuild)
COPY assets assets
COPY priv priv
RUN mix assets.deploy

# Copy runtime config last — changes here don't require recompiling deps or assets
COPY config/runtime.exs config/

# Compile app and create release
RUN mix compile
RUN mix release

# ── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS runtime

# Runtime deps only (no build tools)
# locales required so Erlang/Elixir use UTF-8 name encoding, not latin1
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    libncurses6 \
    libstdc++6 \
    curl \
    locales \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

WORKDIR /app

# Copy the compiled release from the builder stage
COPY --from=builder /app/_build/prod/rel/dashboard ./

ENV PHX_SERVER=true
ENV MIX_ENV=prod

EXPOSE 4000

# Run migrations then start the server. `exec` replaces the shell as PID 1 so
# SIGTERM from Docker is forwarded correctly to the Erlang runtime.
CMD ["/bin/sh", "-c", "bin/dashboard eval 'Dashboard.Release.migrate()' && exec bin/dashboard start"]
