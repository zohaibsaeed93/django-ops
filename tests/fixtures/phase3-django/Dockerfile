FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir Django==5.1.7 gunicorn==23.0.0
COPY . /app
EXPOSE 8000
