FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Set by the deploy workflow; /health reports it (see app/config.py git_sha).
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
