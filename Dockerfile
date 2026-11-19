# Property Intelligence Chatbot: Streamlit + LangGraph. Fits Render free tier (512 MB RAM).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PORT=8501

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Runtime needs only the app code and the SQLite listings snapshot (vectors live in Qdrant Cloud).
COPY app/ app/
COPY .streamlit/ .streamlit/
COPY data/listings.db data/listings.db

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",\"8501\")}/_stcore/health', timeout=4)"

CMD ["sh", "-c", "streamlit run app/ui.py --server.port ${PORT} --server.address 0.0.0.0 --server.headless true"]
