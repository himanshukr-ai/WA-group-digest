FROM python:3.12-slim

WORKDIR /app

# Install the package first so dependency layers cache independently of app code changes.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY prompts ./prompts
COPY groups.yaml ./groups.yaml

EXPOSE 8000

CMD ["python", "-m", "app", "serve"]
