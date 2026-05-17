FROM python:3.11-slim
RUN apt-get update && apt-get install -y locales && \
    echo "ru_RU.UTF-8 UTF-8" >> /etc/locale.gen && \
    locale-gen ru_RU.UTF-8
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
