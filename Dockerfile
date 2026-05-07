FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY web_server.py ./
COPY tools.py ./
COPY auth.py ./
COPY static/ ./static/

# 创建工作数据目录
RUN mkdir -p /app/data/files

EXPOSE 8888

CMD ["python", "web_server.py"]
