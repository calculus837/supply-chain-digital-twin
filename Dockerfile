# ══════════════════════════════════════════════════════════════════════
#  Project Icarus — Multi-Stage Dockerfile
#  Stage 1: Compile the Rust optimizer (release)
#  Stage 2: Slim Python runtime with Streamlit web UI
# ══════════════════════════════════════════════════════════════════════

# ── Stage 1: Build the Rust optimizer ────────────────────────────────
FROM rust:1.85-slim AS builder

WORKDIR /build

# Copy only the manifest and source — deliberately omit Cargo.lock
# to avoid the 'lock file version 4' incompatibility error.
COPY agents/optimizer/Cargo.toml ./
COPY agents/optimizer/src ./src

# Build in release mode
RUN cargo build --release \
    && strip target/release/optimizer

# ── Stage 2: Python runtime + Streamlit UI ───────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="Project Icarus" \
      description="Agentic Supply Chain Digital Twin — Web UI"

WORKDIR /app

# Install system packages needed for binary execution
RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the compiled Rust binary from the builder stage
COPY --from=builder /build/target/release/optimizer /app/agents/optimizer/target/release/optimizer

# Copy Python source, digital twin, and Streamlit app
COPY agents/bridge.py         /app/agents/bridge.py
COPY agents/world_state.json  /app/agents/world_state.json
COPY app.py                   /app/app.py

# Streamlit configuration: disable telemetry, set port
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
