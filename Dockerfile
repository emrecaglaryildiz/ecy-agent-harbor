FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8080 DATABASE_PATH=/data/harbor.db
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm chromium && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 harbor && useradd --uid 10001 --gid harbor --no-create-home harbor && mkdir /data && chown harbor:harbor /data
COPY --chown=harbor:harbor app ./app
COPY --chown=harbor:harbor static ./static
COPY --chown=harbor:harbor run.py ./run.py
USER harbor
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)"
CMD ["python", "-m", "app.server"]
