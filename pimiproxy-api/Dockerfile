FROM python:3.12-alpine
RUN apk add --no-cache docker-cli
RUN pip install --no-cache-dir django gunicorn
COPY . /app
WORKDIR /app
EXPOSE 8000
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
