FROM alpine:3.6

RUN apk add --update --no-cache --virtual=run-deps \
  python3 \
  ca-certificates \
  py3-psycopg2 \
  vim

ENV SLACK_WEBHOOK_URL example_value
ENV SLEEP_SECONDS 60
ENV ENDPOINT_DEFINITIONS_FILE /opt/app/config/endpoints.json
ENV ALERT_DEFINITIONS_FILE /opt/app/config/alerts.json
ENV DB_NAME cupcake.db
ENV DB_TYPE sqlite
ENV CONNECTION_TIMEOUT_SECONDS 10

WORKDIR /opt/app
CMD ["python3", "-u", "main.py"]

COPY app/requirements.txt /opt/app/
RUN pip3 install --no-cache-dir -r /opt/app/requirements.txt

COPY config /opt/app/config/
COPY app /opt/app/
