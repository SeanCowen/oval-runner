FROM rust:1.85-slim-bookworm@sha256:9f841bbe9e7d8e37ceb96ed907265a3a0df7f44e3737d0b100e7907a679acb36 AS planner
WORKDIR /build
COPY planner.rs .
RUN rustc --edition=2021 --test -O planner.rs -o check && ./check && rustc --edition=2021 -C opt-level=3 --crate-type cdylib planner.rs -o liboval.so
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OVAL_REQUIRE_RUST=1
COPY --from=planner /build/liboval.so .
COPY bot.py navigation.py mechfight.py strategy.py ./
USER 65534:65534
ENTRYPOINT ["python", "-u", "bot.py"]
