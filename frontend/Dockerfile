FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS dependencies
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --fund=false

FROM rust:1.93.1-bookworm@sha256:7c4ae649a84014c467d79319bbf17ce2632ae8b8be123ac2fb2ea5be46823f31 AS core-build
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=dependencies /usr/local/bin/node /usr/local/bin/node
RUN rustup target add wasm32-unknown-unknown \
    && cargo install --locked wasm-pack --version 0.15.0 \
    && cargo install --locked wasm-bindgen-cli --version 0.2.127
# wasm-pack 0.15.0's Binaryen version, pinned explicitly to prevent PATH/fallback drift.
# This recipe targets linux/amd64; unsupported platforms fail clearly.
RUN test "$(uname -m)" = x86_64 \
    && curl -fL --retry 3 https://github.com/WebAssembly/binaryen/releases/download/version_117/binaryen-version_117-x86_64-linux.tar.gz -o /tmp/binaryen.tar.gz \
    && echo '3dc677006555b355ea2da5e82602065a161d5e83eaefd3f759afa00b96e83212  /tmp/binaryen.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/binaryen.tar.gz -C /opt \
    && rm /tmp/binaryen.tar.gz
ENV PATH="/opt/binaryen-version_117/bin:${PATH}" HWP_CORE_BUILD_ENV=docker-linux CARGO_TARGET_DIR=/build-target
WORKDIR /app
# Only native-core inputs belong in this expensive stage. Deployment scripts,
# UI tests and the Studio bridge must not invalidate a pinned Rust/WASM build.
COPY scripts/build-release-core.mjs scripts/engine-build-lib.mjs scripts/engine-lock.json \
    scripts/prepare-patched-core.py scripts/record-patched-core.mjs \
    scripts/test-engine-fill.mjs scripts/test-engine-selection.mjs ./scripts/
COPY scripts/patches ./scripts/patches
COPY --from=dependencies /app/frontend/node_modules/fflate ./frontend/node_modules/fflate
# Keep verified package bytes, not gigabytes of Cargo targets/vendor checkout.
# Cleanup must be in this same RUN; a later layer would still export those bytes.
RUN node scripts/build-release-core.mjs \
    && mkdir -p /out \
    && cp -a /app/.cache/rhwp-core-release/pkg/. /out/ \
    && rm -rf /build-target /app/.cache /usr/local/cargo/registry /usr/local/cargo/git

FROM scratch AS core-export
COPY --from=core-build /out/ /

FROM dependencies AS build
ARG HWP_PUBLIC_BASE_PATH=/
ENV HWP_PUBLIC_BASE_PATH=${HWP_PUBLIC_BASE_PATH}
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY scripts /app/scripts
COPY frontend/src/block-layout.ts /app/frontend/src/block-layout.ts
COPY --from=core-build /out/ /app/.cache/rhwp-core-release/pkg/
RUN node --test /app/scripts/public-base.test.mjs && npm run engine
COPY frontend/index.html frontend/compare-renderer.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN node /app/scripts/audit-public-paths.mjs && npm run build

FROM nginx:1.30.6-alpine@sha256:c59d1f8b1e0e42da75be973424cd72e23f7f8adfb54e982f4d1cd0216a5a5576
ARG HWP_REVISION=development
LABEL org.opencontainers.image.revision=${HWP_REVISION}
ARG HWP_NGINX_CONF=frontend/nginx.conf
COPY ${HWP_NGINX_CONF} /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
