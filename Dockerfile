FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY quantbot/ quantbot/
COPY run_bot.py config.example.yaml ./

# State and config are mounted at runtime; see docker-compose.yml.
# Secrets come from environment variables (T212_API_KEY etc.) — never bake
# them into the image.
ENTRYPOINT ["python", "run_bot.py"]
CMD ["run"]
