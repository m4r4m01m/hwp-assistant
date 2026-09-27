FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS dependencies
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

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
COPY scripts ./scripts
COPY --from=dependencies /app/frontend/node_modules/fflate ./frontend/node_modules/fflate
RUN node scripts/build-release-core.mjs

FROM scratch AS core-export
COPY --from=core-build /app/.cache/rhwp-core-release/pkg/ /

FROM dependencies AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY scripts /app/scripts
COPY --from=core-build /app/.cache/rhwp-core-release/pkg /app/.cache/rhwp-core-release/pkg
RUN npm run engine
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM nginx:1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
