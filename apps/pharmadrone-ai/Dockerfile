FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PHARMADRONE_AI_API_DOCS=0

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY pharmadrone ./pharmadrone
COPY pharmadrone_ai ./pharmadrone_ai
COPY apps/pharmadrone-ai ./apps/pharmadrone-ai

RUN chmod 0755 /app/apps/pharmadrone-ai/start-production.sh \
    && addgroup --system pharmadrone \
    && adduser --system --ingroup pharmadrone pharmadrone \
    && chown -R pharmadrone:pharmadrone /app
USER pharmadrone

EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

ENTRYPOINT ["/app/apps/pharmadrone-ai/start-production.sh"]
CMD ["python", "-m", "uvicorn", "pharmadrone_ai.app:app", "--host", "0.0.0.0", "--port", "8000"]
