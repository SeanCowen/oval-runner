FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY bot.py navigation.py mechfight.py ./
USER 65534:65534
ENTRYPOINT ["python", "-u", "bot.py"]
