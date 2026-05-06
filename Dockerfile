FROM python:3.11-slim

WORKDIR /app

COPY web_server.py ./
COPY tools.py ./
COPY requirements.txt ./

RUN pip install --no-cache-dir python-dotenv

EXPOSE 8888

CMD ["python", "web_server.py"]
