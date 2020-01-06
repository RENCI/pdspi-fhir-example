FROM python:3-alpine

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

RUN apk add --no-cache gcc musl-dev
RUN pip3 install --no-cache-dir flask pymongo gunicorn[gevent]==19.9.0 connexion[swagger-ui] requests flask-cors

COPY api/openAPI3 /usr/src/app/api
COPY pdsdpimockfhir /usr/src/app/pdsdpimockfhir

EXPOSE 8080

ENTRYPOINT ["gunicorn"]

CMD ["-w", "4", "-b", "0.0.0.0:8080", "--error-logfile", "-", "--capture-output", "--env", "SERVER_URL=/api", "api.server:create_app()"]
